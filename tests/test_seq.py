"""The step sequencer: its pattern logic, and the bridge playing and recording a pattern.

Run against the simulated devices from sim.py. As in test_arp.py most cases use 1/16 at 150 BPM,
so a step is 100 ms and, with gate 50, a note sounds 50 ms.
"""
import contextlib
import io
import json
import unittest

import yaml

import bridge
import config_schema as cs
import sequencer
import zoom_sysex as zs
from sequencer import REST, TIE
from tests.sim import World, note_off, note_on
from tests.test_arp import FAST, ArpCase, clock, keys, ticks
from tests.test_poly import chord
from tests.test_synth import KEYBOARD_ONLY, KEYSYNTH, SYNTH, control

K = zs.key_for


class Pattern(unittest.TestCase):
    def run_steps(self, seq, key, steps, latch=False):
        return [seq.next_step(key, latch) for _ in range(steps)]

    def test_a_pattern_as_text(self):
        steps = [48, 52, REST, 55, TIE, 42, 70, 0]
        self.assertEqual(sequencer.parse("C3 E3 - G3 _ f#2, Bb4 c-1"), steps)
        self.assertEqual(sequencer.text(steps), "C3 E3 - G3 _ F#2 A#4 C-1")
        self.assertEqual(sequencer.parse(sequencer.text(steps)), steps)
        self.assertEqual((sequencer.parse("  "), sequencer.text([])), ([], ""))
        self.assertEqual(sequencer.parse("G9"), [127])
        for wrong in ("H3", "C", "C10", "G#9", "Cb-1", "--", "rest", "C3E3"):
            with self.subTest(wrong), self.assertRaises(ValueError) as refused:
                sequencer.parse("C3 " + wrong)
            self.assertIn(f"“{wrong}” is not a step", str(refused.exception))
        self.assertEqual(len(sequencer.parse("C3 " * sequencer.MAX_STEPS)), sequencer.MAX_STEPS)
        with self.assertRaises(ValueError) as refused:
            sequencer.parse("C3 " * (sequencer.MAX_STEPS + 1))
        self.assertEqual(str(refused.exception), "A pattern has at most 64 steps; this one has 65.")

    def test_what_a_file_may_hold(self):
        self.assertEqual(sequencer.problems({}), [])
        self.assertEqual(sequencer.problems({"steps": [0, 127, REST, TIE]}), [])
        wrong = "seq: every step must be a note from 0 to 127, “rest” or “tie”."
        for steps in ([128], [-1], [True], [60.0], ["C3"], [None], [[60]], [{"note": 60}]):
            with self.subTest(steps):
                self.assertEqual(sequencer.problems({"steps": steps}), [wrong])
        too_long = ["seq: steps must be a list of at most 64 steps."]
        self.assertEqual(sequencer.problems({"steps": [60] * 65}), too_long)
        self.assertEqual(sequencer.problems({"steps": "C3 E3"}), too_long)
        self.assertEqual(sequencer.problems({"swing": 50}), ["seq: unknown setting “swing”."])
        self.assertEqual(sequencer.problems([60, 62]), ["seq must be a section with the pattern."])
        self.assertEqual(sequencer.read({"steps": [60, REST]}), [60, REST])
        for stored in (None, {}, {"steps": [60, "C3"]}, {"steps": [60], "swing": 50}, [60]):
            with self.subTest(stored):
                self.assertEqual(sequencer.read(stored), [])

    def test_notes_rests_and_ties(self):
        seq = sequencer.Sequencer([48, TIE, REST, 52, 55])
        # (note, tied): C sounds on into its tie, the rest is silent, and the pattern goes round
        self.assertEqual(self.run_steps(seq, 48, 6),
                         [(48, True), (48, False), (None, False), (52, False), (55, False), (48, True)])

    def test_the_key_moves_the_pattern_from_its_first_note(self):
        seq = sequencer.Sequencer([REST, 60, 64, 55])            # the first note is C4, after a rest
        self.assertEqual([note for note, _ in self.run_steps(seq, 60, 4)], [None, 60, 64, 55])
        self.assertEqual([note for note, _ in self.run_steps(seq, 62, 4)], [None, 62, 66, 57])
        self.assertEqual([note for note, _ in self.run_steps(seq, 48, 4)], [None, 48, 52, 43])

    def test_a_note_moved_out_of_reach_is_a_rest(self):
        seq = sequencer.Sequencer([60, 72, TIE, 48], low_note=40, high_note=80)
        self.assertEqual(self.run_steps(seq, 60, 4), [(60, False), (72, True), (72, False), (48, False)])
        seq.reset()
        self.assertEqual(self.run_steps(seq, 70, 4), [(70, False), (None, False), (None, False), (58, False)])
        seq.reset()
        self.assertEqual(self.run_steps(seq, 50, 4), [(50, False), (62, True), (62, False), (None, False)])

    def test_a_tie_with_nothing_before_it_is_a_rest_and_one_at_the_start_ties_the_last_note(self):
        seq = sequencer.Sequencer([TIE, REST, TIE, 60])
        self.assertEqual(self.run_steps(seq, 60, 6),
                         [(None, False), (None, False), (None, False), (60, True), (60, False), (None, False)])

    def test_without_a_key_it_stops_and_starts_over(self):
        seq = sequencer.Sequencer([60, 62, 64])
        self.assertEqual([note for note, _ in self.run_steps(seq, 60, 2)], [60, 62])
        self.assertEqual((seq.step, seq.position), (1, 2))
        self.assertIsNone(seq.next_step(None, False))
        self.assertIsNone(seq.step)
        self.assertEqual(seq.next_step(60, False), (60, False))

    def test_latch_plays_on_from_the_key_pressed_last(self):
        seq = sequencer.Sequencer([60, 62, 64])
        self.assertIsNone(seq.next_step(None, True))             # no key yet
        self.assertEqual([note for note, _ in self.run_steps(seq, 65, 2)], [65, 67])
        self.assertEqual(seq.next_step(None, True), (69, False))     # the key is up: it goes on
        seq.pressed(60)                                          # a key tapped between two steps
        self.assertEqual([note for note, _ in self.run_steps(seq, None, 2, latch=True)], [60, 62])
        seq.clear()                                              # all notes off
        self.assertIsNone(seq.next_step(None, True))

    def test_a_pattern_without_notes_or_one_that_changes(self):
        steps = [REST, TIE]
        seq = sequencer.Sequencer(steps)
        self.assertIsNone(seq.next_step(60, False))
        steps[:] = [60, 62, 64, 65]
        self.assertEqual([note for note, _ in self.run_steps(seq, 60, 3)], [60, 62, 64])
        steps[:] = [60, 67]                                      # shorter while it plays
        self.assertEqual([note for note, _ in self.run_steps(seq, 60, 3)], [67, 60, 67])


