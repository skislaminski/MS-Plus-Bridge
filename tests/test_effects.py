"""Switching the effects of the chain on and off from a controller: the stored switches, the
bridge sending them, learning them in the interface, and their way into export and settings file.

Run against the simulated devices from sim.py. The simulated pedal confirms a switch and reports
its own footswitch the way the MS-60B+ was measured to (README, "What was measured").
"""
import contextlib
import io
import json
import unittest
from unittest import mock

import bridge
import config_schema as cs
import effect_switch
import zoom_sysex as zs
from tests.sim import ID, SimulatedCase, World, cc, mapping, note_off, note_on, patch_dump, sysex
from tests.test_synth import EVERYTHING, KEYSYNTH, PATCH_QUERY, SYNTH, control

FIRST, SECOND, THIRD = 0x01000020, 0x01000030, 0x01000040       # three stock effects
ENABLE = "F0 52 00 6E 50 F7"


def switched(world: World) -> list:
    """(effect number, on) of every switch message sent to the pedal."""
    return [(slot + 1, bool(value)) for _, slot, param, value in world.params if param == zs.SWITCH_PARAM]


def press(seconds: float, control: int = 80) -> list:
    """Script entries: a footswitch that sends 127 while it is held down, then 0."""
    return [(seconds, cc(127, control)), (seconds + 0.05, cc(0, control))]


class Stored(unittest.TestCase):
    def test_switches_are_kept_by_effect_number(self):
        stored = {"1": {"cc": 80}, "3": {"cc": 81, "press": True}}
        switches = effect_switch.read(stored)
        self.assertEqual(switches, {0: {"cc": 80, "press": False}, 2: {"cc": 81, "press": True}})
        self.assertEqual(effect_switch.stored(switches), stored)
        self.assertEqual(effect_switch.read(None), {})
        self.assertEqual(effect_switch.problems({}), [])

    def test_what_cannot_be_right_is_named_and_not_used(self):
        wrong = {
            "not an effect": {"7": {"cc": 80}},
            "no number": {"x": {"cc": 80}},
            "sustain": {"1": {"cc": 64}},
            "bank select": {"1": {"cc": 0}},
            "no controller": {"1": {}},
            "beyond 127": {"1": {"cc": 128}},
            "a flag that is none": {"1": {"cc": 80, "press": "yes"}},
            "something else in it": {"1": {"cc": 80, "toggle": True}},
            "one controller twice": {"1": {"cc": 80}, "2": {"cc": 80}},
            "one effect twice": {"1": {"cc": 80}, 1: {"cc": 81}},
            "a list": [80, 81],
        }
        for name, stored in wrong.items():
            with self.subTest(name):
                self.assertEqual(len(effect_switch.problems(stored)), 1)
                self.assertEqual(effect_switch.read(stored), {})


class EffectCase(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "switch_effect")
        patcher = mock.patch.object(bridge, "SYNTH_QUERY_SECONDS", 0.2)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assign(self, controls=None, **effects) -> None:
        """Writes controls.json as the interface would have: assign(e2={"cc": 80}) for effect 2."""
        bridge.CONTROLS.write_text(json.dumps({
            "controls": controls or {}, "effects": {name[1:]: switch for name, switch in effects.items()}}),
            encoding="utf-8")

    def bridge_alone(self) -> "bridge.Bridge":
        """A bridge that is not running: for requests that need no devices."""
        with contextlib.redirect_stdout(io.StringIO()):
            return bridge.Bridge()


