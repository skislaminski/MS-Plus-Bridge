"""Scenes: stored sets of parameter values and effect switches that a press recalls.

Run against the simulated devices from sim.py.
"""
import contextlib
import io
import json
import unittest

import bridge
import config_schema as cs
import scenes
import zoom_sysex as zs
from tests.sim import ID, World, cc, learned, mapping
from tests.test_effects import FIRST, SECOND, THIRD, EffectCase, press

PRESET = "0/94"
SOLO = {"name": "Solo", "cc": 21, "values": [{"slot": 1, "param": 2, "value": 30},
                                              {"slot": 0, "param": 3, "value": 7}], "effects": {"3": False}}
CLEAN = {"name": "Clean", "cc": 20, "values": [{"slot": 1, "param": 2, "value": 100}], "effects": {"3": True}}
RANGES = {PRESET: learned((1, 2, 0, 100), (0, 3, 0, 10))}


def params(world: World) -> list:
    return [(slot, param, value) for _, slot, param, value in world.params]


class Stored(unittest.TestCase):
    def test_scenes_are_kept_per_preset(self):
        stored = {PRESET: [SOLO, CLEAN]}
        read = scenes.read(stored)
        self.assertEqual(read[(0, 94)][0], {"name": "Solo", "cc": 21, "values": {(1, 2): 30, (0, 3): 7},
                                            "effects": {2: False}, "chain": {}})
        self.assertEqual(scenes.stored(read), {PRESET: [
            dict(SOLO, values=sorted(SOLO["values"], key=lambda value: value["slot"])), CLEAN]})
        self.assertEqual((scenes.read(None), scenes.problems({})), ({}, []))

    def test_what_cannot_be_right_is_named_and_not_used(self):
        wrong = {
            "no preset": {"x": [SOLO]},
            "no name": {PRESET: [dict(SOLO, name="")]},
            "a reserved controller": {PRESET: [dict(SOLO, cc=64)]},
            "one controller twice": {PRESET: [SOLO, dict(CLEAN, cc=21)]},
            "effect on/off as a value": {PRESET: [dict(SOLO, values=[{"slot": 1, "param": 0, "value": 1}])]},
            "a slot beyond the six": {PRESET: [dict(SOLO, values=[{"slot": 6, "param": 2, "value": 1}])]},
            "one parameter twice": {PRESET: [dict(SOLO, values=SOLO["values"][:1] * 2)]},
            "an effect that is none": {PRESET: [dict(SOLO, effects={"7": True})]},
            "too many": {PRESET: [dict(SOLO, cc=30 + index) for index in range(9)]},
            "something else in it": {PRESET: [dict(SOLO, fade=3)]},
        }
        for name, stored in wrong.items():
            with self.subTest(name):
                self.assertEqual(len(scenes.problems(stored)), 1, scenes.problems(stored))
                self.assertEqual(scenes.read(stored), {})


class SceneCase(EffectCase):
    def keep(self, *entries, preset=PRESET) -> None:
        bridge.CONTROLS.write_text(json.dumps({"controls": {}, "scenes": {preset: list(entries)}}), encoding="utf-8")


