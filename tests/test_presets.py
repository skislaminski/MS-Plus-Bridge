"""The presets of the pedal from the interface: choosing one (bank select and program change, as
the pedal sends them when one is chosen on it), their names (read from their places, `46 00 00 …`),
and storing the preset on the pedal into its own place or another one (`45 00 00 …`, the form in
which the pedal itself reports a preset saved on it; tests/pedal_patches.json has three)."""
import json
import unittest
from unittest import mock

import mido
import yaml

import bridge
import zoom_sysex as zs
from tests.sim import ID, PROJECT, SimulatedCase, World, mapping, patch_dump, sysex

STOCK, CHORUS, ROOM = 0x01000020, 0x06000020, 0x0B000020      # BlackOpt, ChorusOne, BrghtRoom
HERE, OTHER = "0/8", (4, 1)                                    # preset 009, and 042
REAL = json.loads((PROJECT / "tests" / "pedal_patches.json").read_text(encoding="utf-8"))


def stored(effects, name: str) -> bytes:
    """A preset as it is kept in a place of the pedal, unpacked."""
    return zs.unpack_7bit(patch_dump([(effect, 0) for effect in effects], name=name)[9:-6])


def ids(data: bytes) -> list:
    return [int.from_bytes(data[36 + 4 * slot:40 + 4 * slot], "little")
            for slot in range(int.from_bytes(data[12:16], "little"))]


class Messages(unittest.TestCase):
    def test_a_preset_stored_as_the_pedal_reports_one_saved_on_it(self):
        for saved in REAL["saved"]:
            message = bytes.fromhex(saved["message"])
            bank, program, _, data = zs.parse_stored_preset(message[4:-1])
            self.assertEqual(zs.build_save_preset(ID, bank, program, data), message, saved["when"])

    def test_only_the_prepared_preset_and_only_into_a_place_there_is(self):
        data = zs.parse_stored_preset(bytes.fromhex(REAL["saved"][0]["message"])[4:-1])[3]
        message = zs.build_save_preset(ID, *OTHER, data)
        self.assertEqual(zs.classify(message, ID, upload=message, upload_kind="save_preset"), "save_preset")
        for upload, kind in ((None, "save_preset"), (message, "move_effect")):
            with self.assertRaises(zs.NotAllowed):
                zs.classify(message, ID, upload=upload, upload_kind=kind)
        with self.assertRaises(ValueError):
            zs.build_save_preset(ID, 10, 0, data)                        # ten banks of ten
        self.assertEqual(zs.classify(zs.build_query_preset(ID, 9, 9), ID), "query_preset")
        for refused in ("F0 52 00 6E 46 00 00 0A 00 00 00 F7",            # no bank 10
                        "F0 52 00 6E 32 01 00 00 05 00 00 00 00 00 F7"):  # the store of the original MS series
            with self.assertRaises(zs.NotAllowed):
                zs.classify(bytes.fromhex(refused.replace(" ", "")), ID)


class PresetCase(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "select_preset", "query_preset", "save_preset")
        patcher = mock.patch.object(bridge, "PRESET_READ_GAP", 0.001)     # the pedal is quicker here
        patcher.start()
        self.addCleanup(patcher.stop)

    def presets(self, script=(), length=2.3) -> World:
        world = World(script, program=8, chain=[STOCK, CHORUS], length=length)
        world.stored = {(0, 8): stored([STOCK, CHORUS], "Mine"), OTHER: stored([ROOM], "Other")}
        return world

    def run_calls(self, *calls, world=None, mappings=None, until=2.0) -> list:
        self.install(mappings if mappings is not None else {HERE: [mapping(1, 2, effect=f"{CHORUS:08X}")]})
        seen = []

        def client(connection):
            connection.at(1.4)                         # the names are read meanwhile
            for call in calls:
                if isinstance(call, float):
                    connection.at(call)
                else:
                    seen.append(connection.call("/api/preset", call)[:2])
            connection.at(until)
            seen.append(connection.call("/api/state")[1])

        world = world or self.presets(length=until + 0.3)
        self.run_bridge(world, client)
        self.world = world
        return seen

    def chosen(self) -> list:
        return [(message.type, getattr(message, "control", None), message.value if message.type == "control_change"
                 else message.program) for message in self.world.channel_messages]


