"""Adding an empty place to the chain from the interface, and choosing an effect for it; and which
effects can be chosen at all: those the pedal's own list of files names (the installer asks for it,
the bridge reads the answers that pass by), and those it reported in a preset.

Adding has no message of its own either: as for moving, the patch is read afresh, sent back with
the place inserted (zs.insert_empty: its id, its 24 bytes of settings, the length; checked against
places added on the MS-60B+ itself, tests/pedal_patches.json), and read again."""
import json
import unittest
from unittest import mock

import bridge
import effect_names
import zoom_sysex as zs
from tests.sim import CONTROLLER_PORT, ID, PROJECT, ZOOM_PORT, SimulatedCase, World, mapping, patch_dump, sysex

PRESET = "0/94"
STOCK, ROOM, HOGI, AMP = 0x01000020, 0x0B000020, 0x09000F83, 0x05000020   # BlackOpt, BrghtRoom, HOGi, AMPG SVT
REAL = json.loads((PROJECT / "tests" / "pedal_patches.json").read_text(encoding="utf-8"))


def file_entry(name: str) -> bytes:
    """One answer of the pedal to the installer's question for its files, as the log has them."""
    return bytes([0xF0, 0x52, 0x00, ID, 0x60, 0x04, 0x25, 0, 0, 0x17, 0, 0, 0, 0, 0]) + (
        name.encode("ascii").ljust(12, b"\0") + b"\x01\x00\x00\x23\x7C" + bytes(9) + b"\xF7")


def within_patch(patch: bytes) -> bytes:
    """What a patch holds, up to its own length, without the effect selected on the pedal."""
    data = bytearray(patch[:int.from_bytes(patch[4:8], "little")])
    prm2 = zs.patch_chunks(bytes(data))[b"PRM2"][0]
    data[prm2 + 10:prm2 + 13] = bytes(3)
    return bytes(data)


class Patches(unittest.TestCase):
    def test_an_empty_place_as_the_pedal_adds_one(self):
        for added in REAL["adds"]:
            before, after = bytes.fromhex(added["before"]), bytes.fromhex(added["after"])
            made = zs.insert_empty(before, added["slot"])
            self.assertEqual(len(made), len(before))                       # the buffer keeps its size
            self.assertEqual(within_patch(made), within_patch(after), added["when"])

    def test_the_bits_of_the_places_after_it_move_up(self):
        patch = bytearray(zs.parse_patch_dump(zs.zoom_body(patch_dump([(STOCK, 0), (AMP, 0)]), ID)).data)
        prm2 = zs.patch_chunks(bytes(patch))[b"PRM2"][0]
        patch[prm2 + 20] = 0b10 << 1                                        # the amp bit of effect 2
        added = zs.insert_empty(bytes(patch), 0)
        self.assertEqual((added[prm2 + 28 + 20] >> 1) & 0x3F, 0b100)        # now effect 3
        self.assertIsNone(zs.check_patch(added))

    def test_not_beyond_six_or_outside_the_chain(self):
        patch = bytes.fromhex(REAL["amp"]["patch"])                        # six effects
        with self.assertRaises(ValueError):
            zs.insert_empty(patch, 6)
        five = bytes.fromhex(REAL["adds"][0]["before"])
        for slot in (-1, 6):
            with self.assertRaises(ValueError):
                zs.insert_empty(five, slot)

    def test_only_the_prepared_patch_as_its_own_kind(self):
        message = zs.build_patch_upload(ID, zs.insert_empty(bytes.fromhex(REAL["adds"][0]["before"]), 5))
        self.assertEqual(zs.classify(message, ID, upload=message, upload_kind="add_effect"), "add_effect")
        with self.assertRaises(zs.NotAllowed):
            zs.classify(message, ID)

    def test_a_file_of_the_pedals_list(self):
        self.assertEqual(zs.parse_file_entry(zs.zoom_body(file_entry("HOGI.ZD2"), ID)), "HOGI.ZD2")
        self.assertIsNone(zs.parse_file_entry(zs.zoom_body(patch_dump([(STOCK, 0)]), ID)))
        self.assertEqual(effect_names.by_file("blackopt.zd2"), STOCK)


