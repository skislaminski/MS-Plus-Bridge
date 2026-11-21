"""The N2Z Bank (nam_bank.py, the NAM tab of `bridge.py --workshop`).

Compact models become the bank that nam2zoom.app builds, byte for byte. Other NAMs are sent back
to nam2zoom.app. The bank lands in the installer's list, and the saved patches that use the bank
on the pedal are named."""
import hashlib
import http.client
import json
import struct
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import bridge
import nam_bank
import workshop
from tests.nam_models import compact_nam
from tests.sim import SimulatedCase

# made by nam2zoom.app 1.2.3's own `python -m nam2zoom build-bank` from the same models (nam_models.py)
GOLDEN_ONE = "cc6f26e5b7215f080b52458a33251eafb098fa3b5aefd562cdc58857b754e97f"     # M0, named ONE
GOLDEN_TEN = "a15cafd323981f2d444986fb8285a4813a89305ae75f41d2cfe730d89a8740cf"     # M0…M9, named M0…M9
ICON = "5a8e36db1d3dcd2d49ce7b77e30c362017b6856fb3cd0284b2c2f6afa516baf8"
N2Z_ON_THE_PEDAL = 0x09000F87                  # the bank as the installer put it in, under SFX (2026-10-05)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ptcf(name: str, ids: list) -> bytes:
    """A saved patch as the installer's backup keeps it, as far as its effects and name."""
    data = bytearray(36 + 4 * len(ids))
    data[:4], data[12:16] = b"PTCF", len(ids).to_bytes(4, "little")
    data[26:26 + len(name)] = name.encode("ascii")
    for slot, effect_id in enumerate(ids):
        data[36 + 4 * slot:40 + 4 * slot] = effect_id.to_bytes(4, "little")
    return bytes(data)


def build_body(models: list) -> bytes:
    """What the page sends to nam/build: [(name, bytes, label)]."""
    listed = json.dumps({"models": [{"name": name, "size": len(data), "label": label}
                                    for name, data, label in models]}).encode()
    return len(listed).to_bytes(4, "little") + listed + b"".join(data for _, data, _ in models)


class Bank(unittest.TestCase):
    def test_one_model_as_nam2zoom_builds_it(self):
        zd2, zic = nam_bank.build([("M0.nam", compact_nam(0))], ["ONE"])
        self.assertEqual((sha(zd2), sha(zic)), (GOLDEN_ONE, ICON))

    def test_ten_models_as_nam2zoom_builds_them(self):
        zd2, _ = nam_bank.build([(f"M{i}.nam", compact_nam(i)) for i in range(10)], [f"M{i}" for i in range(10)])
        self.assertEqual(sha(zd2), GOLDEN_TEN)

    def test_what_nam2zoom_refuses(self):
        with self.assertRaisesRegex(ValueError, "1-10 NAM files"):
            nam_bank.build([(f"M{i}.nam", compact_nam(i)) for i in range(11)], [f"M{i}" for i in range(11)])
        with self.assertRaisesRegex(ValueError, "unique"):
            nam_bank.build([("A.nam", compact_nam(0)), ("B.nam", compact_nam(1))], ["SAME", "SAME"])
        with self.assertRaises(ValueError) as refused:
            nam_bank.build([("Big Amp.nam", b'{"architecture": "WaveNet"}')], ["BIG"])
        self.assertTrue(str(refused.exception).startswith("Big Amp.nam: unsupported NAM model"), refused.exception)

    def test_which_models_go_in_as_they_are(self):
        self.assertEqual({key: value for key, value in nam_bank.check("Ampeg B15 (2).nam", compact_nam(0)).items()
                          if key in ("status", "label")}, {"status": "direct", "label": "AMPEG"})
        trained = json.dumps({"architecture": "WaveNet", "sample_rate": 48000, "config": {}, "weights": []}).encode()
        self.assertEqual(nam_bank.check("Full.nam", trained)["status"], "adaptable")      # nam2zoom.app converts it
        self.assertEqual(nam_bank.check("x.nam", b"not json")["status"], "unsupported")
        self.assertEqual(nam_bank.check("../../../Evil.nam", compact_nam(0))["label"], "EVIL")


