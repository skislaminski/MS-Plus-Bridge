"""The seven synth effects the keyboard plays: KeySynth, KeySweep (KeySynF before), KeyFM, KeyPoly,
KeyWave, KeyBand and Pluck.

Run against the simulated devices from sim.py. The bridge knows the effects by the number in
their id and not by the category in front of it: an installer puts an effect into the category
the user picks, and the id on the pedal changes with it. The ids below are the ones read from an
MS-60B+ on 2026-10-11, where the newer effects sat in "SFX" (09) and KeySynth in "Pitch shift"
(07); KeyBand and Pluck (built as 07000f94 and 07000f8f) as the installer puts them into SFX. No
effect id is set in config.yaml in any of these tests.
"""
import json
import os
import time
import unittest
from pathlib import Path
from unittest import mock

import bridge
import effect_names
import synth_controls
import zoom_sysex as zs
from tests.sim import ID, KEYBOARD_PORT, World, mapping, note_off, note_on
from tests.test_poly import chord, voices
from tests.test_effect_files import FILES
from tests.test_synth import KEYBOARD_ONLY, STOCK, SynthCase, control, sent, wheel

KEYSYNTH, KEYSWEEP, KEYFM, KEYPOLY, KEYWAVE = 0x07000F61, 0x09000F71, 0x09000F72, 0x09000F73, 0x09000F7C
KEYBAND, PLUCK = 0x09000F94, 0x09000F8F
PRESET = "0/94"                                 # the preset of the simulated pedal
OCTSYNTH = 0x07000F62                           # a DIY effect next to them that no keyboard plays
SYNTH = {"keyboard": KEYBOARD_PORT, "channel": 1}
K = zs.key_for                                  # the Key value of a MIDI note
VEL = 13                                        # KeyBand's Vel: parameter 13, after Key1–Key4 (2–5) and seven more
# KeyBand as it was built until 0.20 (pedal_effects.json of the MS-60B+, 2026-10-22): one Key, Chord and Oct
BAND_0_1X = [["Key", 1000], ["Level", 100], ["Voice", 3], ["Chord", 4], ["Oct", 3], ["Atk", 100], ["Dec", 100],
             ["Rel", 100], ["Tone", 100], ["Move", 100], ["Vel", 100]]
PROTOTYPES = Path.home() / "Desktop" / "PROTOTYP" / "prototypes"      # where KeyBand is built, if here


def keyband_read(knobs: list, size: int) -> None:
    """KEYBAND.ZD2 of that build as Read Pedal keeps it (pedal_effects.json), read at the next start."""
    bridge.EFFECT_CACHE.write_text(json.dumps({"KEYBAND.ZD2": {"id": f"{KEYBAND:08X}", "name": "KeyBand",
                                                               "knobs": knobs, "size": size, "dsp": 75.0}}),
                                   encoding="utf-8")


