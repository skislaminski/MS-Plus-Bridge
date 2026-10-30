"""What is assigned goes with its effect: the parameters of a preset, the values and switches of
its scenes and the learned ranges remember the effect they were made for, follow it when it is
moved in the chain on the pedal, and wait while it is not in the chain. Effects are told apart
by their whole id: BlackOpt 01000020 and AnalogDly 0A000020 share their lower bits."""
import json
import unittest

import yaml

import bridge
import config_schema as cs
import scenes
from tests.sim import CONTROLLER_PORT, KEYBOARD_PORT, ZOOM_PORT, SimulatedCase, World, cc, learned, mapping

PRESET = "0/94"
STOCK, DELAY, ROOM = 0x01000020, 0x0A000020, 0x0B000020      # BlackOpt, AnalogDly (Time, …), BrghtRoom
KEYSYNTH = 0x07000F61


def on(effect: int) -> dict:
    return {"effect": f"{effect:08X}"}


def sent(world: World) -> list:
    return [(slot, param, value) for _, slot, param, value in world.params]


def stored() -> list:
    return yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))["mappings"][PRESET]


class FollowCase(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "switch_effect")

    def run_with_state(self, world: World, at: float) -> dict:
        seen = {}

        def client(connection):
            connection.at(at)
            seen.update(connection.call("/api/state")[1])

        self.run_bridge(world, client)
        return seen


class Parameters(FollowCase):
    def test_a_parameter_goes_with_its_effect_when_that_is_moved(self):
        self.install({PRESET: [mapping(1, 2, **on(DELAY))]})
        world = World([(1.0, cc(127)), (1.2, lambda world: world.rearrange([1, 0, 2])), (1.5, cc(0))],
                      chain=[STOCK, DELAY, ROOM])
        self.run_bridge(world)
        self.assertEqual(sent(world), [(1, 2, 100), (0, 2, 0)])
        self.assertEqual(stored(), [mapping(0, 2, **on(DELAY))])
        self.assertIn("Preset 095: AnalogDly is effect 1 now – what is assigned to it goes along", self.output)

    def test_a_parameter_waits_while_its_effect_is_not_in_the_chain(self):
        self.install({PRESET: [mapping(1, 2, **on(DELAY))]})
        world = World([(1.0, lambda world: world.swap_effect(1, ROOM)), (1.3, cc(127)),
                       (1.6, lambda world: world.swap_effect(1, DELAY)), (1.9, cc(64))], chain=[STOCK, DELAY])
        state = self.run_with_state(world, 1.45)
        self.assertEqual(sent(world), [(1, 2, 50)])                      # nothing into BrghtRoom's PRE DLY
        self.assertEqual(state["targets"][0]["away"], "AnalogDly · Time")
        self.assertFalse(state["targets"][0]["stopped"])
        self.assertIn("Nothing is sent to AnalogDly · Time: that effect is not in this preset now", self.output)
        self.assertEqual(stored(), [mapping(1, 2, **on(DELAY))])          # kept for when it is back

    def test_two_effects_swapping_places_take_their_parameters_along(self):
        self.install({PRESET: [mapping(0, 2, 0, 10, **on(STOCK)), mapping(1, 3, 0, 4, cc=21, **on(DELAY))]},
                     measured={PRESET: learned((0, 2, 0, 10), (1, 3, 0, 4))})
        world = World([(1.0, lambda world: world.rearrange([1, 0]))], chain=[STOCK, DELAY])
        self.run_bridge(world)
        self.assertEqual(stored(), [mapping(1, 2, 0, 10, **on(STOCK)), mapping(0, 3, 0, 4, cc=21, **on(DELAY))])
        ranges = json.loads(bridge.MEASUREMENTS.read_text(encoding="utf-8"))["patches"][PRESET]
        self.assertEqual(sorted((entry["slot"], entry["param"], entry["max"]) for entry in ranges.values()),
                         [(0, 3, 4), (1, 2, 10)])

    def test_the_one_in_its_place_keeps_it_and_another_of_its_kind_does_not_take_it(self):
        self.install({PRESET: [mapping(0, 2, **on(DELAY)), mapping(2, 3, cc=21, **on(DELAY))]})
        world = World([(1.0, lambda world: world.rearrange([0, 1])), (1.3, cc(127))], chain=[DELAY, STOCK, DELAY])
        state = self.run_with_state(world, 1.5)
        self.assertEqual(sent(world), [(0, 2, 100)])
        self.assertEqual([target["away"] for target in state["targets"]], [None, "AnalogDly · Mode"])
        self.assertEqual(stored(), [mapping(0, 2, **on(DELAY)), mapping(2, 3, cc=21, **on(DELAY))])

    def test_a_parameter_of_an_effect_that_is_gone_makes_way_for_one_that_moved_into_its_place(self):
        self.install({PRESET: [mapping(0, 2, **on(ROOM)), mapping(1, 2, cc=21, **on(DELAY))]})
        world = World([(1.0, lambda world: world.swap_effect(0, STOCK)), (1.2, lambda world: world.rearrange([1, 0]))],
                      chain=[ROOM, DELAY])
        self.run_bridge(world)
        self.assertEqual(stored(), [mapping(0, 2, cc=21, **on(DELAY))])
        self.assertIn("Preset 095: BrghtRoom · PRE DLY is assigned no longer – another effect took its place",
                      self.output)


    def test_an_effect_moving_into_the_synth_effects_old_place_is_not_taken_for_it(self):
        self.install({PRESET: [mapping(0, 2, **on(DELAY))]}, synth={"keyboard": KEYBOARD_PORT, "channel": 1})
        world = World([(1.0, lambda world: world.rearrange([1, 0])), (1.3, cc(127))],
                      ports=lambda seconds: [ZOOM_PORT, CONTROLLER_PORT, KEYBOARD_PORT], chain=[DELAY, KEYSYNTH])
        self.run_bridge(world)
        self.assertEqual(sent(world), [(1, 2, 100)])                      # AnalogDly's Time, now effect 2
        self.assertNotIn("Key knob", self.output)


