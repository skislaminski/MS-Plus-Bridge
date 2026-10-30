"""The settings of a bridge without screen, changed from its interface (the review of 2026-10-20):
a change that breaks a rule of the settings file is undone, not kept in memory to block every
later one; nothing that is learned stops the bridge; the file is written once per change, so the
previous one stays the one before; the effects the pedal reports are written too. And what made
files that break a rule on the computer as well: a preset with scenes but no assignments, and an
assignment that moves into a place where another effect's learned range was left."""
import contextlib
import io
import json
import unittest
from unittest import mock

import yaml

import bridge
import config_schema as cs
from tests.sim import KEYBOARD_PORT, SimulatedCase, World, learned, mapping
from tests.test_headless import PRESET, STOCK, HeadlessCase

ROOM, CHORUS = 0x0B000020, 0x06000020                    # BrghtRoom, ChorusOne


class OnThePi(HeadlessCase):
    def make(self) -> "bridge.Bridge":
        settings = self.write_card()
        self.forget_the_installation()
        return bridge.Bridge(settings, self.card)

    def written(self) -> dict:
        return yaml.safe_load((self.card / cs.FILE_NAME).read_text(encoding="utf-8"))

    def test_a_change_that_cannot_be_kept_is_undone_and_the_next_one_is_kept(self):
        instance = self.make()                           # switching effects is not approved here
        instance.switches[0] = {"cc": 80, "press": False}
        with self.assertRaises(bridge.UserError) as refused:
            instance._store_controls()
        self.assertIn("Not kept: approvals: not approved: switch_effect", str(refused.exception))
        self.assertEqual(instance.switches, {})
        instance.save_mapping(PRESET, {"targets": [mapping(low=10)]})
        self.assertEqual(self.written()["mappings"][PRESET][0]["min"], 10)

    def test_a_switch_is_not_learned_before_switching_is_approved(self):
        instance = self.make()
        with self.assertRaises(bridge.UserError) as refused:
            instance.effects({"action": "learn", "slot": 0})
        self.assertIn("probe.py approve switch_effect query_patch", str(refused.exception))

    def test_written_once_per_change_so_the_previous_file_is_the_one_before(self):
        instance = self.make()
        before = (self.card / cs.FILE_NAME).read_text(encoding="utf-8")
        instance.save_mapping(PRESET, {"targets": [mapping(low=10)]})
        instance._store_controls()                       # the same once more, as learning a controller does
        self.assertEqual((self.card / cs.PREVIOUS_NAME).read_text(encoding="utf-8"), before)

    def test_the_effects_the_pedal_reports_are_written(self):
        instance = self.make()
        self.assertTrue(instance.found_effects([ROOM]))
        self.assertIn(f"{ROOM:08X}", self.written()["pedal_effects"])
        self.assertFalse(instance.found_effects([ROOM]))

    def test_the_keyboard_chosen_again_keeps_what_was_changed_since_the_start(self):
        instance = self.make()
        instance.synth_control("clear", "KeySynth/Rate")
        with contextlib.redirect_stdout(io.StringIO()):
            instance.devices({"keyboard": ""})                 # the expression controller is left
            instance.devices({"keyboard": KEYBOARD_PORT})
        self.assertEqual(instance.controls, {})                # not the controllers it started with
        self.assertEqual(self.written()["controls"], {})

    def test_the_settings_file_is_not_handed_out(self):
        instance = self.make()
        with self.assertRaises(bridge.UserError):
            instance.settings_file()


class Running(SimulatedCase):
    def test_a_refusal_while_learning_does_not_stop_the_bridge(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        self.install({PRESET: [mapping(0, 2)]})
        seen = {}

        def client(connection):
            connection.at(1.2)
            seen["state"] = connection.call("/api/state")[1]

        with mock.patch.object(bridge.Session, "_check_edits", side_effect=bridge.UserError("Not kept: a test")):
            self.run_bridge(World(chain=[STOCK, ROOM], length=1.5), client)
        self.assertIn("WARNING: Not kept: a test", self.output)
        self.assertTrue(seen["state"]["connected"])


class OnTheComputer(SimulatedCase):
    def test_a_preset_with_scenes_but_no_assignments_gets_no_empty_entry(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        self.install({})
        scene = {"name": "Solo", "values": [{"slot": 1, "param": 3, "value": 20}], "effects": {}}
        bridge.CONTROLS.write_text(json.dumps({"controls": {}, "scenes": {PRESET: [scene]}}), encoding="utf-8")
        self.run_bridge(World([(1.0, lambda world: world.preset_change(94))], program=95, chain=[STOCK, ROOM],
                              length=1.6))                              # selected on the pedal: as saved
        stored = json.loads(bridge.CONTROLS.read_text(encoding="utf-8"))["scenes"][PRESET][0]
        self.assertEqual(stored["chain"], {"2": f"{ROOM:08X}"})                  # the scene learned its effect
        self.assertEqual(yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))["mappings"], {})
        with self.devices(World()):
            bridge.Bridge()                                                     # the next start takes it

    def test_an_empty_preset_written_before_is_skipped(self):
        self.install({})
        bridge.CONFIG.write_text(bridge.CONFIG.read_text(encoding="utf-8").replace(
            "mappings: {}", 'mappings:\n  "0/94":   # preset 095\n'), encoding="utf-8")
        self.approve(*bridge.REQUIRED_KINDS)
        with self.devices(World()):
            self.assertEqual(bridge.Bridge().mappings, {})

    def test_a_range_another_effect_left_makes_way_for_an_assignment_moving_in(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        # BlackOpt's knob was learned, its assignment removed; ChorusOne's assigned without learning
        self.install({PRESET: [mapping(1, 2, 0, 100, effect=f"{CHORUS:08X}")]}, {PRESET: learned((0, 2, 0, 50))})
        self.run_bridge(World([(1.0, lambda world: world.rearrange([1]))], chain=[STOCK, CHORUS], length=1.5))
        self.assertEqual(json.loads(bridge.MEASUREMENTS.read_text(encoding="utf-8"))["patches"].get(PRESET, {}), {})
        stored = yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))["mappings"][PRESET]
        self.assertEqual([(entry["slot"], entry["param"]) for entry in stored], [(0, 2)])
        with self.devices(World()):
            bridge.Bridge()                                                     # the next start takes it


if __name__ == "__main__":
    unittest.main()