class Patches(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.root, ignore_errors=True)
        bank = bytearray(nam_bank.build([("M0.nam", compact_nam(0))], ["ONE"])[0])
        struct.pack_into("<I", bank, nam_bank.ID_AT, N2Z_ON_THE_PEDAL)
        self.bank = bytes(bank)

    def backup(self, name: str, created: float, patches: dict, bank: bool = True) -> None:
        folder = self.root / "Backups" / name
        (folder / "files").mkdir(parents=True)
        (folder / "patches").mkdir()
        (folder / "manifest.json").write_text(json.dumps({"complete": True, "created_ts": created, "model": "MS-60B+",
                                                          "created": "2026-11-20T10:00:00"}))
        if bank:
            (folder / "files" / "N2ZBANK.ZD2").write_bytes(self.bank)
        for number, patch in patches.items():
            (folder / "patches" / f"{number}.zptc").write_bytes(patch)

    def test_the_saved_patches_with_the_bank_in_the_newest_backup(self):
        self.backup("ms-60bp_old", 1.0, {"007": ptcf("OLD", [N2Z_ON_THE_PEDAL])})
        self.backup("ms-60bp_new", 2.0, {"001": ptcf("NAMTONE", [0x01000020, N2Z_ON_THE_PEDAL]),
                                          "002": ptcf("CLEAN", [0x01000020])})
        self.assertEqual(nam_bank.patches_using_bank(str(self.root)),
                         {"backup": "MS-60B+, 2026-11-20 10:00:00", "patches": ["001 NAMTONE"]})

    def test_none_without_a_bank_or_a_backup(self):
        self.assertIsNone(nam_bank.patches_using_bank(str(self.root)))
        self.backup("ms-60bp_new", 2.0, {"001": ptcf("NAMTONE", [N2Z_ON_THE_PEDAL])}, bank=False)
        self.assertEqual(nam_bank.patches_using_bank(str(self.root))["patches"], [])


class ThroughTheInstaller(SimulatedCase):
    def setUp(self) -> None:
        super().setUp()
        self.app = workshop.start(str(bridge.INSTALLER_DATA))
        self.addCleanup(self.app.stop)

    def post(self, path: str, body: bytes) -> tuple:
        try:
            with urllib.request.urlopen(urllib.request.Request(self.app.url + path, data=body), timeout=30) as reply:
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_its_page_and_a_check(self):
        with urllib.request.urlopen(self.app.url + "nam", timeout=10) as reply:
            theme = workshop.FRAMED + b"<style>\n" + workshop.THEME.read_bytes() + b"</style>\n</head>"   # the bridge's look
            self.assertEqual(reply.read(), workshop.NAM_PAGE.read_bytes().replace(b"</head>", theme, 1))
            self.assertIn("default-src 'self'", reply.headers["Content-Security-Policy"])
        status, found = self.post("nam/check?name=Ampeg%20B15.nam", compact_nam(0))
        self.assertEqual((status, found["status"], found["label"]), (200, "direct", "AMPEG"))

    def test_an_empty_file_and_one_of_another_shape_are_not_usable(self):
        status, found = self.post("nam/check?name=cut.nam", b"")              # a download cut off
        self.assertEqual((status, found["status"], found["reason"]), (200, "unsupported", "the file is empty"))
        for architecture in (["WaveNet"], {"name": "WaveNet"}):
            odd = json.dumps({"architecture": architecture, "sample_rate": 48000, "config": {}, "weights": []})
            status, found = self.post("nam/check?name=odd.nam", odd.encode())
            self.assertEqual((status, found["status"]), (200, "unsupported"))

    def test_the_bank_goes_into_the_installers_list(self):
        status, reply = self.post("nam/build", build_body([("M0.nam", compact_nam(0), "ONE")]))
        self.assertEqual((status, reply["ok"], reply["zd2_sha256"], reply["labels"]), (200, True, GOLDEN_ONE, ["ONE"]))
        self.assertIsNone(reply["patches"])                              # no backup in this installer's folder
        with urllib.request.urlopen(self.app.url + "api/state", timeout=10) as state:
            queue = json.loads(state.read())["queue"]
        self.assertEqual([(item["file"], item["ok"]) for item in queue], [("N2ZBANK.ZD2", True)])

    def test_not_while_the_installer_works_on_the_pedal(self):
        self.assertTrue(self.app.start_job("backup", lambda: time.sleep(0.6) or 0))
        status, reply = self.post("nam/build", build_body([("M0.nam", compact_nam(0), "ONE")]))
        self.assertEqual(reply, {"ok": False, "error": "The installer is working on the pedal: build again once it is done."})

    def test_what_is_refused(self):
        # refused from its length alone, before a byte of it is read (the page says so before sending)
        address = urlsplit(self.app.url)
        connection = http.client.HTTPConnection(address.hostname, address.port, timeout=10)
        connection.putrequest("POST", address.path + "nam/check?name=big.nam")
        connection.putheader("Content-Length", str(nam_bank.MAX_CHECK + 1))
        connection.endheaders()
        self.assertEqual(connection.getresponse().status, 413)
        connection.close()
        self.assertEqual(self.post("nam/build", b"\xff\xff\xff\xff{}")[1], {"ok": False, "error": "Invalid request."})
        lying = build_body([("M0.nam", compact_nam(0), "ONE")])[:-1]       # one byte short of what it lists
        self.assertEqual(self.post("nam/build", lying)[1], {"ok": False, "error": "Invalid request."})


if __name__ == "__main__":
    unittest.main()
