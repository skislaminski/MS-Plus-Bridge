"""What a modified firmware of the pedal keeps and does itself (pedal_firmware.py): the question for it,
scene A and B, the active scene, the expression targets, and which way notes and the expression pedal
go – against the simulated pedal, which alone knows the PROVISIONAL form of the messages.
"""
import unittest
from unittest import mock

import yaml

import bridge
import pedal_firmware as pf
import zoom_sysex as zs
from tests.sim import ID, KEYBOARD_PORT, World, cc, learned, mapping, note_off, note_on
from tests.test_effects import FIRST, SECOND, THIRD, EffectCase
from tests.test_synth import EVERYTHING

KEYSYNTH, DELAY, ROOM = 0x07000F61, 0x0A000020, 0x0B000020
SIX = [FIRST, SECOND, THIRD, DELAY, ROOM, 0x06000010]
ALL = pf.SCENES | pf.REPORTS_ACTIVE | pf.PLAYS_NOTES | pf.EXPRESSION_TARGETS
PLACE, OTHER = (9, 4), (9, 5)                    # the simulated pedal's preset 095, and 096
KEY = "9/4"


def scenes(a=None, b=None) -> dict:
    """Scene A and B from (effects, values) pairs."""
    made = pf.empty()
    for name, given in (("A", a), ("B", b)):
        if given:
            made[name] = {"effects": dict(given[0]), "values": dict(given[1])}
    return made


STORED = scenes(a=({0: True, 2: False}, {}))
WITH_VALUES = scenes(a=({0: True, 2: False}, {(0, 2): (FIRST, 40)}))
TARGETS = [(0, FIRST, 2, 0, 100), (1, SECOND, 3, 80, 20)]