class Adding(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "add_effect", "change_effect")

    def run_calls(self, *calls, world=None, until=2.0, reported=(HOGI,)) -> list:
        self.install({PRESET: [mapping(0, 2)]})
        known = json.loads(bridge.MEASUREMENTS.read_text(encoding="utf-8"))
        known["effects"] = [f"{effect:08X}" for effect in reported]
        bridge.MEASUREMENTS.write_text(json.dumps(known), encoding="utf-8")
        seen = []

        def client(connection):
            connection.at(1.0)
            for call in calls:
                if isinstance(call, float):
                    connection.at(call)
                else:
                    seen.append(connection.call("/api/effects", call)[:2])
            connection.at(until)
            seen.append(connection.call("/api/state")[1])

        world = world or World(chain=[STOCK, ROOM], length=until + 0.3)
        self.run_bridge(world, client)
        self.world = world
        return seen

    def test_an_empty_place_is_added_and_an_effect_chosen_for_it(self):
        replies = self.run_calls({"action": "add"}, 1.5, {"action": "change", "slot": 2, "effect": f"{HOGI:08X}"})
        self.assertEqual(replies[:2], [(200, {"result": None})] * 2)
        self.assertEqual(self.world.chain, [STOCK, ROOM, HOGI])
        self.assertEqual(len(self.world.uploads), 1)
        self.assertEqual([entry["name"] for entry in replies[-1]["chain_effects"]], ["BlackOpt", "BrghtRoom", "HOGi"])

    def test_the_place_is_said_once_the_pedal_has_it(self):
        *_, state = self.run_calls({"action": "add"}, until=1.6)
        self.assertEqual(self.world.chain, [STOCK, ROOM, 0])
        self.assertEqual(state["effects"]["change"]["result"], {"ok": True, "text": (
            "Effect 3 is an empty place now – choose an effect for it, and save the preset on the pedal to keep it")})
        self.assertTrue(state["chain_effects"][2]["empty"])

    def test_not_beyond_six_and_not_without_approval(self):
        world = World(chain=[STOCK] * 6, length=1.6)
        reply, _ = self.run_calls({"action": "add"}, world=world, until=1.3)
        self.assertEqual(reply, (400, {"error": "The preset has 6 effects already."}))
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        reply, state = self.run_calls({"action": "add"}, until=1.3)
        self.assertIn("python probe.py approve add_effect query_patch", reply[1]["error"])
        self.assertFalse(state["effects"]["add"]["approved"])
        self.assertEqual(self.world.uploads, [])


class Choices(SimulatedCase):
    def listed(self, names, until=1.6) -> dict:
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        self.install({PRESET: [mapping(0, 2)]})
        known = json.loads(bridge.MEASUREMENTS.read_text(encoding="utf-8"))
        known["effects"] = ["0B000050", "09000FF0"]                  # Hall REV and BPM, reported in presets
        bridge.MEASUREMENTS.write_text(json.dumps(known), encoding="utf-8")
        seen = {}

        def client(connection):
            connection.at(until)
            seen["state"] = connection.call("/api/state")[1]

        script = [(1.0, lambda world: world.zoom_queue.extend(sysex(file_entry(name)) for name in names))]
        self.run_bridge(World(script, chain=[STOCK, ROOM], length=until + 0.3), client)
        return seen["state"]

    def test_every_effect_file_the_pedal_lists_can_be_chosen(self):
        state = self.listed(["HOGI.ZD2", "HOGI.ZIC", "DUMMY.WAV", "NOT_KNOWN.ZD2", "BLACKOPT.ZD2"])
        chosen = {choice["id"] for choice in state["effects"]["choices"]}
        self.assertEqual(chosen, {"0B000050", "09000FF0", "01000020", "0B000020", f"{effect_names.by_file('HOGI.ZD2'):08X}"})
        self.assertEqual(set(json.loads(bridge.MEASUREMENTS.read_text(encoding="utf-8"))["effects"]),
                         chosen)                                     # kept for the next start

    def test_nothing_is_taken_off_by_a_list(self):
        # the installer lists after deleting what it replaces and before writing it again
        state = self.listed(["HOGI.ZD2"])
        self.assertIn("0B000050", {choice["id"] for choice in state["effects"]["choices"]})


