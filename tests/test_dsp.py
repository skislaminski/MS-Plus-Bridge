"""The DSP the effects of a preset take: every effect file declares its own (a float after the first
16 bytes of its chunk INFO), the table and what Read Pedal reads keep it, and the pedal's budget of
270 – measured 2026-10-22: 269.93 ran, 270.04 gave PROCESS OVERFLOW, an effect switched off
counting all the same – is kept by the bridge itself, since over MIDI the pedal takes any effect.
The interface shows it beside the title of the effects and in the list of effects to choose."""
import contextlib
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bridge
import effect_names
from tests.sim import PROJECT, SimulatedCase, World, mapping
from tests.test_change_effect import changes_sent
from tests.test_effect_files import NEW, ReadingCase, effect_file
from tests.test_names import zd2

PRESET = "0/94"
# from zoom_effects.json, as measured with them on the pedal (NOTES.md, "DSP-Grenze des MS-60B+ gemessen")
EBH360, BATALK, BASSGEQ, LOOPROLL = 0x05000050, 0x08000030, 0x02000080, 0x09000010
STOCK, BPM = 0x01000020, 0x09000FF0                         # BlackOpt; BPM is in the pedal itself, no file


def with_dsp(data: bytes, load: float) -> bytes:
    """An effect file (tests/test_names.py's zd2) with a chunk INFO after its header that declares
    the DSP it takes, as the real files do."""
    data = bytearray(data)
    data[4:8] = struct.pack("<I", 112)                      # the chunks begin at 120, after the header
    data[120:148] = b"INFO" + struct.pack("<I", 20) + bytes(16) + struct.pack("<f", load)
    return bytes(data)


class Files(unittest.TestCase):
    def test_a_real_keysynth_declares_52(self):
        data = (PROJECT / "tests" / "files" / "KEYSYNTH.ZD2").read_bytes()      # KeySynth 1.10, a real build
        self.assertEqual(effect_names.read_zd2(data)["dsp"], 52.0)
        done = next(effect for size in range(512, len(data) + 512, 512)
                    for known, effect in [effect_names.read_zd2_part(data[:size])] if known)
        self.assertEqual(done["dsp"], 52.0)                  # as far as Read Pedal reads it

    def test_a_file_declares_it_after_the_first_16_bytes_of_its_chunk_info(self):
        plain = zd2(NEW, "NewFx", [("Tone", 100)])
        self.assertEqual(effect_names.read_zd2(with_dsp(plain, 123.456))["dsp"], 123.46)   # two decimals
        self.assertNotIn("dsp", effect_names.read_zd2(plain))                             # no chunk INFO
        for no_load in (math.nan, math.inf, -1.0):
            self.assertNotIn("dsp", effect_names.read_zd2(with_dsp(plain, no_load)), no_load)
        short = bytearray(with_dsp(plain, 50.0))
        short[124:128] = struct.pack("<I", 16)                # an INFO without the float
        self.assertNotIn("dsp", effect_names.read_zd2(bytes(short)))

    def test_the_beginning_of_a_file_is_read_once_its_dsp_is_known_too(self):
        plain = zd2(NEW, "NewFx", [("Tone", 100), ("Drive", 50)])
        data = bytearray(plain + b"INFO" + struct.pack("<I", 20) + bytes(16) + struct.pack("<f", 75.0))
        data[4:8] = struct.pack("<I", len(plain) - 8)         # its chunks only after the table of knobs
        data = bytes(data)
        for size in (len(plain), len(plain) + 24):            # the knobs ended, the float not in yet
            self.assertEqual(effect_names.read_zd2_part(data[:size]), (False, None), size)
        self.assertEqual(effect_names.read_zd2_part(data), (True, effect_names.read_zd2(data)))
        self.assertEqual(effect_names.read_zd2(data)["dsp"], 75.0)
        self.assertEqual(effect_names.read_zd2_part(plain)[0], True)   # no chunks at all: it declares none


