"""Which MIDI ports the devices are: choosing them in the interface, and profiles for a second bridge.

Run against the simulated devices from sim.py.
"""
import contextlib
import io
import json
import shutil
import socket
import unittest
from unittest import mock

import mido
import rtmidi
import yaml

import bridge
import export
import probe
import zoom_sysex as zs
from tests.sim import CONTROLLER_PORT, KEYBOARD_PORT, PROJECT, ZOOM_PORT, SimulatedCase, World, cc, learned, \
    mapping, note_off, note_on
from tests.test_synth import KEYSYNTH, SYNTH, SynthCase

K = zs.key_for
IDENTITY = "F0 7E 7F 06 01 F7"
OTHER_KEYS = "Other Keys"                       # a second keyboard; the simulation reads it from the controller's queue
WITH_TWO_KEYBOARDS = lambda seconds: [ZOOM_PORT, KEYBOARD_PORT, OTHER_KEYS]
EXAMPLE = (PROJECT / "config.example.yaml").read_text(encoding="utf-8")


class PortNames(unittest.TestCase):
    def test_the_exact_name_wins_over_a_name_that_only_contains_it(self):
        names = ["ZOOM MS Plus Series", "ZOOM MS Plus Series 2", "UAC-232 ZOOM UAC-232 MIDI I/O Port"]
        self.assertEqual(zs.find_port(names, "ZOOM MS Plus Series"), "ZOOM MS Plus Series")
        self.assertEqual(zs.find_port(names, "zoom ms plus series 2"), "ZOOM MS Plus Series 2")
        self.assertEqual(zs.find_port(names, "UAC-232"), "UAC-232 ZOOM UAC-232 MIDI I/O Port")
        for needle in ("ZOOM MS", "ZOOM", "Chocolate"):
            with self.subTest(needle), self.assertRaises(zs.PortError):
                zs.find_port(names, needle)


class Unplugging(SimulatedCase):
    def test_a_device_vanishing_while_the_ports_are_listed_is_a_lost_connection(self):
        """Seen on the real setup: rtmidi counted the ports, one was unplugged, and asking for its
        name raised InvalidPortError. The bridge ended with a traceback."""
        raised = []

        def ports(seconds):
            if seconds > 1.2 and not raised:
                raised.append(seconds)
                raise rtmidi.InvalidPortError("MidiInCore::getPortName: the 'portNumber' argument (6) is invalid.")
            return [ZOOM_PORT, CONTROLLER_PORT]
        world = World([(1.0, cc(20)), (2.6, cc(100))], ports=ports, length=3.0)
        self.install({"0/94": [mapping()]})
        self.run_bridge(world)                                       # ends with the world, not with an error
        self.assertEqual(len(raised), 1)
        self.assertIn("Connection lost: MidiInCore::getPortName", self.output)
        self.assertEqual(self.output.count("Ready:"), 2)
        self.assertEqual([value for *_, value in world.params], [16, 79])   # it bridges again afterwards
        self.assertEqual(world.count("F0 52 00 6E 51 F7"), 2)        # edit mode was switched off in between


class ConfigText(unittest.TestCase):
    def test_values_change_and_everything_else_stays(self):
        changed = bridge.set_config_values(EXAMPLE, {("ports", "zoom"): "ZOOM MS Plus Series 2",
                                                     ("ports", "chocolate"): 'SINCO #1 "x"',
                                                     ("synth", "keyboard"): "Über Keys"})
        before, after = EXAMPLE.split("\n"), changed.split("\n")
        different = [(old, new) for old, new in zip(before, after) if old != new]
        self.assertEqual((len(after), len(different)), (len(before), 3))
        self.assertEqual([new for _, new in different], [
            '  zoom: "ZOOM MS Plus Series 2"',
            '  chocolate: "SINCO #1 \\"x\\""     # your MIDI controller, e.g. "SINCO" for the M-Vave Chocolate Plus',
            '  keyboard: "Über Keys"           # substring of the keyboard\'s port name; empty = none'])
        cfg = yaml.safe_load(changed)
        self.assertEqual((cfg["ports"], cfg["synth"]["keyboard"], cfg["zoom"]),
                         ({"zoom": "ZOOM MS Plus Series 2", "chocolate": 'SINCO #1 "x"'}, "Über Keys",
                          {"device_id": None, "firmware": None}))
        # a value that held a # in quotes is replaced whole, and the comment after it is kept
        again = bridge.set_config_values(changed, {("ports", "chocolate"): ""})
        self.assertIn('  chocolate: ""                   # your MIDI controller', again)
        with self.assertRaises(KeyError):
            bridge.set_config_values(EXAMPLE, {("ports", "keyboard"): "x"})