class Provisional(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()                        # a SimulatedCase's own temporary installation too
        patcher = mock.patch.object(pf, "COMMAND", pf.PROVISIONAL)
        patcher.start()
        self.addCleanup(patcher.stop)


class Messages(Provisional):
    def test_scenes_and_targets_go_into_bytes_and_back(self):
        full = scenes(a=({slot: slot % 2 == 0 for slot in range(6)},
                         {(slot, 2 + slot % 3): (effect, 10 * slot) for slot, effect in enumerate(SIX)}),
                      b=({5: False}, {(3, 2): (DELAY, 962)}))
        for case in (pf.empty(), STORED, WITH_VALUES, full):
            with self.subTest(case=case):
                self.assertEqual(pf.decode(pf.encode(case)), case)
        self.assertEqual(pf.encode(pf.empty()), bytes(6))                       # two empty scenes
        for case in ([], TARGETS):
            self.assertEqual(pf.decode_targets(pf.encode_targets(case)), case)
        for message in (pf.build_write(ID, *PLACE, WITH_VALUES), pf.build_write_targets(ID, *PLACE, TARGETS)):
            self.assertTrue(all(byte < 0x80 for byte in message[1:-1]))
        for wrong in (b"\x01", bytes(5), bytes([0x40, 0, 0, 0, 0, 0]), pf.encode(STORED) + b"\x00"):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                pf.decode(wrong)
        for wrong in (b"", b"\x01", pf.encode_targets(TARGETS) + b"\x00"):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                pf.decode_targets(wrong)

    def test_the_messages_are_on_the_allowlist_only_as_they_should_be(self):
        for what in (pf.QUERY, pf.ACTIVE, pf.EXPRESSION):
            self.assertEqual(zs.classify(pf.build_query(ID, what), ID), "query_pedal_firmware")
        kinds = {pf.build_read(ID, *PLACE): "read_pedal_scenes",
                 pf.build_write(ID, *PLACE, WITH_VALUES): "write_pedal_scenes",
                 pf.build_read(ID, *PLACE, pf.READ_TARGETS): "read_pedal_expression",
                 pf.build_write_targets(ID, *PLACE, TARGETS): "write_pedal_expression"}
        for message, kind in kinds.items():
            self.assertEqual(zs.classify(message, ID), kind)
        refused = {
            "no such preset": pf.build_read(ID, 10, 0),
            "a Key knob in a scene": pf.build_write(ID, *PLACE, scenes(a=({}, {(0, 2): (KEYSYNTH, 500)}))),
            "above the knob's highest": pf.build_write(ID, *PLACE, scenes(a=({}, {(0, 2): (FIRST, 101)}))),
            "no knob of that effect": pf.build_write(ID, *PLACE, scenes(a=({}, {(0, 30): (FIRST, 1)}))),
            "a Key knob as a target": pf.build_write_targets(ID, *PLACE, [(0, KEYSYNTH, 2, 0, 900)]),
            "a toe above the knob's highest": pf.build_write_targets(ID, *PLACE, [(0, FIRST, 2, 0, 101)]),
            "one knob twice": pf.build_write_targets(ID, *PLACE, TARGETS[:1] * 2),
            "not scenes": zs._zoom(ID, pf.COMMAND, pf.WRITE, 9, 0, 4, 0, 1),
            "an operation it does not have": zs._zoom(ID, pf.COMMAND, 0x09, 9, 0, 4, 0),
        }
        for name, message in refused.items():
            with self.subTest(name), self.assertRaises(zs.NotAllowed):
                zs.classify(message, ID)

    def test_until_the_bytes_are_agreed_none_of_them_is_sent_or_heard(self):
        message = pf.build_read(ID, *PLACE)
        with mock.patch.object(pf, "COMMAND", None):
            with self.assertRaises(zs.NotAllowed):
                zs.classify(message, ID)
            self.assertIsNone(pf.parse(message[4:-1]))
        for kind in pf.KINDS:                                 # not offered for approval for a real pedal
            self.assertNotIn(kind, zs.KINDS)

    def test_the_pedals_answers(self):
        body = lambda *data: bytes([pf.COMMAND, *data])
        self.assertEqual(pf.parse(body(0, 20, 0, ALL)), ("query", 20, 0, ALL))
        self.assertEqual(pf.parse(body(3, 9, 0, 4, 0, 2, 1)), ("active", PLACE, "B", True))
        self.assertEqual(pf.parse(body(3, 9, 0, 4, 0, 0, 0)), ("active", PLACE, None, False))
        self.assertEqual(pf.parse(body(4, 6, 11)), ("expression", 6, 11))
        self.assertEqual(pf.parse(body(1, 0, 9, 0, 4, 0) + pf.encode(STORED)), ("read", 0, PLACE, STORED))
        self.assertEqual(pf.parse(body(1, 3, 9, 0, 4, 0)), ("read", 3, PLACE, None))
        self.assertEqual(pf.parse(body(2, 0, 9, 0, 4, 0)), ("write", 0, PLACE))
        self.assertEqual(pf.parse(body(5, 0, 9, 0, 4, 0) + pf.encode_targets(TARGETS)),
                         ("read_targets", 0, PLACE, TARGETS))
        for garbled in (body(1, 0, 9, 0, 4, 0, 9), body(3, 9, 0, 4, 0, 5, 0), body(5, 0, 9, 0, 4, 0, 7), body(0, 1)):
            self.assertIsNone(pf.parse(garbled), garbled)


class FirmwareRun(Provisional, EffectCase):
    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", *pf.KINDS)
        self.install()

    def world(self, chain=(FIRST, SECOND, THIRD), length=3.0, stored=STORED, **settings) -> World:
        world = World(chain=list(chain), length=length, program=PLACE[1])
        world.bank, world.features = PLACE[0], ALL
        if stored is not None:
            world.pedal_scenes[PLACE] = pf.encode(stored)
        for name, value in settings.items():
            setattr(world, name, value)
        return world

    def act(self, world: World, steps, path="/api/scenes") -> list:
        """Runs the bridge with steps of (seconds, action, data) for the path, or (seconds, None, part)
        for that part of the state ("scenes": the pedal's scenes); returns what each gave."""
        seen = []

        def client(connection):
            for seconds, action, data in steps:
                connection.at(seconds)
                if action is None:
                    state = connection.call("/api/state")[1]
                    seen.append(state["scenes"]["pedal"] if data in (None, "scenes") else state[data])
                else:
                    seen.append(connection.call(path, dict(data or {}, action=action))[1])
        self.run_bridge(world, client)
        return seen

    def held(self, world: World) -> dict:
        return pf.decode(world.pedal_scenes[PLACE])

    def firmware_messages(self, world: World) -> list:
        return [text for _, text in world.sent if text.startswith(f"F0 52 00 6E {pf.PROVISIONAL:02X}")]

    def assert_allowed(self, world: World) -> None:
        for _, text in world.sent:                       # everything sent is on the allowlist
            zs.classify(bytes.fromhex(text.replace(" ", "")), ID)

    def set_config(self, section: str, **values) -> None:
        cfg = yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))
        cfg.setdefault(section, {}).update(values)
        bridge.CONFIG.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