class Knowing(unittest.TestCase):
    def test_an_effect_is_known_in_every_category(self):
        for effect_id, effect in ((KEYSYNTH, zs.KEYSYNTH), (0x09000F61, zs.KEYSYNTH), (KEYSWEEP, zs.KEYSWEEP),
                                  (0x07000F71, zs.KEYSWEEP), (0x08000F71, zs.KEYSWEEP), (KEYFM, zs.KEYFM),
                                  (0x08000F72, zs.KEYFM), (KEYPOLY, zs.KEYPOLY), (0x08000F73, zs.KEYPOLY),
                                  (KEYWAVE, zs.KEYWAVE), (0x07000F7C, zs.KEYWAVE), (KEYBAND, zs.KEYBAND),
                                  (0x07000F94, zs.KEYBAND), (PLUCK, zs.PLUCK), (0x07000F8F, zs.PLUCK)):
            with self.subTest(f"{effect_id:08x}"):
                self.assertIs(zs.key_effect(effect_id), effect)
        for effect_id in (OCTSYNTH, STOCK, 0x09000F00, 0x09000FF0, 0x07000020, 0x09000F85, 0):
            with self.subTest(f"{effect_id:08x}"):
                self.assertIsNone(zs.key_effect(effect_id))
        self.assertEqual(len({effect.number for effect in zs.KEY_EFFECTS}), len(zs.KEY_EFFECTS))

    def test_the_knobs_are_those_of_the_manifests(self):
        self.assertEqual([knob for knob, _ in zs.KEYSWEEP.knobs],
                         ["Key", "Wave", "Cutoff", "Reso", "FEnv", "FDcy", "Atk", "Rel", "Glide", "LFO", "Rate", "Level"])
        self.assertEqual([knob for knob, _ in zs.KEYFM.knobs],
                         ["Key", "Ratio", "Index", "MEnv", "MDcy", "Fdbk", "Atk", "Rel", "Glide", "LFO", "Rate", "Level"])
        self.assertEqual(zs.KEYWAVE.knobs, (("Key", 1000), ("Wave", 100), ("WEnv", 100), ("WDcy", 100), ("Sub", 100),
                                            ("Atk", 100), ("Rel", 100), ("Glide", 100), ("LFO", 100), ("Rate", 100),
                                            ("LDest", 2), ("Level", 100)))
        self.assertEqual((zs.synth_knob("Wave", zs.KEYSWEEP), zs.synth_knob("Ratio", zs.KEYFM),
                          zs.synth_knob("Wave", zs.KEYPOLY), zs.synth_knob("Blend", zs.KEYSYNTH)),
                         ((3, 9), (3, 15), (6, 9), (7, 100)))
        self.assertEqual(zs.KEYBAND.knobs, (("Key1", 1000), ("Key2", 1000), ("Key3", 1000), ("Key4", 1000), ("Voice", 3),
                                            ("Level", 100), ("Atk", 100), ("Dec", 100), ("Rel", 100), ("Tone", 100),
                                            ("Move", 100), ("Vel", 100)))
        self.assertEqual(zs.PLUCK.knobs, (("Key", 1000), ("Level", 100), ("Trig", 1), ("Decay", 100), ("Tone", 100),
                                          ("Pick", 2), ("Oct", 2), ("Dyn", 100), ("Thr", 100), ("Dry", 100)))
        for effect in zs.KEY_EFFECTS:                       # the Key knobs first, in KeySynth's format; Level last
            with self.subTest(effect.name):
                self.assertEqual(effect.knobs[:effect.voices], tuple(
                    ("Key" if effect.voices == 1 else f"Key{voice + 1}", zs.KEY_MAX) for voice in range(effect.voices)))
                self.assertEqual((effect.key_index, effect.key_param), (0, 2))
                self.assertNotIn("Key", effect.controls)
                self.assertNotIn("Mix", dict(effect.knobs))  # the bass passes unchanged (since 2026-10-22)
                self.assertEqual(dict(effect.knobs)["Level"], 100)
                if effect not in (zs.KEYBAND, zs.PLUCK):
                    self.assertEqual(effect.knobs[-1], ("Level", 100))
                    self.assertEqual(len(effect.knobs), 12)

    def test_as_many_voices_as_key_knobs(self):
        self.assertEqual([(effect.name, effect.voices) for effect in zs.KEY_EFFECTS],
                         [("KeySynth", 1), ("KeySweep", 1), ("KeyFM", 1), ("KeyPoly", 4), ("KeyWave", 1),
                          ("KeyBand", 4), ("Pluck", 1)])
        self.assertEqual(zs.key_knobs(zs.BAND_KNOBS), range(0, 4))
        self.assertEqual(zs.key_knobs(BAND_0_1X), range(0, 1))
        self.assertEqual(zs.key_knobs([["Level", 100], ["Key", 1000], ["Key2", 1000]]), range(1, 2))
        # Key1 to Key4 next to each other, at most four: a Key3 elsewhere, or a Key5, is no voice
        self.assertEqual(zs.key_knobs([["Key1", 1000], ["Key2", 1000], ["Wave", 9], ["Key3", 1000]]), range(0, 2))
        self.assertEqual(zs.key_knobs([[f"Key{voice}", 1000] for voice in range(1, 6)]), range(0, 4))
        self.assertEqual(zs.key_knobs([["Key2", 1000], ["Tone", 100]]), range(0))
        two = zs.SynthEffect("Two", 0xF99, (("Key1", zs.KEY_MAX), ("Key2", zs.KEY_MAX), ("Tone", 100)), lfo=None)
        self.assertEqual((two.voices, two.key_param, list(two.controls)), (2, 2, ["Tone"]))
        with self.assertRaises(ValueError):
            zs.SynthEffect("None", 0xF99, (("Key2", zs.KEY_MAX), ("Tone", 100)))
        # a Key knob that is no voice would be a control that plays notes: such a build is not played
        for knobs in ((("Key1", zs.KEY_MAX), ("Key2", zs.KEY_MAX), ("Wave", 9), ("Key3", zs.KEY_MAX)),
                      tuple((f"Key{voice}", zs.KEY_MAX) for voice in range(1, 6)),
                      (("Key", zs.KEY_MAX), ("Key2", zs.KEY_MAX), ("Tone", 100))):
            with self.assertRaises(ValueError):
                zs.SynthEffect("Odd", 0xF99, knobs)

    def test_keyband_is_the_build_of_its_manifest_and_its_file(self):
        """0.20, of 2026-10-22: its manifest.json and KEYBAND.ZD2 as installed (where they are here)."""
        manifest, file = PROTOTYPES / "keyband" / "manifest.json", PROTOTYPES / "install_all" / "KEYBAND.ZD2"
        if not manifest.exists() and not file.exists():
            self.skipTest("KeyBand's manifest.json and KEYBAND.ZD2 are not here")
        if manifest.exists():
            params = json.loads(manifest.read_text(encoding="utf-8"))["params"]
            self.assertEqual(tuple((param["name"], param["max"] if "max" in param else len(param["values"]) - 1)
                                   for param in params), zs.BAND_KNOBS)
        if file.exists():
            read = effect_names.read_zd2(file.read_bytes())
            self.assertEqual((read["id"] & zs.EFFECT_NUMBER, read["name"]), (zs.KEYBAND.number, "KeyBand"))
            self.assertEqual(tuple(map(tuple, read["knobs"])), zs.BAND_KNOBS)

    def test_the_upper_half_of_the_lfo_knob_is_tremolo_or_filter(self):
        self.assertEqual([effect.name for effect in zs.KEY_EFFECTS if "Trm" in effect.controls], ["KeySynth", "KeyFM"])
        self.assertEqual([effect.name for effect in zs.KEY_EFFECTS if "Flt" in effect.controls], ["KeySweep", "KeyPoly"])
        # KeyWave's LFO knob is a plain depth: no halves, and at rest (0) it is off
        self.assertEqual([name for name in ("Vib", "Trm", "Flt") if name in zs.KEYWAVE.controls], [])
        self.assertEqual([zs.control_value("LFO", value, zs.KEYWAVE) for value in (0, 64, 127)], [0, 50, 100])
        self.assertEqual([zs.control_value("LDest", value, zs.KEYWAVE) for value in (0, 50, 127)], [0, 1, 2])
        for effect in (zs.KEYSWEEP, zs.KEYPOLY):
            self.assertEqual([zs.control_value("Flt", value, effect) for value in (0, 64, 127)], [50, 75, 100])
            self.assertEqual([zs.control_value("Vib", value, effect) for value in (0, 64, 127)], [50, 25, 0])

    def test_keyband_and_pluck_have_neither_lfo_halves_nor_mix(self):
        self.assertEqual(list(zs.KEYBAND.controls), ["Voice", "Level", "Atk", "Dec", "Rel", "Tone", "Move", "Vel"])
        self.assertEqual(list(zs.PLUCK.controls), ["Level", "Trig", "Decay", "Tone", "Pick", "Oct", "Dyn", "Thr", "Dry"])
        self.assertEqual([zs.control_value("Voice", value, zs.KEYBAND) for value in (0, 64, 127)], [0, 2, 3])
        self.assertEqual([zs.control_value("Trig", value, zs.PLUCK) for value in (0, 63, 64, 127)], [0, 0, 1, 1])
        self.assertEqual(zs.synth_knob("Vel", zs.KEYBAND), (VEL, 100))
        self.assertEqual(zs.synth_knob("Trig", zs.PLUCK), (4, 1))

    def test_what_keyband_and_pluck_read_when_a_note_is_struck(self):
        self.assertEqual([(effect.name, effect.velocity, effect.needs) for effect in zs.KEY_EFFECTS
                          if effect.velocity or effect.needs],
                         [("KeyBand", "Vel", {}), ("Pluck", None, {"Trig": (0, "Key")})])
        # a build without those knobs, or where the value is beyond the knob: nothing to send for them
        other = zs.SynthEffect("KeyBand", 0xF94, (("Key", zs.KEY_MAX), ("Level", 100)), lfo=None, velocity="Vel",
                               needs={"Trig": (0, "Key"), "Level": (101, "?")})
        self.assertEqual((other.velocity, other.needs), (None, {}))

    def test_assignments_to_knobs_0_1x_had_are_left_out_not_refused(self):
        stored = {"KeyBand/Chord": 21, "KeyBand/Oct": 22, "Pluck/Oct": 23, "KeyBand/Voice": 24}
        self.assertEqual(synth_controls.problems(stored), [])
        self.assertEqual(synth_controls.read(stored), {"Pluck/Oct": 23, "KeyBand/Voice": 24})


