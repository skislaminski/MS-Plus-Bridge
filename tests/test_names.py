"""The names of the pedal's effects and knobs (effect_names.py, zoom_effects.json), the preset's
effects with their names in the interface's state, and "any keyboard" as the keyboard's port."""
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bridge
import effect_names
import zoom_sysex as zs
from tests.sim import CONTROLLER_PORT, KEYBOARD_PORT, ZOOM_PORT, World
from tests.test_synth import K, SynthCase, note_off, note_on, sent

KEYSYNTH, STOCK = 0x07000F61, 0x01000020


def zd2(effect_id: int, name: str, knobs) -> bytes:
    """An effect file as far as effect_names reads it: header, id, name, and the knob table that
    starts with on/off and the effect type."""
    data = bytearray(b"ZDLF" + bytes(92))
    data += struct.pack("<I", effect_id) + name.encode().ljust(11, b"\0") + bytes(400)
    for knob, highest in [("OnOff", 1), (name, 0xFFFFFFFF)] + list(knobs):
        data += knob.encode().ljust(12, b"\0") + struct.pack("<II", highest, 0) + bytes(36)
    return bytes(data + bytes(56))


class Table(unittest.TestCase):
    def test_an_effect_file_gives_id_name_and_knobs(self):
        effect = effect_names.read_zd2(zd2(0x0A000020, "AnalogDly", [("Time", 962), ("Mix", 100)]))
        self.assertEqual(effect, {"id": 0x0A000020, "name": "AnalogDly", "knobs": [["Time", 962], ["Mix", 100]]})
        self.assertIsNone(effect_names.read_zd2(b"RIFF" + bytes(200)))

    def test_a_folder_of_backups_makes_the_table_and_the_newer_file_wins(self):
        with tempfile.TemporaryDirectory() as folder:
            old, new = Path(folder, "a", "KEYSYNF.ZD2"), Path(folder, "b", "KEYSWEEP.ZD2")
            for path, name in ((old, "KeySynF"), (new, "KeySweep")):
                path.parent.mkdir()
                path.write_bytes(zd2(0x09000F71, name, [("Key", 1000)]))
            os.utime(old, (1, 1))
            self.assertEqual(effect_names.build([folder]), {"09000F71": {
                "name": "KeySweep", "knobs": [["Key", 1000]], "file": "KEYSWEEP.ZD2",   # as the pedal lists it
                "size": new.stat().st_size}})                                         # … with its size

    def test_a_custom_effect_is_one_effect_whatever_its_category_and_earlier_name(self):
        with tempfile.TemporaryDirectory() as folder:
            files = [("old/TRACKSYN.ZD2", 0x09000F7D, "TrackSyn", [("Quant", 3)], 1),       # a backup of the pedal
                     ("new/FRETSYN.ZD2", 0x07000F7D, "FretSyn", [("Cutoff", 100)], 3),     # the build since
                     ("stock/BLACKOPT.ZD2", 0x01000020, "BlackOpt", [("Comp", 10)], 1),     # Zoom's own share
                     ("stock/ANALOGDLY.ZD2", 0x0A000020, "AnalogDly", [("Time", 962)], 2)]  # numbers: both stay
            for name, effect_id, effect, knobs, when in files:
                path = Path(folder, name)
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(zd2(effect_id, effect, knobs))
                os.utime(path, (when, when))
            made = effect_names.build([folder])
            self.assertEqual({key: dict(entry, size=None) for key, entry in made.items()}, {
                "01000020": {"name": "BlackOpt", "knobs": [["Comp", 10]], "file": "BLACKOPT.ZD2", "size": None},
                "07000F7D": {"name": "FretSyn", "knobs": [["Cutoff", 100]], "file": "FRETSYN.ZD2", "size": None},
                "0A000020": {"name": "AnalogDly", "knobs": [["Time", 962]], "file": "ANALOGDLY.ZD2", "size": None}})

    def test_one_file_on_the_pedal_is_one_effect_the_newer_build_of_it(self):
        # N2ZBANK.ZD2 was 09000F87 in the backups up to 2026-10-11, nam2zoom 1.1.1 made it 04001787
        with tempfile.TemporaryDirectory() as folder:
            for name, effect_id, when in (("old/N2ZBANK.ZD2", 0x09000F87, 1), ("new/N2ZBANK.ZD2", 0x04001787, 2)):
                path = Path(folder, name)
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(zd2(effect_id, "N2Z Bank", [("Model", 31)]))
                os.utime(path, (when, when))
            self.assertEqual(list(effect_names.build([folder])), ["04001787"])

    def test_knobs_are_numbered_as_the_pedal_numbers_them_and_dummies_are_left_out(self):
        table = {"02000080": {"name": "BassGEQ", "knobs": [["50Hz", 48], ["VOL", 100], ["Dummy", 1]]}}
        with mock.patch.object(effect_names, "_table", table):
            self.assertEqual(effect_names.knobs(0x02000080), [{"param": 2, "name": "50Hz", "max": 48},
                                                              {"param": 3, "name": "VOL", "max": 100}])
            self.assertEqual(effect_names.knobs(0x05000090), [])          # not in the table
            self.assertIsNone(effect_names.effect(0))

    def test_knobs_without_a_range_are_left_out(self):
        table = {"06000060": {"name": "Kick FLNG", "knobs": [["Rate", 50], ["Onf_2", 0], ["Mix", 100]]}}
        with mock.patch.object(effect_names, "_table", table):
            self.assertEqual([(knob["param"], knob["name"]) for knob in effect_names.knobs(0x06000060)],
                             [(2, "Rate"), (4, "Mix")])

    def test_another_category_byte_is_found_by_the_lower_bits_if_only_one_has_them(self):
        table = {"09000F61": {"name": "KeySynth", "knobs": []}, "0A000020": {"name": "AnalogDly", "knobs": []},
                 "01000020": {"name": "BlackOpt", "knobs": []}}
        with mock.patch.object(effect_names, "_table", table):
            self.assertEqual(effect_names.effect(0x07000F61)["name"], "KeySynth")
            self.assertIsNone(effect_names.effect(0x05000020))             # two have …020: neither is meant

    def test_the_table_of_the_pedal_knows_its_effects(self):
        self.assertEqual(effect_names.effect(0x0A000020)["name"], "AnalogDly")
        self.assertEqual([knob["name"] for knob in effect_names.knobs(0x0A000020)], ["Time", "Mode", "Repeat", "Mix"])
        self.assertEqual(effect_names.effect(0x09000F7C)["name"], "KeyWave")
        fretsyn = {knob["param"]: knob["name"] for knob in effect_names.knobs(0x09000F7D)}   # as the pedal has it
        self.assertEqual((fretsyn[7], fretsyn[8]), ("Cutoff", "Reso"))                       # once Quant, Glide

    def test_the_table_has_one_entry_per_custom_effect_and_synth_effects_the_bridge_can_play(self):
        numbers = [int(key, 16) & 0xFFFFFF for key in effect_names.table()]
        custom = [number for number in numbers if number >= effect_names.CUSTOM_FROM]
        self.assertEqual(len(custom), len(set(custom)))                # found by any category byte
        self.assertLess(max(number for number in numbers if number < effect_names.CUSTOM_FROM), 0x100)
        for effect in zs.KEY_EFFECTS:      # the build on the pedal, whichever: its Key knobs where the bridge plays them
            entry = effect_names.effect(0x09000000 | effect.number)
            if entry is None:              # not on the pedal the table was read from: known by number only
                self.assertIn(effect.name, ("KeyBand", "Pluck"))
                continue
            installed = zs.SynthEffect(effect.name, effect.number, tuple(map(tuple, entry["knobs"])))
            self.assertEqual((installed.key_param, installed.voices), (effect.key_param, effect.voices), effect.name)

    def test_the_synth_effects_follow_the_build_the_table_describes(self):
        older = {"09000F61": {"name": "KeySynth", "knobs": [["Key", 1000], ["Level", 100], ["Wave1", 3], ["Rate", 100]]}}
        with mock.patch.dict(os.environ, {"MS_PLUS_BRIDGE_SYNTHS": "table"}), \
                mock.patch.object(effect_names, "_table", older):
            synth = zs.installed("KeySynth", 0xF61, zs.SYNTH_KNOBS)
            self.assertEqual([knob for knob, _ in synth.knobs], ["Key", "Level", "Wave1", "Rate"])
            self.assertEqual(zs.synth_knob("Rate", synth), (5, 100))
            older["09000F61"]["knobs"] = [[name, 100] for name in "ABCDE"] + [["Key", 1000]]   # knob 6: 8 bits
            self.assertEqual(zs.installed("KeySynth", 0xF61, zs.SYNTH_KNOBS).knobs, zs.SYNTH_KNOBS)
        self.assertEqual(zs.installed("KeySynth", 0xF61, zs.SYNTH_KNOBS).knobs, zs.SYNTH_KNOBS)   # the tests' own

    def test_bpm_has_a_name_without_a_file(self):
        self.assertEqual(effect_names.effect(0x09000FF0), {"name": "BPM", "knobs": []})

    def test_a_place_of_the_chain_for_the_interface(self):
        self.assertEqual(bridge.chain_entry(0), {"name": None, "empty": True, "knobs": [], "dsp": 0})
        self.assertEqual(bridge.chain_entry(0x0A000020)["knobs"][-1], {"param": 5, "name": "Mix", "max": 100})
        bridge.chain_entry.cache_clear()                                  # made once per id: made anew here
        self.addCleanup(bridge.chain_entry.cache_clear)
        with mock.patch.object(effect_names, "_table", {}):              # a synth effect of this project anyway
            self.assertEqual(bridge.chain_entry(0x09000F72)["name"], "KeyFM")
            self.assertEqual(bridge.chain_entry(0x09000F72)["knobs"][0], {"param": 2, "name": "Key", "max": zs.KEY_MAX})
            self.assertIsNone(bridge.chain_entry(0x09000F72)["dsp"])          # not in the table: not known
            self.assertEqual(bridge.chain_entry(0x0F000123), {"name": None, "empty": False, "knobs": [], "dsp": None})