class SeqCase(ArpCase):
    def pattern(self, *steps, controls=None, **arp) -> None:
        """controls.json: the sequencer's pattern, the controllers and the arpeggiator's settings."""
        bridge.CONTROLS.write_text(json.dumps({"controls": controls or {}, "arp": arp, "seq": {"steps": list(steps)}}),
                                   encoding="utf-8")

    def on(self, seconds: float, on: bool = True) -> tuple:
        return seconds, lambda world: self.instance.switch_seq(on)

    def do(self, seconds: float, **request) -> tuple:
        """A request from the interface while the bridge runs."""
        return seconds, lambda world: self.instance.seq(request)

    def play(self, script, length: float) -> World:
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=length)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        return world


class OwnClock(SeqCase):
    def test_a_key_plays_the_pattern_from_its_pitch_and_ends_it(self):
        self.pattern(48, 52, REST, 55, **FAST)
        world = self.play([self.on(0.9), (1.0, note_on(50)), (1.47, note_off(50))], 1.8)
        # written from C: D plays it a tone higher; the third step is a rest
        self.assert_times(keys(world), [(1.0, K(50)), (1.05, 0), (1.1, K(54)), (1.15, 0), (1.3, K(57)), (1.35, 0),
                                        (1.4, K(50)), (1.45, 0)])
        self.assertIn("Step sequencer on", self.output)
        self.assertNotIn("→ Key", self.output)                       # its notes are not printed

    def test_another_key_moves_the_pattern_without_starting_it_over(self):
        self.pattern(48, 52, 55, **FAST)
        script = [self.on(0.9), (1.0, note_on(48)), (1.12, note_on(50)), (1.14, note_off(48)), (1.37, note_off(50))]
        world = self.play(script, 1.7)
        self.assert_times(keys(world), [(1.0, K(48)), (1.05, 0), (1.1, K(52)), (1.15, 0), (1.2, K(57)), (1.25, 0),
                                        (1.3, K(50)), (1.35, 0)])

    def test_a_tied_note_sounds_through_its_tie(self):
        self.pattern(48, TIE, 52, **FAST)
        world = self.play([self.on(0.9), (1.0, note_on(48)), (1.27, note_off(48))], 1.6)
        self.assert_times(keys(world), [(1.0, K(48)), (1.15, 0), (1.2, K(52)), (1.25, 0)])

    def test_latch_plays_on_and_a_new_key_moves_it(self):
        self.pattern(48, 52, latch=True, **FAST)
        script = [self.on(0.9), (1.0, note_on(48)), (1.03, note_off(48)), (1.26, note_on(50)), (1.28, note_off(50)),
                  self.on(1.47, False)]
        world = self.play(script, 1.8)
        self.assert_times(keys(world), [(1.0, K(48)), (1.05, 0), (1.1, K(52)), (1.15, 0), (1.2, K(48)), (1.25, 0),
                                        (1.3, K(54)), (1.35, 0), (1.4, K(50)), (1.45, 0)])
        self.assertIn("Step sequencer off", self.output)

    def test_all_notes_off_stops_it_even_with_latch(self):
        self.pattern(48, 52, latch=True, gate=100, **FAST)
        world = self.play([self.on(0.9), (1.0, note_on(48)), (1.03, note_off(48)), (1.15, control(123, 0))], 1.6)
        self.assert_times(keys(world), [(1.0, K(48)), (1.1, K(52)), (1.15, 0)])

    def test_the_interface_shows_the_step_it_is_at(self):
        seen = []
        at = lambda seconds: (seconds, lambda world: seen.append(self.instance.session.seq_step()))
        self.pattern(48, REST, 52, **FAST)
        world = self.play([self.on(0.9), at(0.95), (1.0, note_on(48)), at(1.07), at(1.17), at(1.27),
                           (1.29, note_off(48)), at(1.45)], 1.6)
        self.assertEqual(seen, [None, 0, 1, 2, None])            # also in the gaps and during the rest
        self.assertEqual([value for _, value in keys(world)], [K(48), 0, K(52), 0])

    def test_without_a_pattern_nothing_plays(self):
        self.install(synth=SYNTH)
        world = World([self.on(0.9), (1.0, note_on(48)), (1.3, note_off(48))], ports=KEYBOARD_ONLY,
                      chain=[KEYSYNTH], length=1.5)
        self.run_bridge(world)
        self.assertEqual(keys(world), [])
        self.assertTrue(self.instance.seq_on)