class Playing(SynthCase):
    def test_the_keyboard_plays_each_of_them_without_an_id_in_the_settings(self):
        for effect_id, name in ((KEYSYNTH, "KeySynth"), (0x09000F61, "KeySynth"), (KEYSWEEP, "KeySweep"),
                                (KEYWAVE, "KeyWave"), (PLUCK, "Pluck"), (0x07000F8F, "Pluck"),
                                (KEYFM, "KeyFM"), (0x08000F72, "KeyFM")):
            with self.subTest(f"{effect_id:08x}"):
                script = [(1.0, note_on(45)), (1.1, note_on(52)), (1.2, note_off(52)), (1.3, note_off(45))]
                world = World(script, ports=KEYBOARD_ONLY, chain=[STOCK, effect_id])
                self.install(synth=SYNTH)
                self.run_bridge(world)
                self.assertEqual(sent(world), [(1, 2, K(45)), (1, 2, K(52)), (1, 2, K(45)), (1, 2, 0)])
                self.assertIn(f"{name} is effect 2 – the keyboard plays it\n", self.output)
                for _, text in world.sent:                  # everything sent is on that effect's allowlist
                    zs.classify(bytes.fromhex(text.replace(" ", "")), ID, zs.synth_targets(1, zs.key_effect(effect_id)))

    def test_keypoly_takes_a_chord(self):
        world = World(chord(1.0, 48, 52, 55) + chord(1.2, 48, 52, 55, on=False), ports=KEYBOARD_ONLY, chain=[KEYPOLY])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(voices(world), [(1, K(48)), (2, K(52)), (3, K(55)), (1, 0), (2, 0), (3, 0)])
        self.assertIn("KeyPoly is effect 1 – the keyboard plays it with 4 voices", self.output)

    def test_another_diy_effect_is_left_alone(self):
        state = {}
        world = World([(1.0, note_on(45)), (1.1, note_off(45)),
                       (1.2, lambda world: state.update(self.instance.state()["synth"]))],
                      ports=KEYBOARD_ONLY, chain=[OCTSYNTH, STOCK])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [])
        self.assertIn("No KeySynth, KeySweep, KeyFM, KeyPoly, KeyWave, KeyBand or Pluck in this preset – "
                      "the keyboard is ignored", self.output)
        self.assertEqual((state["slot"], state["effect"]), (None, None))
        self.assertEqual(state["effects"], ["KeySynth", "KeySweep", "KeyFM", "KeyPoly", "KeyWave", "KeyBand", "Pluck"])

    def test_a_preset_change_goes_from_one_effect_to_the_next(self):
        def to(effect_id, preset):
            def change(world):
                world.chain = [effect_id]
                world.preset_change(preset)
            return change
        script = [(1.0, note_on(45)), (1.1, note_off(45)), (1.2, to(KEYFM, 5)),
                  (1.6, note_on(47)), (1.7, note_off(47)), (1.8, to(KEYSWEEP, 6)),
                  (2.2, note_on(48)), (2.3, note_off(48))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[STOCK, KEYSYNTH], length=2.6)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(1, 2, K(45)), (1, 2, 0), (0, 2, K(47)), (0, 2, 0), (0, 2, K(48)), (0, 2, 0)])
        for line in ("KeySynth is effect 2", "KeyFM is effect 1", "KeySweep is effect 1"):
            self.assertIn(f"{line} – the keyboard plays it\n", self.output)


class Knobs(SynthCase):
    CONTROLS = {"Wave": 14, "Ratio": 15, "Flt": 16, "Trm": 17, "Cutoff": 18, "Wave1": 19, "Level": 7}

    def played(self, effect_id: int) -> tuple:
        """Every controller of CONTROLS turned up fully: what was sent, and the knobs the interface
        lists for that effect. CONTROLS is in the old form: a knob on every effect that has it."""
        state = {}
        bridge.CONTROLS.write_text(json.dumps({"controls": self.CONTROLS}), encoding="utf-8")
        script = [(1.0 + 0.05 * index, control(number, 127)) for index, number in enumerate(self.CONTROLS.values())]
        world = World(script + [(1.5, lambda world: state.update(self.instance.state()["synth"]))],
                      ports=KEYBOARD_ONLY, chain=[effect_id])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        return sent(world), [knob["label"] for knob in state["knobs"] if knob["effect"] == zs.key_effect(effect_id).name]

    def test_a_controller_sets_the_knob_of_that_name_on_the_effect_in_the_preset(self):
        # (parameter, value) for Wave, Ratio, Flt, Trm, Cutoff, Wave1 and Level; None: the effect has no such knob
        expected = {
            KEYSYNTH: [None, None, None, (11, 100), None, (3, 9), (13, 100)],
            KEYSWEEP: [(3, 9), None, (11, 100), None, (4, 100), None, (13, 100)],
            KEYFM: [None, (3, 15), None, (11, 100), None, None, (13, 100)],
            KEYPOLY: [(6, 9), None, (11, 100), None, (7, 100), None, (13, 100)],
            KEYWAVE: [(3, 100), None, None, None, None, None, (13, 100)],
        }
        for effect_id, writes in expected.items():
            effect = zs.key_effect(effect_id)
            with self.subTest(effect.name):
                params, rows = self.played(effect_id)
                self.assertEqual(params, [(0, *write) for write in writes if write])
                self.assertEqual(rows, list(effect.controls))

    def test_a_controller_can_be_learned_for_a_knob_only_keyfm_has(self):
        script = [(1.0, lambda world: self.instance.synth_control("learn", "KeyFM/Index")), (1.1, control(21, 5)),
                  (1.2, control(21, 127))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYFM])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(self.instance.controls, {"KeyFM/Index": 21})
        self.assertIn("KeyFM: controller 21 now sets Index", self.output)
        self.assertEqual(sent(world), [(0, 4, 100)])


class Renamed(SynthCase):
    def test_an_assignment_kept_under_keysynf_works_on_keysweep_and_is_stored_under_its_new_name(self):
        bridge.CONTROLS.write_text(json.dumps({"controls": {"KeySynF/Cutoff": 74}}), encoding="utf-8")
        script = [(1.0, control(74, 127)),
                  (1.1, lambda world: self.instance.synth_control("learn", "KeyWave/LDest")), (1.2, control(22, 5)),
                  (1.3, control(22, 127))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSWEEP])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 4, 100)])                    # Cutoff on KeySweep; LDest is KeyWave's
        self.assertEqual(json.loads(bridge.CONTROLS.read_text(encoding="utf-8"))["controls"],
                         {"KeySweep/Cutoff": 74, "KeyWave/LDest": 22})
        self.assertIn("KeyWave: controller 22 now sets LDest", self.output)