class Choosing(SynthCase):
    def bridge_alone(self) -> "bridge.Bridge":
        """A bridge that is not running: for requests that are refused or need no devices."""
        with contextlib.redirect_stdout(io.StringIO()):
            return bridge.Bridge()

    def refused(self, instance, request: dict) -> str:
        with self.assertRaises(bridge.UserError) as refused, contextlib.redirect_stdout(io.StringIO()):
            instance.devices(request)
        return str(refused.exception)

    def test_the_interface_lists_the_ports_and_what_is_chosen(self):
        seen = {}

        def client(connection):
            connection.at(1.3)
            seen.update(connection.call("/api/state")[1])
            seen["page"] = connection.call("/")[1]
        self.install(synth=SYNTH)
        self.run_bridge(World(ports=WITH_TWO_KEYBOARDS, chain=[KEYSYNTH], length=1.6), client)
        # as the pedal only ports named like one are offered, and those are not offered as a controller
        self.assertEqual(seen["devices"], {
            "pedal": {"name": ZOOM_PORT, "port": ZOOM_PORT, "choices": [ZOOM_PORT]},
            "keyboard": {"name": KEYBOARD_PORT, "port": KEYBOARD_PORT, "choices": [KEYBOARD_PORT, OTHER_KEYS]},
            "external": {"name": "", "port": None, "choices": [KEYBOARD_PORT, OTHER_KEYS]},     # none set up
            "expression": {"name": CONTROLLER_PORT, "port": None,                       # chosen, but not connected
                           "choices": [KEYBOARD_PORT, OTHER_KEYS]}})
        self.assertIsNone(seen["profile"])
        self.assertIn(b"Devices", seen["page"])

    def test_on_linux_the_loop_back_and_the_bridges_own_ports_are_not_offered(self):
        self.install(synth=SYNTH)
        instance = self.bridge_alone()
        instance.port_names = ([ZOOM_PORT, "Midi Through:Midi Through Port-0 14:0", KEYBOARD_PORT,
                                "RtMidiOut Client:RtMidi output 129:0"], [ZOOM_PORT])
        self.assertEqual(instance._devices()["keyboard"]["choices"], [KEYBOARD_PORT])

    def test_another_keyboard_is_chosen_and_played_at_once(self):
        seen = {}

        def client(connection):
            connection.at(1.3)
            seen["chosen"] = connection.call("/api/devices", {"keyboard": OTHER_KEYS})[:2]
            connection.at(2.4)
            seen["devices"] = connection.call("/api/state")[1]["devices"]
        script = [(1.0, note_on(40)), (1.1, note_off(40)),
                  (2.6, mido.Message("note_on", note=45, velocity=100)), (2.7, mido.Message("note_off", note=45)),
                  (2.9, note_on(50)), (3.0, note_off(50))]            # the first keyboard is not listened to any more
        world = World(script, ports=WITH_TWO_KEYBOARDS, chain=[KEYSYNTH], length=3.4)
        self.install(synth=SYNTH)
        before = bridge.CONFIG.read_text(encoding="utf-8")
        self.run_bridge(world, client)
        self.assertEqual(seen["chosen"], (200, {"result": None}))
        self.assertEqual(seen["devices"]["keyboard"]["name"], OTHER_KEYS)
        self.assertEqual(seen["devices"]["keyboard"]["port"], OTHER_KEYS)
        self.assertEqual([value for _, slot, param, value in world.params if (slot, param) == (0, 2)],
                         [K(40), 0, K(45), 0])
        self.assertEqual(world.count(IDENTITY), 2)                   # connected anew, once
        self.assertIn("Devices: pedal “ZOOM MS Plus Series”, keyboard “Other Keys”, expression controller “SINCO” "
                      "– connecting anew", self.output)
        self.assertIn("Ready: Other Keys → ZOOM MS Plus Series", self.output)
        after = bridge.CONFIG.read_text(encoding="utf-8")
        self.assertEqual([line for line in after.split("\n") if line not in before.split("\n")],
                         ['  keyboard: "Other Keys"'])
        self.assertEqual(yaml.safe_load(after)["synth"], dict(SYNTH, keyboard=OTHER_KEYS))

    def test_the_pedal_and_the_expression_controller_are_chosen_the_same_way(self):
        self.install(synth=SYNTH)
        instance = self.bridge_alone()
        with contextlib.redirect_stdout(io.StringIO()):
            instance.devices({"pedal": "ZOOM MS Plus Series 2", "expression": ""})
        cfg = yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(cfg["ports"], {"zoom": "ZOOM MS Plus Series 2", "chocolate": ""})
        self.assertEqual((instance.cfg["ports"], instance.status), (cfg["ports"], "Connecting…"))
        self.assertEqual(self.bridge_alone().cfg["ports"], cfg["ports"])     # and so it starts the next time
        instance.status = "Ready"
        instance.devices({"pedal": "ZOOM MS Plus Series 2"})                 # as it is already: nothing happens
        self.assertEqual(instance.status, "Ready")

    def test_what_cannot_be_right_is_refused_and_changes_nothing(self):
        self.install(synth=SYNTH)
        before = bridge.CONFIG.read_text(encoding="utf-8")
        instance = self.bridge_alone()
        for request, reason in (({}, "Choose a port."), ({"other": "x"}, "Choose a port."),
                                ({"keyboard": 7}, "Choose a port."), ({"pedal": ["x"]}, "Choose a port."),
                                ({"pedal": "x" * 201}, "Choose a port."),
                                ({"pedal": ""}, "The pedal needs a port.")):
            with self.subTest(request):
                self.assertEqual(self.refused(instance, request), reason)
        self.assertEqual(bridge.CONFIG.read_text(encoding="utf-8"), before)
        self.assertEqual(instance.cfg["ports"]["zoom"], ZOOM_PORT)

    def test_none_of_the_controllers_leaves_the_pedal_alone(self):
        """No keyboard and no foot controller: the bridge connects to the pedal only, whose preset
        is edited in the interface (2026-10-22)."""
        self.install(synth=SYNTH)
        instance = self.bridge_alone()
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            instance.devices({"keyboard": "", "expression": ""})
        cfg = yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual((cfg["ports"]["chocolate"], cfg["synth"]["keyboard"]), ("", ""))
        self.assertEqual((instance.synth, instance.status), (None, "Connecting…"))
        self.assertIn("Devices: pedal “ZOOM MS Plus Series”, no keyboard, no expression controller – connecting anew",
                      printed.getvalue())
        self.assertIsNone(self.bridge_alone().synth)                 # and so it starts the next time

    def test_an_expression_controller_that_was_never_measured_is_not_taken(self):
        self.install(synth=SYNTH)
        cfg = yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))
        cfg["ports"]["chocolate"] = ""
        cfg["expression"].update(cc=None, channel=None)
        bridge.CONFIG.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        self.assertIn("has not been measured yet", self.refused(self.bridge_alone(), {"expression": CONTROLLER_PORT}))

    def test_a_keyboard_chosen_for_the_first_time_brings_its_settings(self):
        self.install(synth=dict(SYNTH, keyboard=""))
        bridge.CONTROLS.write_text(json.dumps({"controls": {"Vib": 1}, "arp": {"gate": 80},
                                               "seq": {"steps": [60, "rest"]}}), encoding="utf-8")
        instance = self.bridge_alone()
        self.assertEqual((instance.synth, instance.controls, instance.state()["synth"]), (None, {}, None))
        with contextlib.redirect_stdout(io.StringIO()):
            instance.devices({"keyboard": KEYBOARD_PORT})
        self.assertEqual(instance.synth["keyboard"], KEYBOARD_PORT)
        self.assertEqual((instance.controls["KeyFM/Vib"], instance.arp_settings["gate"], instance.seq_steps),
                         (1, 80, [60, "rest"]))

    def test_a_keyboard_needs_its_section_to_be_right(self):
        self.install(synth={"keyboard": "", "channel": 17})
        self.assertIn("channel and external_channel must be 0–16",
                      self.refused(self.bridge_alone(), {"keyboard": KEYBOARD_PORT}))
        self.install(synth={"keyboard": "", "channel": 1})           # no effect id: the bridge knows the effects
        instance = self.bridge_alone()
        with contextlib.redirect_stdout(io.StringIO()):
            instance.devices({"keyboard": KEYBOARD_PORT})
        self.assertEqual(instance.synth["keyboard"], KEYBOARD_PORT)
        cfg = yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))
        del cfg["synth"]                                             # a config.yaml from before the keyboard synth
        bridge.CONFIG.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        self.assertIn("no “synth:” section", self.refused(self.bridge_alone(), {"keyboard": KEYBOARD_PORT}))
        self.assertIsNone(self.bridge_alone().state()["devices"]["keyboard"])

    def test_a_bridge_run_from_a_settings_file_keeps_its_devices(self):
        self.install(synth=SYNTH)
        self.assertEqual(export.cs.validate(export.gather()), [])
        instance = bridge.Bridge(export.gather())
        self.assertIn("come from a settings file", self.refused(instance, {"keyboard": OTHER_KEYS}))