class MidiClock(SeqCase):
    def test_it_steps_with_the_clock_and_stop_starts_the_pattern_over(self):
        self.pattern(48, 52, 55, clock="midi", rate="1/16")
        script = ([self.on(0.9), (0.95, note_on(48)), (1.0, clock("start"))] + ticks(1.0, 16)
                  + [(1.32, clock("stop")), (1.4, clock("start"))] + ticks(1.4, 4))
        world = self.play(script, 1.7)
        # a note every 6 clocks (120 ms); after Stop and Start it begins with C again, not with E
        self.assert_times(keys(world), [(1.0, K(48)), (1.06, 0), (1.12, K(52)), (1.18, 0), (1.24, K(55)), (1.30, 0),
                                        (1.4, K(48)), (1.47, 0)], tolerance=0.02)

    def test_changing_to_its_own_clock_starts_it_at_once(self):
        self.pattern(48, 52, clock="midi", latch=True, **FAST)
        script = [self.on(0.9), (0.95, note_on(50)), (1.0, note_off(50)),
                  (1.1, lambda world: self.instance.arp({"clock": "internal"})), self.on(1.27, False)]
        world = self.play(script, 1.6)
        self.assert_times(keys(world), [(1.1, K(50)), (1.15, 0), (1.2, K(54)), (1.25, 0)])