class EachEffectItsOwn(SynthCase):
    def test_one_control_sets_another_knob_on_each_effect(self):
        def to(effect_id, preset):
            def change(world):
                world.chain = [effect_id]
                world.preset_change(preset)
            return change
        bridge.CONTROLS.write_text(json.dumps({"controls": {"KeySweep/Reso": 21, "KeyFM/Ratio": 21}}), encoding="utf-8")
        script = [(1.0, control(21, 127)), (1.2, to(KEYFM, 5)), (1.6, control(21, 64)), (1.8, to(KEYSYNTH, 6)),
                  (2.2, control(21, 0))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSWEEP], length=2.5)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        # Reso on KeySweep, Ratio (0–15) on KeyFM, and on KeySynth nothing
        self.assertEqual(sent(world), [(0, 5, 100), (0, 3, 8)])

    def test_learning_frees_the_control_on_that_effect_only(self):
        def learn(name):
            return lambda world: self.instance.synth_control("learn", name)
        bridge.CONTROLS.write_text(json.dumps({"controls": {"KeySynth/Rate": 1, "KeySynth/Level": 21}}), encoding="utf-8")
        script = [(1.0, learn("KeyFM/Index")), (1.1, control(1, 30)),          # the mod wheel on KeyFM too
                  (1.2, learn("KeySynth/Glide")), (1.3, control(21, 30)),      # Level gives way on KeySynth
                  (1.4, lambda world: seen.update(self.instance.controls)),
                  (1.5, learn("Arp")), (1.6, control(1, 30))]                   # a switch takes it everywhere
        seen = {}
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(seen, {"KeySynth/Rate": 1, "KeyFM/Index": 1, "KeySynth/Glide": 21})
        self.assertEqual(self.instance.controls, {"KeySynth/Glide": 21, "Arp": 1})