class ReadPedal(SimulatedCase):
    FILES = ["BLACKOPT.ZD2", "BLACKOPT.ZIC", "HOGI.ZD2", "HOGI.ZIC", "NOT_KNOWN.ZD2", "DUMMY.WAV"] + [
        f"{name}.ZD2" for name in ("KKFLNG1U", "KICK", "KEYSYNTH", "FRETSYN", "GLIDER", "RINGER", "LADDER", "FREEZER")]

    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "read_pedal")

    def read(self, files, approved=True, before=("0B000050", "01000020")) -> tuple:
        if not approved:
            self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        self.install({PRESET: [mapping(0, 2)]})
        known = json.loads(bridge.MEASUREMENTS.read_text(encoding="utf-8"))
        known["effects"] = list(before)                                     # Hall REV and BlackOpt, reported before
        bridge.MEASUREMENTS.write_text(json.dumps(known), encoding="utf-8")
        seen = {}

        def client(connection):
            connection.at(1.0)
            seen["reply"] = connection.call("/api/effects", {"action": "read_pedal"})[:2]
            connection.at(2.0)
            seen["state"] = connection.call("/api/state")[1]

        world = World(chain=[STOCK, ROOM], length=2.4)
        world.files = files
        self.run_bridge(world, client)
        return world, seen

    def test_only_the_effects_of_the_pedals_own_files_can_be_chosen_after_it(self):
        world, seen = self.read(self.FILES)
        self.assertEqual(seen["reply"], (200, {"result": None}))
        chosen = {choice["id"] for choice in seen["state"]["effects"]["choices"]}
        self.assertIn("01000020", chosen)                                   # BlackOpt: a file of it is there
        self.assertIn("0B000020", chosen)                                   # BrghtRoom: in the chain
        self.assertNotIn("0B000050", chosen)                                # Hall REV: reported before, no file now
        self.assertTrue(seen["state"]["effects"]["read"]["result"]["ok"])
        self.assertFalse(world.pc_mode)                                     # PC mode is left again
        self.assertEqual(world.file_log[0][:6], "602500")                   # the first entry, the list of everything
        self.assertEqual(world.file_log[-2:], ["6021400000000" + "0", "6009"])   # the end of the file session

    def test_bpm_in_the_pedal_itself_stays_as_it_was_reported(self):
        # it has no file: it was kept as 00000FF0 (its number only), an id the pedal does not have
        _, seen = self.read(self.FILES, before=("09000FF0", "00000FF0"))     # (and that one is dropped)
        self.assertEqual([choice["id"] for choice in seen["state"]["effects"]["choices"] if choice["name"] == "BPM"],
                         ["09000FF0"])

    def test_nothing_but_the_listing_is_sent_and_a_short_list_is_not_taken(self):
        world, seen = self.read(["BLACKOPT.ZD2"])
        result = seen["state"]["effects"]["read"]["result"]
        self.assertFalse(result["ok"])
        self.assertIn("0B000050", {choice["id"] for choice in seen["state"]["effects"]["choices"]})   # as before
        self.assertFalse(world.pc_mode)
        self.assertTrue(all(body[:4] in ("6025", "6026", "6021", "6009") for body in world.file_log))

    def test_without_approval_nothing_is_sent(self):
        world, seen = self.read(self.FILES, approved=False)
        self.assertIn("python probe.py approve read_pedal", seen["reply"][1]["error"])
        self.assertEqual(world.file_log, [])