class Choosing(PresetCase):
    def test_their_names_are_read_for_the_list(self):
        state, = self.run_calls(until=1.6)
        names = {(entry["bank"], entry["program"]): entry["name"] for entry in state["presets"]["list"]}
        self.assertEqual((len(names), names[(0, 8)], names[OTHER], names[(9, 9)]), (100, "Mine", "Other", "Empty"))
        self.assertFalse(state["presets"]["reading"])
        self.assertEqual(state["presets"]["list"][41]["display"], "042")

    def test_a_preset_is_chosen_and_the_pedal_loads_it_as_stored(self):
        reply, state = self.run_calls({"action": "select", "bank": 4, "program": 1})
        self.assertEqual(reply, (200, {"result": None}))
        self.assertEqual(self.chosen(), [("control_change", 0, 0), ("control_change", 32, 4), ("program_change", None, 1)])
        self.assertEqual(state["patch"]["key"], "4/1")
        self.assertEqual([entry["name"] for entry in state["chain_effects"]], ["BrghtRoom"])

    def test_a_preset_saved_on_the_pedal_is_listed_under_its_new_name(self):
        report = sysex(zs.build_save_preset(ID, *OTHER, stored([CHORUS], "Renamed")))
        state, = self.run_calls(world=self.presets([(1.5, lambda world: world.zoom_queue.append(report))]))
        self.assertEqual(state["presets"]["list"][41]["name"], "Renamed")


class Saving(PresetCase):
    def test_into_its_own_place(self):
        world = self.presets([(1.2, lambda world: world.swap_effect(1, ROOM))])     # changed on the pedal
        reply, state = self.run_calls({"action": "save", "bank": 0, "program": 8}, world=world)
        self.assertEqual(reply, (200, {"result": None}))
        self.assertEqual(ids(self.world.stored[(0, 8)]), [STOCK, ROOM])
        self.assertEqual(state["presets"]["result"], {"ok": True, "text": "Preset 009 saved"})
        self.assertEqual(self.chosen(), [])                                         # it stays where it is

    def test_into_another_place_with_what_the_bridge_keeps_for_it(self):
        reply, state = self.run_calls({"action": "save", "bank": 4, "program": 1}, until=2.3)
        self.assertEqual(ids(self.world.stored[OTHER]), [STOCK, CHORUS])
        self.assertEqual(state["presets"]["result"]["text"], "Saved as preset 042 – the pedal shows it now")
        self.assertEqual(self.chosen()[-1], ("program_change", None, 1))
        self.assertEqual(state["patch"]["key"], "4/1")
        mappings = yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))["mappings"]
        self.assertEqual(mappings["4/1"], mappings[HERE])
        self.assertEqual(state["presets"]["list"][41]["name"], "USER-001")         # the name the patch has

    def test_a_preset_chosen_on_the_pedal_before_the_patch_came_is_not_saved(self):
        # the footswitch picks preset 002 just before the pedal answers the query: 002's patch
        # went into place 009, and the result said "Preset 009 saved"
        world = self.presets(length=2.6)
        receive = world.receive

        def footswitch_first(message, world=world):
            raw = bytes(message.bytes()) if message.type == "sysex" else b""
            if raw[4:6] == b"\x64\x13" and getattr(world, "switch_next", False):
                world.switch_next = False
                world.new_chain([ROOM])
                world.preset_change(1)
            receive(message)

        world.receive = footswitch_first
        world.script.append((1.45, lambda world: setattr(world, "switch_next", True)))
        world.script.sort(key=lambda item: item[0])
        _, state = self.run_calls(1.5, {"action": "save", "bank": 0, "program": 8}, world=world, until=2.3)
        self.assertEqual(ids(self.world.stored[(0, 8)]), [STOCK, CHORUS])          # as it was
        self.assertEqual(state["presets"]["result"], {"ok": False, "text": "Saving stopped: preset changed"})

    def test_a_place_the_pedal_does_not_read_back_is_said(self):
        world = self.presets(length=5.0)
        world.answers_presets = False
        *_, state = self.run_calls({"action": "save", "bank": 0, "program": 8}, world=world, until=4.7)
        self.assertEqual(state["presets"]["result"], {
            "ok": False, "text": "The pedal did not answer: whether preset 009 holds it is not known"})
        self.assertIn("the pedal does not answer for its stored presets", self.output)
        self.assertIsNone(state["presets"]["list"][41]["name"])