class Velocity(SynthCase):
    """KeyBand reads Vel when a voice strikes a note and keeps it for that voice: the bridge sends
    the key's velocity there first (127 = 100), right before the Key of the voice that strikes and
    through the same path. Since 0.20 it has four voices, Key1–Key4 (parameters 2–5), given to the
    notes as on KeyPoly; Vel is parameter 13."""

    def play(self, script, controls=None, vel=None, mappings=None, deaf=None, **settings) -> World:
        bridge.CONTROLS.write_text(json.dumps({"controls": controls or {}, "arp": settings}), encoding="utf-8")
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYBAND])
        if vel is not None:                         # what the preset holds
            world.whole_patch, world.values[(0, VEL)] = True, vel
        if deaf is not None:                        # (from, to): the pedal takes nothing then
            world.accept = lambda slot, param, value: not deaf[0] <= world.now() < deaf[1]
        self.install(mappings, synth=SYNTH)
        self.run_bridge(world)
        return world

    def assert_right_before_its_key(self, world: World) -> None:
        """Every Vel is followed at once by a Key."""
        for index, (seconds, _, param, _) in enumerate(world.params):
            if param == VEL:
                self.assertIn(world.params[index + 1][2], range(2, 6))
                self.assertLess(world.params[index + 1][0] - seconds, 0.005)

    def test_the_velocity_goes_right_before_the_key_of_a_new_note(self):
        notes = [(1.0, note_on(45, 127)), (1.1, note_off(45)),
                 (1.2, note_on(47, 64)), (1.25, note_on(48, 64)), (1.3, note_off(48)), (1.35, note_off(47)),
                 (1.5, note_on(50, 1)), (1.6, note_off(50))]
        state = {}
        world = self.play(notes + [(1.7, lambda world: state.update(values=dict(self.instance.session.knob_values[0]),
                                                                    totals=dict(self.instance.session.key_totals)))])
        # 64 is 50; the same velocity again is not sent again. Each note has a voice of its own: 47
        # and 48 sound together, and 50 takes the voice that has been free longest.
        self.assertEqual(sent(world), [(0, VEL, 100), (0, 2, K(45)), (0, 2, 0),
                                       (0, VEL, 50), (0, 3, K(47)), (0, 4, K(48)), (0, 4, 0), (0, 3, 0),
                                       (0, VEL, 1), (0, 5, K(50)), (0, 5, 0)])
        keys = [seconds for seconds, _, param, _ in world.params if param in range(2, 6)]
        for (due, _), seconds in zip(notes, keys):  # the Key is not held up by it
            self.assertLess(seconds - due, 0.02)
        self.assert_right_before_its_key(world)
        for _, text in world.sent:                  # everything sent is on the effect's allowlist
            zs.classify(bytes.fromhex(text.replace(" ", "")), ID, zs.synth_targets(0, zs.KEYBAND))
        # the pedal confirmed each (64 20 01): the bridge knows what Vel holds, the notes are counted as before
        self.assertEqual((world.values[(0, VEL)], state["values"][VEL]), (1, 1))
        self.assertEqual(state["totals"], {"sent": 8, "acked": 8, "missed": 0, "overtaken": 0})
        self.assertIn("KeyBand is effect 1 – the keyboard plays it with 4 voices", self.output)
        self.assertNotIn("WARNING", self.output)

    def test_a_chord_strikes_each_voice_with_the_velocity_of_its_own_key(self):
        script = [(1.0, note_on(48, 127)), (1.0, note_on(52, 64)), (1.0, note_on(55, 1)), (1.0, note_on(59, 100))]
        world = self.play(script + chord(1.2, 48, 52, 55, 59, on=False))
        self.assertEqual(sent(world), [(0, VEL, 100), (0, 2, K(48)), (0, VEL, 50), (0, 3, K(52)),
                                       (0, VEL, 1), (0, 4, K(55)), (0, VEL, 79), (0, 5, K(59)),
                                       (0, 2, 0), (0, 3, 0), (0, 4, 0), (0, 5, 0)])
        self.assert_right_before_its_key(world)
        self.assertLess(world.params[7][0] - 1.0, 0.02)             # the whole chord at once
        # struck as hard (100 is 79): Vel once, before the first
        world = self.play(chord(1.0, 48, 52, 55, 59) + chord(1.2, 48, 52, 55, 59, on=False))
        self.assertEqual(sent(world)[:5], [(0, VEL, 79), (0, 2, K(48)), (0, 3, K(52)), (0, 4, K(55)), (0, 5, K(59))])

    def test_a_fifth_key_takes_a_voice_as_on_keypoly(self):
        # it takes the voice of the oldest note, which gets it back with its own velocity when the
        # fifth is released
        script = [(1.0, note_on(48, 127)), (1.05, note_on(52, 127)), (1.1, note_on(55, 127)), (1.15, note_on(59, 127)),
                  (1.2, note_on(62, 64)), (1.3, note_off(62))]
        world = self.play(script + chord(1.4, 48, 52, 55, 59, on=False))
        self.assertEqual(sent(world), [(0, VEL, 100), (0, 2, K(48)), (0, 3, K(52)), (0, 4, K(55)), (0, 5, K(59)),
                                       (0, VEL, 50), (0, 2, K(62)), (0, VEL, 100), (0, 2, K(48)),
                                       (0, 2, 0), (0, 3, 0), (0, 4, 0), (0, 5, 0)])
        self.assert_right_before_its_key(world)

    def test_notes_the_sustain_pedal_holds_keep_their_voice_and_velocity(self):
        # four keys struck and released with the pedal down; a fifth takes the voice of the oldest
        script = [(0.95, control(64, 127)), (1.0, note_on(48, 127)), (1.05, note_off(48)), (1.1, note_on(52, 64)),
                  (1.15, note_off(52)), (1.2, note_on(55, 1)), (1.25, note_off(55)), (1.3, note_on(59, 100)),
                  (1.35, note_off(59)), (1.4, note_on(62, 127)), (1.5, control(64, 0)), (1.6, note_off(62))]
        world = self.play(script)
        self.assertEqual(sent(world), [(0, VEL, 100), (0, 2, K(48)), (0, VEL, 50), (0, 3, K(52)),
                                       (0, VEL, 1), (0, 4, K(55)), (0, VEL, 79), (0, 5, K(59)),
                                       (0, VEL, 100), (0, 2, K(62)), (0, 3, 0), (0, 4, 0), (0, 5, 0), (0, 2, 0)])

    def test_not_for_a_bend(self):
        world = self.play([(1.0, note_on(45, 127)), (1.1, wheel(8191)), (1.4, note_off(45))])
        self.assertEqual(sent(world), [(0, VEL, 100), (0, 2, K(45)), (0, 2, K(45) + 9), (0, 2, K(45) + 18),
                                       (0, 2, K(45) + 20), (0, 2, 0)])

    def test_not_for_a_new_key_the_effect_takes_for_a_bend(self):
        # Four keys, 45 a click low with the others; the wheel back and a fifth key, 44, struck: it
        # takes the voice of 45, 9 clicks from where its Key is, which the effect takes for a bend.
        # Back to 45 is a new note again, at the Vel the pedal holds.
        down = -410 / 8192 * 2                      # the wheel a tenth of a semitone down (bend range 2)
        script = [(1.0, note_on(45, 127)), (1.01, note_on(50, 127)), (1.02, note_on(55, 127)),
                  (1.03, note_on(60, 127)), (1.1, wheel(-410)), (1.2, wheel(0)), (1.2, note_on(44, 64)),
                  (1.3, note_off(44))]
        world = self.play(script + chord(1.4, 45, 50, 55, 60, on=False))
        self.assertEqual(K(45, down), K(44) + 9)
        self.assertEqual([write for write in sent(world) if write[1] in (VEL, 2)],
                         [(0, VEL, 100), (0, 2, K(45)), (0, 2, K(45, down)), (0, 2, K(44)), (0, 2, K(45)), (0, 2, 0)])
        self.assertIn((0, 5, K(60, down)), sent(world))                 # the wheel moved every voice

    def test_two_keys_struck_together_send_it_once(self):
        # the second before the pedal has confirmed the first: the bridge takes what it sent for what Vel holds
        world = self.play([(1.0, note_on(45, 127)), (1.0, note_on(47, 127)), (1.1, note_off(47)), (1.2, note_off(45))])
        self.assertEqual(sent(world), [(0, VEL, 100), (0, 2, K(45)), (0, 3, K(47)), (0, 3, 0), (0, 2, 0)])

    def test_a_late_ack_of_an_earlier_velocity_is_not_taken_for_what_vel_holds(self):
        # The pedal answers 25 ms late for a moment: Vel 100 is confirmed after Vel 50 went out,
        # and before Vel 50 is confirmed a key is struck at 127 – it needs Vel 100 again.
        script = [(1.0, note_on(45, 127)), (1.02, note_on(47, 64)), (1.035, note_on(48, 127)),
                  (1.2, note_off(48)), (1.25, note_off(47)), (1.3, note_off(45))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYBAND])
        receive = world.receive

        def late(message):
            before = len(world.zoom_queue)
            receive(message)
            if 1.0 <= world.now() < 1.1:
                for answer in world.zoom_queue[before:]:
                    world.script.append((world.now() + 0.025,
                                         lambda world, answer=answer: world.zoom_queue.append(answer)))
                del world.zoom_queue[before:]
                world.script.sort(key=lambda item: item[0])
        world.receive = late
        bridge.CONTROLS.write_text(json.dumps({"controls": {}}), encoding="utf-8")
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, VEL, 100), (0, 2, K(45)), (0, VEL, 50), (0, 3, K(47)),
                                       (0, VEL, 100), (0, 4, K(48)), (0, 4, 0), (0, 3, 0), (0, 2, 0)])
        self.assertEqual(world.values[(0, VEL)], 100)

    def test_a_note_the_pedal_did_not_take_goes_again_with_its_own_velocity(self):
        # Two keys struck while the pedal takes nothing (its effect menu open): each Key goes
        # again, with the Vel of its own key right before it – not the one sent last.
        world = self.play([(1.0, note_on(48, 127)), (1.0, note_on(52, 64))] + chord(1.3, 48, 52, on=False),
                          deaf=(1.0, 1.03))
        self.assertEqual(sent(world), [(0, VEL, 100), (0, 2, K(48)), (0, VEL, 50), (0, 3, K(52)),
                                       (0, VEL, 100), (0, 2, K(48)), (0, VEL, 50), (0, 3, K(52)),
                                       (0, 2, 0), (0, 3, 0)])
        self.assertEqual(world.values[(0, VEL)], 50)

    def test_a_bend_step_the_pedal_did_not_take_goes_again_without_it(self):
        # Two keys, the wheel while the pedal takes nothing for a moment: each step goes again
        # alone, though Vel holds the velocity of the other key – the effect reads it on a strike only.
        up = 1000 / 8192 * 2                        # the wheel a quarter of a semitone up (bend range 2)
        world = self.play([(1.0, note_on(48, 127)), (1.0, note_on(52, 64)), (1.2, wheel(1000))]
                          + chord(1.6, 48, 52, on=False), deaf=(1.19, 1.22))
        self.assertEqual(sent(world), [(0, VEL, 100), (0, 2, K(48)), (0, VEL, 50), (0, 3, K(52)),
                                       (0, 2, K(48, up)), (0, 3, K(52, up)), (0, 2, K(48, up)), (0, 3, K(52, up)),
                                       (0, 2, 0), (0, 3, 0)])
        self.assertEqual(world.values[(0, VEL)], 50)

    def test_not_when_the_pedal_holds_it_already(self):
        world = self.play([(1.0, note_on(45, 100)), (1.1, note_off(45)), (1.2, note_on(47, 127)), (1.3, note_off(47))],
                          vel=79)
        self.assertEqual(sent(world), [(0, 2, K(45)), (0, 2, 0), (0, VEL, 100), (0, 3, K(47)), (0, 3, 0)])

    def test_a_controller_assigned_to_vel_wins(self):
        world = self.play([(1.0, note_on(45, 127)), (1.1, note_off(45)), (1.2, control(20, 127)),
                           (1.3, note_on(47, 1)), (1.4, note_off(47))], controls={"KeyBand/Vel": 20})
        self.assertEqual(sent(world), [(0, 2, K(45)), (0, 2, 0), (0, VEL, 100), (0, 3, K(47)), (0, 3, 0)])

    def test_a_controller_assigned_to_vel_in_the_preset_wins(self):
        # assigned under Preset → Effects, as the keyboard's controllers are since 2026-10-19
        world = self.play([(1.0, note_on(45, 127)), (1.1, note_off(45)), (1.2, control(20, 127)),
                           (1.3, note_on(47, 1)), (1.4, note_off(47))],
                          mappings={PRESET: [mapping(0, VEL, 0, 100, cc=20, effect=f"{KEYBAND:08X}")]})
        self.assertEqual(sent(world), [(0, 2, K(45)), (0, 2, 0), (0, VEL, 100), (0, 3, K(47)), (0, 3, 0)])

    def test_another_build_gets_notes_only_also_right_after_read_pedal(self):
        # Read Pedal has every effect checked again with the next patch; until then the allowlist
        # has the Key knobs only, and the note must not wait for that
        def read(world):
            self.instance.session.unmatched.clear()
            self.instance.session.recheck = True
        script = [(1.0, note_on(45, 127)), (1.1, note_off(45)), (1.2, read), (1.3, note_on(47, 127)),
                  (1.4, note_off(47))]
        with mock.patch.object(bridge, "matches_table", lambda found: zs.key_effect(found.id) is not zs.KEYBAND):
            world = self.play(script)
        self.assertEqual(sent(world), [(0, 2, K(45)), (0, 2, 0), (0, 3, K(47)), (0, 3, 0)])
        self.assertIn("is another build than zoom_effects.json describes", self.output)
        self.assertNotIn("not sent", self.output)

    def test_the_arpeggiator_takes_the_velocity_of_the_key_each_note_comes_from(self):
        script = [(0.9, lambda world: self.instance.switch_arp(True)), (1.0, note_on(48, 127)), (1.0, note_on(55, 64)),
                  (1.37, note_off(48)), (1.37, note_off(55))]
        world = self.play(script, rate="1/16", tempo=150, octaves=2)        # a step is 100 ms, a note 50
        # 60 and 67 are 48 and 55 an octave up: struck as hard as those keys; each note on the
        # voice that has been free longest
        self.assertEqual(sent(world), [(0, VEL, 100), (0, 2, K(48)), (0, 2, 0), (0, VEL, 50), (0, 3, K(55)), (0, 3, 0),
                                       (0, VEL, 100), (0, 4, K(60)), (0, 4, 0), (0, VEL, 50), (0, 5, K(67)), (0, 5, 0)])

    def test_the_sequencer_takes_the_velocity_of_the_key_it_plays_from(self):
        bridge.CONTROLS.write_text(json.dumps({"controls": {}, "arp": {"rate": "1/16", "tempo": 150},
                                               "seq": {"steps": [48, 52]}}), encoding="utf-8")
        script = [(0.6, note_on(54, 127)), (0.7, note_off(54)),
                  (0.9, lambda world: self.instance.switch_seq(True)), (1.0, note_on(50, 64)), (1.25, note_off(50))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYBAND])
        self.install(synth=SYNTH)
        self.run_bridge(world)
        # its 54 is played from 50: as hard as 50 was struck, not as the key 54 was before (on the
        # voice that played 54 last)
        self.assertEqual(sent(world), [(0, VEL, 100), (0, 2, K(54)), (0, 2, 0),
                                       (0, VEL, 50), (0, 3, K(50)), (0, 3, 0), (0, 2, K(54)), (0, 2, 0),
                                       (0, 3, K(50)), (0, 3, 0)])

    def test_the_pedal_confirming_it_is_not_a_knob_turned_on_the_pedal(self):
        seen = {}
        script = [(1.0, lambda world: self.instance.learn("start")), (1.05, note_on(45, 127)), (1.1, note_off(45)),
                  (1.3, lambda world: seen.update(self.instance.session.learning))]
        world = self.play(script)
        self.assertEqual(sent(world), [(0, VEL, 100), (0, 2, K(45)), (0, 2, 0)])
        log = (self.folder / "logs" / "sysex.log").read_text(encoding="utf-8")
        self.assertIn("RX  F0 52 00 6E 64 20 01 00 0D 64 00 00 00 00 F7", log)     # the pedal confirmed Vel
        self.assertEqual(seen, {})                  # nothing learned from it