class Recalling(SceneCase):
    def test_a_press_sets_the_values_and_switches_the_effects(self):
        self.install(measured=RANGES)
        self.keep(SOLO, CLEAN)
        state = {}
        world = World(press(1.0, 21) + press(1.5, 20) + press(1.9, 20)        # Clean twice: nothing more to send
                      + [(1.3, lambda world: state.update(self.instance.state()["scenes"]))],
                      chain=[FIRST, SECOND, THIRD], length=2.6)
        self.run_bridge(world)
        self.assertEqual(sorted(params(world)[:3]), [(0, 3, 7), (1, 2, 30), (2, 0, 0)])
        self.assertEqual(sorted(params(world)[3:]), [(1, 2, 100), (2, 0, 1)])
        times = [seconds for seconds, _, param, _ in world.params[:3] if param != 0]
        self.assertGreaterEqual(times[1] - times[0], 0.009)                   # values keep the minimum interval
        self.assertLess(world.params[2][0] - 1.0, 0.05)
        self.assertEqual((state["active"], [scene["name"] for scene in state["list"]]), (0, ["Solo", "Clean"]))
        self.assertIn("Scene “Solo”", self.output)
        self.assertNotIn("WARNING", self.output)
        for _, text in world.sent:
            zs.classify(bytes.fromhex(text.replace(" ", "")), ID, self.instance_targets, switchable=(0, 1, 2))

    instance_targets = (zs.ParamTarget(1, 2, 0, 100), zs.ParamTarget(0, 3, 0, 10))

    def test_a_value_the_pedal_holds_already_is_no_trouble(self):
        self.install(measured=RANGES)
        self.keep(dict(SOLO, effects={}), dict(CLEAN, effects={}))
        world = World(press(1.0, 21) + [(1.4, lambda world: (world.knob(1, 2, 30), world.knob(0, 3, 7)))]
                      + press(1.6, 21) + press(2.2, 21) + press(2.8, 20), chain=[FIRST, SECOND], length=3.4)
        self.run_bridge(world)
        # after the knobs were touched the scene is sent again, although the pedal holds those values
        self.assertEqual(sorted(params(world)), [(0, 3, 7), (0, 3, 7), (1, 2, 30), (1, 2, 30), (1, 2, 100)])
        self.assertNotIn("WARNING", self.output)
        self.assertEqual(self.instance.state()["totals"], None)

    def test_the_expression_pedal_and_a_scene_share_a_parameter(self):
        self.install({PRESET: [mapping(1, 2, 20, 60)]}, measured=RANGES)
        self.keep(dict(CLEAN, effects={}))
        world = World([(1.0, cc(127))] + press(1.3, 20) + [(1.6, cc(0))], chain=[FIRST, SECOND])
        self.run_bridge(world)
        # the scene may go beyond the range the pedal was given, within what was learned
        self.assertEqual(params(world), [(1, 2, 60), (1, 2, 100), (1, 2, 20)])

    def test_what_was_not_learned_that_far_is_left_out_and_another_preset_has_none(self):
        self.install(measured={PRESET: learned((1, 2, 0, 50))})
        self.keep(dict(CLEAN, effects={}), dict(SOLO, effects={}, values=[{"slot": 0, "param": 3, "value": 7}]))
        world = World(press(1.0, 20) + press(1.3, 21)
                      + [(1.6, lambda world: world.preset_change(95))] + press(2.2, 21), chain=[FIRST, SECOND])
        self.run_bridge(world)
        self.assertEqual(params(world), [(0, 3, 7)])                          # not learned: its own value only
        self.assertIn("Scene “Clean” does not set effect 2, parameter 1: 100 is outside what was learned", self.output)


class Interface(SceneCase):
    def test_a_scene_is_added_captured_from_the_pedal_learned_and_recalled(self):
        seen = {}

        def client(connection):
            call = lambda **data: connection.call("/api/scenes", data)[0]
            connection.at(1.0)
            seen["add"] = call(action="add", name=" Solo ")
            seen["capture"] = call(action="capture", index=0)
            connection.at(1.6)
            seen["during"] = connection.call("/api/state")[1]
            seen["apply"] = call(action="apply", index=0)
            seen["learn"] = call(action="learn", index=0)
            connection.at(2.2)
            seen["after"] = connection.call("/api/state")[1]["scenes"]
            seen["file"] = json.loads(bridge.CONTROLS.read_text(encoding="utf-8"))
            connection.at(3.6)
            seen["recall"] = call(action="recall", index=0)
            seen["drop"] = call(action="drop", index=0, slot=1)
            seen["none"] = connection.call("/api/scenes", {"action": "recall", "index": 3})[:2]
            seen["page"] = connection.call("/")[1]
        self.install()
        script = ([(1.2, lambda world: [world.knob(1, 2, value) for value in (80, 55, 30)]),
                   (1.4, lambda world: world.stomp(1, False))]
                  + press(1.9, 22)                                             # learned
                  + [(2.5, lambda world: (world.knob(1, 2, 90), world.stomp(1, True)))]
                  + press(3.2, 22))                                            # recalled
        world = World(script, chain=[FIRST, SECOND], length=4.0)
        self.run_bridge(world, client)
        self.assertEqual([seen[key] for key in ("add", "capture", "apply", "learn", "recall", "drop")], [200] * 6)
        self.assertEqual((seen["during"]["scenes"]["capturing"], seen["during"]["scenes"]["seen"],
                          seen["during"]["learning"]), (0, 2, None))
        # shown while it is captured: the last value of each knob turned, the switches
        self.assertEqual(seen["during"]["scenes"]["captured"], {"values": [{"slot": 1, "param": 2, "value": 30}],
                                                                "effects": [{"slot": 1, "on": False}]})
        self.assertEqual(seen["after"]["captured"], {"values": [], "effects": []})
        self.assertEqual(seen["after"]["list"], [{"name": "Solo", "cc": 22,
                                                  "values": [{"slot": 1, "param": 2, "value": 30, "away": None}],
                                                  "effects": [{"slot": 1, "on": False, "away": None}]}])
        self.assertEqual(seen["file"]["scenes"], {PRESET: [{"name": "Solo", "cc": 22,
                                                             "values": [{"slot": 1, "param": 2, "value": 30}],
                                                             "effects": {"2": False},
                                                             "chain": {"2": f"{SECOND:08X}"}}]})
        self.assertEqual(seen["none"], (400, {"error": "This preset has no such scene."}))
        # the press, and the button: the second finds everything as the scene wants it
        self.assertEqual(sorted(params(world)), [(1, 0, 0), (1, 2, 30)])
        self.assertEqual(bridge.zs.Measurements(bridge.MEASUREMENTS).get((0, 94), 1, 2)["values"], [30, 55, 80])
        self.assertIn("Scene “Solo”: controller 22 now recalls it", self.output)
        self.assertIn(b"<h3>Scenes</h3>", seen["page"])
        self.assertEqual(json.loads(bridge.CONTROLS.read_text(encoding="utf-8"))["scenes"][PRESET][0]["effects"], {})


    def test_the_values_of_an_effect_replaced_since_do_not_count_towards_the_most(self):
        # eight values made on FIRST in place 1, which holds SECOND now: one knob of SECOND captured
        # was refused as a ninth value (and the capture was gone)
        full = {"name": "Full", "cc": 21, "values": [{"slot": 0, "param": param, "value": 1} for param in range(3, 11)],
                "effects": {}, "chain": {"1": f"{FIRST:08X}"}}
        self.keep(full)
        seen = {}

        def client(connection):
            connection.at(1.0)
            connection.call("/api/scenes", {"action": "capture", "index": 0})
            connection.at(1.4)
            seen["apply"] = connection.call("/api/scenes", {"action": "apply", "index": 0})[:2]

        self.install()
        world = World([(1.2, lambda world: world.knob(0, 2, 30))], chain=[SECOND], length=1.8)
        self.run_bridge(world, client)
        self.assertEqual(seen["apply"], (200, {"result": None}))
        stored = json.loads(bridge.CONTROLS.read_text(encoding="utf-8"))["scenes"][PRESET][0]
        self.assertEqual((stored["values"], stored["chain"]), ([{"slot": 0, "param": 2, "value": 30}],
                                                               {"1": f"{SECOND:08X}"}))