class Scenes(FirmwareRun):
    def test_read_edit_write_and_read_back(self):
        world = self.world(scene_most=8, stored=WITH_VALUES)
        seen = self.act(world, [
            (1.0, "pedal_read", None), (1.3, None, None),
            (1.4, "pedal_value", {"scene": "B", "slot": 1, "param": 3, "value": 70}),
            (1.45, "pedal_switch", {"scene": "B", "slot": 0, "on": False}),
            (1.5, "pedal_switch", {"scene": "A", "slot": 2, "on": None}),       # out of scene A
            (1.55, None, None), (1.6, "pedal_write", None), (1.9, None, None)])
        read, edited, written = seen[1], seen[5], seen[7]
        self.assertEqual(read["place"]["display"], "095")
        self.assertFalse(read["dirty"])
        self.assertEqual(read["data"]["A"]["effects"], [{"slot": 0, "on": True}, {"slot": 2, "on": False}])
        self.assertEqual(read["data"]["A"]["values"][0]["label"], "BlackOpt · Comp")
        self.assertEqual(read["result"]["text"], "Scenes A and B of preset 095 read")
        self.assertTrue(edited["dirty"])
        self.assertEqual(edited["data"]["B"]["values"], [{"slot": 1, "param": 3, "value": 70, "effect": "01000030",
                                                          "label": "OptComp · Lo", "away": False}])
        self.assertEqual(written["result"], {"ok": True, "text": "Scenes A and B written into preset 095 and read "
                                                                "back the same"})
        self.assertFalse(written["dirty"])
        self.assertEqual(self.held(world), scenes(a=({0: True}, {(0, 2): (FIRST, 40)}),
                                                  b=({0: False}, {(1, 3): (SECOND, 70)})))
        self.assertEqual(len([text for text in self.firmware_messages(world) if text[15:17] == "01"]), 2)
        self.assert_allowed(world)

    def test_native_scenes_keep_switches_only(self):
        world = self.world()                                       # scene_most 0, as the firmware answers
        seen = self.act(world, [(1.0, "pedal_read", None),
                                (1.3, "pedal_value", {"scene": "A", "slot": 1, "param": 2, "value": 5}),
                                (1.35, "pedal_switch", {"scene": "B", "slot": 1, "on": True}),
                                (1.4, "pedal_write", None), (1.7, None, None)])
        self.assertIn("keeps only the switches", seen[1]["error"])
        self.assertEqual(seen[-1]["most"], 0)
        self.assertTrue(seen[-1]["result"]["ok"], seen[-1]["result"])
        self.assertEqual(self.held(world), scenes(a=({0: True, 2: False}, {}), b=({1: True}, {})))

    def test_empty_scenes_and_all_six_effects(self):
        world = self.world(chain=SIX, stored=None, scene_most=8)
        steps = [(1.0, "pedal_read", None), (1.3, None, None)]
        steps += [(1.4 + 0.02 * slot, "pedal_switch", {"scene": "A", "slot": slot, "on": slot != 3}) for slot in range(6)]
        steps += [(1.55 + 0.02 * slot, "pedal_value", {"scene": "B", "slot": slot, "param": 2, "value": slot % 2})
                  for slot in range(6)]                                  # CloneCho's Depth: 0 or 1
        steps += [(1.8, "pedal_write", None), (2.1, None, None)]
        seen = self.act(world, steps)
        self.assertEqual(seen[1]["data"], {"A": {"effects": [], "values": []}, "B": {"effects": [], "values": []}})
        self.assertEqual([entry for entry in seen[2:14] if entry.get("error")], [])
        self.assertTrue(seen[-1]["result"]["ok"], seen[-1]["result"])
        self.assertEqual(self.held(world), scenes(
            a=({slot: slot != 3 for slot in range(6)}, {}),
            b=({}, {(slot, 2): (effect, slot % 2) for slot, effect in enumerate(SIX)})))

    def test_what_a_scene_cannot_hold_is_refused(self):
        world = self.world(chain=(KEYSYNTH, FIRST), scene_most=1)
        seen = self.act(world, [
            (1.0, "pedal_read", None), (1.3, None, None),
            (1.4, "pedal_value", {"scene": "B", "slot": 0, "param": 2, "value": 500}),     # KeySynth's Key
            (1.45, "pedal_value", {"scene": "B", "slot": 1, "param": 2, "value": 101}),    # above BlackOpt's Comp
            (1.5, "pedal_value", {"scene": "B", "slot": 0, "param": 3, "value": 2}),       # Wave1: fine
            (1.55, "pedal_value", {"scene": "B", "slot": 1, "param": 2, "value": 5}),      # one more than it keeps
            (1.6, "pedal_value", {"scene": "B", "slot": 4, "param": 2, "value": 5}),       # no effect there
            (1.7, None, None)])
        self.assertNotIn((0, 2), {(choice["slot"], choice["param"]) for choice in seen[1]["choices"]})
        self.assertIn({"slot": 0, "param": 3, "name": "KeySynth · Wave1", "max": 9}, seen[1]["choices"])
        self.assertIn("Key knob", seen[2]["error"])
        self.assertIn("101 is not a value", seen[3]["error"])
        self.assertNotIn("error", seen[4])
        self.assertIn("the pedal keeps 1", seen[5]["error"])
        self.assertIn("no effect 5", seen[6]["error"])
        self.assertEqual(len(seen[7]["data"]["B"]["values"]), 1)