class Builds(SynthCase):
    """KeyBand as the build on the pedal has it (the table; here pedal_effects.json as Read Pedal
    keeps it): 0.20 with four voices, a 0.1x (one Key, Chord and Oct) with one voice. A build read
    after the start is played as soon as it is read (until 2026-10-27: from the next start)."""

    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(zs.use_installed_synths)    # the bridge's own lists again (MS_PLUS_BRIDGE_SYNTHS)

    def play(self, script, table: bool, values=None) -> World:
        """table: the bridge takes the builds the table describes at its start, as it does outside
        the tests. values: what the preset holds, (slot, parameter) → value."""
        bridge.CONTROLS.write_text(json.dumps({"controls": {}}), encoding="utf-8")
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYBAND])
        world.whole_patch = True
        world.values.update(values or {})
        self.install(synth=SYNTH)
        with mock.patch.dict(os.environ, {"MS_PLUS_BRIDGE_SYNTHS": "table" if table else "builtin"}):
            self.run_bridge(world)
        return world

    def read_pedal(self, knobs: list, size: int):
        """A script action: Read Pedal reads KEYBAND.ZD2 of that build, and the patch is read again."""
        def read(world):
            session = self.instance.session
            session._take_read([{"id": KEYBAND, "name": "KeyBand", "knobs": knobs, "file": "KEYBAND.ZD2",
                                 "size": size, "dsp": 75.0}])
            session._look_for_synth(time.monotonic(), force=True)
        return read

    def test_a_pedal_with_0_1x_is_played_with_one_voice(self):
        keyband_read(BAND_0_1X, 26747)
        world = self.play(chord(1.0, 45, 48) + chord(1.1, 48, 45, on=False), table=True)
        # Key and Vel, parameter 12 there; the last key wins, as on KeySynth
        self.assertEqual(sent(world), [(0, 12, 79), (0, 2, K(45)), (0, 2, K(48)), (0, 2, K(45)), (0, 2, 0)])
        self.assertIn("KeyBand is effect 1 – the keyboard plays it\n", self.output)
        self.assertEqual((zs.KEYBAND.voices, zs.synth_knob("Vel", zs.KEYBAND), "Chord" in zs.KEYBAND.controls),
                         (1, (12, 100), True))
        self.assertNotIn("WARNING", self.output)

    def test_a_pedal_with_0_20_is_played_with_four_voices(self):
        keyband_read([list(knob) for knob in zs.BAND_KNOBS], 27321)
        world = self.play(chord(1.0, 45, 48) + chord(1.1, 48, 45, on=False), table=True)
        self.assertEqual(sent(world), [(0, VEL, 79), (0, 2, K(45)), (0, 3, K(48)), (0, 3, 0), (0, 2, 0)])
        self.assertIn("KeyBand is effect 1 – the keyboard plays it with 4 voices", self.output)
        self.assertNotIn("WARNING", self.output)

    def test_a_preset_made_with_0_1x_sounds_its_level_and_voice_on_0_20_which_the_bridge_closes(self):
        # 0.20 takes the knobs of such a preset by place: 0.1x's Level 80 is Key2, a note; Voice 2
        # is Key3, Chord 0 is Key4. The bridge closes what nothing plays at the first patch.
        world = self.play([(1.0, note_on(45, 127)), (1.1, note_off(45))], table=True,
                          values={(0, 3): 80, (0, 4): 2, (0, 5): 0})
        self.assertEqual(sent(world), [(0, 3, 0), (0, 4, 0), (0, VEL, 100), (0, 2, K(45)), (0, 2, 0)])
        self.assertNotIn("WARNING", self.output)

    def test_played_as_0_20_a_pedal_with_0_1x_is_played_as_0_1x_once_it_is_read(self):
        # nothing read before the start: the bridge's own list (0.20); then Read Pedal finds 0.1x,
        # and the next notes go to its one Key
        script = (chord(1.0, 45, 48) + chord(1.1, 48, 45, on=False) + [(1.2, self.read_pedal(BAND_0_1X, 26747))]
                  + chord(1.5, 45, 48) + chord(1.6, 48, 45, on=False))
        world = self.play(script, table=True)
        self.assertEqual(sent(world)[:5], [(0, VEL, 79), (0, 2, K(45)), (0, 3, K(48)), (0, 3, 0), (0, 2, 0)])
        self.assertEqual([item for item in sent(world)[5:] if item[1] != 12],            # (Vel of 0.1x aside)
                         [(0, 2, K(45)), (0, 2, K(48)), (0, 2, K(45)), (0, 2, 0)])     # the last key wins
        self.assertEqual(zs.KEYBAND.voices, 1)
        self.assertFalse(bridge.plays_other_build(KEYBAND))
        self.assertIn("KeyBand: the keyboard plays it as the build on the pedal from now on", self.output)
        # (the simulated pedal still holds values of 0.20 that 0.1x's knobs do not go up to: said as such)
        self.assertNotIn("another build than the bridge plays", self.output)

    def test_played_as_0_1x_a_pedal_with_0_20_gets_four_voices_once_it_is_read(self):
        keyband_read(BAND_0_1X, 26747)
        script = (chord(1.0, 45, 48) + chord(1.1, 48, 45, on=False)
                  + [(1.2, self.read_pedal([list(knob) for knob in zs.BAND_KNOBS], 27321)),
                     (1.5, note_on(45, 127)), (1.5, note_on(48, 127))] + chord(1.6, 48, 45, on=False))
        world = self.play(script, table=True)
        self.assertEqual(sent(world)[:5], [(0, 12, 79), (0, 2, K(45)), (0, 2, K(48)), (0, 2, K(45)), (0, 2, 0)])
        # after Read Pedal: Vel where 0.20 has it, and two voices for the two keys
        self.assertEqual(sent(world)[5:], [(0, VEL, 100), (0, 2, K(45)), (0, 3, K(48)), (0, 3, 0), (0, 2, 0)])
        self.assertEqual(zs.KEYBAND.voices, 4)
        self.assertFalse(bridge.plays_other_build(KEYBAND))
        self.assertIn("KeyBand: the keyboard plays it as the build on the pedal from now on", self.output)
        self.assertNotIn("WARNING", self.output)


    def test_a_pluck_read_after_the_start_has_its_knobs_and_controllers_at_once(self):
        # 2026-10-27: Pluck 0.13 read by Read Pedal after the start was played as 0.11 until the
        # next start: the editor showed no knobs, the keyboard's list Bright and no Dry
        earlier = [list(knob) for knob in zs.PLUCK_KNOBS[:4]] + [["Bright", 100]] + [
            list(knob) for knob in zs.PLUCK_KNOBS[5:9]]
        bridge.EFFECT_CACHE.write_text(json.dumps({"PLUCK.ZD2": {"id": f"{PLUCK:08X}", "name": "Pluck",
                                                                 "knobs": earlier, "size": 26683, "dsp": 75.0}}),
                                       encoding="utf-8")
        bridge.CONTROLS.write_text(json.dumps({"controls": {"Pluck/Bright": 21}}), encoding="utf-8")
        seen = {}

        def read(world):
            session = self.instance.session
            seen["before"] = [knob[0] for knob in zs.PLUCK.knobs]
            session._take_read([{"id": PLUCK, "name": "Pluck", "knobs": [list(knob) for knob in zs.PLUCK_KNOBS],
                                 "file": "PLUCK.ZD2", "size": 27465, "dsp": 117.0}])
            session._look_for_synth(time.monotonic(), force=True)

        world = World([(1.0, read), (1.4, lambda world: seen.update(state=self.instance.state()))],
                      ports=KEYBOARD_ONLY, chain=[PLUCK])
        self.install(synth=SYNTH)
        with mock.patch.dict(os.environ, {"MS_PLUS_BRIDGE_SYNTHS": "table"}):
            self.run_bridge(world)
        self.assertIn("Bright", seen["before"])                                       # as the start had it
        self.assertEqual([knob["label"] for knob in seen["state"]["synth"]["knobs"] if knob["effect"] == "Pluck"],
                         ["Level", "Trig", "Decay", "Tone", "Pick", "Oct", "Dyn", "Thr", "Dry"])
        self.assertEqual(self.instance.controls, {"Pluck/Tone": 21})                  # the same knob
        editor = seen["state"]["chain_effects"][0]
        self.assertFalse(editor.get("unmatched"))
        self.assertEqual([knob["name"] for knob in editor["knobs"]][-2:], ["Thr", "Dry"])
        self.assertIn("Pluck: the keyboard plays it as the build on the pedal from now on", self.output)


    def test_a_knob_waiting_for_its_controller_that_the_build_read_lacks_waits_no_longer(self):
        # Learn on KeyBand/Chord (0.1x), then Read Pedal finds 0.20: the controller moved next
        # stopped the bridge (refresh looked for Chord on the 0.20 it played by then)
        keyband_read(BAND_0_1X, 26747)
        bridge.CONTROLS.write_text(json.dumps({"controls": {}}), encoding="utf-8")
        script = [(1.0, lambda world: self.instance.synth_control("learn", "KeyBand/Chord")),
                  (1.2, self.read_pedal([list(knob) for knob in zs.BAND_KNOBS], 27321)),
                  (1.6, control(20, 64)), (1.8, note_on(45)), (1.9, note_off(45))]
        world = self.play(script, table=True)
        self.assertIsNone(self.instance.control_learning)
        self.assertEqual(self.instance.controls, {})
        self.assertEqual(sent(world)[-2:], [(0, 2, K(45)), (0, 2, 0)])          # and it plays on

    def test_a_file_of_another_size_not_read_again_leaves_its_build_as_it_is(self):
        # Read Pedal forgets what it read of a file whose size changed; until the file is read the
        # bridge plays the build it had (not the table's or its own list, nor says it plays the new)
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "read_pedal")     # read_effect_file not
        keyband_read(BAND_0_1X, 26747)
        bridge.CONTROLS.write_text(json.dumps({"controls": {}}), encoding="utf-8")

        def client(connection):
            connection.at(1.0)
            connection.call("/api/effects", {"action": "read_pedal"})

        script = chord(1.8, 45, 48) + chord(1.9, 48, 45, on=False)
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYBAND], length=2.2)
        world.whole_patch = True
        world.files = FILES + ["KEYBAND.ZD2"]
        world.listed_sizes = {"KEYBAND.ZD2": 26800}
        self.install(synth=SYNTH)
        with mock.patch.dict(os.environ, {"MS_PLUS_BRIDGE_SYNTHS": "table"}):
            self.run_bridge(world, client)
        self.assertEqual(zs.KEYBAND.voices, 1)
        self.assertEqual([item for item in sent(world) if item[1] != 12],
                         [(0, 2, K(45)), (0, 2, K(48)), (0, 2, K(45)), (0, 2, 0)])     # one Key, as before
        self.assertNotIn("the keyboard plays it as the build on the pedal from now on", self.output)