class ReadAtStart(SimulatedCase):
    """With the stored presets read, the pedal's list is read once by itself at the start – only when
    the preset on the pedal is as it is stored, as reading loads it again."""

    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "read_pedal", "query_preset")
        patcher = mock.patch.object(bridge, "PRESET_READ_GAP", 0.001)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start(self, stored_chain, script=(), length=2.6, ports=None) -> tuple:
        self.install({})
        world = World(script, program=8, chain=[STOCK, ROOM], length=length, **({"ports": ports} if ports else {}))
        world.files = ReadPedal.FILES
        world.stored[(0, 8)] = zs.unpack_7bit(patch_dump([(effect, 0) for effect in stored_chain], name=world.name)[9:-6])
        seen = {}

        def client(connection):
            connection.at(2.3)
            seen["state"] = connection.call("/api/state")[1]

        self.run_bridge(world, client)
        return world, seen["state"]

    def test_read_when_nothing_would_be_lost(self):
        world, state = self.start([STOCK, ROOM])
        self.assertEqual(world.file_log[0][:6], "602500")
        self.assertTrue(state["effects"]["read"]["result"]["ok"])
        self.assertFalse(world.pc_mode)

    def test_not_read_when_the_preset_has_changes_not_saved(self):
        world, state = self.start([STOCK])                              # stored: one effect, on the pedal: two
        self.assertEqual(world.file_log, [])
        self.assertIn("were not read at the start", self.output)

    def test_not_read_when_a_knob_was_turned_after_the_patch_came(self):
        # the pedal reports a knob turned on it, not its patch: compared with the patch of the
        # start, nothing had changed, and reading loaded the preset again without it
        def turn(world):
            world.values[(0, 2)] = 77
            world.knob(0, 2, 77)

        # the names are read until well after the knob is turned (with the quick pace of the other
        # tests a busy machine could be through with them before)
        with mock.patch.object(bridge, "PRESET_READ_GAP", 0.02):
            world, state = self.start([STOCK, ROOM], [(1.0, turn)], length=4.0)
        self.assertEqual(world.file_log, [])
        self.assertIn("were not read at the start", self.output)

    def test_read_once_also_when_the_pedal_is_connected_anew(self):
        # a cable pulled while playing: no PC mode a few seconds after it is back
        # gone for longer than the bridge takes between two looks at the ports (PORT_CHECK_SECONDS)
        world, _ = self.start([STOCK, ROOM], length=7.0,
                              ports=lambda seconds: [CONTROLLER_PORT] if 3.0 < seconds < 4.3 else [ZOOM_PORT, CONTROLLER_PORT])
        self.assertIn("Connection lost", self.output)
        self.assertEqual(sum(1 for _, text in world.sent if text == "F0 52 00 6E 52 F7"), 1)   # PC mode once


class Allowlist(unittest.TestCase):
    def test_the_file_messages_only_while_the_list_is_read(self):
        first = bytes([0xF0, 0x52, 0, ID, 0x60, 0x25, 0, 0, 0x2A, 0, 0xF7])
        self.assertEqual(zs.classify(first, ID, listing=True), "read_pedal")
        for message in (first, bytes([0xF0, 0x52, 0, ID, 0x52, 0xF7])):
            with self.assertRaises(zs.NotAllowed):
                zs.classify(message, ID)
        for refused in (bytes([0xF0, 0x52, 0, ID, 0x60, 0x24, 0x4B, 0, 0xF7]),       # delete a file
                        bytes([0xF0, 0x52, 0, ID, 0x60, 0x20, 0x02, 0, 0, 0xF7]),     # open one
                        bytes([0xF0, 0x52, 0, ID, 0x60, 0x25, 0, 0, 0x41, 0, 0xF7])):  # a file by name
            with self.assertRaises(zs.NotAllowed):
                zs.classify(refused, ID, listing=True)


if __name__ == "__main__":
    unittest.main()
