"""The allowlist: which SysEx messages may leave the program."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import zoom_sysex as zs

ID = 0x6E
TARGETS = [zs.ParamTarget(slot=2, param=3, min=10, max=150)]


def message(text: str) -> bytes:
    return bytes.fromhex(text)


class PortNames(unittest.TestCase):
    """The settings are made on a Mac and run on a Raspberry Pi, where ALSA names every port
    "client:port client-number:port-number"."""
    MAC = ["ZOOM MS Plus Series", "ZOOM MS Plus Series 2", "KOMPLETE KONTROL A61", "Komplete Kontrol A DAW", "SINCO"]
    LINUX = ["Midi Through:Midi Through Port-0 14:0", "ZOOM MS Plus Series:ZOOM MS Plus Series MIDI 1 24:0",
             "KOMPLETE KONTROL A61:KOMPLETE KONTROL A61 MIDI 1 28:0",
             "KOMPLETE KONTROL A61:Komplete Kontrol A DAW 28:1", "SINCO:SINCO MIDI 1 32:0",
             "RtMidiIn Client:RtMidi Input 130:0"]

    def test_a_name_from_the_mac_finds_its_port_on_linux(self):
        for needle, port in (("ZOOM MS Plus Series", self.LINUX[1]), ("KOMPLETE KONTROL A61", self.LINUX[2]),
                             ("Komplete Kontrol A DAW", self.LINUX[3]), ("sinco", self.LINUX[4])):
            with self.subTest(needle):
                self.assertEqual(zs.find_port(self.LINUX, needle), port)

    def test_on_the_mac_an_exact_name_wins_and_a_part_must_be_unique(self):
        self.assertEqual(zs.find_port(self.MAC, "ZOOM MS Plus Series"), "ZOOM MS Plus Series")
        self.assertEqual(zs.find_port(self.MAC, "SINCO"), "SINCO")
        with self.assertRaises(zs.PortError) as refused:
            zs.find_port(self.MAC, "Kontrol")                 # two devices, not two ports of one
        self.assertIn("found: KOMPLETE KONTROL A61, Komplete Kontrol A DAW", str(refused.exception))

    def test_two_devices_of_the_same_name_on_linux_are_not_guessed(self):
        two = self.LINUX + ["ZOOM MS Plus Series:ZOOM MS Plus Series MIDI 1 36:0"]
        with self.assertRaises(zs.PortError):
            zs.find_port(two, "ZOOM MS Plus Series")

    def test_a_linux_name_chosen_in_the_interface_finds_its_device_under_other_numbers(self):
        # Bluetooth clients are numbered as they connect, USB ones as they are plugged in
        self.assertEqual(zs.find_port(self.LINUX, "SINCO:SINCO MIDI 1 129:0"), self.LINUX[4])
        self.assertEqual(zs.find_port(self.LINUX, "KOMPLETE KONTROL A61:Komplete Kontrol A DAW 20:1"), self.LINUX[3])
        self.assertEqual(zs.find_port(self.LINUX, "ZOOM MS Plus Series:ZOOM MS Plus Series MIDI 1 24:0"), self.LINUX[1])

    def test_a_missing_port_names_the_ports_there_are(self):
        with self.assertRaises(zs.PortError) as refused:
            zs.find_port(self.LINUX, "UAC-232")
        self.assertIn("found: none", str(refused.exception))
        self.assertIn("ports there are: KOMPLETE KONTROL A61 MIDI 1, Komplete Kontrol A DAW, Midi Through Port-0",
                      str(refused.exception))
        self.assertEqual(zs.port_candidates(self.LINUX, "UAC-232"), [])


class Allowlist(unittest.TestCase):
    def test_the_message_kinds_that_are_allowed(self):
        allowed = {
            "F0 7E 7F 06 01 F7": "identity_request",
            "F0 52 00 6E 50 F7": "edit_enable",
            "F0 52 00 6E 51 F7": "edit_disable",
            "F0 52 00 6E 33 F7": "query_program",
            "F0 52 00 6E 64 13 F7": "query_patch",
            "F0 52 00 6E 64 20 00 02 03 0A 00 00 00 00 F7": "set_param",   # 10, the minimum
            "F0 52 00 6E 64 20 00 02 03 16 01 00 00 00 F7": "set_param",   # 150, the maximum
        }
        for text, kind in allowed.items():
            with self.subTest(text):
                self.assertEqual(zs.classify(message(text), ID, TARGETS), kind)

    def test_everything_else_is_refused(self):
        refused = {
            "firmware mode": "F0 52 00 6E 01 F7",
            "exit firmware mode": "F0 52 00 6E 04 F7",
            "factory reset": "F0 52 00 6E 5B F7",
            "overwrite current patch": "F0 52 00 6E 28 00 F7",
            "file access": "F0 52 00 6E 60 05 F7",
            "forbidden 64 47": "F0 52 00 6E 64 47 F7",
            "auto save": "F0 52 00 6E 64 20 00 64 0F 01 00 00 00 00 F7",
            "delete effect": "F0 52 00 6E 64 20 00 64 6E 02 00 00 00 00 F7",
            "patch name, place 29": "F0 52 00 6E 64 20 00 5F 1C 41 00 00 00 00 F7",
            "patch name, no character of a name": "F0 52 00 6E 64 20 00 5F 00 2A 00 00 00 00 F7",
            "patch name, a value above 127": "F0 52 00 6E 64 20 00 5F 00 41 01 00 00 00 F7",
            "patch name, trailing byte not zero": "F0 52 00 6E 64 20 00 5F 00 41 00 01 00 00 F7",
            "below the range": "F0 52 00 6E 64 20 00 02 03 09 00 00 00 00 F7",
            "above the range": "F0 52 00 6E 64 20 00 02 03 17 01 00 00 00 F7",
            "parameter without a target": "F0 52 00 6E 64 20 00 02 04 0A 00 00 00 00 F7",
            "slot without a target": "F0 52 00 6E 64 20 00 03 03 0A 00 00 00 00 F7",
            "effect type (param 1)": "F0 52 00 6E 64 20 00 02 01 0A 00 00 00 00 F7",
            "trailing byte not zero": "F0 52 00 6E 64 20 00 02 03 0A 00 01 00 00 F7",
            "the pedal's own ack": "F0 52 00 6E 64 20 01 02 03 0A 00 00 00 00 F7",
            "patch dump": "F0 52 00 6E 64 12 F7",
            "say hi": "F0 52 00 6E 05 F7",
            "pc mode": "F0 52 00 6E 52 F7",
            "another device ID": "F0 52 00 6D 50 F7",
            "extra byte": "F0 52 00 6E 50 00 F7",
            "empty command": "F0 52 00 6E F7",
            "not terminated": "F0 52 00 6E 50",
            "empty": "F0 F7",
        }
        for name, text in refused.items():
            with self.subTest(name):
                with self.assertRaises(zs.NotAllowed):
                    zs.classify(message(text), ID, TARGETS)

    def test_effect_on_off_is_refused_even_with_a_target(self):
        target = [zs.ParamTarget(slot=2, param=0, min=0, max=1)]
        with self.assertRaises(zs.NotAllowed):
            zs.classify(zs.build_set_param(ID, 2, 0, 1), ID, target)

    def test_an_effect_is_switched_only_where_the_preset_has_one_and_only_on_or_off(self):
        on, off = zs.build_switch_effect(ID, 2, True), zs.build_switch_effect(ID, 2, False)
        self.assertEqual(zs.to_hex(on), "F0 52 00 6E 64 20 00 02 00 01 00 00 00 00 F7")
        self.assertEqual(zs.to_hex(off), "F0 52 00 6E 64 20 00 02 00 00 00 00 00 00 F7")
        for raw in (on, off):
            self.assertEqual(zs.classify(raw, ID, TARGETS, switchable=(0, 1, 2)), "switch_effect")
        refused = {
            "a slot the preset does not have": (on, (0, 1)),
            "no slots known": (on, ()),
            "another value": (zs.build_set_param(ID, 2, 0, 2), (0, 1, 2)),
            "a value in the high byte": (message("F0 52 00 6E 64 20 00 02 00 00 01 00 00 00 F7"), (0, 1, 2)),
            "the effect type": (zs.build_set_param(ID, 2, 1, 1), (0, 1, 2)),
            "patch settings": (message("F0 52 00 6E 64 20 00 64 00 01 00 00 00 00 F7"), (0x64,)),
            "the patch name": (message("F0 52 00 6E 64 20 00 5F 00 01 00 00 00 00 F7"), (0x5F,)),
            "a slot beyond the six": (message("F0 52 00 6E 64 20 00 06 00 01 00 00 00 00 F7"), (6,)),
        }
        for name, (raw, switchable) in refused.items():
            with self.subTest(name), self.assertRaises(zs.NotAllowed):
                zs.classify(raw, ID, TARGETS, switchable=switchable)
        with self.assertRaises(ValueError):
            zs.build_switch_effect(ID, 6, True)

    def test_only_the_identity_request_is_allowed_before_the_device_is_known(self):
        self.assertEqual(zs.classify(zs.build_identity_request()), "identity_request")
        for text in ("F0 52 00 6E 50 F7", "F0 52 00 6E 33 F7"):
            with self.subTest(text), self.assertRaises(zs.NotAllowed):
                zs.classify(message(text), None)


class Encoding(unittest.TestCase):
    def test_values_survive_the_seven_bit_split(self):
        for value in (0, 1, 127, 128, 150, 16383):
            with self.subTest(value):
                self.assertEqual(zs.decode_value(*zs.encode_value(value)), value)

    def test_set_param_bytes(self):
        self.assertEqual(zs.build_set_param(ID, 2, 3, 150),
                         message("F0 52 00 6E 64 20 00 02 03 16 01 00 00 00 F7"))

    def test_parsers(self):
        identity = zs.parse_identity_reply(message("F0 7E 00 06 02 52 6E 00 27 00 31 2E 32 30 F7"))
        self.assertEqual((identity.device_id, identity.version), (ID, "1.20"))
        body = zs.zoom_body(message("F0 52 00 6E 64 20 01 00 02 32 00 00 00 00 F7"), ID)
        ack = zs.parse_param(body)
        self.assertEqual((ack.ack, ack.slot, ack.param, ack.value), (True, 0, 2, 50))
        self.assertEqual(zs.parse_program_info(message("64 26 00 00 09 00 04 00")), (9, 4))
        self.assertIsNone(zs.zoom_body(message("F0 52 00 6D 50 F7"), ID))


class Sender(unittest.TestCase):
    """Rules 4 and 5: no backup confirmation or approval, no message."""

    class Port:
        def __init__(self):
            self.sent = []

        def send(self, sent):
            self.sent.append(sent)

    def setUp(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.approvals = zs.Approvals(folder / "approvals.json")
        self.port = self.Port()
        self.sender = zs.ZoomSender(self.port, self.approvals, device_id=ID)

    def test_nothing_is_sent_without_backup_confirmation(self):
        self.approvals.approve("edit_enable")
        with self.assertRaises(zs.NotApproved):
            self.sender.send(zs.build_edit_enable(ID))
        self.assertEqual(self.port.sent, [])

    def test_nothing_is_sent_without_approval_of_the_kind(self):
        self.approvals.confirm_backup()
        self.approvals.approve("edit_enable")
        with self.assertRaises(zs.NotApproved):
            self.sender.send(zs.build_edit_disable(ID))
        self.assertEqual(self.sender.send(zs.build_edit_enable(ID)), "edit_enable")
        self.assertEqual(len(self.port.sent), 1)



class Files(unittest.TestCase):
    def test_a_file_is_written_whole_or_not_at_all(self):
        # config.yaml, controls.json, measurements.json and approvals.json: stopped in the middle,
        # the file was empty or half written (until 2026-10-23)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "controls.json"
            path.write_text("old\n", encoding="utf-8")
            with mock.patch("os.fsync", side_effect=OSError("the card is gone")):
                with self.assertRaises(OSError):
                    zs.write_whole(path, "new\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
            zs.write_whole(path, "new\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            self.assertEqual([item.name for item in Path(folder).iterdir()], ["controls.json"])


if __name__ == "__main__":
    unittest.main()