class Failing(FirmwareRun):
    """Whatever goes wrong while writing, what was edited stays in the bridge, not written."""

    def edit_then_write(self, world: World, check=1.8, before=None) -> dict:
        steps = [(1.0, "pedal_read", None), (1.3, "pedal_switch", {"scene": "B", "slot": 1, "on": True})]
        if before:
            world.script.append((1.35, before))
        return self.act(world, steps + [(1.4, "pedal_write", None), (check, None, None)])[-1]

    def assert_kept(self, state: dict, text: str) -> None:
        self.assertEqual(state["result"]["ok"], False, state["result"])
        self.assertIn(text, state["result"]["text"])
        self.assertTrue(state["dirty"])
        self.assertEqual(state["data"]["B"]["effects"], [{"slot": 1, "on": True}])     # the edit, still there

    def test_a_readback_that_differs(self):
        world = self.world(scene_store=lambda data: pf.encode(STORED))      # it keeps the old ones
        self.assert_kept(self.edit_then_write(world), "holds other scenes than were written")

    def test_busy(self):
        world = self.world(scene_status=3)
        self.assert_kept(self.edit_then_write(world), "not written into preset 095: the pedal is busy")
        self.assertEqual(self.held(world), STORED)

    def test_an_answer_for_another_preset(self):
        world = self.world(length=3.6)
        state = self.edit_then_write(world, check=3.2,
                                     before=lambda world: setattr(world, "answer_address", OTHER))
        self.assert_kept(state, "whether preset 095 holds the scenes is not known")
        self.assertIn("scenes of preset 096 not asked for", (self.folder / "logs" / "sysex.log").read_text())

    def test_no_answer(self):
        world = self.world(length=3.6)
        state = self.edit_then_write(world, check=3.2,
                                     before=lambda world: setattr(world, "scene_answers", False))
        self.assert_kept(state, "whether preset 095 holds the scenes is not known")

    def test_an_answer_that_holds_no_scenes(self):
        world = self.world(answer_garbled=True)
        seen = self.act(world, [(1.0, "pedal_read", None), (1.3, None, None), (2.7, None, None)])
        self.assertEqual(seen[1]["busy"], "read")
        self.assertEqual(seen[2]["result"], {"ok": False, "text": "The pedal did not answer: the scenes of preset 095 "
                                                                 "were not read"})
        self.assertIsNone(seen[2]["data"])

    def test_scenes_being_read_when_the_preset_changes_are_not_taken(self):
        world = self.world(scene_delay=0.4)
        world.script.append((1.2, lambda world: world.preset_change(OTHER[1])))
        seen = self.act(world, [(1.0, "pedal_read", None), (1.7, None, None)])
        self.assertEqual(seen[-1]["result"], {"ok": False, "text": "Reading the scenes stopped: preset changed"})
        self.assertIsNone(seen[-1]["data"])             # the answer that came after it is not taken

    def test_scenes_are_written_only_into_the_preset_they_came_from(self):
        world = self.world()
        world.script.append((1.3, lambda world: world.preset_change(OTHER[1])))
        seen = self.act(world, [(1.0, "pedal_read", None),
                                (1.5, "pedal_switch", {"scene": "B", "slot": 1, "on": True}),
                                (1.55, "pedal_write", None), (1.6, None, None)])
        self.assertIn("scenes of preset 095", seen[1]["error"])
        self.assertIn("scenes of preset 095, the pedal is on 096", seen[2]["error"])
        self.assertEqual(seen[3]["place"]["display"], "095")
        self.assertNotIn(OTHER, world.pedal_scenes)
        self.assertEqual(self.held(world), STORED)

    def test_scenes_on_their_way_when_the_preset_changes_are_read_back_by_their_address(self):
        world = self.world(scene_delay=0.3)
        world.script.append((1.7, lambda world: world.preset_change(OTHER[1])))
        seen = self.act(world, [(1.0, "pedal_read", None),
                                (1.5, "pedal_switch", {"scene": "B", "slot": 1, "on": True}),
                                (1.6, "pedal_write", None), (2.4, None, None)])
        self.assertTrue(seen[-1]["result"]["ok"], seen[-1]["result"])
        self.assertEqual(self.held(world)["B"]["effects"], {1: True})
        self.assertNotIn(OTHER, world.pedal_scenes)