class Remembering(FollowCase):
    def test_older_assignments_learn_their_effect_when_the_preset_is_selected_not_at_the_start(self):
        self.install({PRESET: [mapping(1, 2)]})
        world = World([(1.0, lambda world: world.preset_change(5)), (1.4, lambda world: world.preset_change(94)),
                       (1.9, lambda world: world.rearrange([1, 0])), (2.2, cc(127))], chain=[STOCK, DELAY], length=2.5)
        at_start = {}
        world.script.insert(0, (0.9, lambda world: at_start.update(mappings=stored())))
        self.run_bridge(world)
        self.assertEqual(at_start["mappings"], [mapping(1, 2)])           # the pedal may hold unsaved changes
        self.assertEqual(stored(), [mapping(0, 2, **on(DELAY))])
        self.assertEqual(sent(world)[-1], (0, 2, 100))

    def test_after_a_preset_change_values_wait_for_the_chain_and_never_reach_another_effect(self):
        """Moved, not saved, the preset changed and back: until the pedal reports the chain, the
        stored place is where the effect was moved to, which holds another effect now."""
        self.install({PRESET: [mapping(1, 2, **on(DELAY))]})

        def back_as_saved(world):
            world.chain = [STOCK, DELAY]
            world.preset_change(94)

        world = World([(1.0, lambda world: world.rearrange([1, 0])), (1.2, lambda world: world.preset_change(5)),
                       (1.5, back_as_saved), (1.55, cc(127)), (2.0, cc(0))], chain=[STOCK, DELAY], length=2.3)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(1, 2, 0)])                        # nothing into BlackOpt at effect 1
        self.assertEqual(stored(), [mapping(1, 2, **on(DELAY))])

    def test_a_chain_that_does_not_come_leaves_the_place_as_it_is(self):
        self.install({PRESET: [mapping(1, 2, **on(DELAY))]})
        world = World([(2.0, cc(127))], chain=[STOCK, DELAY])
        world.patches = False
        self.run_bridge(world)
        self.assertEqual(sent(world), [(1, 2, 100)])
        self.assertIn("The pedal has not reported the effects of this preset – what is assigned goes by its place",
                      self.output)

    def test_without_the_patch_query_everything_stays_by_its_place(self):
        self.approve(*bridge.REQUIRED_KINDS)
        self.install({PRESET: [mapping(1, 2, **on(DELAY))]})
        world = World([(1.0, cc(127))], chain=[STOCK, DELAY])
        self.run_bridge(world)
        self.assertEqual(sent(world), [(1, 2, 100)])