class InTheState(SynthCase):
    def test_the_interface_gets_the_effects_of_the_preset_by_name(self):
        state = {}
        world = World([(1.0, lambda world: state.update(self.instance.state()))],
                      ports=lambda seconds: [ZOOM_PORT, CONTROLLER_PORT, KEYBOARD_PORT], chain=[STOCK, KEYSYNTH])
        self.install(synth={"keyboard": KEYBOARD_PORT, "channel": 1})
        self.run_bridge(world)
        self.assertEqual([(entry["slot"], entry["id"], entry["name"]) for entry in state["chain_effects"]],
                         [(0, "01000020", "BlackOpt"), (1, "07000F61", "KeySynth")])
        self.assertEqual(state["patch"]["name"], "USER-001")


class ReachedOverTheNetwork(unittest.TestCase):
    def test_only_names_of_this_computer_reach_the_interface(self):
        import ui
        with mock.patch.object(ui.socket, "gethostname", lambda: "MSPlusExpress"):
            for host in ("10.42.0.1", "10.42.0.1:80", "[fe80::1]:80", "msplusexpress.local", "MSPlusExpress",
                         "localhost:8765"):
                self.assertTrue(ui.reachable_name(host), host)
            for host in ("evil.example", "evil.example:80", "msplusexpress.local.evil.example"):
                self.assertFalse(ui.reachable_name(host), host)


