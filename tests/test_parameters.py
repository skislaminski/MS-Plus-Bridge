"""Parameters of a preset set by any controller: the expression pedal, or a knob, slider, wheel or
pedal of the keyboard, the foot controller or the external sequencer, learned in the interface."""
import json
import unittest
from unittest import mock

import mido
import yaml

import bridge
import config_schema as cs
import zoom_sysex as zs
from tests.sim import (CONTROLLER_PORT, KEYBOARD_PORT, ZOOM_PORT, SimulatedCase, World, cc, keyboard, mapping)

PRESET, OTHER = "0/94", "0/5"
KEYSYNTH, STOCK = 0x07000F61, 0x01000020
ON_STOCK = {"effect": "01000020"}          # kept with an assignment made while STOCK is effect 1
SYNTH = {"keyboard": KEYBOARD_PORT, "channel": 1}
EVERYTHING = lambda seconds: [ZOOM_PORT, CONTROLLER_PORT, KEYBOARD_PORT]


def knob(number: int, value: int):
    """A knob of the keyboard."""
    return keyboard(mido.Message("control_change", control=number, value=value))


def sent(world: World) -> list:
    return [(slot, param, value) for _, slot, param, value in world.params]


def stored() -> dict:
    return yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))["mappings"]


class ParameterCase(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        patcher = mock.patch.object(bridge, "SYNTH_QUERY_SECONDS", 0.2)
        patcher.start()
        self.addCleanup(patcher.stop)


class Setting(ParameterCase):
    def test_a_knob_of_the_keyboard_sets_a_parameter_of_another_effect(self):
        self.install({PRESET: [mapping(0, 2, 0, 100), mapping(0, 3, 10, 50, cc=21)]}, synth=SYNTH)
        world = World([(1.0, knob(21, 127)), (1.3, knob(21, 0)), (1.6, cc(127))], ports=EVERYTHING,
                      chain=[STOCK, KEYSYNTH])
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 3, 50), (0, 3, 10), (0, 2, 100)])     # the pedal sets only its own

    def test_a_second_control_of_the_foot_controller_sets_one_as_well(self):
        self.install({PRESET: [mapping(0, 2, 0, 100), mapping(0, 4, 0, 100, invert=True, cc=30)]})
        world = World([(1.0, cc(127, control=30)), (1.3, cc(0))], chain=[STOCK])
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 4, 0), (0, 2, 0)])

    def test_a_controller_that_switches_an_effect_does_that_and_nothing_else(self):
        self.install({PRESET: [mapping(0, 3, 0, 100, cc=20)]})
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "switch_effect")
        bridge.CONTROLS.write_text(json.dumps({"effects": {"1": {"cc": 20}}}), encoding="utf-8")
        world = World([(1.0, cc(0, control=20))], chain=[STOCK])
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, zs.SWITCH_PARAM, 0)])


class Learning(ParameterCase):
    def learn(self, request: dict, script: list, mappings: list) -> dict:
        self.install({PRESET: mappings}, synth=SYNTH)
        seen = {}

        def client(connection):
            connection.at(1.0)
            seen["learn"] = connection.call("/api/parameter", request)
            seen["waiting"] = connection.call("/api/state")[1]["param_learning"]
            connection.at(1.6)
            seen["state"] = connection.call("/api/state")[1]

        world = World(script, ports=EVERYTHING, chain=[STOCK, KEYSYNTH], length=1.8)
        self.run_bridge(world, client)
        return seen

    def test_the_next_knob_moved_sets_the_parameter(self):
        seen = self.learn({"action": "learn", "slot": 0, "param": 3}, [(1.2, knob(21, 64)), (1.4, knob(21, 127))],
                          [mapping(0, 2), mapping(0, 3, 0, 80)])
        self.assertEqual(seen["learn"][0], 200)
        self.assertEqual(seen["waiting"], {"slot": 0, "param": 3, "new": False})
        self.assertIsNone(seen["state"]["param_learning"])
        self.assertEqual(stored()[PRESET], [mapping(0, 2, **ON_STOCK),          # "cc" only where it is one
                                            mapping(0, 3, 0, 80, cc=21, **ON_STOCK)])
        self.assertEqual(seen["state"]["mappings"][0]["targets"][1]["cc"], 21)
        self.assertIn("Preset 095: controller 21 now sets effect 1, parameter 2", self.output)

    def test_a_parameter_not_assigned_yet_is_assigned_with_the_knob_moved(self):
        new = {"min": 0, "max": 100, "invert": False, "curve": "log", "name": "Tone"}
        seen = self.learn({"action": "learn", "slot": 0, "param": 4, "new": new}, [(1.2, knob(22, 10))],
                          [mapping(0, 2)])
        self.assertEqual(seen["waiting"], {"slot": 0, "param": 4, "new": True})
        self.assertEqual(stored()[PRESET], [mapping(0, 2, **ON_STOCK),
                                            mapping(0, 4, curve="log", name="Tone", cc=22, **ON_STOCK)])

    def test_moving_the_expression_pedal_gives_the_parameter_back_to_it(self):
        self.learn({"action": "learn", "slot": 0, "param": 3}, [(1.2, cc(90))], [mapping(0, 3, cc=21)])
        self.assertEqual(stored()[PRESET], [mapping(0, 3, **ON_STOCK)])

    def test_a_place_given_as_text_is_refused(self):
        # once taken, the controller moved next stopped the bridge: "0" never matched the 0 stored
        new = {"min": 0, "max": 100}
        seen = self.learn({"action": "learn", "slot": "0", "param": 4, "new": new}, [(1.2, knob(22, 10))],
                          [mapping(0, 2)])
        self.assertEqual(seen["learn"][:2], (400, {"error": "Say which parameter."}))
        self.assertIsNone(seen["waiting"])
        self.assertEqual(stored()[PRESET], [mapping(0, 2)])   # and the knob moved stopped nothing

    def test_a_controller_with_four_parameters_takes_no_fifth(self):
        mappings = [mapping(0, param, cc=21) for param in range(2, 6)] + [mapping(0, 6)]
        seen = self.learn({"action": "learn", "slot": 0, "param": 6}, [(1.2, knob(21, 64))], mappings)
        self.assertEqual(stored()[PRESET], mappings)
        self.assertIn("Not learned: Controller 21 can set at most 4 parameters of a preset.", self.output)
        self.assertIsNone(seen["state"]["param_learning"])