class WholeExport(SynthCase):
    """“Export…” → Backup and “Import…” carry the keyboard's part too; the settings file carries everything."""
    KEYBOARD = {"controls": {"KeySynth/Vib": 1, "KeyFM/Index": 1, "Arp": 14}, "arp": {"rate": "1/8T", "gate": 80},
                "seq": {"steps": [48, "rest", 50, "tie"]}}

    def installation(self, keyboard=None, mappings=None) -> "bridge.Bridge":
        self.install(mappings, {"0/94": learned((0, 2, 0, 100))} if mappings else None, synth=SYNTH)
        bridge.CONTROLS.write_text(json.dumps(keyboard or {"controls": {}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            return bridge.Bridge()

    def imported(self, instance, data) -> str:
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            instance.import_data(data)
        return printed.getvalue()

    def test_export_all_holds_the_keyboard_part_and_import_brings_it_back(self):
        exported = self.installation(self.KEYBOARD, {"0/94": [mapping(low=20, high=70)]}).export_data()
        self.assertEqual(exported["keyboard"], self.KEYBOARD)
        self.assertEqual(json.loads(json.dumps(exported)), exported)             # it is what the file will hold
        fresh = self.installation()                                             # another computer
        fresh.arp_on = True
        self.assertIn("Imported: the keyboard's controllers", self.imported(fresh, exported))
        self.assertEqual((fresh.controls, fresh.seq_steps, fresh.arp_on),
                         (self.KEYBOARD["controls"], self.KEYBOARD["seq"]["steps"], False))
        self.assertEqual((fresh.arp_settings["rate"], fresh.arp_settings["gate"], fresh.arp_settings["mode"]),
                         ("1/8T", 80, "up"))
        self.assertEqual(json.loads(bridge.CONTROLS.read_text(encoding="utf-8")), self.KEYBOARD)
        self.assertEqual([asdict["min"] for asdict in fresh.export_data()["presets"]["0/94"]["targets"]], [20])

    def test_one_preset_is_exported_without_it_and_a_file_without_it_leaves_the_keyboard_alone(self):
        instance = self.installation(self.KEYBOARD, {"0/94": [mapping()]})
        one = instance.export_data("0/94")
        self.assertNotIn("keyboard", one)
        self.assertNotIn("keyboard's", self.imported(instance, one))
        self.assertEqual(instance.controls, self.KEYBOARD["controls"])

    def test_the_keyboard_part_alone_is_a_file_too(self):
        exported = self.installation(self.KEYBOARD).export_data()               # no expression assignment at all
        self.assertEqual((exported["presets"], exported["keyboard"]), ({}, self.KEYBOARD))
        fresh = self.installation()
        self.imported(fresh, exported)
        self.assertEqual(fresh.seq_steps, [48, "rest", 50, "tie"])
        with self.assertRaises(bridge.UserError):
            fresh.import_data({"format": bridge.EXPORT_FORMAT, "presets": {}})   # nothing in it

    def test_an_older_keyboard_part_gives_each_knob_to_every_synth_effect_that_has_it(self):
        instance = self.installation(self.KEYBOARD)
        self.imported(instance, {"format": bridge.EXPORT_FORMAT, "presets": {}, "keyboard": {"controls": {"Cutoff": 74}}})
        self.assertEqual(instance.controls, {"KeySweep/Cutoff": 74, "KeyPoly/Cutoff": 74})

    def test_a_keyboard_part_that_cannot_be_right_changes_nothing(self):
        instance = self.installation(self.KEYBOARD)
        for wrong in ([], {"swing": 1}, {"controls": {"Nope": 5}}, {"controls": {"Vib": 64}}, {"controls": {"Vib": "1"}},
                      {"controls": {"Vib": 1, "Rate": 1}}, {"controls": {"KeyFM/Index": 2, "KeyFM/Fdbk": 2}},
                      {"controls": {"KeyFM/Index": 2, "Arp": 2}}, {"controls": {"KeyFM/Wave": 3}},
                      {"controls": [1]}, {"arp": {"tempo": 999}}, {"seq": {"steps": ["C3"]}}):
            with self.subTest(wrong), self.assertRaises(bridge.UserError) as refused:
                instance.import_data({"format": bridge.EXPORT_FORMAT, "presets": {}, "keyboard": wrong})
            self.assertTrue(str(refused.exception).startswith("Keyboard settings: "))
        self.assertEqual((instance.controls, instance.seq_steps), (self.KEYBOARD["controls"], [48, "rest", 50, "tie"]))
        self.assertEqual(json.loads(bridge.CONTROLS.read_text(encoding="utf-8")), self.KEYBOARD)

    def test_the_interface_hands_out_the_settings_file_for_the_pi(self):
        seen = {}

        def client(connection):
            connection.at(1.2)
            seen["file"] = connection.call("/api/settings-file")
            seen["page"] = connection.call("/")[1]
        self.install({"0/94": [mapping()]}, {"0/94": learned((0, 2, 0, 100))}, synth=SYNTH)
        bridge.CONTROLS.write_text(json.dumps(self.KEYBOARD), encoding="utf-8")
        self.run_bridge(World(ports=WITH_TWO_KEYBOARDS, chain=[KEYSYNTH], length=1.5), client)
        status, body, headers = seen["file"]
        self.assertEqual(status, 200)
        self.assertIn('filename="expression-bridge.yaml"', headers["Content-Disposition"])
        settings = yaml.safe_load(body)
        self.assertEqual(export.cs.validate(settings), [])                      # the Pi would take it
        self.assertEqual({name: settings[name] for name in ("controls", "arp", "seq")}, self.KEYBOARD)
        self.assertEqual((settings["ports"]["zoom"], settings["synth"]["keyboard"], list(settings["mappings"])),
                         (ZOOM_PORT, KEYBOARD_PORT, ["0/94"]))
        self.assertEqual(dict(settings, exported_at=""), dict(export.gather(), exported_at=""))   # as export.py writes it
        self.assertIn(b"File for the Pi", seen["page"])

    def test_incomplete_settings_are_not_handed_out(self):
        instance = self.installation(self.KEYBOARD)
        self.approve(*bridge.REQUIRED_KINDS)                                    # the patch query is not approved
        with self.assertRaises(bridge.UserError) as refused:
            instance.settings_file()
        self.assertIn("The settings are not complete yet", str(refused.exception))
        self.assertIn("query_patch", str(refused.exception))


class Profiles(SynthCase):
    def setUp(self) -> None:
        super().setUp()
        for name, value in (("HOME", self.folder), ("PROFILE", None)):     # use_profile() sets them
            patcher = mock.patch.object(bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_profile_keeps_its_own_settings(self):
        bridge.use_profile("second")
        home = self.folder / "profiles" / "second"
        self.assertTrue(home.is_dir())
        self.assertEqual((bridge.HOME, bridge.PROFILE), (home, "second"))
        self.assertEqual([bridge.CONFIG, bridge.APPROVALS, bridge.MEASUREMENTS, bridge.CONTROLS],
                         [home / name for name in ("config.yaml", "approvals.json", "measurements.json", "controls.json")])
        self.assertIn("config.yaml is missing", self.start_fails())  # it is set up like the first one
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")          # its own approvals
        self.install(synth=SYNTH)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bridge.Bridge().state()["profile"], "second")
        self.assertFalse((self.folder / "config.yaml").exists())

    def test_a_name_that_could_leave_the_folder_is_refused(self):
        for name in ("", "../x", "a/b", "a b", ".hidden", "x" * 33):
            with self.subTest(name), self.assertRaises(SystemExit):
                bridge.use_profile(name)
        self.assertFalse((self.folder / "profiles").exists())

    def test_the_setup_script_sets_a_profile_up(self):
        shutil.copy(PROJECT / "config.example.yaml", self.folder)
        self._close_log()                                            # the script sets up its own log
        with self.devices(World()), mock.patch.object(probe.sys, "argv", ["probe.py", "--profile", "second", "ports"]):
            probe.main()
        home = self.folder / "profiles" / "second"
        self.assertEqual((home / "config.yaml").read_text(encoding="utf-8"), EXAMPLE)
        self.assertTrue((home / "logs").is_dir())
        self.assertFalse((self.folder / "config.yaml").exists())
        self.assertIn("Created config.yaml", self.output)
        self.assertIn(ZOOM_PORT, self.output)

    def test_two_bridges_cannot_share_the_port_of_the_interface(self):
        self.install(synth=SYNTH)
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            cfg = yaml.safe_load(bridge.CONFIG.read_text(encoding="utf-8"))
            cfg["ui"] = {"port": taken.getsockname()[1]}
            bridge.CONFIG.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
            self._close_log()                                        # main() sets up its own log
            with mock.patch.object(bridge.signal, "signal"), self.devices(World()), \
                    self.assertRaises(SystemExit) as stopped:
                bridge.main([])
        self.assertIn("give this one a port of its own", str(stopped.exception))


if __name__ == "__main__":
    unittest.main()
