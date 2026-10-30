"""The editor: every knob of the effects in the chain, with what it holds (read from the patch,
then as the pedal reports and confirms it), set from the interface within the range its effect
file gives, for the effect the slider was made for. The Key knobs of a synth effect are notes:
not set from here. Also what the review of 2026-10-20 found around it: values meant for an
effect that is gone never reach the one in its place, and notes follow a synth effect that moved."""
import json
import os
import unittest
from unittest import mock

import mido

import bridge
import effect_names
import synth_controls
import zoom_sysex as zs
from tests.sim import ID, KEYBOARD_PORT, SimulatedCase, World, cc, mapping, note_off, note_on, patch_dump
from tests.test_synth import EVERYTHING, KEYBOARD_ONLY

PRESET = "0/94"
STOCK, CHORUS, ROOM = 0x01000020, 0x06000020, 0x0B000020      # BlackOpt, ChorusOne, BrghtRoom (Decay 0–29)
KEYSYNTH, KEYFM, HOGI = 0x07000F61, 0x09000F72, 0x09000F83
SYNTH = {"keyboard": KEYBOARD_PORT, "channel": 1, "effect_id": KEYSYNTH}
K = zs.key_for


def sent(world: World, without=()) -> list:
    """(slot, param, value) of every "set parameter" to the pedal, but parameter 1 (another effect)."""
    return [(slot, param, value) for _, slot, param, value in world.params
            if param != zs.EFFECT_PARAM and (slot, param) not in without]


class Values(unittest.TestCase):
    def test_every_knob_is_read_from_the_patch(self):
        dump = zs.parse_patch_dump(zs.zoom_body(patch_dump([(STOCK, 70), (ROOM, 12)]), ID))
        self.assertEqual([effect.values[:2] for effect in dump.effects], [(70, 0), (12, 0)])
        self.assertEqual(len(dump.effects[0].values), len(zs.EDTB_WIDTHS))
        # 12 bits for knobs 1–5 and 9, 8 for the others: a block with all bits set
        self.assertEqual(zs.edtb_values((1 << 192) - 1)[:12], (4095,) * 5 + (255,) * 3 + (4095,) + (255,) * 3)

    def test_key_knobs_only_where_a_patch_keeps_12_bits(self):
        knobs = tuple((name, 100) for name in "ABCDE") + (("Key", zs.KEY_MAX),) + (("F", 100),) * 6
        with self.assertRaises(ValueError):                             # knob 6: 8 bits
            zs.SynthEffect("Late", 0xF99, knobs)
        self.assertEqual(zs.SynthEffect("Ninth", 0xF99, knobs[:5] + knobs[6:9] + knobs[5:6] + knobs[9:]).key_param, 10)

    def test_assignments_to_mix_are_left_out_not_refused(self):
        stored = {"KeySynth/Mix": 7, "Mix": 8, "KeySynth/Rate": 9}
        self.assertEqual(synth_controls.problems(stored), [])
        self.assertEqual(synth_controls.read(stored), {"KeySynth/Rate": 9})

    def test_level_is_back_on_every_synth_effect(self):
        stored = {"KeySynth/Level": 7, "Level": 8}
        self.assertEqual(synth_controls.problems(stored), [])
        # named without an effect it is the Level of every one (since 2026-10-22); KeySynth's own wins
        self.assertEqual(synth_controls.read(stored), {"KeySynth/Level": 7, "KeySweep/Level": 8, "KeyFM/Level": 8,
                                                       "KeyPoly/Level": 8, "KeyWave/Level": 8, "KeyBand/Level": 8,
                                                       "Pluck/Level": 8})

    def test_a_build_with_mix_keeps_it_and_leaves_level_out(self):
        older = {"09000F61": {"name": "KeySynth", "knobs": [[name, top] for name, top in zs.SYNTH_KNOBS[:-1]]
                              + [["Mix", 100]]}}
        try:
            with mock.patch.dict(os.environ, {"MS_PLUS_BRIDGE_SYNTHS": "table"}), \
                    mock.patch.object(effect_names, "_table", older):
                zs.use_installed_synths()
                stored = {"KeySynth/Mix": 7, "KeySynth/Level": 8}
                self.assertEqual(synth_controls.problems(stored), [])
                self.assertEqual(synth_controls.read(stored), {"KeySynth/Mix": 7})
        finally:
            zs.use_installed_synths()


    def test_plucks_bright_is_its_tone_since_0_12(self):
        # the same knob, renamed: an assignment goes along, to whichever name the build played has
        stored = {"Pluck/Bright": 7, "KeyBand/Tone": 8}
        self.assertEqual(synth_controls.problems(stored), [])
        self.assertEqual(synth_controls.read(stored), {"Pluck/Tone": 7, "KeyBand/Tone": 8})
        older = {"09000F8F": {"name": "Pluck", "knobs": [[name, top] for name, top in zs.PLUCK_KNOBS[:4]]
                              + [["Bright", 100]] + [[name, top] for name, top in zs.PLUCK_KNOBS[5:9]]}}   # 0.11
        try:
            with mock.patch.dict(os.environ, {"MS_PLUS_BRIDGE_SYNTHS": "table"}), \
                    mock.patch.object(effect_names, "_table", older):
                zs.use_installed_synths()
                self.assertEqual(synth_controls.read({"Pluck/Tone": 7}), {"Pluck/Bright": 7})   # a 0.11 on the pedal
                self.assertEqual(synth_controls.read({"Pluck/Bright": 7}), {"Pluck/Bright": 7})
        finally:
            zs.use_installed_synths()


