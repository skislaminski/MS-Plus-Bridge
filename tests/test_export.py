"""The settings file for a bridge without screen (Raspberry Pi): its rules, and writing it to the card."""
import contextlib
import copy
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import yaml

import bridge
import config_schema as cs
import export
import synth_controls
from tests.sim import KEYBOARD_PORT, SimulatedCase, learned, mapping

PRESET = "0/94"
SYNTH = {"keyboard": KEYBOARD_PORT, "channel": 1, "effect_id": 0x07000F61}


class ExportCase(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.card = self.folder / "card"
        self.card.mkdir()

    def export(self, *arguments: str) -> str:
        """Runs export.py; returns what it printed, or the message it stopped with."""
        printed = io.StringIO()
        try:
            with contextlib.redirect_stdout(printed):
                export.main(list(arguments))
        except SystemExit as stopped:
            return str(stopped)
        return printed.getvalue()

    def complete(self, synth=SYNTH) -> None:
        """An installation with an assignment, a learned range, a keyboard and its controllers."""
        self.install({PRESET: [mapping(low=20, high=70, name="Hold")]}, {PRESET: learned((0, 2, 0, 100), (1, 4, 0, 50))},
                     synth=synth)
        self.approve(*bridge.REQUIRED_KINDS, "query_patch")
        bridge.CONTROLS.write_text(json.dumps({"controls": {"Vib": 1, "Rate": 15}}), encoding="utf-8")

    def settings(self) -> dict:
        return export.gather()


class Writing(ExportCase):
    def test_everything_the_pi_needs_is_in_one_valid_file(self):
        self.complete()
        self.assertIn("Written:", self.export("--to", str(self.card)))
        data = yaml.safe_load((self.card / cs.FILE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(cs.validate(data), [])
        self.assertEqual(data["schema_version"], 2)
        self.assertRegex(data["exported_at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertEqual(data["mappings"][PRESET][0]["name"], "Hold")
        # of a learned range only the limits travel, not the list of values the pedal reported
        self.assertEqual(data["learned"], {PRESET: [{"slot": 0, "param": 2, "min": 0, "max": 100},
                                                    {"slot": 1, "param": 4, "min": 0, "max": 50}]})
        self.assertTrue(data["approvals"]["backup_confirmed"])
        self.assertEqual(set(data["approvals"]["messages"]), set(bridge.REQUIRED_KINDS) | {"query_patch"})
        self.assertEqual(data["controls"], synth_controls.read({"Vib": 1, "Rate": 15}))   # one per synth effect now
        self.assertEqual(data["synth"]["keyboard"], KEYBOARD_PORT)
        self.assertEqual(cs.load_with_fallback(self.card), (data, "current", []))

    def test_the_file_that_was_there_is_kept_as_the_previous_one(self):
        self.complete()
        self.export("--to", str(self.card))
        first = (self.card / cs.FILE_NAME).read_text(encoding="utf-8")
        bridge.CONTROLS.write_text(json.dumps({"controls": {"Glide": 17}}), encoding="utf-8")
        self.export("--to", str(self.card))
        self.assertEqual(yaml.safe_load((self.card / cs.PREVIOUS_NAME).read_text(encoding="utf-8"))["controls"],
                         yaml.safe_load(first)["controls"])
        self.assertEqual(yaml.safe_load((self.card / cs.FILE_NAME).read_text(encoding="utf-8"))["controls"],
                         synth_controls.read({"Glide": 17}))
        self.assertEqual(sorted(path.name for path in self.card.iterdir()), sorted([cs.FILE_NAME, cs.PREVIOUS_NAME]))

    def test_without_a_keyboard_no_controllers_and_no_patch_query_are_needed(self):
        self.install({PRESET: [mapping()]}, {PRESET: learned((0, 2, 0, 100))})
        bridge.CONTROLS.write_text(json.dumps({"controls": {"Vib": 1}}), encoding="utf-8")   # left over from earlier
        self.assertIn("complete and valid: 1 preset(s) with assignments, 1 learned range(s), 0 keyboard controller(s)",
                      self.export("--check"))
        self.assertEqual(list(self.card.iterdir()), [])                 # --check writes nothing

    def test_incomplete_settings_are_not_written(self):
        self.complete()
        self.approve(*bridge.REQUIRED_KINDS)                             # the patch query is not approved
        message = self.export("--to", str(self.card))
        self.assertIn("Not exported", message)
        self.assertIn("not approved: query_patch (python probe.py approve)", message)
        self.assertEqual(list(self.card.iterdir()), [])

        self.complete()
        self.approve(*bridge.REQUIRED_KINDS, "query_patch", backup=False)
        self.assertIn("the backup of the patches has not been confirmed", self.export("--to", str(self.card)))
        bridge.CONFIG.unlink()
        self.assertIn("config.yaml is missing", self.export("--to", str(self.card)))

    def test_only_a_raspberry_pi_boot_partition_is_written_to_by_default(self):
        self.complete()
        with mock.patch.object(export, "DEFAULT_TARGET", self.folder / "nowhere"):
            self.assertIn("is not there. Put the Pi's SD card into this computer", self.export())
        with mock.patch.object(export, "DEFAULT_TARGET", self.card), \
                mock.patch.object(subprocess, "run") as run, mock.patch.object(export.sys, "platform", "darwin"):
            self.assertIn("does not look like the boot partition of a Raspberry Pi", self.export())
            self.assertEqual(list(self.card.iterdir()), [])
            (self.card / "config.txt").write_text("# the Pi's own file\n", encoding="utf-8")
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            printed = self.export()
            self.assertIn("Written:", printed)
            self.assertIn("The card is ejected", printed)
            run.assert_called_with(["diskutil", "eject", str(self.card)], capture_output=True, text=True)
            self.assertEqual(sorted(path.name for path in self.card.iterdir()),
                             sorted(["config.txt", cs.FILE_NAME, export.PROGRAM_FOLDER]))

    def test_a_named_folder_does_not_exist(self):
        self.complete()
        self.assertIn("is not there", self.export("--to", str(self.folder / "missing")))


class Card(ExportCase):
    """A card freshly written with Raspberry Pi OS in Raspberry Pi Imager: the program goes along,
    and cloud-init is told to install it at the first start."""
    USER_DATA = ("#cloud-config\n# written by Raspberry Pi Imager\nhostname: expression-bridge\nmanage_etc_hosts: true\n"
                 "users:\n- name: lukas\n  groups: users,adm,sudo\n  passwd: $6$salt$hash\n  lock_passwd: false\n"
                 "enable_ssh: false\n")

    def boot(self, user_data=USER_DATA) -> None:
        (self.card / "config.txt").write_text("# the Pi's own file\n", encoding="utf-8")
        if user_data is not None:
            (self.card / "user-data").write_text(user_data, encoding="utf-8")

    def test_the_program_goes_along_and_is_installed_at_the_first_start(self):
        self.complete()
        self.boot()
        printed = self.export("--to", str(self.card))
        self.assertIn("At its first start the Pi installs the bridge by itself", printed)
        program = self.card / export.PROGRAM_FOLDER
        for name in export.PROGRAM:
            self.assertEqual((program / name).read_bytes(), (export.PROJECT / name).read_bytes(), name)
        self.assertRegex((program / "VERSION").read_text(encoding="utf-8"), r"^(commit \w+ of \d{4}-\d\d-\d\d|version unknown)")
        text = (self.card / "user-data").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#cloud-config\n"))
        config = yaml.safe_load(text)
        self.assertEqual(config["runcmd"], [["bash", "/boot/firmware/expression-bridge/pi/install.sh"]])
        self.assertEqual((config["hostname"], config["users"][0]["passwd"], config["enable_ssh"]),
                         ("expression-bridge", "$6$salt$hash", False))           # what Imager wrote stays

        self.assertIn("Program and settings are up to date", self.export("--to", str(self.card)))
        self.assertEqual(yaml.safe_load((self.card / "user-data").read_text(encoding="utf-8"))["runcmd"],
                         config["runcmd"])                                          # not twice

    def test_commands_of_imager_stay_and_an_old_file_goes(self):
        self.complete()
        self.boot(self.USER_DATA + "runcmd:\n- [rfkill, unblock, wifi]\n")
        (self.card / export.PROGRAM_FOLDER).mkdir()
        (self.card / export.PROGRAM_FOLDER / "probe.py").write_text("# from an older version\n", encoding="utf-8")
        self.export("--to", str(self.card))
        self.assertEqual(yaml.safe_load((self.card / "user-data").read_text(encoding="utf-8"))["runcmd"],
                         [["rfkill", "unblock", "wifi"], ["bash", "/boot/firmware/expression-bridge/pi/install.sh"]])
        self.assertFalse((self.card / export.PROGRAM_FOLDER / "probe.py").exists())

    def test_without_cloud_init_it_says_how_to_install_by_hand(self):
        self.complete()
        self.boot(user_data=None)
        printed = self.export("--to", str(self.card))
        self.assertIn("sudo bash /boot/firmware/expression-bridge/pi/install.sh", printed)
        self.assertTrue((self.card / export.PROGRAM_FOLDER / "bridge.py").exists())
        self.assertFalse((self.card / "user-data").exists())

    def test_a_user_data_that_is_not_imagers_is_left_alone(self):
        self.complete()
        self.boot("#cloud-config\nruncmd: echo hello\n")
        self.assertIn("is not as Raspberry Pi Imager writes it", self.export("--to", str(self.card)))
        self.assertEqual((self.card / "user-data").read_text(encoding="utf-8"), "#cloud-config\nruncmd: echo hello\n")

    def test_the_program_on_the_card_has_everything_it_imports(self):
        self.complete()
        self.boot()
        self.export("--to", str(self.card))
        program = self.card / export.PROGRAM_FOLDER
        found = subprocess.run([sys.executable, "-c", "import bridge, ui, config_schema; print(bridge.__file__)"],
                               cwd=program, capture_output=True, text=True,
                               env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"})
        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertEqual(Path(found.stdout.strip()).resolve().parent, program.resolve())


class Rules(ExportCase):
    def broken(self, change) -> list:
        """The problems of otherwise valid settings after one change."""
        data = copy.deepcopy(self.valid)
        change(data)
        return cs.validate(data)

    def setUp(self) -> None:
        super().setUp()
        self.complete()
        self.valid = self.settings()
        self.assertEqual(cs.validate(self.valid), [])

    def test_what_is_refused(self):
        def drop(*path):
            def change(data):
                for key in path[:-1]:
                    data = data[key]
                del data[path[-1]]
            return change

        def put(value, *path):
            def change(data):
                for key in path[:-1]:
                    data = data[key]
                data[path[-1]] = value
            return change

        cases = {
            "Unknown entry “extra”": put(1, "extra"),
            "Unknown setting “colour” in “bridge”": put("red", "bridge", "colour"),
            "The section “expression” is missing": drop("expression"),
            "schema_version must be 1 to 2": put(0, "schema_version"),
            "Made by a newer program (schema_version 3)": put(3, "schema_version"),
            "device_id must be a number from 0 to 127": put(None, "zoom", "device_id"),
            "cc must be a number from 0 to 127": put(128, "expression", "cc"),
            "channel must be a number from 1 to 16": put(0, "expression", "channel"),
            "min below max": put(127, "expression", "min"),
            "min_interval_ms must be a number from 1 to 1000": put(0, "bridge", "min_interval_ms"),
            "bend_range must be a number from 0 to 12": put(24, "synth", "bend_range"),
            "low_note ≤ high_note": put(100, "synth", "low_note") if False else put(-1, "synth", "low_note"),
            "leaves its learned range 0–100": put(120, "mappings", PRESET, 0, "max"),
            "is assigned twice": lambda data: data["mappings"][PRESET].append(dict(data["mappings"][PRESET][0])),
            "must be a preset with 1 to 12 parameters": put([], "mappings", PRESET),
            "is outside effects 1–6 / parameters 1–12": put(7, "mappings", PRESET, 0, "slot"),
            "unknown curve": put("wild", "mappings", PRESET, 0, "curve"),
            "has an entry that is not a valid range": put("all", "learned", PRESET, 0, "max"),
            "“format_card” is not a message kind of the bridge": put("2026-10-07 12:00:00", "approvals", "messages", "format_card"),
            "not approved: set_param": drop("approvals", "messages", "set_param"),
            "not approved: query_patch": drop("approvals", "messages", "query_patch"),
            "the backup of the patches has not been confirmed": put(None, "approvals", "backup_confirmed"),
            "“Key” is nothing a controller can set": put(20, "controls", "Key"),
            "“Key2” is nothing a controller can set": put(20, "controls", "Key2"),
            "effect_id and poly_effect_id must be two different effects": put(0x07000F61, "synth", "poly_effect_id"),
            "poly_effect_id must be the id of the KeyPoly effect": put(-1, "synth", "poly_effect_id"),
            "effect_id must be the id of the KeySynth effect": put(0x09000F72, "synth", "effect_id"),
            "Rate needs a controller number from 0 to 127 that is not reserved": put(64, "controls", "Rate"),
            "one controller is assigned twice": put(1, "controls", "KeySynth/Rate"),
            "“KeyPoly/Glide” is nothing a controller can set": put(20, "controls", "KeyPoly/Glide"),
            "looks like raw bytes": put("F0 52 00 6E 50 F7", "mappings", PRESET, 0, "name"),
            "“ports.zoom” looks like raw bytes": put("f052006e50f7", "ports", "zoom"),
            # written by hand: said, not a crash of the check (2026-10-23)
            "KeySynth/Rate needs a controller number": put([1], "controls", "KeySynth/Rate"),
            "“²” is not an effect from 1 to 6": put({"²": {"cc": 30}}, "effects"),
            "ui: listen must be true or false": put({"port": 8765, "listen": "yes"}, "ui"),
        }
        for expected, change in cases.items():
            with self.subTest(expected):
                problems = self.broken(change)
                self.assertTrue(any(expected in problem for problem in problems), problems)

    def test_what_is_allowed(self):
        def put(value, *path):
            return lambda data: data[path[0]].update({path[1]: value})

        fine = {
            "keyboard only, no expression controller": lambda data: (
                data["ports"].update(chocolate=""), data["expression"].update(cc=None, channel=None),
                data.update(mappings={})),
            "no controller at all: the pedal alone, edited in the interface": lambda data: (
                data["ports"].update(chocolate=""), data["synth"].update(keyboard=""),
                data["expression"].update(cc=None, channel=None)),
            "no assignments at all": lambda data: data.update(mappings={}),
            "a parameter that was not learned, on the fixed grid": lambda data: data["mappings"][PRESET].append(
                {"slot": 5, "param": 13, "min": 0, "max": 16383}),
            "no interface section": lambda data: data.pop("ui"),
            "a name with numbers in it": lambda data: data["mappings"][PRESET][0].update(name="Mix 50 to 70, take 2"),
            "KeyPoly besides KeySynth": put(0x07000F62, "synth", "poly_effect_id"),
            "KeyPoly only": lambda data: data["synth"].update(effect_id=None, poly_effect_id=0x07000F62),
            "no effect id, the bridge knows the effects": put(None, "synth", "effect_id"),
            "KeySynth in another category of the pedal": put(0x09000F61, "synth", "effect_id"),
            "a controller on a knob only KeyFM has": lambda data: data["controls"].update(Ratio=20, Flt=21),
            "one controller on another knob of each synth effect": lambda data: data["controls"].update(
                {"KeySweep/Cutoff": 30, "KeyFM/Index": 30, "KeyPoly/Reso": 30, "KeyWave/Sub": 30}),
            "assignments from before they were kept per synth effect": lambda data: data.update(
                controls={"Vib": 1, "Arp": 14}),
            "a controller on a knob only KeyPoly has": lambda data: data["controls"].update(Cutoff=20, Reso=21),
        }
        for what, change in fine.items():
            with self.subTest(what):
                self.assertEqual(self.broken(change), [])

    def test_bytes_and_other_shapes_are_not_settings(self):
        self.assertEqual(cs.validate(["a", "list"]), ["The file does not contain settings."])
        self.assertTrue(any("looks like raw bytes" in problem for problem in self.broken(
            lambda data: data["ports"].update(zoom=b"\xf0\x52\x00"))))


class Fallback(ExportCase):
    def test_a_broken_file_falls_back_to_the_previous_one(self):
        self.complete()
        self.export("--to", str(self.card))
        self.export("--to", str(self.card))                              # now there is a previous one
        current = self.card / cs.FILE_NAME
        current.write_text(current.read_text(encoding="utf-8").replace("schema_version: 2", "schema_version: 0"),
                           encoding="utf-8")
        settings, source, problems = cs.load_with_fallback(self.card)
        self.assertEqual((source, problems), ("previous", ["schema_version must be 1 to 2."]))
        self.assertEqual(cs.validate(settings), [])

        (self.card / cs.PREVIOUS_NAME).write_text("ports: [", encoding="utf-8")
        settings, source, problems = cs.load_with_fallback(self.card)
        self.assertEqual((settings, source), (None, None))
        self.assertEqual(problems, ["schema_version must be 1 to 2.", f"{cs.PREVIOUS_NAME} is not a readable settings file."])

    def test_no_file_at_all(self):
        settings, source, problems = cs.load_with_fallback(self.card)
        self.assertEqual((settings, source), (None, None))
        self.assertEqual(len(problems), 2)
        self.assertIn(f"{cs.FILE_NAME} cannot be read", problems[0])
