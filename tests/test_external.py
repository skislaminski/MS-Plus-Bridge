"""An external sequencer (an MPC) next to the keyboard: its notes play the synth effect on a
channel of its own, and its MIDI clock is the one the arpeggiator and the step sequencer follow.

Run against the simulated devices from sim.py.
"""
import contextlib
import io
import json
import unittest

import mido
import yaml

import bridge
import config_schema as cs
import zoom_sysex as zs
from tests.sim import CONTROLLER_PORT, EXTERNAL_PORT, KEYBOARD_PORT, ZOOM_PORT, World, external, note_off, note_on
from tests.test_arp import ArpCase, clock, keys, ticks
from tests.test_poly import chord
from tests.test_synth import KEYSYNTH, SYNTH, SynthCase, sent

K = zs.key_for
WITH_MPC = dict(SYNTH, external=EXTERNAL_PORT, external_channel=3)
BOTH = lambda seconds: [ZOOM_PORT, KEYBOARD_PORT, EXTERNAL_PORT]
MPC_ONLY = lambda seconds: [ZOOM_PORT, EXTERNAL_PORT]


def played(note: int, on: bool = True, channel: int = 2):
    """A script action: a note of the external sequencer, on its channel 3 unless said otherwise."""
    return external(mido.Message("note_on" if on else "note_off", channel=channel, note=note, velocity=100))


def mpc_ticks(start: float, count: int, period: float = 0.02) -> list:
    return [(start + period * index, external(mido.Message("clock"))) for index in range(count)]


class Notes(SynthCase):
    def test_its_notes_play_the_synth_next_to_the_keyboard(self):
        script = [(1.0, played(40)), (1.1, played(40, on=False)),
                  (1.2, played(45, channel=0)), (1.25, played(45, on=False, channel=0)),     # not its channel
                  (1.3, note_on(50)), (1.4, note_off(50)),                                    # the keyboard
                  (1.5, played(52)), (1.55, note_on(55)), (1.6, note_off(55)), (1.7, played(52, on=False))]
        state = {}
        world = World(script + [(1.45, lambda world: state.update(self.instance.state()))], ports=BOTH,
                      chain=[KEYSYNTH])
        self.install(synth=WITH_MPC)
        self.run_bridge(world)
        # the last key wins whoever played it, and letting it go returns to the note still held
        self.assertEqual(sent(world), [(0, 2, K(40)), (0, 2, 0), (0, 2, K(50)), (0, 2, 0),
                                       (0, 2, K(52)), (0, 2, K(55)), (0, 2, K(52)), (0, 2, 0)])
        self.assertIn(f"Ready: {KEYBOARD_PORT} + {EXTERNAL_PORT}", self.output)
        self.assertEqual({key: state["synth"][key] for key in ("keyboard", "external", "playable")},
                         {"keyboard": True, "external": True, "playable": True})
        self.assertEqual(state["devices"]["external"]["port"], EXTERNAL_PORT)

    def test_it_is_enough_on_its_own_and_may_come_and_go(self):
        ports = lambda seconds: [ZOOM_PORT] + ([] if 1.25 < seconds < 1.9 else [EXTERNAL_PORT])
        script = [(1.0, played(40)),                                   # still down when it is unplugged
                  (2.3, played(47)), (2.4, played(47, on=False))]
        world = World(script, ports=ports, chain=[KEYSYNTH], length=2.7)
        self.install(synth=dict(WITH_MPC, keyboard=""))                 # no keyboard at all
        bridge.CONFIG.write_text(bridge.CONFIG.read_text(encoding="utf-8").replace(
            f"chocolate: {CONTROLLER_PORT}", "chocolate: ''"), encoding="utf-8")
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 2, K(40)), (0, 2, 0), (0, 2, K(47)), (0, 2, 0)])
        self.assertIn(f"Ready: {EXTERNAL_PORT} →", self.output)
        # the last controller gone, the session stays with the pedal
        self.assertNotIn("Connection lost", self.output)
        self.assertEqual(self.output.count(bridge.NO_CONTROLLER), 1)
        self.assertEqual(self.output.count("Ready:"), 1)

    def test_unplugged_next_to_a_keyboard_it_leaves_no_note_hanging(self):
        ports = lambda seconds: [ZOOM_PORT, KEYBOARD_PORT] + ([] if 1.25 < seconds < 1.9 else [EXTERNAL_PORT])
        script = [(1.0, played(40)), (1.6, note_on(50)), (1.7, note_off(50)),
                  (2.3, played(47)), (2.4, played(47, on=False))]
        world = World(script, ports=ports, chain=[KEYSYNTH], length=2.7)
        self.install(synth=WITH_MPC)
        self.run_bridge(world)
        self.assertEqual(sent(world), [(0, 2, K(40)), (0, 2, 0), (0, 2, K(50)), (0, 2, 0), (0, 2, K(47)), (0, 2, 0)])
        self.assertIn(f"{EXTERNAL_PORT} is gone", self.output)
        self.assertIn(f"{EXTERNAL_PORT} connected", self.output)
        self.assertEqual(self.output.count("Ready:"), 1)               # the keyboard kept the bridge going