class OneFootswitch(SceneCase):
    def test_one_footswitch_recalls_a_scene_in_every_preset(self):
        seen = {}

        def client(connection):
            connection.at(1.0)
            seen["learn"] = connection.call("/api/scenes", {"action": "learn", "index": 0})[0]
            connection.at(1.6)
            seen["odd"] = [connection.call("/api/scenes", dict(action="drop", index=0, **place))[:2]
                           for place in ({"slot": [1], "param": 2}, {"slot": {"1": 2}}, {"slot": 1, "param": "2"})]
        self.install(measured=RANGES)
        other = {key: value for key, value in CLEAN.items() if key != "cc"}
        bridge.CONTROLS.write_text(json.dumps({"controls": {}, "scenes": {PRESET: [SOLO], "0/95": [other]}}),
                                   encoding="utf-8")
        world = World([(0.9, lambda world: world.preset_change(95))] + press(1.2, 21),
                      chain=[FIRST, SECOND, THIRD], length=2.0)
        self.run_bridge(world, client)
        stored = json.loads(bridge.CONTROLS.read_text(encoding="utf-8"))["scenes"]
        self.assertEqual(seen["learn"], 200)
        self.assertEqual((stored[PRESET][0]["cc"], stored["0/95"][0]["cc"]), (21, 21))
        self.assertEqual(seen["odd"], [(400, {"error": "The scene does not set that."})] * 3)
        self.assertIn("Scene “Clean”: controller 21 now recalls it", self.output)


class Carried(SceneCase):
    def bridge_alone(self) -> "bridge.Bridge":
        with contextlib.redirect_stdout(io.StringIO()):
            return bridge.Bridge()

    def test_export_import_and_the_settings_file_carry_them(self):
        self.install(measured=RANGES)
        self.keep(SOLO)
        exported = self.bridge_alone().export_data()
        self.assertEqual(exported["scenes"][PRESET][0]["name"], "Solo")
        settings = bridge.gather_settings()
        self.assertEqual(settings["scenes"], exported["scenes"])
        self.assertEqual(cs.validate(settings), [])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bridge.Bridge(settings).scenes[(0, 94)][0]["values"], {(1, 2): 30, (0, 3): 7})
        del settings["approvals"]["messages"]["switch_effect"]                  # the scene switches an effect
        self.assertIn("switch_effect", " ".join(cs.validate(settings)))
        self.assertIn("needs a name", " ".join(cs.validate(dict(settings, scenes={PRESET: [dict(SOLO, name="")]}))))

        bridge.CONTROLS.unlink()
        fresh = self.bridge_alone()
        with contextlib.redirect_stdout(io.StringIO()):
            fresh.import_data({"format": bridge.EXPORT_FORMAT, "presets": {}, "scenes": exported["scenes"]})
        self.assertEqual(fresh.scenes[(0, 94)][0]["cc"], 21)
        with self.assertRaises(bridge.UserError):
            fresh.import_data({"format": bridge.EXPORT_FORMAT, "presets": {}, "scenes": {PRESET: [{"name": ""}]}})


if __name__ == "__main__":
    unittest.main()