class Undoing(PresetCase):
    def test_a_save_into_another_place_is_taken_back(self):
        _, reply, state = self.run_calls({"action": "save", "bank": 4, "program": 1}, 2.0, {"action": "undo"}, until=3.2)
        self.assertEqual(reply, (200, {"result": None}))
        self.assertEqual(ids(self.world.stored[OTHER]), [ROOM])                    # as it was before
        self.assertEqual(state["presets"]["result"]["text"], "Preset 042 is as it was before")
        self.assertIsNone(state["presets"]["undo"])
        mappings = yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))["mappings"]
        self.assertNotIn("4/1", mappings)                                           # what the bridge kept went back too
        self.assertIn(HERE, mappings)

    def test_a_save_into_its_own_place_is_taken_back(self):
        world = self.presets([(1.2, lambda world: world.swap_effect(1, ROOM))])
        self.run_calls({"action": "save", "bank": 0, "program": 8}, 2.0, {"action": "undo"}, world=world, until=3.2)
        self.assertEqual(ids(self.world.stored[(0, 8)]), [STOCK, CHORUS])

    def test_nothing_to_undo_before_a_save(self):
        reply = self.run_calls({"action": "undo"})[0]
        self.assertEqual(reply, (400, {"error": "There is no save to undo."}))

    def test_a_save_whose_place_was_not_read_yet_leaves_nothing_to_undo(self):
        # not the save before it: undo wrote preset 002 back after a save into 100
        with mock.patch.object(bridge, "PRESET_READ_GAP", 0.03):                     # 100 is read last
            *_, state = self.run_calls(0.8, {"action": "save", "bank": 0, "program": 1}, 2.2,
                                       {"action": "save", "bank": 9, "program": 9}, world=self.presets(length=4.0),
                                       until=3.6)
        self.assertEqual(state["presets"]["result"]["text"][:20], "Saved as preset 100 ")
        self.assertIsNone(state["presets"]["undo"])

    def test_the_symbol_shows_what_it_takes_back(self):
        *_, state = self.run_calls({"action": "save", "bank": 4, "program": 1}, until=2.3)
        self.assertEqual(state["presets"]["undo"], {"display": "042", "name": "Other"})


class Approval(PresetCase):
    def test_nothing_without_approval(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        replies = self.run_calls({"action": "select", "bank": 4, "program": 1},
                                 {"action": "save", "bank": 0, "program": 8})
        self.assertIn("python probe.py approve select_preset", replies[0][1]["error"])
        self.assertIn("python probe.py approve save_preset query_preset query_patch", replies[1][1]["error"])
        self.assertEqual(self.chosen(), [])
        self.assertFalse(any(hex_[12:14] in ("45", "46") for _, hex_ in self.world.sent))
        self.assertEqual((replies[-1]["presets"]["select"], replies[-1]["presets"]["save"]), (False, False))

    def test_no_preset_the_pedal_does_not_have(self):
        reply, _ = self.run_calls({"action": "select", "bank": 10, "program": 0})
        self.assertEqual(reply, (400, {"error": "The pedal has no preset 101."}))


if __name__ == "__main__":
    unittest.main()