class Switching(EffectCase):
    def test_a_switch_that_sends_its_value_switches_the_effect_on_and_off(self):
        self.install()
        self.assign(e2={"cc": 80})
        script = [(1.0, cc(127, 80)), (1.3, cc(127, 80)),          # on, and on again: nothing more to send
                  (1.5, cc(0, 80)), (1.7, cc(30, 80))]             # off, and still off
        world = World(script, chain=[FIRST, SECOND, THIRD], off=[1])
        self.run_bridge(world)
        self.assertEqual(switched(world), [(2, True), (2, False)])
        self.assertEqual(world.sent[-3][1], "F0 52 00 6E 64 20 00 01 00 01 00 00 00 00 F7")
        self.assertLess(world.params[0][0] - 1.0, 0.02)                         # at once, like a note
        self.assertEqual(world.count(PATCH_QUERY), 1)                          # the chain is read at the start
        self.assertIn("Effect 2 on", self.output)
        self.assertIn("Effect 2 off", self.output)
        self.assertNotIn("WARNING", self.output)
        for _, text in world.sent:                                             # everything sent is on the allowlist
            zs.classify(bytes.fromhex(text.replace(" ", "")), ID, switchable=(0, 1, 2))

    def test_every_press_switches_it_over(self):
        self.install()
        self.assign(e1={"cc": 80, "press": True}, e3={"cc": 81, "press": True})
        state = {}
        world = World(press(1.0) + press(1.3) + press(1.6, 81)
                      + [(1.15, lambda world: state.update(early=self.instance.state()["effects"]["chain"])),
                         (1.9, lambda world: state.update(late=self.instance.state()["effects"]["chain"]))],
                      chain=[FIRST, SECOND, THIRD], off=[2])
        self.run_bridge(world)
        # the first was on, the third off: letting go of the switch (value 0) does nothing
        self.assertEqual(switched(world), [(1, False), (1, True), (3, True)])
        self.assertEqual(state, {"early": [False, True, False], "late": [True, True, True]})

    def test_it_follows_what_is_switched_on_the_pedal(self):
        self.install()
        self.assign(e1={"cc": 80, "press": True})
        world = World([(1.0, lambda world: world.stomp(0, False))] + press(1.3), chain=[FIRST, SECOND])
        self.run_bridge(world)
        self.assertEqual(switched(world), [(1, True)])
        self.assertIn("Effect 1 off, switched on the pedal", self.output)

    def test_another_preset_has_its_own_chain(self):
        self.install()
        self.assign(e2={"cc": 80, "press": True})

        def preset(program, chain, off=()):
            return lambda world: (world.new_chain(chain, off), world.preset_change(program))
        script = (press(1.0)
                  + [(1.4, preset(95, [FIRST]))] + press(2.0) + press(2.2)      # no second effect there
                  + [(2.6, preset(96, [FIRST, SECOND, THIRD], off=[1]))] + press(3.2))
        world = World(script, chain=[FIRST, SECOND])
        self.run_bridge(world)
        self.assertEqual(switched(world), [(2, False), (2, True)])
        self.assertEqual(world.count(PATCH_QUERY), 3)
        self.assertEqual(self.output.count("This preset has no effect 2 – its switch is ignored"), 1)

    def test_nothing_is_switched_without_the_approval(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        self.install()
        self.assign(e1={"cc": 80})
        world = World([(1.0, cc(0, 80))], chain=[FIRST])
        self.run_bridge(world)
        self.assertEqual(switched(world), [])
        self.assertGreater(world.count(PATCH_QUERY), 0)                         # the chain is still read: its names
        self.assertIn("Effect switches: not approved (`python probe.py approve switch_effect query_patch`)",
                      self.output)
        self.assertEqual(self.instance.state()["effects"]["approved"], False)

    def test_a_switch_the_pedal_does_not_confirm_is_repeated_once_and_then_read_back(self):
        self.install()
        self.assign(e2={"cc": 80})
        state = {}
        world = World([(1.0, cc(127, 80)), (2.1, lambda world: state.update(self.instance.state()["effects"]))],
                      chain=[FIRST, SECOND], off=[1], length=2.2)
        world.accept = lambda slot, param, value: param != zs.SWITCH_PARAM      # the pedal takes no switch
        self.run_bridge(world)
        self.assertEqual(switched(world), [(2, True), (2, True)])
        gap = world.params[1][0] - world.params[0][0]
        self.assertTrue(bridge.ACK_TIMEOUT - 0.01 <= gap <= bridge.ACK_TIMEOUT + 0.05, gap)
        self.assertIn("WARNING: the pedal did not confirm effect 2 on – reading the preset again", self.output)
        self.assertEqual((world.count(PATCH_QUERY), world.count(ENABLE)), (2, 2))
        self.assertEqual(state["chain"], [True, False])                         # as the pedal says it is

    def test_a_control_of_the_keyboard_switches_too_and_the_keys_play_on(self):
        self.install(synth=SYNTH)
        self.assign(e2={"cc": 20})
        script = [(1.0, control(20, 127)), (1.2, note_on(48)), (1.3, control(20, 0)), (1.4, note_off(48))]
        world = World(script, ports=EVERYTHING, chain=[KEYSYNTH, SECOND], off=[1])
        self.run_bridge(world)
        self.assertEqual([(slot, param, value) for _, slot, param, value in world.params],
                         [(1, 0, 1), (0, 2, zs.key_for(48)), (1, 0, 0), (0, 2, 0)])
        self.assertEqual(world.count(PATCH_QUERY), 1)

    def test_the_expression_pedal_is_never_taken_for_a_switch(self):
        self.install({"0/94": [mapping()]})
        self.assign(e1={"cc": 25, "press": True})                               # the number of the pedal's controller
        world = World([(1.0, cc(100)), (1.2, cc(127, 25, channel=4))], chain=[FIRST])
        self.run_bridge(world)
        # on the pedal's own channel it is the pedal; from another channel it is a switch
        self.assertEqual([(slot, param, value) for _, slot, param, value in world.params], [(0, 2, 79), (0, 0, 0)])


class ChainChanges(EffectCase):
    def test_a_switch_is_not_repeated_into_a_chain_that_no_longer_has_the_effect(self):
        """The pedal does not confirm a switch of effect 3, and before it is repeated the preset
        turns out to have one effect only: repeating it would have been refused by the allowlist,
        which stops the bridge."""
        def fewer(world):
            world.chain = [FIRST]
            world.zoom_queue.append(sysex(patch_dump([(FIRST, 0)])))
        self.install()
        self.assign(e3={"cc": 80})
        world = World([(1.0, cc(0, 80)), (1.05, fewer)], chain=[FIRST, SECOND, THIRD], length=1.8)
        world.accept = lambda slot, param, value: param != zs.SWITCH_PARAM      # never confirmed
        self.run_bridge(world)
        self.assertEqual(switched(world), [(3, False)])
        self.assertIn("Stopped.", self.output)
        self.assertNotIn("WARNING", self.output)


class Interface(EffectCase):
    def test_a_footswitch_is_learned_and_how_it_switches_is_chosen(self):
        seen = {}

        def client(connection):
            connection.at(1.0)
            seen["learn"] = connection.call("/api/effects", {"action": "learn", "slot": 1})[0]
            seen["waiting"] = connection.call("/api/state")[1]["effects"]["learning"]
            connection.at(1.6)
            seen["learned"] = connection.call("/api/state")[1]["effects"]
            seen["file"] = json.loads(bridge.CONTROLS.read_text(encoding="utf-8"))
            connection.at(2.7)
            seen["press"] = connection.call("/api/effects", {"action": "press", "slot": 1, "press": True})[0]
            connection.at(3.4)
            seen["clear"] = connection.call("/api/effects", {"action": "clear", "slot": 1})[0]
        self.install()
        script = ([(1.2, cc(60))]                                    # the expression pedal moves: not what is learned
                  + press(1.3)                                       # learned; letting go does not switch it at once
                  + [(2.5, cc(0, 80))]                               # its value switches: off
                  + press(2.9)                                       # every press switches: on again
                  + press(3.6))                                      # cleared: nothing
        world = World(script, chain=[FIRST, SECOND], length=3.9)
        self.run_bridge(world, client)
        self.assertEqual((seen["learn"], seen["waiting"], seen["press"], seen["clear"]), (200, 1, 200, 200))
        self.assertEqual(seen["learned"]["learning"], None)
        self.assertEqual(seen["learned"]["switches"][:3], [
            {"cc": None, "press": False, "slot": 0}, {"cc": 80, "press": False, "slot": 1},
            {"cc": None, "press": False, "slot": 2}])
        self.assertEqual(seen["learned"]["chain"], [True, True])
        self.assertEqual(seen["file"], {"controls": {}, "effects": {"2": {"cc": 80}}})
        self.assertEqual(switched(world), [(2, False), (2, True)])
        self.assertIn("Effect 2: controller 80 now switches it on and off", self.output)
        self.assertEqual(json.loads(bridge.CONTROLS.read_text(encoding="utf-8")), {"controls": {}})

    def test_the_button_switches_the_effect_and_says_why_not(self):
        seen = {}

        def client(connection):
            connection.at(1.0)
            seen["over"] = connection.call("/api/effects", {"action": "switch", "slot": 0})[0]
            connection.at(1.3)
            seen["on"] = connection.call("/api/effects", {"action": "switch", "slot": 0, "on": True})[0]
            seen["again"] = connection.call("/api/effects", {"action": "switch", "slot": 0, "on": True})[0]
            seen["missing"] = connection.call("/api/effects", {"action": "switch", "slot": 4})[:2]
            seen["unknown"] = connection.call("/api/effects", {"action": "switch", "slot": 6})[:2]
            seen["press"] = connection.call("/api/effects", {"action": "press", "slot": 0, "press": True})[:2]
            seen["what"] = connection.call("/api/effects", {"action": "dance", "slot": 0})[:2]
            seen["page"] = connection.call("/")[1]
        self.install()
        world = World(chain=[FIRST, SECOND], length=1.8)
        self.run_bridge(world, client)
        self.assertEqual((seen["over"], seen["on"], seen["again"]), (200, 200, 200))
        self.assertEqual(switched(world), [(1, False), (1, True)])
        self.assertEqual(seen["missing"], (400, {"error": "This preset has no effect 5."}))
        self.assertEqual(seen["unknown"], (400, {"error": "Unknown effect."}))
        self.assertEqual(seen["press"], (400, {"error": "Learn a controller for the effect first."}))
        self.assertEqual(seen["what"], (400, {"error": "Unknown action."}))
        self.assertIn(b"<h3>Effects</h3>", seen["page"])

    def test_without_the_approval_the_button_says_how_to_get_it(self):
        seen = {}

        def client(connection):
            connection.at(1.0)
            seen["switch"] = connection.call("/api/effects", {"action": "switch", "slot": 0})[:2]
        self.approve(*bridge.REQUIRED_KINDS)
        self.install()
        world = World(chain=[FIRST], length=1.4)
        self.run_bridge(world, client)
        self.assertEqual(seen["switch"][0], 400)
        self.assertIn("python probe.py approve switch_effect query_patch", seen["switch"][1]["error"])
        self.assertEqual(switched(world), [])

    def test_one_controller_does_one_thing(self):
        seen = {}

        def client(connection):
            connection.at(1.0)
            connection.call("/api/effects", {"action": "learn", "slot": 0})
            connection.at(1.4)
            seen["switch"] = json.loads(bridge.CONTROLS.read_text(encoding="utf-8"))
            connection.call("/api/synth", {"action": "learn", "knob": "KeySynth/Level"})
            seen["waiting"] = connection.call("/api/state")[1]
            connection.at(1.8)
            seen["knob"] = json.loads(bridge.CONTROLS.read_text(encoding="utf-8"))
        self.install(synth=SYNTH)
        self.assign(controls={"KeySynth/Level": 21, "KeySynth/Rate": 22})
        world = World([(1.2, control(21, 127)), (1.6, control(21, 100))], ports=EVERYTHING, chain=[KEYSYNTH],
                      length=2.1)
        self.run_bridge(world, client)
        self.assertEqual(seen["switch"], {"controls": {"KeySynth/Rate": 22}, "effects": {"1": {"cc": 21}}})
        self.assertEqual((seen["waiting"]["synth"]["learning"], seen["waiting"]["effects"]["learning"]),
                         ("KeySynth/Level", None))
        self.assertEqual(seen["knob"], {"controls": {"KeySynth/Rate": 22, "KeySynth/Level": 21}})
        self.assertEqual(switched(world), [])                                   # learning a switch does not flip it


class Carried(EffectCase):
    def test_export_all_holds_the_switches_and_import_brings_them_back(self):
        self.install({"0/94": [mapping()]})
        self.assign(e2={"cc": 80, "press": True})
        exported = self.bridge_alone().export_data()
        self.assertEqual(exported["effects"], {"2": {"cc": 80, "press": True}})
        self.assertNotIn("effects", self.bridge_alone().export_data("0/94"))    # one preset: its assignments only

        bridge.CONTROLS.unlink()
        fresh = self.bridge_alone()
        self.assertEqual(fresh.switches, {})
        with contextlib.redirect_stdout(io.StringIO()):
            fresh.import_data({"format": bridge.EXPORT_FORMAT, "presets": {}, "effects": exported["effects"]})
        self.assertEqual(fresh.switches, {1: {"cc": 80, "press": True}})
        self.assertEqual(json.loads(bridge.CONTROLS.read_text(encoding="utf-8")),
                         {"controls": {}, "effects": {"2": {"cc": 80, "press": True}}})

    def test_a_keyboard_part_imported_takes_its_controllers_from_the_switches(self):
        # one controller then switched effect 1 and was shown on Rate, which it never set; and the
        # file for the Pi was refused
        self.install(synth=SYNTH)
        self.assign(e1={"cc": 20})
        instance = self.bridge_alone()
        with contextlib.redirect_stdout(io.StringIO()):
            instance.import_data({"format": bridge.EXPORT_FORMAT, "presets": {},
                                  "keyboard": {"controls": {"KeySynth/Rate": 20}}})
        self.assertEqual((instance.controls, instance.switches), ({"KeySynth/Rate": 20}, {}))
        self.assertEqual(json.loads(bridge.CONTROLS.read_text(encoding="utf-8")), {"controls": {"KeySynth/Rate": 20}})

    def test_switches_that_cannot_be_right_change_nothing(self):
        self.install()
        self.assign(e1={"cc": 80})
        instance = self.bridge_alone()
        for effects in ({"9": {"cc": 81}}, {"1": {"cc": 64}}, [81]):
            with self.subTest(effects=effects), self.assertRaises(bridge.UserError) as refused:
                instance.import_data({"format": bridge.EXPORT_FORMAT, "presets": {}, "effects": effects})
            self.assertTrue(str(refused.exception).startswith("Effect switches: "))
        with self.assertRaises(bridge.UserError):                               # one controller, two jobs
            instance.import_data({"format": bridge.EXPORT_FORMAT, "presets": {}, "effects": {"1": {"cc": 81}},
                                  "keyboard": {"controls": {"Level": 81}}})
        self.assertEqual(instance.switches, {0: {"cc": 80, "press": False}})

    def test_the_settings_file_holds_them_and_wants_their_approval(self):
        self.install({"0/94": [mapping()]})
        self.assign(e1={"cc": 80}, e4={"cc": 81, "press": True})
        settings = bridge.gather_settings()
        self.assertEqual(settings["effects"], {"1": {"cc": 80}, "4": {"cc": 81, "press": True}})
        self.assertEqual(cs.validate(settings), [])
        settings, problems = cs.read(cs.dump(settings))                         # as it comes back from the card
        self.assertEqual(problems, [])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bridge.Bridge(settings).switches,
                             {0: {"cc": 80, "press": False}, 3: {"cc": 81, "press": True}})

        del settings["approvals"]["messages"]["switch_effect"]
        self.assertEqual(cs.validate(settings), ["approvals: not approved: switch_effect (python probe.py approve)."])
        del settings["effects"]
        self.assertEqual(cs.validate(settings), [])                             # without switches it is not needed

    def test_a_settings_file_with_wrong_switches_is_refused(self):
        self.install()
        self.assign(e1={"cc": 80})
        settings = bridge.gather_settings()
        for effects, word in (({"7": {"cc": 80}}, "not an effect"), ({"1": {"cc": 64}}, "not reserved"),
                              ({"1": {"cc": 80}, "2": {"cc": 80}}, "two effects")):
            with self.subTest(word):
                self.assertIn(word, " ".join(cs.validate(dict(settings, effects=effects))))
        clash = cs.validate(dict(settings, controls={"Level": 80}))
        self.assertEqual(clash, ["effects: one controller both switches an effect and is assigned on the keyboard."])


if __name__ == "__main__":
    unittest.main()