class Switching(SeqCase):
    def test_only_one_of_arpeggiator_and_sequencer_plays(self):
        seen = []
        both = lambda seconds: (seconds, lambda world: seen.append((self.instance.arp_on, self.instance.seq_on)))
        self.pattern(60, 62, gate=100, **FAST)
        script = ([(0.9, lambda world: self.instance.switch_arp(True)), both(0.95)] + chord(1.0, 48, 52)
                  + [self.on(1.15), both(1.17)] + chord(1.4, 48, 52, on=False)
                  + [(1.5, lambda world: self.instance.switch_arp(True)), both(1.52),
                     (1.55, lambda world: self.instance.switch_arp(False)), both(1.57)])
        world = self.play(script, 1.7)
        self.assertEqual(seen, [(True, False), (False, True), (True, False), (False, False)])
        # the chord note by note, then the pattern from E, the key pressed last (its first note is sounding already)
        self.assert_times(keys(world), [(1.0, K(48)), (1.1, K(52)), (1.25, K(54)), (1.35, K(52)), (1.45, 0)])
        said = [line.split("  ", 1)[1] for line in self.output.splitlines() if "Arpeggiator o" in line or "sequencer o" in line]
        self.assertEqual(said, ["Arpeggiator on", "Arpeggiator off", "Step sequencer on", "Step sequencer off",
                                "Arpeggiator on", "Arpeggiator off"])

    def test_a_controller_switches_it_on_and_off(self):
        self.pattern(48, controls={"Seq": 21}, **FAST)
        script = [(1.0, note_on(48)), (1.1, control(21, 127)), (1.27, control(21, 0)), (1.4, note_off(48))]
        world = self.play(script, 1.6)
        # the key as usual, then the pattern (its first note is sounding already), then the key again
        self.assert_times(keys(world), [(1.0, K(48)), (1.15, 0), (1.2, K(48)), (1.25, 0), (1.27, K(48)), (1.4, 0)])

    def test_its_switch_is_learned_like_a_knob(self):
        script = [(1.0, lambda world: self.instance.synth_control("learn", "Seq")), (1.1, control(22, 127))]
        self.play(script, 1.3)
        self.assertEqual(self.instance.controls, {"Seq": 22})
        self.assertFalse(self.instance.seq_on)                      # the move that was learned switches nothing
        self.assertIn("Step sequencer: controller 22 now switches it on and off", self.output)
        self.assertEqual(json.loads(bridge.CONTROLS.read_text(encoding="utf-8")), {"controls": {"Seq": 22}})

    def test_it_is_off_when_the_bridge_starts_and_a_bad_stored_pattern_is_not_played(self):
        bridge.CONTROLS.write_text(json.dumps({"controls": {}, "seq": {"steps": [60, "C3"]}}), encoding="utf-8")
        self.play([], 0.3)
        self.assertEqual((self.instance.seq_on, self.instance.seq_steps), (False, []))