class EditorCase(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", "change_effect", "move_effect")

    def edit(self, *calls, world=None, synth=None, mappings=None, at=1.0, until=1.5) -> list:
        """Calls /api/effects with each (data …) in turn from `at`; a "knob" call is (slot, param, value,
        effect id). Returns the replies and the state at `until`."""
        self.install(mappings or {PRESET: [mapping(0, 2)]}, synth=synth)
        known = json.loads(bridge.MEASUREMENTS.read_text(encoding="utf-8"))
        known["effects"] = [f"{effect:08X}" for effect in (HOGI, ROOM, CHORUS)]
        bridge.MEASUREMENTS.write_text(json.dumps(known), encoding="utf-8")
        seen = []

        def client(connection):
            connection.at(at)
            for call in calls:
                if isinstance(call, tuple):
                    slot, param, value, effect = call
                    call = {"action": "knob", "slot": slot, "param": param, "value": value, "effect": f"{effect:08X}"}
                elif isinstance(call, float):
                    connection.at(call)
                    continue
                seen.append(connection.call("/api/effects", call)[:2])
            connection.at(until)
            seen.append(connection.call("/api/state")[1])

        world = world or World(chain=[STOCK, ROOM], length=until + 0.3)
        self.run_bridge(world, client)
        self.world = world
        return seen


class Editing(EditorCase):
    def test_a_knob_is_set_and_shown_once_the_pedal_has_it(self):
        reply, state = self.edit((1, 3, 29, ROOM))
        self.assertEqual(reply, (200, {"result": None}))
        self.assertEqual(sent(self.world, without={(0, 2)}), [(1, 3, 29)])
        self.assertEqual(state["chain_effects"][1]["values"]["3"], 29)
        self.assertEqual(state["chain_effects"][1]["values"]["2"], 0)      # from the patch

    def test_a_value_the_pedal_does_not_take_goes_once_more_and_is_not_shown(self):
        world = World(chain=[STOCK, ROOM], length=1.8)
        world.accept = lambda slot, param, value: (slot, param) != (1, 3)
        *_, state = self.edit((1, 3, 29, ROOM), world=world)
        self.assertEqual(sent(world, without={(0, 2)}), [(1, 3, 29), (1, 3, 29)])
        self.assertEqual(state["chain_effects"][1]["values"]["3"], 0)

    def test_a_knob_turned_on_the_pedal_shows(self):
        world = World([(1.2, lambda world: world.knob(0, 4, 66))], chain=[STOCK, ROOM], length=1.8)
        *_, state = self.edit(world=world)
        self.assertEqual(state["chain_effects"][0]["values"]["4"], 66)

    def test_only_within_the_range_of_the_effect_file(self):
        replies = self.edit((1, 3, 30, ROOM), (1, 6, 1, ROOM), (6, 2, 1, ROOM), (1, 3, -1, ROOM))[:-1]
        self.assertEqual(replies[0], (400, {"error": "Decay goes from 0 to 29."}))
        self.assertEqual(replies[1], (400, {"error": "Effect 2 has no knob 5 that is known."}))
        self.assertEqual([status for status, _ in replies], [400] * 4)
        self.assertEqual(sent(self.world, without={(0, 2)}), [])

    def test_only_for_the_effect_the_slider_was_made_for(self):
        reply, _ = self.edit((1, 3, 20, CHORUS))
        self.assertEqual(reply, (400, {"error": "Effect 2 is BrghtRoom now: nothing was sent."}))
        self.assertEqual(sent(self.world, without={(0, 2)}), [])

    def test_the_key_knobs_of_any_synth_effect_are_notes(self):
        world = World(chain=[KEYSYNTH, KEYFM], ports=KEYBOARD_ONLY, length=1.8)
        replies = self.edit((0, 2, 500, KEYSYNTH), (1, 2, 500, KEYFM), (0, 3, 9, KEYSYNTH), world=world, synth=SYNTH)
        self.assertEqual([status for status, _ in replies[:-1]], [400, 400, 200])
        self.assertEqual(replies[1], (400, {"error": "Key is a note: the keyboard plays it."}))
        self.assertEqual(sent(world), [(0, 3, 9)])
        self.assertEqual([effect["keys"] for effect in replies[-1]["chain_effects"]], [[2], [2]])

    def test_a_scene_recalled_after_the_editor_sets_its_value_again(self):
        scene = {"name": "Solo", "values": [{"slot": 1, "param": 3, "value": 20}], "effects": {},
                 "chain": {"2": f"{ROOM:08X}"}}
        recall = {"action": "recall", "index": 0}
        self.install()
        bridge.CONTROLS.write_text(json.dumps({"controls": {}, "scenes": {PRESET: [scene]}}), encoding="utf-8")
        seen = []

        def client(connection):
            connection.at(1.0)
            connection.call("/api/scenes", recall)
            connection.at(1.4)
            seen.append(connection.call("/api/effects", {"action": "knob", "slot": 1, "param": 3, "value": 5,
                                                         "effect": f"{ROOM:08X}"})[0])
            connection.at(1.8)
            connection.call("/api/scenes", recall)

        world = World(chain=[STOCK, ROOM], length=2.2)
        self.run_bridge(world, client)
        self.assertEqual((seen, sent(world)), ([200], [(1, 3, 20), (1, 3, 5), (1, 3, 20)]))

    def test_a_value_set_while_a_scene_is_captured_is_part_of_it(self):
        self.install()
        seen = []

        def client(connection):
            scenes = lambda **data: connection.call("/api/scenes", data)[0]
            connection.at(1.0)
            seen.extend([scenes(action="add", name="Solo"), scenes(action="capture", index=0)])
            seen.append(connection.call("/api/effects", {"action": "knob", "slot": 1, "param": 3, "value": 20,
                                                         "effect": f"{ROOM:08X}"})[0])
            connection.at(1.3)
            seen.append(scenes(action="apply", index=0))
            seen.append(connection.call("/api/state")[1]["scenes"]["list"][0]["values"])

        self.run_bridge(World(chain=[STOCK, ROOM], length=1.6), client)
        self.assertEqual(seen, [200, 200, 200, 200, [{"slot": 1, "param": 3, "value": 20, "away": None}]])

    def test_after_a_program_change_passed_through_nothing_of_the_old_chain_is_set(self):
        world = World([(1.0, mido.Message("program_change", program=3))], chain=[STOCK, ROOM], length=1.4)
        self.install({PRESET: [mapping(0, 2)]}, passthrough=True)
        seen = []

        def client(connection):
            connection.at(1.02)
            seen.append(connection.call("/api/effects", {"action": "knob", "slot": 1, "param": 3, "value": 29,
                                                         "effect": f"{ROOM:08X}"})[:2])

        self.run_bridge(world, client)
        self.assertEqual(seen, [(400, {"error": "The pedal has not reported the effects of this preset yet."})])
        self.assertEqual(sent(world), [])


class GoneEffects(EditorCase):
    def test_a_pedal_value_waiting_for_a_replaced_effect_never_reaches_the_new_one(self):
        # the expression pedal sets ChorusOne · Tone; ChorusOne is replaced by HOGi while it moves
        on_chorus = {PRESET: [mapping(1, 4, effect=f"{CHORUS:08X}")]}
        world = World([(1.1, cc(64))], chain=[STOCK, CHORUS], length=2.0)
        replies = self.edit({"action": "change", "slot": 1, "effect": f"{HOGI:08X}"}, world=world,
                            mappings=on_chorus, until=1.8)
        self.assertEqual(replies[0], (200, {"result": None}))
        self.assertEqual(world.chain, [STOCK, HOGI])
        self.assertEqual(sent(world), [])
        self.assertEqual(replies[-1]["targets"][0]["away"], "ChorusOne · Tone")

    def test_editor_values_wait_no_longer_than_the_effect_change_and_are_refused_meanwhile(self):
        world = World([(0.9, lambda world: setattr(world, "patches", False))],    # it stops answering the query
                      chain=[STOCK, ROOM], length=3.8)
        replies = self.edit({"action": "change", "slot": 1, "effect": f"{HOGI:08X}"}, (1, 3, 29, ROOM),
                            world=world, until=3.5)
        self.assertEqual(replies[1], (400, {"error": "An effect is being changed or moved: try again in a moment."}))
        self.assertEqual(sent(world, without={(0, 2)}), [])
        self.assertIn("is not known", replies[-1]["effects"]["change"]["result"]["text"])


class SynthMoved(EditorCase):
    def moved(self, script) -> World:
        world = World(script, ports=EVERYTHING, chain=[KEYSYNTH, ROOM], length=2.0)
        self.edit({"action": "move", "slot": 0, "to": 1}, world=world, synth=SYNTH, until=1.8)
        return world

    def test_a_key_released_while_the_synth_moves_closes_the_note_where_it_is_now(self):
        world = self.moved([(0.9, note_on(45)), (1.1, note_off(45))])
        self.assertEqual(world.chain, [ROOM, KEYSYNTH])
        self.assertEqual(sent(world), [(0, 2, K(45)), (1, 2, 0)])     # nothing to BrghtRoom's PRE DLY

    def test_a_note_played_while_the_synth_moves_sounds_where_it_is_now(self):
        world = self.moved([(0.9, note_on(45)), (1.1, note_on(50))])
        self.assertEqual(sent(world), [(0, 2, K(45)), (1, 2, K(50)), (1, 2, 0)])   # the last: the bridge stops


if __name__ == "__main__":
    unittest.main()
