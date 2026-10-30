"""The texts the pedal shows for the values of knobs ("Rock", "Sync", "1/8."): read from the
custom effects' files (effect_names: the function their builds share, and its table of texts),
kept in the table and with what Read Pedal reads from the pedal, and given to the interface (a
knob's "text" in the state, GET /api/labels). The real file is this project's own KeySynth build
(tests/files, KeySynth 1.10); the others are made here as the custom builds are."""
import contextlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bridge
import effect_names
from tests.sim import PROJECT, World, mapping
from tests.test_effect_files import FILES, PRESET, ReadingCase, STOCK

KEYSYNTH_FILE = PROJECT / "tests" / "files" / "KEYSYNTH.ZD2"
N2Z, NEW = 0x04001787, 0x09000FA0
MODELS = ["Fender", "Marshall", "Vox"]


def custom_file(effect_id: int, name: str, knobs, texts=None, gap: int = 0, after=(), code_max=None) -> bytes:
    """An effect file as the custom builds are made (stomphacks' generator): the header, and the
    chunk DATA with the effect's ELF – its code (the function that copies a knob's text from its
    table, effect_names.TEMPLATE, once for each knob in texts), then .const: the table of knobs (its
    last knob marked so, entries after it: after), gap bytes, and the tables of texts. texts: {knob:
    [text per value]}; code_max: {knob: the highest value its function has, if not the knob's}."""
    texts, code_max = texts or {}, code_max or {}
    entries = [("OnOff", 1), (name, 0)] + list(knobs) + list(after)
    tables, where = b"", {}
    for knob, values in texts.items():
        where[knob] = effect_names.CONST_ADDRESS + effect_names.KNOB_SIZE * len(entries) + gap + len(tables)
        tables += b"".join(text.encode("latin1").ljust(effect_names.TEXT_SIZE, b"\0") for text in values)
    code, function = bytes(32), {}                       # nothing at address 0: that is "no function"
    for knob, highest in knobs:
        if knob in texts:
            words = list(effect_names.TEMPLATE)
            words[0] |= code_max.get(knob, highest) << 7
            words[2] |= (where[knob] & 0xFFFF) << 7
            words[3] |= (where[knob] >> 16) << 7
            function[knob] = len(code)
            code += struct.pack(f"<{len(words)}I", *words)
    const = b""
    for index, (knob, highest) in enumerate(entries):
        last = effect_names.LAST if index == 1 + len(knobs) else 0
        const += knob.encode().ljust(12, b"\0") + struct.pack(
            "<11I", highest, 0, 0, 0, 0, 0, function.get(knob, 0) if index >= 2 else 0, 0, 0, last, 0)
    elf = b"\x7fELF" + bytes(effect_names.TEXT_AT - 4) + code + const + bytes(gap) + tables
    head = (b"ZDLF" + struct.pack("<I", 120) + bytes(88) + struct.pack("<I", effect_id)
            + name.encode().ljust(11, b"\0")).ljust(128, b"\0")
    return head + b"DATA" + struct.pack("<I", len(elf)) + elf + bytes(600)


def labelled_effect(**extra) -> bytes:
    return custom_file(NEW, "NewFx", [("Mode", 2), ("Mix", 100), ("Sync", 3)],
                       {"Mode": ["Up", "Down", "Both"], "Sync": ["Off", "1/4", "1/8", "1/16"]}, **extra)


