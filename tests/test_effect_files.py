"""Effects read from the pedal's own files (`read_effect_file`): while Read Pedal lists the files,
an effect file the table does not describe – not in it, or a custom effect of another size – is
read from the pedal as the installer reads one for its backup, only as far as its table of knobs
goes; what is read is kept beside the settings and taken with the table from then on. The list's
sizes, a block of a file, its CRC and the pedal's answers are checked against what the MS-60B+
sent the installer (tests/pedal_patches.json, the log of 2026-10-21)."""
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import bridge
import effect_names
import export
import synth_controls
import zoom_sysex as zs
from tests.sim import ID, PROJECT, SimulatedCase, World, mapping
from tests.test_editor import SYNTH
from tests.test_headless import HeadlessCase
from tests.test_names import zd2

STOCK, ROOM = 0x01000020, 0x0B000020                      # BlackOpt, BrghtRoom
NEW, OTHER = 0x09000FA0, 0x09000FA1                      # custom effects the table does not know
KEYSYNTH = 0x07000F61                                     # as installed on the pedal (tests/test_editor.py)
PRESET = "0/94"
REAL = json.loads((PROJECT / "tests" / "pedal_patches.json").read_text(encoding="utf-8"))["files"]
FILES = ["BLACKOPT.ZD2", "BLACKOPT.ZIC", "HOGI.ZD2", "NEWFX.ZD2", "NEWFX.ZIC"] + [
    f"{name}.ZD2" for name in ("KKFLNG1U", "KICK", "KEYSYNTH", "FRETSYN", "GLIDER", "RINGER", "LADDER", "FREEZER")]


def effect_file(effect_id=NEW, name="NewFx", knobs=(("Tone", 100), ("Drive", 50))) -> bytes:
    """An effect file whose table of knobs ends in its third 512 bytes and that goes on long after."""
    data = zd2(effect_id, name, knobs)
    return data[:120] + bytes(1400 - len(data)) + data[120:] + bytes(3000)


def body(message: str) -> bytes:
    return bytes.fromhex(message)[4:-1]


def table_entry(file: str) -> tuple:
    return next((key, entry) for key, entry in effect_names.table().items() if entry["file"] == file)


class Messages(unittest.TestCase):
    def test_the_size_in_the_pedals_list(self):
        sizes = {entry["name"]: zs.file_entry_size(body(entry["message"])) for entry in REAL["entries"]}
        self.assertEqual(sizes, {"160_COMP.ZD2": 15907, "SLAMDRV.ZD2": 33993})
        self.assertIsNone(zs.file_entry_size(b"\x60\x00\x25\x00\x00\x00"))

    def test_a_block_of_a_file_as_the_pedal_sent_it(self):
        block = body(REAL["first_block"]["message"])
        data = zs.parse_file_block(block)
        self.assertEqual((len(data), data[:4]), (512, b"ZDLF"))
        self.assertEqual(effect_names.read_zd2(data)["name"], "Wider")
        self.assertEqual(effect_names.read_zd2_part(data), (False, None))   # its knobs come later in the file
        broken = bytearray(block)
        broken[100] ^= 1
        self.assertIsNone(zs.parse_file_block(bytes(broken)))         # its CRC is wrong
        self.assertEqual(zs.parse_file_block(b"\x60\x04\x22\x01\x00\x00\x00" + bytes(5)), b"")   # the end

    def test_only_the_effect_file_being_read_and_only_while_the_list_is(self):
        name = "NEWFX.ZD2"
        opened = zs.build_file_open(ID, name)
        for message in (opened, zs.build_file_status(ID), zs.build_file_read_next(ID)):
            self.assertEqual(zs.classify(message, ID, listing=True, file_name=name), "read_effect_file")
            for listing, file_name in ((False, name), (True, None)):
                with self.assertRaises(zs.NotAllowed):
                    zs.classify(message, ID, listing=listing, file_name=file_name)
        for other, file_name in ((zs.build_file_open(ID, "HOGI.ZD2"), name),        # another file
                                 (zs.build_file_open(ID, "NEWFX.ZIC"), "NEWFX.ZIC"),  # no effect file
                                 (zs.build_file_open(ID, "../X.ZD2"), "../X.ZD2")):
            with self.assertRaises(zs.NotAllowed):
                zs.classify(other, ID, listing=True, file_name=file_name)
        for refused in ("F0 52 00 6E 60 20 01 00 00 00 00 00 00 00 00 00 4E 45 57 46 58 2E 5A 44 32 00 F7",  # to write
                        "F0 52 00 6E 60 24 00 00 00 00 F7"):                                              # delete
            with self.assertRaises(zs.NotAllowed):
                zs.classify(bytes.fromhex(refused.replace(" ", "")), ID, listing=True, file_name=name)