class OneControllerOneJob(ParameterCase):
    """A controller that switches (an effect, a scene, the arpeggiator) does nothing else: learned
    for one of those, it lets go of the parameters it set, and the other way round. A knob of a
    synth effect and parameters can share one."""

    def make(self) -> "bridge.Bridge":
        self.install({PRESET: [mapping(0, 2), mapping(0, 3, cc=20)], OTHER: [mapping(1, 2, cc=20)]}, synth=SYNTH)
        return bridge.Bridge()

    def test_an_effect_switch_takes_the_controller_from_every_preset(self):
        instance = self.make()
        instance.effect_learning = 0
        instance.learned_switch(20)
        self.assertEqual(stored(), {PRESET: [mapping(0, 2)]})

    def test_a_scene_takes_it_in_its_own_preset_only(self):
        instance = self.make()
        instance.scenes = {(0, 94): [{"name": "Solo", "cc": None, "values": {}, "effects": {}}]}
        instance.scene_learning = ((0, 94), 0)
        instance.learned_scene(20)
        self.assertEqual(stored(), {PRESET: [mapping(0, 2)], OTHER: [mapping(1, 2, cc=20)]})

    def test_a_synth_knob_shares_it(self):
        instance = self.make()
        instance.control_learning = "KeySynth/Rate"
        instance.learned_control(20)
        self.assertEqual(instance.controls, {"KeySynth/Rate": 20})
        self.assertEqual(stored()[PRESET], [mapping(0, 2), mapping(0, 3, cc=20)])

    def test_a_parameter_takes_it_from_a_switch_and_keeps_the_synth_knob(self):
        instance = self.make()
        instance.controls = {"KeySynth/Rate": 20, "Arp": 20}
        instance.switches = {2: {"cc": 20, "press": False}}
        instance.param_learning = ((0, 94), 0, 2, None)
        instance.learned_param(20)
        self.assertEqual(instance.controls, {"KeySynth/Rate": 20})
        self.assertEqual(instance.switches, {})
        self.assertEqual(stored()[PRESET], [mapping(0, 2, cc=20), mapping(0, 3, cc=20)])


class Files(ParameterCase):
    def test_the_settings_file_takes_controllers_and_checks_them(self):
        self.install({PRESET: [mapping(0, 2), mapping(0, 3, cc=21)]})
        settings = bridge.gather_settings()
        self.assertEqual(cs.validate(settings), [])
        self.assertEqual(settings["mappings"][PRESET][1]["cc"], 21)
        for wrong, problem in (([mapping(0, 2, cc=64)], "a controller that cannot set a parameter (64)"),
                               ([mapping(0, param, cc=21) for param in range(2, 7)], "one controller sets more than 4"),
                               ([mapping(0, param) for param in range(2, 15)], "1 to 12 parameters")):
            broken = dict(settings, mappings={PRESET: wrong})
            self.assertTrue(any(problem in found for found in cs.validate(broken)), (problem, cs.validate(broken)))

    def test_export_and_import_keep_the_controller(self):
        self.install({PRESET: [mapping(0, 2), mapping(0, 3, cc=21)]})
        exported = bridge.Bridge().export_data()
        self.assertEqual(exported["presets"][PRESET]["targets"], [mapping(0, 2), mapping(0, 3, cc=21)])
        self.install({})
        instance = bridge.Bridge()
        instance.import_data(exported)
        self.assertEqual(stored()[PRESET], [mapping(0, 2), mapping(0, 3, cc=21)])

    def test_twelve_parameters_and_four_per_controller(self):
        self.install({})
        instance = bridge.Bridge()
        twelve = tuple(bridge.Mapping(slot, param, 0, 100, cc=None if slot == 0 else 20 + slot)   # 4 each
                       for slot in range(3) for param in range(2, 6))
        self.assertIsNone(instance.check((0, 94), twelve))
        self.assertIn("1 to 12", instance.check((0, 94), twelve + (bridge.Mapping(4, 2, 0, 100, cc=40),)))
        five = tuple(bridge.Mapping(0, param, 0, 100) for param in range(2, 7))
        self.assertEqual(instance.check((0, 94), five), "The expression pedal can set at most 4 parameters of a preset.")


if __name__ == "__main__":
    unittest.main()