class ActiveScene(FirmwareRun):
    def test_the_active_scene_is_shown_and_nothing_is_sent_for_it(self):
        world = self.world(active={PLACE: (1, False)})
        world.script += [(1.3, lambda world: world.press_scene(2, differs=True)),
                         (1.5, lambda world: world.press_scene(1, place=OTHER))]     # another preset's: not this one
        sent = {}
        world.script += [(1.25, lambda world: sent.update(before=len(world.sent))),
                         (1.8, lambda world: sent.update(after=len(world.sent)))]
        world.script.sort(key=lambda item: item[0])
        seen = self.act(world, [(1.0, None, None), (1.45, None, None), (1.7, None, None)])
        self.assertEqual(seen[0]["active"], {"scene": "A", "differs": False})
        self.assertEqual(seen[1]["active"], {"scene": "B", "differs": True})
        self.assertEqual(seen[2]["active"], {"scene": "B", "differs": True})
        self.assertEqual(sent["after"], sent["before"])     # no scene recalled, no effect switched, nothing read
        self.assertEqual(self.firmware_messages(world)[:2], ["F0 52 00 6E 7D 00 F7", "F0 52 00 6E 7D 04 F7"])
        self.assertEqual(self.firmware_messages(world).count("F0 52 00 6E 7D 03 F7"), 1)   # asked once, at the start

    def test_after_a_preset_change_its_active_scene_is_asked_for(self):
        world = self.world(active={OTHER: (2, False)})
        world.script.append((1.2, lambda world: world.preset_change(OTHER[1])))
        seen = self.act(world, [(1.0, None, None), (1.6, None, None)])
        self.assertEqual(seen[0]["active"], {"scene": None, "differs": False})        # none clearly holds
        self.assertEqual(seen[1]["active"], {"scene": "B", "differs": False})
        self.assertEqual(self.firmware_messages(world).count("F0 52 00 6E 7D 03 F7"), 2)


