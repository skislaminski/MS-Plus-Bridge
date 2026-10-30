"""Guards that the reviews of 2026-10-20 asked for: values, switches and notes reach only the
effect they are meant for, whatever moves on the pedal, reconnects or is installed; a refusal of the
allowlist never stops the bridge; and an effect whose build on the pedal is not the one the table
of effect files describes is set by number only."""
import json
import unittest
from unittest import mock

import mido
import yaml

import bridge
import zoom_sysex as zs
from tests.sim import CONTROLLER_PORT, KEYBOARD_PORT, ZOOM_PORT, SimulatedCase, World, cc, learned, mapping, note_on
from tests.test_editor import CHORUS, KEYFM, KEYSYNTH, PRESET, ROOM, STOCK, SYNTH, EditorCase, sent
from tests.test_synth import EVERYTHING

PONG = 0x0A002110                                      # Pong Delay: 14 knobs, parameter 14 beyond the unlearned grid
SCENE = {"name": "S", "cc": 30, "values": [{"slot": 2, "param": 3, "value": 20}], "effects": {"3": False},
         "chain": {"3": f"{ROOM:08X}"}}


def gone(start: float, end: float):
    """Ports while the pedal's cable is out from start to end (it stays powered)."""
    return lambda seconds: ([] if start < seconds < end else [ZOOM_PORT]) + [CONTROLLER_PORT, KEYBOARD_PORT]


class Editor(EditorCase):
    def test_a_value_repeated_without_ack_is_dropped_when_the_effect_is_changed(self):
        world = World(chain=[STOCK, CHORUS], length=2.4)
        self.edit((1, 3, 50, CHORUS), 1.1, (1, 3, 50, CHORUS), 1.15,          # the second draws no ack
                  {"action": "change", "slot": 1, "effect": f"{ROOM:08X}"}, world=world, until=2.1)
        self.assertEqual(world.chain, [STOCK, ROOM])
        changed = next(seconds for seconds, slot, param, _ in world.params if param == zs.EFFECT_PARAM)
        self.assertEqual([(slot, param) for seconds, slot, param, _ in world.params
                          if seconds > changed and param != zs.EFFECT_PARAM], [])   # nothing for BrghtRoom's Decay

    def test_a_refusal_of_the_allowlist_does_not_stop_the_bridge(self):
        with mock.patch.object(bridge.Session, "_check_edits", side_effect=zs.NotAllowed("a test refusal")):
            *_, state = self.edit()
        self.assertIn("WARNING: not sent – a test refusal", self.output)
        self.assertTrue(state["connected"])

    def test_a_build_the_table_does_not_describe_goes_by_number(self):
        world = World(chain=[STOCK, ROOM], length=1.8)
        world.values[(1, 2)] = 120                       # BrghtRoom's PRE DLY goes to 99: another build
        reply, state = self.edit((1, 3, 20, ROOM), world=world)
        self.assertEqual(reply, (400, {"error": "BrghtRoom on the pedal is another build than the table describes."}))
        self.assertEqual((state["chain_effects"][1]["knobs"], state["chain_effects"][1].get("unmatched")), ([], True))
        self.assertEqual(state["chain_effects"][0]["knobs"][0]["name"], "Comp")       # the others as they are
        self.assertIn("WARNING: BrghtRoom on the pedal is another build than zoom_effects.json describes", self.output)


class Notes(EditorCase):
    def test_the_expression_pedal_never_writes_a_key_knob_of_any_synth_effect(self):
        for chain, slot, synth, ports in (([STOCK, KEYSYNTH], 1, None, None),                 # nothing played
                                          ([KEYSYNTH, KEYFM], 1, SYNTH, EVERYTHING)):         # the second synth
            with self.subTest(chain=chain):
                world = World([(1.1, cc(64)), (1.3, cc(127))], chain=chain, ports=ports, length=2.0)
                self.edit(world=world, synth=synth, mappings={PRESET: [mapping(slot, 2, 0, 1000)]}, until=1.8)
                self.assertEqual(sent(world), [])
                self.assertIn("that is a Key knob of a synth effect", self.output)

    def test_a_gate_left_open_while_the_cable_was_out_is_closed(self):
        self.install({}, synth=SYNTH)
        world = World([(1.0, note_on(60))], ports=gone(1.2, 1.7), chain=[KEYSYNTH, ROOM], length=3.2)
        receive = world.receive
        world.receive = lambda message: None if 1.2 < world.now() < 1.7 else receive(message)
        self.run_bridge(world)
        closed = [seconds for seconds, slot, param, value in world.params if (slot, param, value) == (0, 2, 0)]
        self.assertTrue(closed and 1.7 < closed[0] < 3.0, world.params)        # not only when the bridge stops

    def test_after_a_program_change_passed_through_no_note_goes_to_the_old_place(self):
        self.install({}, synth=SYNTH, passthrough=True)
        world = World([(1.0, mido.Message("program_change", program=3)),
                       (1.0, lambda world: world.new_chain([STOCK, ROOM])), (1.02, note_on(60))],
                      ports=EVERYTHING, chain=[KEYSYNTH, ROOM], length=1.4)
        self.run_bridge(world)
        self.assertEqual([item for item in sent(world) if item[0] == 0], [])     # BlackOpt's Comp left alone