class AnyKeyboard(SynthCase):
    def test_any_keyboard_is_the_input_that_is_none_of_the_others(self):
        ports = lambda seconds: [ZOOM_PORT, CONTROLLER_PORT, "Midi Through:Midi Through Port-0 14:0"] + (
            [KEYBOARD_PORT] if seconds >= 0.8 else [])
        world = World([(1.6, note_on(48)), (1.8, note_off(48))], ports=ports, chain=[KEYSYNTH], length=2.2)
        self.install(synth={"keyboard": bridge.ANY_PORT, "channel": 1})
        with mock.patch.object(bridge, "PORT_CHECK_SECONDS", 0.2):
            self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 2, K(48)), (0, 2, 0)])
        self.assertIn(f"{KEYBOARD_PORT} connected", self.output)
        self.assertNotIn("Midi Through connected", self.output)

    def test_loop_backs_network_sessions_and_daw_ports_are_no_keyboard(self):
        """The inputs of a Mac with an A61 on 2026-10-19: alphabetically Akai's network session came first."""
        names = ["UAC-232 ZOOM UAC-232 MIDI I/O Port", "UAC-232 for ZOOM UAC-232 Mix Control", "IAC-Treiber Bus 1",
                 ZOOM_PORT, KEYBOARD_PORT, "Akai Network - DAW Control", "Akai Network - MIDI", "Komplete Kontrol A DAW",
                 "IAC Driver Bus 1", "Midi Through:Midi Through Port-0 14:0"]
        self.assertEqual([name for name in names if not bridge.NOT_A_KEYBOARD.search(name)], [KEYBOARD_PORT])

    def test_any_keyboard_leaves_the_foot_controller_alone(self):
        world = World([(1.0, note_on(48))], ports=lambda seconds: [ZOOM_PORT, KEYBOARD_PORT, CONTROLLER_PORT],
                      chain=[KEYSYNTH])
        self.install(synth={"keyboard": bridge.ANY_PORT, "channel": 1})
        self.run_bridge(world)
        self.assertIn(f"Ready: {CONTROLLER_PORT} + {KEYBOARD_PORT} →", self.output)


if __name__ == "__main__":
    unittest.main()