class ExpressionTargets(FirmwareRun):
    def test_read_edit_write_and_read_back(self):
        world = self.world()
        world.pedal_targets[PLACE] = pf.encode_targets(TARGETS[:1])
        seen = self.act(world, [(1.0, "read", None), (1.3, None, "pedal_expression"),
                                (1.35, "add", {"slot": 2, "param": 3, "heel": 9, "toe": 0}),
                                (1.4, "drop", {"slot": 0, "param": 2}),
                                (1.45, "write", None), (1.8, None, "pedal_expression")], path="/api/pedal_expression")
        self.assertEqual(seen[1]["data"], [{"slot": 0, "param": 2, "heel": 0, "toe": 100, "effect": "01000020",
                                            "label": "BlackOpt · Comp", "away": False}])
        self.assertEqual((seen[1]["most"], seen[1]["cc"]), (6, 11))
        self.assertTrue(seen[-1]["result"]["ok"], seen[-1]["result"])
        self.assertEqual(pf.decode_targets(world.pedal_targets[PLACE]), [(2, THIRD, 3, 9, 0)])
        self.assert_allowed(world)

    def test_the_bridges_own_assignments_become_the_targets_where_they_can(self):
        self.install({KEY: [mapping(0, 2, 10, 90), mapping(1, 3, 20, 80, invert=True),
                            mapping(2, 4, 0, 50, curve="log"), mapping(2, 2, 0, 9, cc=21)]},
                     {KEY: learned((0, 2, 0, 100), (1, 3, 0, 100), (2, 4, 0, 100), (2, 2, 0, 9))})
        world = self.world(target_most=1)
        seen = self.act(world, [(1.0, "read", None), (1.3, "from_bridge", None), (1.35, None, "pedal_expression"),
                                (1.4, "write", None), (1.8, None, "pedal_expression")], path="/api/pedal_expression")
        text = seen[2]["result"]["text"]
        self.assertIn("1 of the bridge's assignments taken", text)
        self.assertIn("(log curve)", text)
        self.assertIn("1 beyond the 1 the pedal keeps", text)
        self.assertEqual([(target["slot"], target["heel"], target["toe"]) for target in seen[2]["data"]], [(0, 10, 90)])
        self.assertTrue(seen[2]["dirty"])
        self.assertTrue(seen[-1]["result"]["ok"])
        self.assertEqual(pf.decode_targets(world.pedal_targets[PLACE]), [(0, FIRST, 2, 10, 90)])

    def test_an_inverted_assignment_is_heel_above_toe(self):
        self.install({KEY: [mapping(1, 3, 20, 80, invert=True)]}, {KEY: learned((1, 3, 0, 100))})
        world = self.world()
        seen = self.act(world, [(1.0, "read", None), (1.3, "from_bridge", None), (1.35, None, "pedal_expression")],
                        path="/api/pedal_expression")
        self.assertEqual([(target["heel"], target["toe"]) for target in seen[2]["data"]], [(80, 20)])