class Scenes(EditorCase):
    def keep(self, scene) -> None:
        bridge.CONTROLS.write_text(json.dumps({"controls": {}, "scenes": {PRESET: [scene]}}), encoding="utf-8")

    def test_scene_values_wait_for_the_chain_after_a_preset_change(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        self.install({})
        self.keep(dict(SCENE, effects={}))
        script = [(1.0, lambda world: world.rearrange([0, 2, 1])),             # BrghtRoom to place 2, not saved
                  (1.5, lambda world: (world.new_chain([STOCK, STOCK]), world.preset_change(95))),
                  (2.0, lambda world: (world.new_chain([STOCK, CHORUS, ROOM]), world.preset_change(94))),
                  (2.03, cc(127, control=30))]                                 # the scene's footswitch at once
        world = World(script, chain=[STOCK, CHORUS, ROOM], length=2.6)
        self.run_bridge(world)
        self.assertNotIn((1, 3, 20), sent(world))                              # ChorusOne's Rate left alone
        self.assertIn((2, 3, 20), sent(world))

    def test_a_scene_switch_waits_no_effect_while_one_is_being_moved(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "switch_effect", "move_effect")
        self.install({})
        self.keep(dict(SCENE, values=[]))

        def client(connection):
            connection.at(1.0)
            connection.call("/api/effects", {"action": "move", "slot": 2, "to": 0})

        world = World([(1.05, cc(127, control=30))], chain=[STOCK, CHORUS, ROOM], length=1.8)
        self.run_bridge(world, client)
        self.assertEqual(world.chain, [ROOM, STOCK, CHORUS])
        self.assertNotIn((2, 0, 0), sent(world) + [(s, p, v) for _, s, p, v in world.params])   # not ChorusOne

    def test_a_capture_cut_off_by_a_reconnect_can_be_left(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        self.install({PRESET: [mapping(0, 2)]})
        seen = []

        def client(connection):
            scenes = lambda **data: connection.call("/api/scenes", data)[0]
            connection.at(1.0)
            seen.extend([scenes(action="add", name="Solo"), scenes(action="capture", index=0)])
            connection.at(2.2)
            seen.append(connection.call("/api/state")[1]["scenes"]["capturing"])
            seen.append(connection.call("/api/learn", {"action": "start"})[0])

        self.run_bridge(World(ports=gone(1.2, 1.5), chain=[STOCK, ROOM], length=2.6), client)
        self.assertEqual(seen, [200, 200, None, 200])


class Learned(SimulatedCase):
    def test_a_range_still_needed_stays_when_another_effect_moves_into_the_place(self):
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        self.install({PRESET: [mapping(1, 14, 0, 50, effect=f"{PONG:08X}"),
                               mapping(2, 3, 0, 20, cc=21, effect=f"{ROOM:08X}")]},
                     {PRESET: learned((1, 14, 0, 100), (2, 3, 0, 29))})
        script = [(1.0, lambda world: world.swap_effect(1, CHORUS)),           # Pong Delay replaced on the pedal
                  (1.3, lambda world: world.rearrange([0, 2, 1]))]             # BrghtRoom into its place
        self.run_bridge(World(script, chain=[STOCK, PONG, ROOM], length=1.8))
        kept = json.loads(bridge.MEASUREMENTS.read_text(encoding="utf-8"))["patches"][PRESET]
        self.assertEqual(sorted(kept), ["1/14", "1/3"])
        stored = yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))["mappings"][PRESET]
        self.assertEqual(sorted((entry["slot"], entry["param"]) for entry in stored), [(1, 3), (1, 14)])
        with self.devices(World()):
            bridge.Bridge()                                                    # the next start takes it


if __name__ == "__main__":
    unittest.main()