class Table(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(effect_names.use_cache, None)
        effect_names.use_cache(None)

    def test_every_effect_of_the_table_has_its_dsp(self):
        table = json.loads(effect_names.TABLE.read_text(encoding="utf-8"))
        self.assertEqual([key for key, entry in table.items() if not isinstance(entry.get("dsp"), float)], [])
        self.assertEqual([effect_names.dsp(effect) for effect in (EBH360, BATALK, BASSGEQ, LOOPROLL)],
                         [116.89, 139.51, 13.53, 13.64])
        self.assertEqual(round(116.89 + 139.51 + 13.53, 2), 269.93)                 # ran on the pedal
        self.assertEqual(round(116.89 + 139.51 + 13.64, 2), 270.04)                 # PROCESS OVERFLOW
        self.assertIsNone(effect_names.dsp(BPM))                                    # no file to declare it
        self.assertIsNone(effect_names.dsp(0x0F000123))

    def test_the_dsp_of_the_tables_effects_set_from_their_files_and_nothing_else(self):
        made = {"09000FA0": {"name": "NewFx", "knobs": [["Tone", 100]], "file": "NEWFX.ZD2", "size": 0},
                "09000FA1": {"name": "Other", "knobs": [], "file": "OTHER.ZD2", "size": 1, "labels": {"2": ["A", "B"]}},
                "09000FA2": {"name": "Gone", "knobs": [], "file": "GONE.ZD2", "size": 1, "dsp": 9.5}}
        with tempfile.TemporaryDirectory() as folder:
            paths = {}
            for place, name, load, extra, when in (("a", "NEWFX.ZD2", 40.0, 0, 1), ("b", "NEWFX.ZD2", 41.0, 7, 2),
                                                   ("a", "OTHER.ZD2", 60.0, 0, 1), ("b", "OTHER.ZD2", 61.0, 3, 2)):
                path = Path(folder, place, name)
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(with_dsp(zd2(NEW, "NewFx", [("Tone", 100)]), load) + bytes(extra))
                paths[place, name] = path
                os.utime(path, (when, when))
            made["09000FA0"]["size"] = paths["a", "NEWFX.ZD2"].stat().st_size       # the older one is of its size
            before = json.loads(json.dumps(made))
            said = []
            done = effect_names.set_dsp(made, [folder], say=said.append)
            self.assertEqual(done, {"09000FA0": paths["a", "NEWFX.ZD2"], "09000FA1": paths["b", "OTHER.ZD2"]})
            self.assertEqual([made[key].get("dsp") for key in made], [40.0, 61.0, 9.5])   # the newest of its name
            self.assertEqual({key: {name: value for name, value in entry.items() if name != "dsp"}
                              for key, entry in made.items()},
                             {key: {name: value for name, value in entry.items() if name != "dsp"}
                              for key, entry in before.items()})
            self.assertEqual(len(said), 2)
            self.assertIn("no OTHER.ZD2 of 1 bytes there, the newest of that name is read", said[0])
            self.assertIn("no GONE.ZD2 there – its DSP stays as it is", said[1])
            table = Path(folder, "zoom_effects.json")
            effect_names.write_table(before, table)
            with mock.patch.object(effect_names, "TABLE", table), contextlib.redirect_stdout(io.StringIO()):
                effect_names.main(["--dsp", folder])
            self.assertEqual(json.loads(table.read_text(encoding="utf-8")), made)   # as write_table writes it

    def test_a_table_read_again_takes_the_dsp_from_the_files(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "NEWFX.ZD2").write_bytes(with_dsp(zd2(NEW, "NewFx", [("Tone", 100)]), 33.3))
            entry = effect_names.build([folder])[f"{NEW:08X}"]
        self.assertEqual(list(entry), ["name", "knobs", "file", "size", "dsp"])
        self.assertAlmostEqual(entry["dsp"], 33.3)


class Cache(unittest.TestCase):
    """What Read Pedal read is kept with the DSP; what was read before 2026-10-22 has none and is
    read once more."""

    def setUp(self) -> None:
        self.addCleanup(effect_names.use_cache, None)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cache = Path(folder.name, "pedal_effects.json")

    def test_an_effect_read_before_is_read_again_once(self):
        self.cache.write_text(json.dumps({"NEWFX.ZD2": {"id": f"{NEW:08X}", "name": "NewFx", "knobs": [["Tone", 100]],
                                                        "size": 4400},
                                          "EMPTY.ZD2": {"id": None, "size": 10}}), encoding="utf-8")
        effect_names.use_cache(self.cache)
        self.assertEqual(effect_names.effect(NEW)["name"], "NewFx")        # known meanwhile, by name and knobs
        self.assertIsNone(effect_names.dsp(NEW))
        self.assertFalse(effect_names.described("NEWFX.ZD2", 4400))       # the next Read Pedal reads it again
        self.assertTrue(effect_names.described("EMPTY.ZD2", 10))          # holds no effect: nothing to read
        effect_names.remember([{"id": NEW, "name": "NewFx", "knobs": [["Tone", 100]], "file": "NEWFX.ZD2",
                                "size": 4400, "dsp": 55.0}])
        self.assertTrue(effect_names.described("NEWFX.ZD2", 4400))
        self.assertEqual(effect_names.dsp(NEW), 55.0)
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8"))["NEWFX.ZD2"]["dsp"], 55.0)
        effect_names.use_cache(self.cache)                                 # the next start
        self.assertTrue(effect_names.described("NEWFX.ZD2", 4400))
        self.assertEqual(effect_names.dsp(NEW), 55.0)

    def test_one_whose_file_declares_none_is_not_read_again(self):
        effect_names.use_cache(self.cache)
        effect_names.remember([{"id": NEW, "name": "NewFx", "knobs": [], "file": "NEWFX.ZD2", "size": 100}])
        self.assertIsNone(json.loads(self.cache.read_text(encoding="utf-8"))["NEWFX.ZD2"]["dsp"])
        effect_names.use_cache(self.cache)
        self.assertTrue(effect_names.described("NEWFX.ZD2", 100))
        self.assertIsNone(effect_names.dsp(NEW))

    def test_a_dsp_that_is_no_load_is_left_out_with_its_entry(self):
        entry = {"name": "X", "knobs": [], "size": 100}
        self.cache.write_text(json.dumps({"A.ZD2": dict(entry, id="09000FA0", dsp="55"),
                                          "B.ZD2": dict(entry, id="09000FA1", dsp=-1),
                                          "C.ZD2": dict(entry, id="09000FA2", dsp=True),
                                          "D.ZD2": dict(entry, id="09000FA3", dsp=52)}), encoding="utf-8")
        effect_names.use_cache(self.cache)
        self.assertEqual([effect_names.by_file(name) for name in ("A.ZD2", "B.ZD2", "C.ZD2")], [None] * 3)
        self.assertEqual(effect_names.dsp(0x09000FA3), 52.0)

    def test_the_build_read_brings_its_own_dsp(self):
        effect_names.use_cache(None)
        hogi = effect_names.by_file("HOGI.ZD2")
        size = next(entry["size"] for entry in effect_names.table().values() if entry["file"] == "HOGI.ZD2")
        effect_names.remember([{"id": hogi, "name": "HOGi", "knobs": [["Gain", 10]], "file": "HOGI.ZD2",
                                "size": size + 1, "dsp": 99.5}])          # another build than the table's
        self.assertEqual(effect_names.dsp(hogi), 99.5)
        effect_names.remember([{"id": hogi, "name": "HOGi", "knobs": [["Gain", 10]], "file": "HOGI.ZD2",
                                "size": size + 2}])                       # one that declares none
        self.assertIsNone(effect_names.dsp(hogi))                         # not the table build's


class ReadPedal(ReadingCase):
    def test_an_effect_read_before_gets_its_dsp_at_the_next_read_pedal(self):
        bridge.EFFECT_CACHE.write_text(json.dumps({"NEWFX.ZD2": {            # as read before 2026-10-22
            "id": f"{NEW:08X}", "name": "NewFx", "knobs": [["Tone", 100], ["Drive", 50]], "size": 4400}}),
            encoding="utf-8")
        data = {"NEWFX.ZD2": with_dsp(effect_file(), 75.0)}
        seen = self.read(data)
        self.assertEqual(self.opened(), ["NEWFX.ZD2", "NEWFX.ZD2"])
        self.assertEqual(self.kept()["NEWFX.ZD2"]["dsp"], 75.0)
        self.assertEqual({choice["id"]: choice["dsp"] for choice in seen["state"]["effects"]["choices"]}[f"{NEW:08X}"],
                         75.0)
        self.read(data)
        self.assertEqual(self.opened(), [])                               # once


class Budget(SimulatedCase):
    """The effects of the other places (on or off) and the one chosen together may not take more
    than DSP_BUDGET: the bridge refuses it, the pedal would take it and show PROCESS OVERFLOW."""

    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "change_effect")

    def change(self, slot, effect, chain=(EBH360, BATALK, STOCK), off=(), reported=(BASSGEQ, LOOPROLL, NEW, BPM)):
        """Calls /api/effects "change" once; returns the reply and the state after."""
        self.install({PRESET: [mapping(0, 2)]})
        known = json.loads(bridge.MEASUREMENTS.read_text(encoding="utf-8"))
        known["effects"] = [f"{effect_id:08X}" for effect_id in reported]
        bridge.MEASUREMENTS.write_text(json.dumps(known), encoding="utf-8")
        seen = []

        def client(connection):
            connection.at(1.0)
            seen.append(connection.call("/api/effects", {"action": "change", "slot": slot,
                                                         "effect": f"{effect:08X}"})[:2])
            connection.at(1.8)
            seen.append(connection.call("/api/state")[1])

        self.world = World(chain=list(chain), off=off, length=2.0)
        self.run_bridge(self.world, client)
        return seen

    def test_one_that_fits_goes_to_the_pedal(self):
        reply, state = self.change(2, BASSGEQ)                             # 269.93, as it ran on the pedal
        self.assertEqual(reply, (200, {"result": None}))
        self.assertEqual(changes_sent(self.world), [(2, BASSGEQ)])
        self.assertEqual([entry["dsp"] for entry in state["chain_effects"]], [116.89, 139.51, 13.53])

    def test_one_that_does_not_fit_is_refused(self):
        reply, state = self.change(2, LOOPROLL)                            # 270.04: PROCESS OVERFLOW
        self.assertEqual(reply, (400, {"error": "LoopRoll takes 14 of the DSP, 14 are free in this preset – the "
                                                "pedal would show PROCESS OVERFLOW"}))
        self.assertEqual(changes_sent(self.world), [])
        self.assertEqual(self.world.chain, [EBH360, BATALK, STOCK])

    def test_an_effect_that_is_off_counts_all_the_same(self):
        reply, state = self.change(2, LOOPROLL, off=(0,))
        self.assertEqual(reply[0], 400)
        self.assertEqual(state["effects"]["chain"], [False, True, True])
        self.assertEqual(changes_sent(self.world), [])

    def test_the_effect_it_replaces_does_not_count(self):
        reply, _ = self.change(0, LOOPROLL, chain=(EBH360, BATALK, BASSGEQ))   # 139.51 + 13.53 + 13.64
        self.assertEqual(reply, (200, {"result": None}))

    def test_an_effect_whose_dsp_is_not_known_is_let_through(self):
        reply, state = self.change(2, NEW)                                 # the table does not know it
        self.assertEqual(reply, (200, {"result": None}))
        self.assertEqual(changes_sent(self.world), [(2, NEW)])
        reply, _ = self.change(2, LOOPROLL, chain=(EBH360, NEW, STOCK))    # … and does not count beside it
        self.assertEqual(reply, (200, {"result": None}))

    def test_the_interface_gets_the_budget_and_the_dsp_of_the_choices(self):
        _, state = self.change(2, BASSGEQ)
        self.assertEqual(state["effects"]["dsp_budget"], bridge.DSP_BUDGET)
        self.assertEqual(bridge.DSP_BUDGET, 270)
        self.assertEqual({choice["name"]: choice["dsp"] for choice in state["effects"]["choices"]},
                         {"BlackOpt": 27.15, "BassGEQ": 13.53, "EBH360": 116.89, "BaTalkSyn": 139.51,
                          "LoopRoll": 13.64, "BPM": None})               # NewFx: no name, not offered
        self.assertEqual(bridge.chain_entry(0)["dsp"], 0)                  # an empty place takes none

    def test_the_refusal_compares_the_files_loads_and_says_them_rounded(self):
        self.assertIsNone(bridge.over_budget([EBH360, BATALK, 0], 2, BASSGEQ))
        self.assertIsNone(bridge.over_budget([EBH360, BATALK, 0], 2, NEW))
        self.assertIn("are free", bridge.over_budget([EBH360, BATALK, 0], 2, LOOPROLL))
        self.assertIsNone(bridge.over_budget([EBH360, BATALK, LOOPROLL], 2, BASSGEQ))   # in place of LoopRoll
        self.assertIn("0 are free", bridge.over_budget([EBH360, BATALK, LOOPROLL, BASSGEQ, 0], 4, BASSGEQ))
        self.assertEqual([bridge._shown(load) for load in (120.5, 129.45, 13.6)], [121, 129, 14])


class Page(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_the_script_of_the_interface_is_valid(self):
        page = (PROJECT / "ui.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
        self.assertEqual(len(scripts), 1)
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder, "ui.js")
            script.write_text(scripts[0], encoding="utf-8")
            checked = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        for part in ('id="dsp"', "function showDsp(", "select option:disabled"):
            self.assertIn(part, page)


if __name__ == "__main__":
    unittest.main()
