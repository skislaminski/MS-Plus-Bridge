"""The bridge on a computer without screen (Raspberry Pi): settings from one file, a status light
instead of a display, and a safe mode in which nothing is sent."""
import contextlib
import io
import json
import shutil
import socket
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import yaml

import bridge
import config_schema as cs
import export
import status_led
import zoom_sysex as zs
from tests.sim import (CONTROLLER_PORT, KEYBOARD_PORT, ZOOM_PORT, Client, SimulatedCase, World, cc, keyboard,
                       learned, mapping, note_off, note_on)

import mido

PRESET = "0/94"
KEYSYNTH, STOCK = 0x07000F61, 0x01000020
SYNTH = {"keyboard": KEYBOARD_PORT, "channel": 1, "effect_id": KEYSYNTH}
EVERYTHING = lambda seconds: [ZOOM_PORT, CONTROLLER_PORT, KEYBOARD_PORT]
K = zs.key_for


class RecordingLed:
    """Stands in for the status light and notes every state it was asked to show."""

    def __init__(self):
        self.states, self.problem, self.closed = [], None, False

    def show(self, state: str) -> None:
        if not self.states or self.states[-1] != state:
            self.states.append(state)

    def close(self) -> None:
        self.closed = True


class HeadlessCase(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.card = self.folder / "card"
        self.card.mkdir()
        self.led = RecordingLed()
        self.journal = ""
        for target, name, value in ((bridge, "HEADLESS", False), (bridge, "_wait_forever", lambda: None),
                                    (bridge, "SYNTH_QUERY_SECONDS", 0.2)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(status_led.StatusLed, "find", classmethod(lambda cls: self.led))
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_card(self) -> dict:
        """Sets an installation up and exports it, as the user does on the computer."""
        self.install({PRESET: [mapping()]}, {PRESET: learned((0, 2, 0, 100))}, synth=SYNTH)
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        bridge.CONTROLS.write_text(json.dumps({"controls": {"KeySynth/Rate": 1}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            export.main(["--to", str(self.card)])
        return yaml.safe_load((self.card / cs.FILE_NAME).read_text(encoding="utf-8"))

    def forget_the_installation(self) -> None:
        """On the Pi there is only the settings file: none of the computer's own files exist."""
        for path in (bridge.CONFIG, bridge.APPROVALS, bridge.MEASUREMENTS, bridge.CONTROLS):
            path.unlink(missing_ok=True)

    def run_headless(self, world: World, stick=None) -> None:
        journal = io.StringIO()
        with self.devices(world), contextlib.redirect_stderr(journal):
            bridge.run_headless(self.card, stick)
        self.journal = journal.getvalue()


class FromTheSettingsFile(HeadlessCase):
    def test_it_plays_as_on_the_computer_and_stores_nothing(self):
        self.write_card()
        self.forget_the_installation()
        before = sorted(path.name for path in self.folder.iterdir())
        world = World([(0.9, cc(127)), (1.0, note_on(45)), (1.1, keyboard(mido.Message("control_change", control=1, value=127))),
                       (1.2, note_off(45))], ports=EVERYTHING, chain=[STOCK, KEYSYNTH])
        self.run_headless(world)

        self.assertEqual([(slot, param, value) for _, slot, param, value in world.params],
                         [(0, 2, 100), (1, 2, K(45)), (1, 12, 100), (1, 2, 0)])   # Rate is knob 11
        self.assertEqual(self.led.states, ["waiting", "ready"])
        self.assertTrue(self.led.closed)
        self.assertEqual(world.sent[-1][1], "F0 52 00 6E 51 F7")        # edit mode off at the end
        # everything goes to the console (the service's journal), every message as hex
        self.assertIn("Settings exported 20", self.journal)
        self.assertIn("TX  F0 52 00 6E 64 20 00 01 02", self.journal)
        self.assertIn("RX  F0 52 00 6E 64 20 01", self.journal)
        self.assertIn("Ready:", self.journal)
        self.assertEqual(self.output, "")                               # nothing is printed a second time
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), before)   # no file was made

    def test_the_settings_cannot_be_changed_there(self):
        settings = self.write_card()
        instance = bridge.Bridge(settings)
        for change in (lambda: instance.save_mapping(PRESET, {"targets": [mapping(low=10)]}),
                       lambda: instance.delete_mapping(PRESET),
                       lambda: instance.learn("start"),
                       lambda: instance.synth_control("clear", "KeySynth/Rate")):
            with self.assertRaises(bridge.UserError) as refused:
                change()
            self.assertIn("come from a settings file", str(refused.exception))
        self.assertEqual(instance.controls, {"KeySynth/Rate": 1})

    def test_the_previous_file_is_used_when_the_current_one_is_broken(self):
        self.write_card()
        with contextlib.redirect_stdout(io.StringIO()):
            export.main(["--to", str(self.card)])                        # the first one is now the previous one
        (self.card / cs.FILE_NAME).write_text("mappings: [oops", encoding="utf-8")
        world = World([(1.0, note_on(45)), (1.1, note_off(45))], ports=EVERYTHING, chain=[KEYSYNTH])
        self.run_headless(world)
        self.assertEqual([value for *_, value in world.params], [K(45), 0])
        self.assertEqual(self.led.states, ["waiting", "fallback"])
        self.assertIn(f"Settings: {cs.FILE_NAME} is not a readable settings file.", self.journal)
        self.assertIn("this is the PREVIOUS file", self.journal)


class TheInterfaceOnThePi(HeadlessCase):
    """--listen: the interface for a phone in the same WLAN; changes go into the settings file."""

    def run_listening(self, world: World, client) -> None:
        with socket.socket() as probe_socket:
            probe_socket.bind(("127.0.0.1", 0))
            port = probe_socket.getsockname()[1]
        failures = []

        def guarded() -> None:
            try:
                client(Client(world, port))
            except Exception as error:              # reported in the main thread below
                failures.append(error)

        thread = threading.Thread(target=guarded, daemon=True)
        journal = io.StringIO()
        with self.devices(world), contextlib.redirect_stderr(journal), mock.patch.object(bridge, "LISTEN_PORT", port):
            thread.start()
            bridge.run_headless(self.card, None, listen=True)
        thread.join(5)
        self.journal = journal.getvalue()
        if failures:
            raise failures[0]

    def test_what_is_changed_there_is_written_into_the_settings_file(self):
        self.write_card()
        self.forget_the_installation()
        seen = {}

        def client(connection):
            connection.at(1.0)
            seen["state"] = connection.call("/api/state")[1]
            seen["mapping"] = connection.call("/api/mapping", {"key": PRESET, "targets": [
                {"slot": 0, "param": 2, "min": 10, "max": 90, "invert": True, "curve": "log", "name": "Wah"}]})
            seen["clear"] = connection.call("/api/synth", {"action": "clear", "knob": "KeySynth/Rate"})
            seen["download"] = connection.call("/api/settings-file")
            seen["foreign"] = connection.call("/api/delete", {"key": PRESET},
                                              headers={"Origin": "http://elsewhere.example"})

        world = World([(1.6, note_on(45)), (1.7, note_off(45))], ports=EVERYTHING, chain=[STOCK, KEYSYNTH],
                      length=2.0)
        self.run_listening(world, client)

        self.assertEqual([entry["name"] for entry in seen["state"]["chain_effects"]], ["BlackOpt", "KeySynth"])
        self.assertEqual(seen["mapping"][0], 200)
        self.assertEqual(seen["clear"][0], 200)
        self.assertEqual(seen["foreign"][0], 403)                        # another website: refused
        written = yaml.safe_load((self.card / cs.FILE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(cs.validate(written), [])
        self.assertEqual(written["mappings"][PRESET], [{"slot": 0, "param": 2, "min": 10, "max": 90, "invert": True,
                                                        "curve": "log", "name": "Wah", "effect": f"{STOCK:08X}"}])
        self.assertEqual(written["controls"], {})
        self.assertTrue((self.card / cs.PREVIOUS_NAME).exists())         # the one before, to fall back to
        self.assertIn("changed in its interface", (self.card / cs.FILE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(seen["download"][0], 400)                      # it holds the password of the Pi's WLAN
        self.assertIn("Interface: http://", self.journal)

    def test_an_import_goes_into_the_file_as_a_whole(self):
        # the presets were written, then the keyboard's controllers were checked against the switches
        # that were there and refused: half the file was taken
        settings = self.write_card()
        settings["approvals"]["messages"]["switch_effect"] = "2026-10-01 00:00:00"
        settings["effects"] = {"1": {"cc": 20}}
        self.forget_the_installation()
        instance = bridge.Bridge(settings, self.card)
        exported = {"format": bridge.EXPORT_FORMAT, "presets": {"0/5": {"targets": [mapping(0, 2, 0, 50)], "learned": []}},
                    "keyboard": {"controls": {"KeySynth/Rate": 20}}, "effects": {"2": {"cc": 21}}}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(instance.import_data(exported), ["006"])
        written = yaml.safe_load((self.card / cs.FILE_NAME).read_text(encoding="utf-8"))
        self.assertEqual((sorted(written["mappings"]), written["controls"], written["effects"]),
                         (["0/5", PRESET], {"KeySynth/Rate": 20}, {"2": {"cc": 21}}))

    def test_in_safe_mode_the_interface_says_why(self):
        seen = {}

        def client(connection):
            connection.at(0.5)
            seen["state"] = connection.call("/api/state")[1]
            seen["change"] = connection.call("/api/delete", {"key": PRESET})

        with mock.patch.object(bridge, "_wait_forever", lambda: time.sleep(1.0)):
            self.run_listening(World(length=1.0), client)
        self.assertIn("SAFE MODE", seen["state"]["unavailable"])
        self.assertIn("there is no usable settings file", seen["change"][1]["error"])


class FromAStick(HeadlessCase):
    """A settings file on a USB stick is taken over into the card's folder; the stick can go again."""

    def setUp(self) -> None:
        super().setUp()
        self.media = self.folder / "media"                           # where sticks are mounted
        self.stick = self.media / "STICK"
        self.stick.mkdir(parents=True)
        self.elsewhere = self.folder / "elsewhere"                   # a file waiting to be put on the stick
        self.elsewhere.mkdir()
        patcher = mock.patch.object(bridge, "STICK_SECONDS", 0.1)
        patcher.start()
        self.addCleanup(patcher.stop)

    def export_other_settings(self, folder: Path) -> str:
        """The same installation with the pedal's range narrowed to 20–70, exported into the folder."""
        self.install({PRESET: [mapping(low=20, high=70)]}, {PRESET: learned((0, 2, 0, 100))}, synth=SYNTH)
        with contextlib.redirect_stdout(io.StringIO()):
            export.main(["--to", str(folder)])
        return (folder / cs.FILE_NAME).read_text(encoding="utf-8")

    def values(self, world: World) -> list:
        return [value for _, slot, param, value in world.params if (slot, param) == (0, 2)]

    def test_its_settings_are_taken_over_and_stay_when_it_is_removed(self):
        self.write_card()
        old = (self.card / cs.FILE_NAME).read_text(encoding="utf-8")
        new = self.export_other_settings(self.stick)
        self.forget_the_installation()
        world = World([(0.9, cc(127))], ports=EVERYTHING, chain=[STOCK, KEYSYNTH])
        self.run_headless(world, self.media)
        self.assertEqual(self.values(world), [70])
        self.assertIn("Settings taken over from the USB stick – it can be removed", self.journal)
        self.assertEqual((self.card / cs.FILE_NAME).read_text(encoding="utf-8"), new)
        self.assertEqual((self.card / cs.PREVIOUS_NAME).read_text(encoding="utf-8"), old)   # the old one is kept

        shutil.rmtree(self.stick)                                    # the stick is pulled out
        world = World([(0.9, cc(127))], ports=EVERYTHING, chain=[STOCK, KEYSYNTH])
        self.run_headless(world, self.media)
        self.assertEqual(self.values(world), [70])
        self.assertNotIn("USB stick", self.journal)

    def test_a_stick_put_in_while_it_runs_starts_it_again_with_its_settings(self):
        self.write_card()
        self.export_other_settings(self.elsewhere)
        self.forget_the_installation()
        script = [(0.9, cc(127)), (1.3, lambda world: shutil.copy(self.elsewhere / cs.FILE_NAME, self.stick)),
                  (2.9, cc(126)), (3.0, cc(127))]
        world = World(script, ports=EVERYTHING, chain=[STOCK, KEYSYNTH], length=3.4)
        self.run_headless(world, self.stick)                         # the stick's own folder works as well
        self.assertEqual(self.values(world)[0], 100)
        self.assertEqual(self.values(world)[-1], 70)
        self.assertEqual(self.led.states, ["waiting", "ready", "waiting", "ready"])
        self.assertEqual(self.journal.count("A USB stick with other settings was put in"), 1)
        self.assertEqual(world.count("F0 52 00 6E 51 F7"), 2)        # edit mode was switched off in between

    def test_a_file_that_breaks_a_rule_is_said_once_and_not_used(self):
        self.write_card()
        self.forget_the_installation()
        (self.stick / cs.FILE_NAME).write_text("mappings: [oops", encoding="utf-8")
        world = World([(0.9, cc(127))], ports=EVERYTHING, chain=[STOCK, KEYSYNTH], length=1.6)
        self.run_headless(world, self.media)
        self.assertEqual(self.values(world), [100])                  # the card's settings
        self.assertEqual(self.journal.count("on the USB stick, not used"), 1)
        self.assertEqual(self.led.states, ["waiting", "ready"])      # and it was not started again for it

    def test_without_a_file_on_the_card_the_stick_is_enough(self):
        self.write_card()
        (self.card / cs.FILE_NAME).replace(self.stick / cs.FILE_NAME)
        self.forget_the_installation()
        world = World([(0.9, cc(127))], ports=EVERYTHING, chain=[STOCK, KEYSYNTH])
        self.run_headless(world, self.media)
        self.assertEqual(self.values(world), [100])
        self.assertTrue((self.card / cs.FILE_NAME).exists())

    def test_on_a_card_that_cannot_be_written_the_stick_has_to_stay_in(self):
        self.write_card()
        self.export_other_settings(self.stick)
        self.forget_the_installation()
        world = World([(0.9, cc(127))], ports=EVERYTHING, chain=[STOCK, KEYSYNTH], length=1.6)
        with mock.patch.object(cs, "store", side_effect=OSError(30, "Read-only file system")):
            self.run_headless(world, self.media)
        self.assertEqual(self.values(world), [70])
        self.assertIn("could not be stored (Read-only file system): leave the stick in", self.journal)
        self.assertEqual(self.led.states, ["waiting", "ready"])      # it does not start again and again

    def test_safe_mode_ends_when_a_stick_with_settings_is_put_in(self):
        answers = iter([False, False, True])
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(bridge.safe_mode(self.led, "there is no usable settings file", lambda: next(answers)))
        self.assertEqual(self.led.states, ["safe"])


class SafeMode(HeadlessCase):
    def assert_safe(self, world: World, reason: str, sent=()) -> None:
        self.assertEqual([text for _, text in world.sent], list(sent))
        self.assertEqual(self.led.states[-1], "safe")
        self.assertTrue(self.led.closed)
        self.assertIn("SAFE MODE – nothing is sent to the pedal", self.journal)
        self.assertIn(reason, self.journal)

    def test_without_a_settings_file_nothing_is_sent(self):
        world = World(ports=EVERYTHING, chain=[KEYSYNTH])
        self.run_headless(world)
        self.assert_safe(world, "there is no usable settings file")
        self.assertIn(f"Settings: {cs.FILE_NAME} cannot be read", self.journal)

    def test_settings_that_break_a_rule_are_not_used(self):
        cases = {
            "leaves its learned range": lambda data: data["mappings"][PRESET][0].update(max=127),
            "not approved: set_param": lambda data: data["approvals"]["messages"].pop("set_param"),
            "the backup of the patches has not been confirmed": lambda data: data["approvals"].update(backup_confirmed=None),
            "is not a message kind of the bridge": lambda data: data["approvals"]["messages"].update(erase="2026-10-07 10:00:00"),
            "looks like raw bytes": lambda data: data["mappings"][PRESET][0].update(name="F0 52 00 6E 50 F7"),
            "Unknown setting": lambda data: data["bridge"].update(turbo=True),
        }
        for reason, change in cases.items():
            with self.subTest(reason):
                data = self.write_card()
                change(data)
                (self.card / cs.FILE_NAME).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
                self.led.states.clear()
                world = World(ports=EVERYTHING, chain=[KEYSYNTH])
                self.run_headless(world)
                self.assert_safe(world, reason)

    def test_another_pedal_than_the_settings_are_for(self):
        data = self.write_card()
        data["zoom"]["device_id"] = 0x6D                                 # the simulated pedal answers with 6E
        (self.card / cs.FILE_NAME).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        world = World([(1.0, note_on(45))], ports=EVERYTHING, chain=[KEYSYNTH])
        self.run_headless(world)
        self.assert_safe(world, "Device ID 6E does not match", sent=["F0 7E 7F 06 01 F7"])   # the question only


class Light(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self.leds = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.leds, ignore_errors=True))

    def make(self, name: str = "ACT") -> Path:
        folder = self.leds / name
        folder.mkdir()
        (folder / "trigger").write_text("none rc-feedback [mmc0] timer heartbeat\n")
        (folder / "brightness").write_text("0\n")
        return folder

    def test_it_blinks_and_gives_the_led_back(self):
        folder = self.make()
        levels = []

        class Watched(status_led.StatusLed):
            def _write(self, level):
                levels.append(level)
                super()._write(level)

        led = Watched.find(self.leds)
        self.assertEqual((led.folder, (folder / "trigger").read_text()), (folder, "none"))
        led.show("safe")
        time.sleep(0.7)
        led.close()
        self.assertGreaterEqual(levels.count(1), 2)
        self.assertGreaterEqual(levels.count(0), 2)
        self.assertEqual((folder / "trigger").read_text(), "mmc0")      # what it did before
        with self.assertRaises(ValueError):
            led.show("disco")

    def test_older_systems_call_the_led_led0(self):
        folder = self.make("led0")
        led = status_led.StatusLed.find(self.leds)
        self.assertEqual(led.folder, folder)
        led.close()

    def test_without_an_led_nothing_happens(self):
        led = status_led.StatusLed.find(self.leds)
        self.assertIsNone(led.folder)
        led.show("ready")
        led.close()

    def test_an_led_that_may_not_be_written_is_left_alone(self):
        folder = self.make()
        (folder / "trigger").chmod(0o444)
        self.addCleanup((folder / "trigger").chmod, 0o644)
        led = status_led.StatusLed(folder)
        self.assertIsNone(led.folder)
        self.assertIn("ACT", led.problem)
        led.show("ready")
        led.close()