class Ways(FirmwareRun):
    """Notes and the expression pedal go to the pedal one way only: the bridge's, or as MIDI to a
    firmware that takes them itself."""

    def keyboard_world(self, **settings) -> World:
        world = self.world(chain=(FIRST, KEYSYNTH), **settings)
        world.ports = EVERYTHING
        world.script += [(1.0, note_on(48)), (1.2, note_off(48))]
        world.script.sort(key=lambda item: item[0])
        return world

    def test_notes_go_to_a_firmware_that_plays_them_and_the_bridge_plays_no_key(self):
        self.install(synth={"keyboard": KEYBOARD_PORT, "channel": 1, "via": "pedal"})
        world = self.keyboard_world()
        seen = self.act(world, [(1.4, None, "firmware"), (1.45, None, "synth")])
        notes = [message for message in world.channel_messages if message.type in ("note_on", "note_off")]
        self.assertEqual([(message.type, message.note) for message in notes], [("note_on", 48), ("note_off", 48)])
        self.assertEqual([param for param in world.params if param[1] == 1], [])      # no Key knob
        self.assertEqual((seen[0]["plays_notes"], seen[0]["notes_via"]), (True, "pedal"))
        self.assertTrue(seen[1]["playable"])            # a synth effect is in the chain: something else

    def test_by_default_the_bridge_plays_them_and_passes_none(self):
        self.install(synth={"keyboard": KEYBOARD_PORT, "channel": 1})
        world = self.keyboard_world()
        seen = self.act(world, [(1.4, None, "firmware")])
        self.assertEqual([message for message in world.channel_messages if message.type == "note_on"], [])
        self.assertTrue([param for param in world.params if param[1] == 1])          # the Key knob
        self.assertEqual((seen[0]["plays_notes"], seen[0]["notes_via"]), (True, "bridge"))

    def test_without_the_firmware_saying_so_notes_do_not_go_through(self):
        self.install(synth={"keyboard": KEYBOARD_PORT, "channel": 1, "via": "pedal"})
        world = self.keyboard_world(features=pf.SCENES)
        seen = self.act(world, [(1.4, None, "firmware")])
        self.assertEqual([message for message in world.channel_messages if message.type == "note_on"], [])
        self.assertEqual((seen[0]["plays_notes"], seen[0]["notes_via"]), (False, "bridge"))

    def expression_world(self) -> World:
        self.install({KEY: [mapping(0, 2, 0, 100)]}, {KEY: learned((0, 2, 0, 100))})
        world = self.world()
        world.script += [(1.0, cc(127)), (1.2, cc(0))]
        world.script.sort(key=lambda item: item[0])
        return world

    def test_the_expression_pedal_goes_as_cc_11_and_the_bridge_sets_nothing(self):
        world = self.expression_world()
        self.set_config("expression", via="pedal")
        seen = self.act(world, [(1.4, None, "firmware")])
        passed = [(message.control, message.value) for message in world.channel_messages
                  if message.type == "control_change" and message.control == 11]
        self.assertEqual(passed, [(11, 127), (11, 0)])
        self.assertEqual(world.params, [])
        self.assertEqual(seen[0]["expression_via"], "pedal")

    def test_by_default_the_bridge_sets_the_parameters_and_passes_no_cc_11(self):
        world = self.expression_world()
        self.act(world, [(1.4, None, "firmware")])
        self.assertEqual([message for message in world.channel_messages if message.type == "control_change"
                          and message.control == 11], [])
        self.assertEqual([value for *_, value in world.params], [100, 0])


class WithoutFirmware(FirmwareRun):
    def test_zoom_firmware_that_does_not_answer_gets_the_bridges_own(self):
        world = self.world(features=0, length=3.2)
        seen = self.act(world, [(1.0, None, None), (2.9, None, "firmware"), (2.95, "pedal_read", None),
                                (3.0, "add", {"name": "Solo"})])
        self.assertIsNone(seen[0])                       # no scenes of the pedal: the bridge's are offered
        self.assertEqual((seen[1]["ok"], seen[1]["why"]), (False, "it did not answer the question (a firmware of Zoom's)"))
        self.assertIn("keeps no scenes of its own: it did not answer", seen[2]["error"])
        self.assertNotIn("error", seen[3])
        self.assertEqual(self.instance.scenes[PLACE][0]["name"], "Solo")
        self.assertEqual(self.firmware_messages(world), ["F0 52 00 6E 7D 00 F7"])      # asked once, nothing else

    def test_while_the_bytes_are_not_agreed_the_pedal_is_not_asked(self):
        world = self.world()
        with mock.patch.object(pf, "COMMAND", None):
            seen = self.act(world, [(1.0, None, None), (1.1, "pedal_read", None), (1.2, None, "firmware")])
        self.assertIsNone(seen[0])
        self.assertIn("not agreed with it yet", seen[1]["error"])
        self.assertFalse(seen[2]["ok"])
        self.assertFalse([text for _, text in world.sent if text.startswith(f"F0 52 00 6E {pf.PROVISIONAL:02X}")])

    def test_without_approval_nothing_is_asked(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        world = self.world()
        seen = self.act(world, [(1.0, "pedal_read", None)])
        self.assertIn("asking the pedal what its firmware does is not approved", seen[0]["error"])
        self.assertFalse([text for _, text in world.sent if text.startswith(f"F0 52 00 6E {pf.PROVISIONAL:02X}")])


if __name__ == "__main__":
    unittest.main()