class FromTheFiles(unittest.TestCase):
    def test_the_texts_of_a_real_custom_build(self):
        effect = effect_names.read_zd2(KEYSYNTH_FILE.read_bytes())
        self.assertEqual([name for name, _ in effect["knobs"]][2:5], ["Wave1", "Wave2", "Pitch"])
        self.assertEqual(sorted(effect["labels"], key=int), ["4", "5", "6", "12"])   # Wave1, Wave2, Pitch, LFO
        self.assertEqual(effect["labels"]["4"], ["Saw", "Sqr", "Tri", "Sine"])
        self.assertEqual(effect["labels"]["5"], ["Off", "Saw", "Sqr", "Tri", "Sine"])
        pitch = effect["labels"]["6"]
        self.assertEqual((len(pitch), pitch[0], pitch[24], pitch[-1]), (49, "-24", "0", "+24"))
        lfo = effect["labels"]["12"]
        self.assertEqual((len(lfo), lfo[0], lfo[50], lfo[-1]), (101, "Vib50", "Off", "Trm50"))

    def test_read_from_the_pedal_until_the_texts_are_in(self):
        data = KEYSYNTH_FILE.read_bytes()
        start = data.find(b"OnOff\0")
        parts = [data[:512 * blocks] for blocks in range(1, len(data) // 512 + 2)]
        done = next(index for index, part in enumerate(parts) if effect_names.read_zd2_part(part)[0])
        self.assertGreater(done, 0)
        self.assertTrue(effect_names._knob_entries(parts[done - 1], start)[1])   # its knobs had ended before
        self.assertEqual(effect_names.read_zd2_part(parts[done]), (True, effect_names.read_zd2(data)))
        self.assertLess(len(parts[done]), len(data))                              # not to its end

    def test_texts_as_the_custom_builds_have_them(self):
        effect = effect_names.read_zd2(labelled_effect(gap=300))
        self.assertEqual(effect["knobs"], [["Mode", 2], ["Mix", 100], ["Sync", 3]])
        self.assertEqual(effect["labels"], {"2": ["Up", "Down", "Both"], "4": ["Off", "1/4", "1/8", "1/16"]})

    def test_only_texts_that_are_not_just_the_numbers(self):
        knobs = [("Numbers", 2), ("Padded", 1), ("Broken", 1), ("Other", 1), ("Notes", 1)]
        texts = {"Numbers": [" 0", "1 ", "2"], "Padded": [" Off  ", "On"], "Broken": ["Off", "O\x01"],
                 "Other": ["A", "B"], "Notes": ["\x18.", "\x19 3"]}
        effect = effect_names.read_zd2(custom_file(NEW, "NewFx", knobs, texts, code_max={"Other": 2}))
        # no text, or another highest value: none; the pedal's note characters are kept
        self.assertEqual(effect["labels"], {"3": ["Off", "On"], "6": ["\x18.", "\x19 3"]})

    def test_another_function_shows_no_text(self):
        data = bytearray(labelled_effect())
        start = data.find(b"OnOff\0")
        function = struct.unpack_from("<I", data, start + 2 * effect_names.KNOB_SIZE + effect_names.SHOWN_BY)[0]
        code = data.find(b"\x7fELF") + effect_names.TEXT_AT + function
        data[code + 4 * 5] ^= 1                                       # one instruction of it is another
        self.assertEqual(effect_names.read_zd2(bytes(data))["labels"], {"4": ["Off", "1/4", "1/8", "1/16"]})

    def test_the_part_read_is_done_once_the_tables_of_texts_are_in(self):
        data = labelled_effect(gap=2000)
        ends = data.find(b"OnOff\0") + 5 * effect_names.KNOB_SIZE + 2000 + 7 * effect_names.TEXT_SIZE
        self.assertEqual(effect_names.read_zd2_part(data[:ends - 1]), (False, None))
        self.assertEqual(effect_names.read_zd2_part(data[:ends]), (True, effect_names.read_zd2(data)))

    def test_the_table_of_knobs_ends_at_its_last(self):
        # as HoldDelay (HLDDLY1U.ZD2): after its last knob (Dummy) an entry that is no knob, ['2', 20]
        data = custom_file(0x0A000070, "HoldDelay", [("Time", 962), ("Tail", 1), ("Dummy", 0)], after=[("2", 20)])
        self.assertEqual(effect_names.read_zd2(data)["knobs"], [["Time", 962], ["Tail", 1], ["Dummy", 0]])
        ends = data.find(b"OnOff\0") + 5 * effect_names.KNOB_SIZE
        self.assertTrue(effect_names.read_zd2_part(data[:ends])[0])

    def test_the_table_made_again_keeps_the_texts_of_zooms_effects(self):
        with tempfile.TemporaryDirectory() as folder:
            files = Path(folder, "files")
            files.mkdir()
            (files / "NEWFX.ZD2").write_bytes(labelled_effect())
            (files / "ANALOGDLY.ZD2").write_bytes(custom_file(0x0A000020, "AnalogDly", [("Time", 2), ("Mix", 100)]))
            (files / "HLDDLY1U.ZD2").write_bytes(custom_file(0x0A000070, "HoldDelay", [("Time", 1)]))
            table = Path(folder, "zoom_effects.json")
            table.write_text(json.dumps({
                "0A000020": {"name": "AnalogDly", "knobs": [["Time", 2], ["Mix", 100]], "file": "ANALOGDLY.ZD2",
                             "size": 1, "labels": {"2": ["1ms", "2ms", "3ms"]}},
                "0A000070": {"name": "HoldDelay", "knobs": [["Time", 1], ["2", 20]], "file": "HLDDLY1U.ZD2",
                             "size": 1, "labels": {"2": ["A", "B"]}},
                "09000FA0": {"name": "NewFx", "knobs": [["Mode", 2], ["Mix", 100], ["Sync", 3]], "file": "NEWFX.ZD2",
                             "size": 1, "labels": {"2": ["Old", "Old", "Old"], "3": ["x"] * 101}}}), encoding="utf-8")
            with mock.patch.object(effect_names, "TABLE", table), contextlib.redirect_stdout(io.StringIO()):
                effect_names.main([str(files)])
            made = json.loads(table.read_text(encoding="utf-8"))
        self.assertEqual(made["0A000020"]["labels"], {"2": ["1ms", "2ms", "3ms"]})   # the same file, the same knobs
        self.assertNotIn("labels", made["0A000070"])                                 # other knobs now
        self.assertEqual(made["09000FA0"]["labels"], {"2": ["Up", "Down", "Both"], "3": ["x"] * 101,
                                                      "4": ["Off", "1/4", "1/8", "1/16"]})   # the file's win

    def test_the_table_holds_them_one_list_per_line(self):
        made = {"0A000020": {"name": "AnalogDly", "knobs": [["Time", 1]], "file": "ANALOGDLY.ZD2", "size": 1,
                             "labels": {"2": ["\x18.", "\x19 3"]}}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "zoom_effects.json")
            effect_names.write_table(made, path)
            text = path.read_text(encoding="utf-8")
        self.assertEqual(json.loads(text), made)
        self.assertIn('\n   "2": ["\\u0018.", "\\u0019 3"]}', text)


class InTheTable(unittest.TestCase):
    TABLE = {"09000F8A": {"name": "DrumBox", "knobs": [["Pattern", 2], ["Level", 100], ["Fill", 1]], "file": "DRUMBOX.ZD2",
                          "size": 30065, "labels": {"2": ["Rock", "Rock2", "HalfT"], "4": ["Off", "4Bar"]}}}

    def setUp(self) -> None:
        self.addCleanup(effect_names.use_cache, None)
        effect_names.use_cache(None)
        bridge.chain_entry.cache_clear()
        self.addCleanup(bridge.chain_entry.cache_clear)
        patcher = mock.patch.object(effect_names, "_table", self.TABLE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_texts_of_an_effect_by_its_id(self):
        self.assertEqual(effect_names.labels(0x09000F8A), {2: ["Rock", "Rock2", "HalfT"], 4: ["Off", "4Bar"]})
        self.assertEqual(effect_names.labels(0x07000F8A)[2][0], "Rock")              # another category byte
        self.assertEqual(effect_names.labels(0x01000020), {})                        # not known
        self.assertEqual(effect_names.labels(0), {})

    def test_the_knobs_with_texts_in_the_interface(self):
        knobs = bridge.chain_entry(0x09000F8A)["knobs"]
        self.assertEqual(knobs, [{"param": 2, "name": "Pattern", "max": 2, "text": True},
                                 {"param": 3, "name": "Level", "max": 100},
                                 {"param": 4, "name": "Fill", "max": 1, "text": True}])
        self.assertEqual(bridge.Bridge.value_labels(0x09000F8A), {"2": ["Rock", "Rock2", "HalfT"], "4": ["Off", "4Bar"]})

    def test_read_from_the_pedal_and_kept(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        cache = Path(folder.name, "pedal_effects.json")
        effect_names.use_cache(cache)
        effect_names.remember([dict(effect_names.read_zd2(labelled_effect()), file="NEWFX.ZD2", size=4000)])
        self.assertEqual(effect_names.labels(NEW), {2: ["Up", "Down", "Both"], 4: ["Off", "1/4", "1/8", "1/16"]})
        self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["NEWFX.ZD2"]["labels"]["2"], ["Up", "Down", "Both"])
        effect_names.use_cache(None)
        self.assertEqual(effect_names.labels(NEW), {})
        effect_names.use_cache(cache)                                                 # the next start
        self.assertEqual(effect_names.labels(NEW)[4], ["Off", "1/4", "1/8", "1/16"])

    def test_another_build_read_keeps_the_tables_texts_for_the_same_knobs(self):
        # another build read from the pedal: the texts its file holds win, those of the table's entry
        # for the same file are kept for the other knobs (as Pong Delay's, WFPONG.ZD2, which only its
        # code makes: tools/value_labels) – while the knobs are the same
        knobs = [["Pattern", 2], ["Level", 100], ["Fill", 1]]
        effect_names.remember([{"id": 0x09000F8A, "name": "DrumBox", "knobs": knobs, "file": "DRUMBOX.ZD2",
                                "size": 30163, "labels": {"2": ["Rock", "Rock2", "Half"]}}])
        self.assertEqual(effect_names.labels(0x09000F8A), {2: ["Rock", "Rock2", "Half"], 4: ["Off", "4Bar"]})
        effect_names.remember([{"id": 0x09000F8A, "name": "DrumBox", "knobs": knobs[:2], "file": "DRUMBOX.ZD2",
                                "size": 30200}])
        self.assertEqual(effect_names.labels(0x09000F8A), {})                        # other knobs: none
        bank = {"04001787": {"name": "N2Z Bank", "knobs": [["Model", 2]], "file": "N2ZBANK.ZD2", "size": 5,
                             "labels": {"2": MODELS}}}
        with mock.patch.object(effect_names, "_table", bank):
            effect_names.remember([{"id": N2Z, "name": "N2Z Bank", "knobs": [["Model", 2]], "file": "N2ZBANK.ZD2",
                                    "size": 5}])
            self.assertEqual(effect_names.labels(N2Z), {})                           # read at every Read Pedal: as read

    def test_texts_in_the_cache_that_do_not_fit_its_knobs_are_left_out(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        cache = Path(folder.name, "pedal_effects.json")
        cache.write_text(json.dumps({"NEWFX.ZD2": {"id": f"{NEW:08X}", "name": "NewFx", "size": 5,
                                                   "knobs": [["Mode", 2], ["Mix", 100], ["Sync", 1]], "labels": {
                                                       "2": ["Up", "Down", "Both"], "3": ["a", "b"], "4": ["Off", 1],
                                                       "5": ["x", "y"], "x": ["a"], "1": ["a", "b"]}},
                                     "OTHER.ZD2": {"id": "09000FA1", "name": "Other", "size": 5, "knobs": [],
                                                   "labels": ["a"]}}), encoding="utf-8")
        effect_names.use_cache(cache)
        self.assertEqual(effect_names.labels(NEW), {2: ["Up", "Down", "Both"]})
        self.assertEqual(effect_names.effect(0x09000FA1)["name"], "Other")         # the entry stays

    def test_the_number_the_interface_asks_again_by(self):
        seen = effect_names.version()
        self.assertEqual(effect_names.version(), seen)
        effect_names.remember([{"file": "NEWFX.ZD2", "size": 5, "id": None}])
        self.assertGreater(effect_names.version(), seen)
        seen = effect_names.version()
        with mock.patch.object(effect_names, "_table", {}):                          # another table
            self.assertGreater(effect_names.version(), seen)


class ReadFromThePedal(ReadingCase):
    def test_n2z_bank_is_read_at_every_read_pedal_even_at_the_size_the_table_has(self):
        size = next(entry["size"] for entry in effect_names.table().values() if entry["file"] == "N2ZBANK.ZD2")
        self.assertFalse(effect_names.described("N2ZBANK.ZD2", size))
        self.world_setup = lambda world: world.listed_sizes.update({"N2ZBANK.ZD2": size})
        data = custom_file(N2Z, "N2Z Bank", [("Model", 2), ("Mix", 100)], {"Model": MODELS})
        files = [name for name in FILES if not name.startswith("NEWFX")] + ["N2ZBANK.ZD2"]
        self.read({"N2ZBANK.ZD2": data}, files=files, reads=2, until=3.0)
        self.assertEqual(self.opened(), ["N2ZBANK.ZD2"] * 4)                        # read twice, twice opened each
        self.assertEqual(effect_names.effect(N2Z)["knobs"], [["Model", 2], ["Mix", 100]])   # not the table's
        self.assertEqual(effect_names.labels(N2Z), {2: MODELS})

    def test_the_interface_gets_the_texts_of_an_effect_read(self):
        self.install({PRESET: [mapping(0, 2)]})
        seen = {}

        def client(connection):
            connection.at(1.0)
            seen["before"] = connection.call("/api/state")[1]["effects"]["labels_version"]
            seen["read"] = connection.call("/api/effects", {"action": "read_pedal"})[:2]
            for key, effect_id in (("labels", f"{NEW:08X}"), ("lower", f"{NEW:08x}"), ("unknown", "0F000123"),
                                   ("bad", "12345"), ("none", None)):
                seen[key] = connection.call("/api/labels" + (f"?id={effect_id}" if effect_id else ""))[:2]
            connection.at(2.6)
            seen["state"] = connection.call("/api/state")[1]

        world = World(chain=[STOCK, NEW], length=3.0)
        world.files, world.file_data = FILES, {"NEWFX.ZD2": labelled_effect(gap=1500)}
        self.run_bridge(world, client)
        self.assertEqual(seen["read"], (200, {"result": None}))
        texts = {"2": ["Up", "Down", "Both"], "4": ["Off", "1/4", "1/8", "1/16"]}
        self.assertEqual(seen["labels"], (200, {"labels": texts}))
        self.assertEqual(seen["lower"], (200, {"labels": texts}))
        self.assertEqual(seen["unknown"], (200, {"labels": {}}))
        self.assertEqual(seen["bad"][0], 400)
        self.assertEqual(seen["none"][0], 400)
        self.assertGreater(seen["state"]["effects"]["labels_version"], seen["before"])
        knobs = seen["state"]["chain_effects"][1]["knobs"]
        self.assertEqual([(knob["name"], knob.get("text", False)) for knob in knobs],
                         [("Mode", True), ("Mix", False), ("Sync", True)])


if __name__ == "__main__":
    unittest.main()