class Table(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(effect_names.use_cache, None)
        effect_names.use_cache(None)

    def test_the_beginning_of_a_file_as_far_as_its_knobs(self):
        data = effect_file()
        ends = data.find(b"OnOff\0") + 5 * effect_names.KNOB_SIZE    # on/off, type, two knobs and the end
        self.assertEqual(effect_names.read_zd2_part(data[:ends - 1]), (False, None))
        self.assertEqual(effect_names.read_zd2_part(data[:ends]), (True, effect_names.read_zd2(data)))
        self.assertEqual(effect_names.read_zd2_part(b"ZBMP" + bytes(600)), (True, None))   # no effect file
        self.assertEqual(effect_names.read_zd2_part(b"ZD"), (False, None))

    def test_what_the_table_describes(self):
        _, hogi = table_entry("HOGI.ZD2")
        _, black = table_entry("BLACKOPT.ZD2")
        self.assertTrue(effect_names.described("HOGI.ZD2", hogi["size"]))
        self.assertFalse(effect_names.described("HOGI.ZD2", hogi["size"] + 1))       # a custom effect rebuilt
        self.assertTrue(effect_names.described("BLACKOPT.ZD2", black["size"] + 1))   # Zoom's: by its name
        self.assertTrue(effect_names.described("hogi.zd2", None))
        self.assertFalse(effect_names.described("NEWFX.ZD2", 4000))
        effect_names.remember([{"file": "NEWFX.ZD2", "size": 4000, "id": None}])     # read: no effect in it
        self.assertTrue(effect_names.described("NEWFX.ZD2", 4000))
        self.assertFalse(effect_names.described("NEWFX.ZD2", 4001))
        self.assertIsNone(effect_names.by_file("NEWFX.ZD2"))

    def test_what_was_read_takes_the_place_of_another_build_and_is_kept(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        cache = Path(folder.name, "pedal_effects.json")
        effect_names.use_cache(cache)
        hogi, entry = table_entry("HOGI.ZD2")
        effect_names.remember([{"id": int(hogi, 16), "name": "HOGi", "knobs": [["Gain", 10]], "file": "HOGI.ZD2",
                                "size": entry["size"] + 1}])
        self.assertEqual(effect_names.effect(int(hogi, 16))["knobs"], [["Gain", 10]])
        self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["HOGI.ZD2"]["knobs"], [["Gain", 10]])
        effect_names.use_cache(None)
        self.assertEqual(effect_names.effect(int(hogi, 16))["knobs"], entry["knobs"])   # without it: the table
        effect_names.use_cache(cache)                                                 # the next start
        self.assertEqual(effect_names.effect(int(hogi, 16))["knobs"], [["Gain", 10]])

    def test_a_table_read_again_from_the_same_build_wins(self):
        slam, entry = table_entry("SLAMDRV.ZD2")
        effect_names.remember([{"id": int(slam, 16), "name": "SlamDrive", "knobs": [["Old", 1]], "file": "SLAMDRV.ZD2",
                                "size": entry["size"]}])                     # read before the table was read again
        self.assertEqual(effect_names.effect(int(slam, 16))["knobs"], entry["knobs"])
        self.assertTrue(effect_names.described("SLAMDRV.ZD2", entry["size"]))

    def test_one_id_is_one_effect(self):
        hogi, _ = table_entry("HOGI.ZD2")
        effect_names.remember([{"id": int(hogi, 16), "name": "HOGiOld", "knobs": [["Gain", 10]], "file": "HOGIOLD.ZD2",
                                "size": 100},
                               {"id": NEW, "name": "NewFx", "knobs": [], "file": "NEWB.ZD2", "size": 100},
                               {"id": NEW, "name": "NewFx2", "knobs": [], "file": "NEWA.ZD2", "size": 100}])
        self.assertEqual(effect_names.by_file("HOGIOLD.ZD2"), int(hogi, 16))   # what is on the pedal
        self.assertIsNone(effect_names.by_file("HOGI.ZD2"))
        self.assertEqual(effect_names.effect(NEW)["name"], "NewFx2")            # the first by file name
        self.assertEqual(effect_names.CONFLICTS, ["NewFx (NEWB.ZD2) is left out: NewFx2 (NEWA.ZD2) has its id"])
        self.assertTrue(effect_names.described("NEWB.ZD2", 100))               # not read again

    def test_a_file_read_without_an_effect_in_it_is_no_effect_of_the_table_either(self):
        # an install broken off leaves the file empty: the effect was still offered as the table's
        _, entry = table_entry("HOGI.ZD2")
        effect_names.remember([{"file": "HOGI.ZD2", "size": 0, "id": None}])
        self.assertTrue(effect_names.described("HOGI.ZD2", 0))              # not read again
        self.assertIsNone(effect_names.by_file("HOGI.ZD2"))
        effect_names.remember([{"file": "HOGI.ZD2", "size": entry["size"], "id": None, "unread": True}])
        self.assertIsNotNone(effect_names.by_file("HOGI.ZD2"))              # not read: the table's it may be

    def test_a_file_whose_build_lost_its_id_is_not_taken_for_the_table_build(self):
        _, entry = table_entry("HOGI.ZD2")                                  # HOGI.ZD2 rebuilt with another id
        effect_names.remember([{"id": NEW, "name": "HOGi", "knobs": [], "file": "HOGI.ZD2", "size": entry["size"] + 1},
                               {"id": NEW, "name": "Aaa", "knobs": [], "file": "AAA.ZD2", "size": 100}])
        self.assertIsNone(effect_names.by_file("HOGI.ZD2"))                 # not the old build's id

    def test_what_was_read_of_a_file_gone_or_changed_is_forgotten_after_a_complete_list(self):
        slam, entry = table_entry("SLAMDRV.ZD2")
        effect_names.remember([{"id": int(slam, 16), "name": "SlamDrive", "knobs": [["Old", 1]], "file": "SLAMDRV.ZD2",
                                "size": entry["size"] + 7},
                               {"id": NEW, "name": "NewFx", "knobs": [], "file": "NEWFX.ZD2", "size": 100}])
        self.assertEqual(effect_names.effect(int(slam, 16))["knobs"], [["Old", 1]])
        effect_names.forget_unlisted([("SLAMDRV.ZD2", entry["size"]), ("HOGI.ZD2", 1)])   # the table's build now
        self.assertEqual(effect_names.effect(int(slam, 16))["knobs"], entry["knobs"])
        self.assertIsNone(effect_names.by_file("NEWFX.ZD2"))                # no longer on the pedal

    def test_a_file_that_could_not_be_read_is_tried_again_after_the_others(self):
        effect_names.remember([{"file": "NEWFX.ZD2", "size": 100, "id": None, "unread": True}])
        self.assertFalse(effect_names.described("NEWFX.ZD2", 100))
        self.assertTrue(effect_names.tried("NEWFX.ZD2", 100))
        self.assertFalse(effect_names.tried("NEWFX.ZD2", 101))
        effect_names.remember([{"id": NEW, "name": "NewFx", "knobs": [], "file": "NEWFX.ZD2", "size": 100}])
        effect_names.remember([{"file": "NEWFX.ZD2", "size": 100, "id": None, "unread": True}])
        self.assertEqual(effect_names.by_file("NEWFX.ZD2"), NEW)            # what was read of it stays

    def test_a_cache_that_is_not_as_written_is_left_out(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        cache = Path(folder.name, "pedal_effects.json")
        cache.write_text(json.dumps({"A.ZD2": {"id": "zz"}, "B.ZD2": [], "C.ZD2": {
            "id": "09000FA0", "name": "C", "knobs": [["Tone", 100]], "size": 5}}), encoding="utf-8")
        effect_names.use_cache(cache)
        self.assertEqual(effect_names.by_file("C.ZD2"), NEW)
        self.assertFalse(effect_names.described("A.ZD2", None))

    def test_an_effect_read_before_the_custom_effects_moved_category_is_read_again(self):
        """On 2026-10-30 the custom effects left SFX for their own categories at the same size: what
        was read of them before is taken until the next Read Pedal reads their files again."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        cache = Path(folder.name, "pedal_effects.json")
        older = {"id": "09000FA0", "name": "C", "knobs": [["Tone", 100]], "size": 5, "dsp": 10.0}
        cache.write_text(json.dumps({"C.ZD2": older}), encoding="utf-8")
        effect_names.use_cache(cache)
        self.assertEqual(effect_names.by_file("C.ZD2"), NEW)                # taken meanwhile
        self.assertFalse(effect_names.described("C.ZD2", 5))                # … and read again
        effect_names.remember([{"id": 0x08000FA0, "name": "C", "knobs": [["Tone", 100]], "file": "C.ZD2",
                                "size": 5, "dsp": 10.0}])
        self.assertTrue(effect_names.described("C.ZD2", 5))
        effect_names.use_cache(cache)                                       # the next start
        self.assertTrue(effect_names.described("C.ZD2", 5))
        self.assertEqual(effect_names.by_file("C.ZD2"), 0x08000FA0)

    def test_a_synth_effect_is_played_as_the_build_read_from_the_next_start(self):
        knobs = [["Key", 1000], ["Level", 100], ["Wave1", 3], ["Wave2", 4]]
        self.addCleanup(zs.use_installed_synths)
        effect_names.remember([{"id": KEYSYNTH, "name": "KeySynth", "knobs": knobs, "file": "KEYSYNTH.ZD2",
                                "size": 19040}])
        self.assertTrue(bridge.plays_other_build(KEYSYNTH))            # until the next start
        with mock.patch.dict(os.environ, {"MS_PLUS_BRIDGE_SYNTHS": "table"}):
            zs.use_installed_synths()                                  # the next start
        self.assertEqual(zs.KEYSYNTH.knobs, tuple(map(tuple, knobs)))
        self.assertIs(zs.key_effect(KEYSYNTH), zs.KEYSYNTH)
        self.assertFalse(bridge.plays_other_build(KEYSYNTH))
        self.assertNotIn("Blend", zs.KEYSYNTH.controls)
        self.assertEqual(zs.synth_targets(0)[-1], zs.ParamTarget(0, 5, 0, 4))   # by the build read

    def test_a_synth_build_whose_key_knobs_moved_is_not_played(self):
        effect_names.remember([{"id": KEYSYNTH, "name": "KeySynth", "knobs": [["Level", 100], ["Key", 1000]],
                                "file": "KEYSYNTH.ZD2", "size": 19040}])
        self.assertTrue(bridge.can_play_build(KEYSYNTH))                   # from the next start
        self.assertFalse(bridge.keys_in_place(KEYSYNTH))                   # until then no notes into Level
        effect_names.remember([{"id": KEYSYNTH, "name": "KeySynth", "knobs": [["Level", 100]],
                                "file": "KEYSYNTH.ZD2", "size": 19041}])
        self.assertFalse(bridge.can_play_build(KEYSYNTH))                  # no Key knob at all


class ReadingCase(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "read_pedal", "read_effect_file")
        self.world_setup = lambda world: None

    def read(self, data=None, files=FILES, chain=(STOCK, ROOM), reads=1, calls=(), synth=None, until=2.2) -> dict:
        self.install({PRESET: [mapping(0, 2)]}, synth=synth)
        seen = {"replies": []}

        def client(connection):
            connection.at(1.0)
            for _ in range(reads):
                seen["reply"] = connection.call("/api/effects", {"action": "read_pedal"})[:2]
            for call in calls:
                seen["replies"].append(connection.call("/api/effects", call)[:2])
            connection.at(until)
            seen["state"] = connection.call("/api/state")[1]

        world = World(chain=list(chain), length=until + 0.4)
        world.files = files
        world.file_data = {"NEWFX.ZD2": effect_file()} if data is None else data
        self.world_setup(world)
        self.run_bridge(world, client)
        self.world = world
        return seen

    def opened(self) -> list:
        return [bytes.fromhex(entry)[12:-1].decode() for entry in self.world.file_log if entry[:6] == "602002"]

    def kept(self) -> dict:
        return json.loads(bridge.EFFECT_CACHE.read_text(encoding="utf-8"))


class Reading(ReadingCase):
    def test_an_effect_the_table_does_not_know_is_read_and_can_be_chosen(self):
        seen = self.read()
        self.assertEqual(seen["reply"], (200, {"result": None}))
        choices = {choice["id"]: choice["name"] for choice in seen["state"]["effects"]["choices"]}
        self.assertEqual(choices[f"{NEW:08X}"], "NewFx")
        self.assertEqual(self.opened(), ["NEWFX.ZD2", "NEWFX.ZD2"])   # opened twice, as the installer does
        self.assertEqual(sum(entry == zs.FILE_READ_NEXT.hex() for entry in self.world.file_log), 3)  # not to its end
        self.assertIn("1 of them read from the pedal now: NewFx", seen["state"]["effects"]["read"]["result"]["text"])
        self.assertIn("Reading 1 effect file the table does not describe from the pedal", self.output)
        self.assertFalse(self.world.pc_mode)
        self.assertEqual(self.world.file_log[-2:], ["60214000000000", "6009"])    # the file session ended
        self.assertEqual(self.kept()["NEWFX.ZD2"], {"id": f"{NEW:08X}", "name": "NewFx",
                                                    "knobs": [["Tone", 100], ["Drive", 50]], "size": 4400,
                                                    "dsp": None, "since": effect_names.READ_SINCE})   # no DSP in its file

    def test_its_knobs_have_their_names_in_the_interface(self):
        self.read()
        self.assertEqual([knob["name"] for knob in bridge.chain_entry(NEW)["knobs"]], ["Tone", "Drive"])

    def test_read_once_and_then_known(self):
        self.read()
        first = len(self.world.file_log)
        self.read()
        self.assertEqual(self.opened(), [])
        self.assertLess(len(self.world.file_log), first)

    def test_a_custom_effect_rebuilt_is_read_again(self):
        hogi = int(table_entry("HOGI.ZD2")[0], 16)
        seen = self.read({"HOGI.ZD2": effect_file(hogi, "HOGi", [("Gain", 100), ("Tone", 10), ("Blend", 100)])},
                         files=[name for name in FILES if not name.startswith("NEWFX")])
        self.assertEqual(self.opened(), ["HOGI.ZD2", "HOGI.ZD2"])
        self.assertEqual([knob["name"] for knob in bridge.chain_entry(hogi)["knobs"]], ["Gain", "Tone", "Blend"])
        self.assertTrue(seen["state"]["effects"]["read"]["result"]["ok"])

    def test_a_file_that_holds_no_effect_is_known_as_such(self):
        seen = self.read({"NEWFX.ZD2": b"ZBMP" + bytes(2000)})
        self.assertNotIn(f"{NEW:08X}", {choice["id"] for choice in seen["state"]["effects"]["choices"]})
        self.assertEqual(sum(entry == zs.FILE_READ_NEXT.hex() for entry in self.world.file_log), 1)  # its first part
        self.assertIn("NEWFX.ZD2 holds no effect", self.output)
        self.assertEqual(self.kept(), {"NEWFX.ZD2": {"id": None, "size": 2004}})
        self.read({"NEWFX.ZD2": b"ZBMP" + bytes(2000)})
        self.assertEqual(self.opened(), [])                            # not again

    def test_an_effect_of_the_preset_can_be_edited_once_it_is_read(self):
        seen = self.read(chain=(STOCK, NEW), calls=[{"action": "knob", "slot": 1, "param": 3, "value": 20,
                                                     "effect": f"{NEW:08X}"}], until=2.6)
        self.assertEqual(seen["replies"], [(200, {"result": None})])
        self.assertIn((1, 3, 20), [(slot, param, value) for _, slot, param, value in self.world.params])
        self.assertNotIn("not sent", self.output)


class Answers(ReadingCase):
    """The forms of the pedal's answers the installer got (log of 2026-10-21)."""

    def test_a_second_open_refused_and_the_end_of_the_session_late(self):
        def setup(world):
            world.second_open_refused, world.session_end_delay = True, 0.6
        self.world_setup = setup
        self.read({"NEWFX.ZD2": effect_file(), "NEWFY.ZD2": effect_file(OTHER, "Other")}, files=FILES + ["NEWFY.ZD2"],
                  until=4.6)
        self.assertEqual((bridge.chain_entry(NEW)["name"], bridge.chain_entry(OTHER)["name"]), ("NewFx", "Other"))
        self.assertFalse(self.world.pc_mode)

    def test_a_file_shorter_than_its_list_says_is_not_taken_for_one_without_knobs(self):
        for empty_end in (False, True):                                     # both forms of its end
            with self.subTest(empty_end=empty_end):
                def setup(world, empty_end=empty_end):
                    world.listed_sizes["NEWFX.ZD2"], world.empty_end = 9000, empty_end
                self.world_setup = setup
                self.read({"NEWFX.ZD2": zd2(NEW, "NewFx", [("Tone", 100)])[:-56]})   # its knobs run to its end
                self.assertIsNone(effect_names.by_file("NEWFX.ZD2"))
                self.assertTrue(effect_names.tried("NEWFX.ZD2", 9000))
                self.assertIn("ended after", (self.folder / "logs" / "sysex.log").read_text(encoding="utf-8"))

    def test_an_empty_file_holds_no_effect_and_is_not_opened(self):
        self.read({"NEWFX.ZD2": b""})
        self.assertEqual(self.opened(), [])
        self.assertEqual(self.kept(), {"NEWFX.ZD2": {"id": None, "size": 0}})


class Failing(ReadingCase):
    def test_the_list_ending_halfway_still_ends_the_file_session_before_pc_mode(self):
        self.world_setup = lambda world: setattr(world, "list_ends_after", 4)
        seen = self.read(until=3.4)
        self.assertFalse(seen["state"]["effects"]["read"]["result"]["ok"])
        sent = [text for _, text in self.world.sent]
        self.assertLess(sent.index("F0 52 00 6E 60 09 F7"), sent.index("F0 52 00 6E 53 F7"))
        self.assertEqual(self.opened(), [])
        self.assertFalse(self.world.pc_mode)

    def test_a_file_the_pedal_stops_answering_for_keeps_the_list_and_ends_the_reading(self):
        self.world_setup = lambda world: world.silent_files.add("NEWFX.ZD2")
        seen = self.read({"NEWFX.ZD2": effect_file(), "NEWFY.ZD2": effect_file(OTHER, "Other")},
                         files=FILES + ["NEWFY.ZD2"], until=3.6)
        result = seen["state"]["effects"]["read"]["result"]
        self.assertTrue(result["ok"])                                  # the list is taken
        self.assertIn("01000020", {choice["id"] for choice in seen["state"]["effects"]["choices"]})
        self.assertIn("WARNING: NEWFX.ZD2 was not read: the pedal stopped answering while NEWFX.ZD2 was read",
                      self.output)
        self.assertEqual(self.opened(), ["NEWFX.ZD2", "NEWFX.ZD2"])    # and no further file now
        self.assertIn("2 effect files not read yet", self.output)
        self.assertEqual(self.world.file_log[-2:], ["60214000000000", "6009"])

    def test_one_that_was_not_read_comes_after_the_others_the_next_time(self):
        self.world_setup = lambda world: world.silent_files.add("NEWFX.ZD2")
        with mock.patch.object(bridge, "FILES_READ_AT_ONCE", 1):
            self.read({"NEWFX.ZD2": effect_file(), "NEWFY.ZD2": effect_file(OTHER, "Other")},
                      files=FILES + ["NEWFY.ZD2"], reads=2, until=4.4)
        self.assertEqual(self.opened(), ["NEWFX.ZD2", "NEWFX.ZD2", "NEWFY.ZD2", "NEWFY.ZD2"])
        self.assertEqual(bridge.chain_entry(OTHER)["name"], "Other")

    def test_and_also_at_the_next_start(self):
        self.world_setup = lambda world: world.silent_files.add("NEWFX.ZD2")
        data = {"NEWFX.ZD2": effect_file(), "NEWFY.ZD2": effect_file(OTHER, "Other")}
        with mock.patch.object(bridge, "FILES_READ_AT_ONCE", 1):
            self.read(data, files=FILES + ["NEWFY.ZD2"], until=3.0)
            self.read(data, files=FILES + ["NEWFY.ZD2"], until=3.0)        # a bridge started again
        self.assertEqual(self.opened(), ["NEWFY.ZD2", "NEWFY.ZD2"])

    def test_a_request_given_up_on_is_not_run_later(self):
        done = []

        def client(connection):
            connection.at(1.0)
            self.instance.commands.put((lambda: done.append(1), threading.Event(), {"late": True}))
            connection.at(1.4)

        self.install({PRESET: [mapping(0, 2)]})
        self.run_bridge(World(chain=[STOCK, ROOM], length=1.6), client)
        self.assertEqual(done, [])


class Synth(ReadingCase):
    def test_a_synth_effect_of_another_build_is_played_with_notes_only_until_the_next_start(self):
        knobs = [("Key", 1000), ("Level", 100), ("Wave1", 3), ("Wave2", 4)]
        self.addCleanup(zs.use_installed_synths)
        seen = self.read({"KEYSYNTH.ZD2": effect_file(KEYSYNTH, "KeySynth", knobs)}, chain=(KEYSYNTH, ROOM),
                         files=[name for name in FILES if not name.startswith("NEWFX")], synth=SYNTH, until=2.6)
        self.assertEqual(self.opened(), ["KEYSYNTH.ZD2", "KEYSYNTH.ZD2"])
        self.assertIn("KeySynth on the pedal is another build than the bridge plays", self.output)
        self.assertTrue(seen["state"]["chain_effects"][0].get("unmatched"))


class Approval(ReadingCase):
    def test_without_its_approval_only_the_list_is_read(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "read_pedal")
        seen = self.read()
        self.assertEqual(self.opened(), [])
        self.assertTrue(all(entry[:4] in ("6025", "6026", "6021", "6009") for entry in self.world.file_log))
        self.assertIn("not in the table: NEWFX.ZD2 – `python probe.py approve read_effect_file` reads them",
                      seen["state"]["effects"]["read"]["result"]["text"])


class OnThePi(HeadlessCase):
    def test_kept_beside_its_settings_file(self):
        settings = self.write_card()
        self.forget_the_installation()
        bridge.Bridge(settings, self.card)
        self.assertEqual(effect_names.CACHE, self.card / "pedal-effects.json")
        bridge.Bridge(settings)                                       # without its folder: for this run only
        self.assertIsNone(effect_names.CACHE)

    def test_without_network_only_read_from_there(self):
        settings = self.write_card()
        self.forget_the_installation()
        kept = self.card / "pedal-effects.json"
        kept.write_text(json.dumps({"NEWFX.ZD2": {"id": f"{NEW:08X}", "name": "NewFx", "knobs": [], "size": 5}}),
                        encoding="utf-8")
        before = kept.read_text(encoding="utf-8")
        bridge.Bridge(settings, None, kept)
        self.assertEqual(effect_names.by_file("NEWFX.ZD2"), NEW)
        effect_names.remember([{"id": OTHER, "name": "Other", "knobs": [], "file": "NEWFY.ZD2", "size": 5}])
        self.assertEqual(kept.read_text(encoding="utf-8"), before)    # nothing is stored there

    def test_the_export_takes_them_along(self):
        self.write_card()
        bridge.EFFECT_CACHE.write_text(json.dumps({"NEWFX.ZD2": {"id": f"{NEW:08X}", "name": "NewFx", "knobs": [],
                                                                 "size": 5}}), encoding="utf-8")
        (self.card / "pedal-effects.json").write_text(json.dumps({"OLD.ZD2": {"id": None, "size": 3}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            export.main(["--to", str(self.card)])
        self.assertIn("pedal-effects.json", printed.getvalue())
        self.assertEqual(set(json.loads((self.card / "pedal-effects.json").read_text(encoding="utf-8"))),
                         {"NEWFX.ZD2", "OLD.ZD2"})                  # what the Pi read itself stays

    def test_a_synth_build_read_on_the_computer_is_checked_as_it_is_played(self):
        knobs = [["Key", 1000], ["Level", 100], ["Wave1", 3], ["NewKnob", 4]]
        self.addCleanup(zs.use_installed_synths)
        (self.card / "pedal-effects.json").write_text(json.dumps({"KEYSYNTH.ZD2": {
            "id": f"{KEYSYNTH:08X}", "name": "KeySynth", "knobs": knobs, "size": 19040}}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"MS_PLUS_BRIDGE_SYNTHS": "table"}):
            effect_names.use_cache(self.card / "pedal-effects.json", keep=False)
            zs.use_installed_synths()
            self.assertEqual(synth_controls.problems({"KeySynth/NewKnob": 21}), [])


if __name__ == "__main__":
    unittest.main()