class Clock(ArpCase):
    def test_the_arpeggiator_steps_with_its_clock_and_not_with_the_keyboards(self):
        self.settings(clock="midi", rate="1/16")
        script = ([self.on(0.9)] + chord(0.95, 48, 52, 55)
                  + ticks(0.95, 40, period=0.007)                       # a clock at the keyboard's port: not this one
                  + [(1.0, external(mido.Message("start")))] + mpc_ticks(1.0, 30)
                  + [(1.6, external(mido.Message("stop")))])
        state = {}
        world = World(script + [(1.3, lambda world: state.update(self.instance.state()["synth"]["arp"]))],
                      ports=BOTH, chain=[KEYSYNTH], length=1.9)
        self.install(synth=WITH_MPC)
        self.run_bridge(world)
        # a note every 6 of its clocks (120 ms), sounding half of it: the keys are the keyboard's
        self.assert_times(keys(world), [(1.0, K(48)), (1.06, 0), (1.12, K(52)), (1.18, 0), (1.24, K(55)), (1.30, 0),
                                        (1.36, K(48)), (1.42, 0), (1.48, K(52)), (1.54, 0)], tolerance=0.02)
        self.assertLess(abs(state["clock_bpm"] - 125), 3)

    def test_without_an_external_sequencer_the_keyboards_clock_still_counts(self):
        self.settings(clock="midi", rate="1/16", gate=100)
        script = [self.on(0.9)] + chord(0.95, 48, 52) + [(1.0, clock("start"))] + ticks(1.0, 13)
        world = World(script, ports=BOTH, chain=[KEYSYNTH], length=1.5)
        self.install(synth=SYNTH)
        self.run_bridge(world)
        self.assertEqual([value for _, value in keys(world)][:3], [K(48), K(52), K(48)])

    def test_its_sequence_feeds_the_arpeggiator_too(self):
        self.settings(clock="midi", rate="1/16", gate=100)
        script = ([self.on(0.9), (0.95, played(48)), (0.95, played(55))]
                  + [(1.0, external(mido.Message("start")))] + mpc_ticks(1.0, 19))
        world = World(script, ports=MPC_ONLY, chain=[KEYSYNTH], length=1.5)
        self.install(synth=WITH_MPC)
        self.run_bridge(world)
        self.assertEqual([value for _, value in keys(world)][:4], [K(48), K(55), K(48), K(55)])


class Setup(SynthCase):
    def bridge_alone(self) -> "bridge.Bridge":
        with contextlib.redirect_stdout(io.StringIO()):
            return bridge.Bridge()

    def test_it_is_chosen_in_the_interface_also_where_config_yaml_never_had_it(self):
        seen = {}

        def client(connection):
            connection.at(1.2)
            seen["before"] = connection.call("/api/state")[1]["devices"]["external"]
            seen["chosen"] = connection.call("/api/devices", {"external": EXTERNAL_PORT})[:2]
            connection.at(2.3)
            seen["after"] = connection.call("/api/state")[1]["devices"]["external"]
        self.install(synth=SYNTH)
        text = bridge.CONFIG.read_text(encoding="utf-8")
        self.assertNotIn("external", text)                             # a config.yaml from before there was one
        world = World([(2.5, played(40, channel=7)), (2.6, played(40, on=False, channel=7))], ports=BOTH,
                      chain=[KEYSYNTH], length=3.0)
        self.run_bridge(world, client)
        self.assertEqual(seen["before"], {"name": "", "port": None, "choices": [KEYBOARD_PORT, EXTERNAL_PORT]})
        self.assertEqual(seen["chosen"], (200, {"result": None}))
        self.assertEqual(seen["after"]["port"], EXTERNAL_PORT)
        self.assertEqual(yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))["synth"],
                         dict(SYNTH, external=EXTERNAL_PORT))
        self.assertEqual(sent(world), [(0, 2, K(40)), (0, 2, 0)])      # any channel until one is set
        self.assertIn(f"external sequencer “{EXTERNAL_PORT}”", self.output)

    def test_the_same_port_cannot_be_both(self):
        self.install(synth=SYNTH)
        instance = self.bridge_alone()
        with self.assertRaises(bridge.UserError) as refused:
            instance.devices({"external": KEYBOARD_PORT})
        self.assertIn("two different ports", str(refused.exception))
        self.install(synth=dict(SYNTH, external=KEYBOARD_PORT.lower()))
        self.assertIn("keyboard and external must be two different ports", self.start_fails())
        self.install(synth=dict(WITH_MPC, external_channel=17))
        self.assertIn("external_channel must be 0–16", self.start_fails())

    def test_the_settings_file_takes_it_with_or_without_a_keyboard(self):
        self.install(synth=WITH_MPC)
        bridge.CONTROLS.write_text(json.dumps({"controls": {"KeyFM/Rate": 1}}), encoding="utf-8")
        settings = bridge.gather_settings()
        self.assertEqual(cs.validate(settings), [])
        alone = dict(settings, synth=dict(settings["synth"], keyboard=""))
        self.assertEqual(cs.validate(alone), [])
        self.assertEqual(alone["controls"], {"KeyFM/Rate": 1})               # the controllers go along for it as well
        for wrong, word in ((dict(external=KEYBOARD_PORT), "two different ports"),
                            (dict(external_channel=17), "external_channel"),
                            (dict(external=5), "external must be a text")):
            with self.subTest(word):
                self.assertIn(word, " ".join(cs.validate(dict(settings, synth=dict(settings["synth"], **wrong)))))
        del alone["approvals"]["messages"]["query_patch"]               # it plays the synth: the patch query is needed
        self.assertIn("query_patch", " ".join(cs.validate(alone)))


if __name__ == "__main__":
    unittest.main()