class Trig(SynthCase):
    """Pluck plucks by the Key knob only with Trig on Key (0); its default is Bass. Before a note
    the bridge sets Trig to Key, once, and says so."""

    SAID = "Pluck: Trig set to Key, so the keyboard plays it – save the preset on the pedal to keep it"

    def play(self, script, trig: int, controls=None, mappings=None, deaf=None) -> World:
        self.assign(**(controls or {}))
        world = World(script, ports=KEYBOARD_ONLY, chain=[PLUCK])
        world.whole_patch, world.values[(0, 4)] = True, trig        # what the preset holds
        if deaf is not None:                        # (from, to): the pedal takes nothing then
            world.accept = lambda slot, param, value: not deaf[0] <= world.now() < deaf[1]
        self.install(mappings, synth=SYNTH)
        self.run_bridge(world)
        return world

    def test_trig_is_set_to_key_right_before_the_first_note(self):
        world = self.play([(1.0, note_on(45)), (1.1, note_off(45)), (1.2, note_on(47)), (1.3, note_off(47))], trig=1)
        self.assertEqual(sent(world), [(0, 4, 0), (0, 2, K(45)), (0, 2, 0), (0, 2, K(47)), (0, 2, 0)])
        (trig_at, *_), (key_at, *_) = world.params[:2]
        self.assertLess(key_at - 1.0, 0.02)
        self.assertLess(key_at - trig_at, 0.005)
        self.assertEqual(world.values[(0, 4)], 0)
        self.assertEqual(self.output.count(self.SAID), 1)
        self.assertLess(self.output.index("Pluck is effect 1"), self.output.index(self.SAID))
        self.assertNotIn("WARNING", self.output)
        for _, text in world.sent:
            zs.classify(bytes.fromhex(text.replace(" ", "")), ID, zs.synth_targets(0, zs.PLUCK))

    def test_nothing_is_sent_while_trig_is_on_key(self):
        world = self.play([(1.0, note_on(45)), (1.1, note_off(45))], trig=0)
        self.assertEqual(sent(world), [(0, 2, K(45)), (0, 2, 0)])
        self.assertNotIn("Trig set", self.output)

    def test_turned_back_to_bass_on_the_pedal_it_is_set_again_with_the_next_note(self):
        def turn(world):
            world.values[(0, 4)] = 1
            world.knob(0, 4, 1)
        world = self.play([(1.0, note_on(45)), (1.1, note_off(45)), (1.2, turn),
                           (1.3, note_on(47)), (1.35, note_on(48)), (1.4, note_off(48)), (1.45, note_off(47))], trig=1)
        self.assertEqual(sent(world), [(0, 4, 0), (0, 2, K(45)), (0, 2, 0),
                                       (0, 4, 0), (0, 2, K(47)), (0, 2, K(48)), (0, 2, K(47)), (0, 2, 0)])
        self.assertEqual(self.output.count(self.SAID), 2)

    def test_a_controller_assigned_to_trig_wins(self):
        world = self.play([(1.0, note_on(45)), (1.1, note_off(45)), (1.2, control(20, 0)),
                           (1.3, note_on(47)), (1.4, note_off(47))], trig=1, controls={"Pluck/Trig": 20})
        self.assertEqual(sent(world), [(0, 2, K(45)), (0, 2, 0), (0, 4, 0), (0, 2, K(47)), (0, 2, 0)])
        self.assertNotIn("Trig set", self.output)

    def test_the_expression_pedal_assigned_to_trig_wins(self):
        world = self.play([(1.0, note_on(45)), (1.1, note_off(45))], trig=1,
                          mappings={PRESET: [mapping(0, 4, 0, 1, effect=f"{PLUCK:08X}")]})
        self.assertEqual(sent(world), [(0, 2, K(45)), (0, 2, 0)])
        self.assertNotIn("Trig set", self.output)

    def test_an_assignment_that_sends_nothing_is_not_in_the_way(self):
        # learned with a wider range than Trig has: refused, so Trig is set for the keyboard as without it
        world = self.play([(1.0, note_on(45)), (1.1, note_off(45))], trig=1,
                          mappings={PRESET: [mapping(0, 4, 0, 100, effect=f"{PLUCK:08X}")]})
        self.assertIn("Pluck's Trig knob only goes up to 1", self.output)
        self.assertEqual(sent(world), [(0, 4, 0), (0, 2, K(45)), (0, 2, 0)])
        self.assertEqual(self.output.count(self.SAID), 1)
        self.assertNotIn("WARNING", self.output)

    def test_a_trig_the_pedal_did_not_take_goes_again_with_the_note(self):
        # The pedal takes nothing for a moment (seen while its effect menu is open): the note is
        # sent again, and Trig right before it as the first time; it is said once.
        world = self.play([(1.0, note_on(45)), (1.3, note_off(45)), (1.4, note_on(47)), (1.5, note_off(47))],
                          trig=1, deaf=(1.0, 1.03))
        self.assertEqual(sent(world), [(0, 4, 0), (0, 2, K(45)), (0, 4, 0), (0, 2, K(45)), (0, 2, 0),
                                       (0, 2, K(47)), (0, 2, 0)])
        self.assertEqual(world.values[(0, 4)], 0)
        self.assertEqual(self.output.count(self.SAID), 1)

    def test_a_bend_step_the_pedal_did_not_take_goes_again_without_it(self):
        # Trig turned back to Bass on the pedal while the note sounds, then a step of the wheel the
        # pedal does not take: it goes again alone, Trig stays as the player turned it.
        def turn(world):
            world.values[(0, 4)] = 1
            world.knob(0, 4, 1)
        up = 1000 / 8192 * 2                        # the wheel a quarter of a semitone up (bend range 2)
        world = self.play([(1.0, note_on(45)), (1.1, turn), (1.2, wheel(1000)), (1.6, note_off(45))],
                          trig=1, deaf=(1.19, 1.22))
        self.assertEqual(sent(world), [(0, 4, 0), (0, 2, K(45)), (0, 2, K(45, up)), (0, 2, K(45, up)), (0, 2, 0)])
        self.assertEqual(world.values[(0, 4)], 1)
        self.assertEqual(self.output.count(self.SAID), 1)


if __name__ == "__main__":
    unittest.main()