class Scenes(FollowCase):
    def scene(self, **extra) -> dict:
        return dict({"name": "Solo", "cc": 20, "values": [{"slot": 1, "param": 2, "value": 30}],
                     "effects": {"2": False}, "chain": {"2": f"{DELAY:08X}"}}, **extra)

    def test_values_and_switches_of_a_scene_go_with_their_effect(self):
        self.install({PRESET: [mapping(0, 3, **on(STOCK))]}, measured={PRESET: learned((1, 2, 0, 962))})
        bridge.CONTROLS.write_text(json.dumps({"controls": {}, "scenes": {PRESET: [self.scene()]}}), encoding="utf-8")
        world = World([(1.0, lambda world: world.rearrange([1, 0])), (1.3, cc(127, control=20))],
                      chain=[STOCK, DELAY])
        self.run_bridge(world)
        self.assertCountEqual(sent(world), [(0, 2, 30), (0, 0, 0)])        # AnalogDly, now effect 1
        kept = json.loads(bridge.CONTROLS.read_text(encoding="utf-8"))["scenes"][PRESET][0]
        self.assertEqual((kept["values"], kept["effects"], kept["chain"]),
                         ([{"slot": 0, "param": 2, "value": 30}], {"1": False}, {"1": f"{DELAY:08X}"}))

    def test_a_scene_leaves_what_belongs_to_an_effect_that_is_not_there(self):
        self.install({PRESET: [mapping(0, 3, **on(STOCK))]}, measured={PRESET: learned((1, 2, 0, 962))})
        bridge.CONTROLS.write_text(json.dumps({"controls": {}, "scenes": {PRESET: [self.scene()]}}), encoding="utf-8")
        world = World([(1.0, lambda world: world.swap_effect(1, ROOM)), (1.3, cc(127, control=20))],
                      chain=[STOCK, DELAY])
        state = self.run_with_state(world, 1.5)
        self.assertEqual(sent(world), [])
        self.assertEqual(state["scenes"]["list"][0]["values"][0]["away"], "AnalogDly · Time")
        self.assertEqual(state["scenes"]["list"][0]["effects"][0]["away"], "AnalogDly")


class Files(SimulatedCase):
    def test_the_effect_is_kept_in_hex_and_checked(self):
        kept = bridge.stored_mapping(bridge.Mapping(1, 2, 0, 100, effect=DELAY))
        self.assertEqual(kept["effect"], "0A000020")
        self.assertEqual(bridge.to_mapping(kept), bridge.Mapping(1, 2, 0, 100, effect=DELAY))
        self.assertNotIn("effect", bridge.stored_mapping(bridge.Mapping(1, 2, 0, 100)))
        with self.assertRaises(ValueError):
            bridge.to_mapping(dict(kept, effect="AnalogDly"))
        self.install({PRESET: [kept]})
        settings = bridge.gather_settings()                          # the settings file for the Pi
        self.assertEqual(cs.validate(settings), [])
        problems = cs.validate(dict(settings, mappings={PRESET: [dict(kept, effect="xyz")]}))
        self.assertTrue(any("not a valid assignment" in problem for problem in problems), problems)

    def test_a_scene_names_the_effects_of_its_places(self):
        scene = {"name": "Solo", "values": [{"slot": 1, "param": 2, "value": 30}], "effects": {}, "chain": {"2": "0A000020"}}
        self.assertEqual(scenes.read({PRESET: [scene]})[(0, 94)][0]["chain"], {1: DELAY})
        self.assertEqual(scenes.stored(scenes.read({PRESET: [scene]}))[PRESET][0]["chain"], {"2": "0A000020"})
        self.assertTrue(scenes.problems({PRESET: [dict(scene, chain={"9": "0A000020"})]}))
        self.assertTrue(scenes.problems({PRESET: [dict(scene, chain={"2": "Delay"})]}))


if __name__ == "__main__":
    unittest.main()