class Recording(SeqCase):
    def test_a_pattern_is_recorded_from_the_keyboard_in_the_interface(self):
        seen = {}

        def client(connection):
            connection.at(1.0)
            seen["start"] = connection.call("/api/seq", {"record": "start"})[:2]
            connection.at(1.3)
            for action in ("rest", "tie", "back"):                # the tie is taken away again
                connection.call("/api/seq", {"record": action})
            connection.at(1.6)
            seen["recording"] = connection.call("/api/state")[1]["synth"]["seq"]["recording"]
            seen["stop"] = connection.call("/api/seq", {"record": "stop"})[:2]
            seen["state"] = connection.call("/api/state")[1]["synth"]["seq"]
            seen["page"] = connection.call("/")[1]
        script = [(1.1, note_on(60)), (1.15, note_off(60)), (1.2, note_on(64)), (1.25, note_off(64)),
                  (1.45, note_on(67)), (1.5, note_off(67))]
        world = World(script, ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.9)
        self.install(synth=SYNTH)
        self.run_bridge(world, client)
        self.assertEqual((seen["start"], seen["stop"]), ((200, {"result": None}),) * 2)
        self.assertEqual(seen["recording"], "C4 E4 - G4")
        self.assertEqual(seen["state"], {"on": False, "cc": None, "pattern": "C4 E4 - G4", "steps": 4, "max_steps": 64,
                                         "step": None, "recording": None})
        self.assertEqual(json.loads(bridge.CONTROLS.read_text(encoding="utf-8")),
                         {"controls": {}, "seq": {"steps": [60, 64, "rest", 67]}})
        self.assertIn("Step sequencer: recorded 4 steps", self.output)
        self.assertEqual([value for _, value in keys(world)], [K(60), 0, K(64), 0, K(67), 0])   # the keys sound meanwhile
        self.assertIn(b"Step sequencer", seen["page"])

    def test_switching_it_on_right_after_playing_the_steps_keeps_them(self):
        self.pattern(72, **FAST)
        script = [self.do(1.0, record="start"), (1.05, note_on(60)), (1.1, note_off(60)), (1.15, note_on(62)),
                  (1.2, note_off(62)), self.do(1.3, on=True), (1.4, note_on(48)), (1.62, note_off(48))]
        world = self.play(script, 1.9)
        self.assertEqual((self.instance.recording, self.instance.seq_steps), (None, [60, 62]))
        self.assertEqual([value for _, value in keys(world)], [K(60), 0, K(62), 0, K(48), 0, K(50), 0, K(48), 0])

    def test_recording_switches_both_off_and_without_a_note_the_pattern_stays(self):
        self.pattern(48, 52, **FAST)
        script = [self.on(0.9), self.do(1.0, record="start"), self.do(1.1, record="rest"), self.do(1.2, record="stop"),
                  self.do(1.3, record="start"), (1.35, note_on(60)), self.do(1.4, record="cancel")]
        self.play(script, 1.6)
        self.assertEqual((self.instance.seq_on, self.instance.recording, self.instance.seq_steps), (False, None, [48, 52]))
        self.assertIn("Step sequencer off", self.output)
        self.assertIn("Step sequencer: no note was played, the pattern stays as it was", self.output)

    def test_the_last_step_a_pattern_can_have_ends_the_recording(self):
        self.install(synth=SYNTH)
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            instance = bridge.Bridge()
            instance.recording = []
            for index in range(sequencer.MAX_STEPS + 3):
                instance.record_step(48 + index % 12)
        self.assertIsNone(instance.recording)
        self.assertEqual(instance.seq_steps, [48 + index % 12 for index in range(sequencer.MAX_STEPS)])
        self.assertIn("recorded 64 steps", printed.getvalue())

    def test_the_interface_writes_the_pattern_and_refuses_what_is_wrong(self):
        seen = {}

        def client(connection):
            connection.at(1.0)
            seen["pattern"] = connection.call("/api/seq", {"pattern": "c3 E3 - g3 _", "on": True})[:2]
            seen["state"] = connection.call("/api/state")[1]["synth"]["seq"]
            seen["wrong"] = [connection.call("/api/seq", body)[:2] for body in
                             ({"pattern": "C3 H3"}, {"pattern": ["C3"]}, {"on": "yes"}, {"record": "rest"},
                              {"record": "dance"}, {"record": ["start"]})]
            seen["empty"] = connection.call("/api/seq", {"pattern": ""})[:2]
        world = World(ports=KEYBOARD_ONLY, chain=[KEYSYNTH], length=1.5)
        self.install(synth=SYNTH)
        self.run_bridge(world, client)
        self.assertEqual(seen["pattern"], (200, {"result": None}))
        self.assertEqual({key: seen["state"][key] for key in ("on", "pattern", "steps")},
                         {"on": True, "pattern": "C3 E3 - G3 _", "steps": 5})
        step = "is not a step: write a note like C3 or F#2, - for a rest or _ for a tie."
        self.assertEqual(seen["wrong"], [(400, {"error": f"“H3” {step}"}),
                                         (400, {"error": "The pattern must be a text."}),
                                         (400, {"error": "on must be true or false."}),
                                         (400, {"error": "No pattern is being recorded."}),
                                         (400, {"error": "Unknown action."}),
                                         (400, {"error": "Unknown action."})])
        self.assertEqual(seen["empty"], (200, {"result": None}))     # an empty field: no pattern any more
        self.assertEqual(json.loads(bridge.CONTROLS.read_text(encoding="utf-8")), {"controls": {}})
        self.assertEqual(self.instance.seq_steps, [])


class SettingsFile(SeqCase):
    def test_the_pattern_travels_to_the_pi(self):
        import export
        self.install(synth=SYNTH)
        self.pattern(48, TIE, REST, 55, controls={"Seq": 21}, gate=75)
        settings = export.gather()
        self.assertEqual(cs.validate(settings), [])
        self.assertEqual((settings["seq"], settings["controls"]), ({"steps": [48, "tie", "rest", 55]}, {"Seq": 21}))
        self.assertEqual(yaml.safe_load(cs.dump(settings))["seq"], {"steps": [48, "tie", "rest", 55]})
        self.assertIn("seq: every step must be a note from 0 to 127, “rest” or “tie”.",
                      cs.validate(dict(settings, seq={"steps": [48, "C3"]})))

        instance = bridge.Bridge(settings)                          # as on the Pi
        self.assertEqual((instance.seq_steps, instance.arp_settings["gate"]), ([48, "tie", "rest", 55], 75))
        for request in ({"pattern": "C3 D3"}, {"record": "start"}):
            with self.subTest(request), self.assertRaises(bridge.UserError):
                instance.seq(request)                               # the pattern cannot be changed there
        with contextlib.redirect_stdout(io.StringIO()):
            instance.seq({"on": True})                              # but it can be switched
        self.assertTrue(instance.seq_on)

        bridge.CONTROLS.write_text(json.dumps({"controls": {}}), encoding="utf-8")
        self.assertNotIn("seq", export.gather())                    # no pattern: no section


if __name__ == "__main__":
    unittest.main()
