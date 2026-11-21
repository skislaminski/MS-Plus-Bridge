"""The pedal's own pictures of its effects for the interface (effect_pictures.py, GET /api/picture):
a .ZIC decoded to a PNG, where it is looked for in the installer's data folder (made here, not the
one of this computer), and the ids of each preset's effects in the state's list of presets."""
import json
import shutil
import socket
import struct
import tempfile
import unittest
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import bridge
import effect_pictures
import ui
from tests.test_presets import CHORUS, OTHER, ROOM, STOCK, PresetCase, stored

BPM = 0x0A000FF0                    # in the pedal itself: no file, no picture


def zic(*frames) -> bytes:
    """A .ZIC as the pedal's are: frames given as (width, height, white pixels {(x, y)}), the
    descriptor filled up with zeros as theirs are."""
    descriptor = b"".join(struct.pack("<HH", width, height) for width, height, _ in frames) + bytes(8)
    data = b""
    for width, height, white in frames:
        frame = bytearray(width * -(-height // 8))
        for x, y in white:
            frame[(y // 8) * width + x] |= 1 << (y % 8)
        data += frame
    return b"ZBMP" + struct.pack("<I", len(descriptor)) + descriptor + data


def wide(width: int) -> bytes:
    """A picture told apart by its width."""
    return zic((width, 8, set()))


def backup(root: Path, name: str, created: float, complete: bool, files: dict) -> None:
    """A backup of the pedal as the installer keeps it: manifest.json and files/."""
    folder = root / "Backups" / name
    (folder / "files").mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"created_ts": created, "complete": complete}), encoding="utf-8")
    for file, data in files.items():
        (folder / "files" / file).write_bytes(data)


def get(port: int, path: str) -> tuple:
    """(status, body, headers) of a GET to the interface."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as reply:
            return reply.status, reply.read(), reply.headers
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read(), error.headers


class PictureCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def pixels(self, picture: bytes) -> tuple:
        """(width, height, white pixels) of a PNG as effect_pictures.png writes it."""
        self.assertEqual(picture[:8], b"\x89PNG\r\n\x1a\n")
        chunks, at = {}, 8
        while at < len(picture):
            length, kind = struct.unpack_from(">I4s", picture, at)
            data = picture[at + 8:at + 8 + length]
            self.assertEqual(struct.unpack_from(">I", picture, at + 8 + length)[0], zlib.crc32(kind + data))
            chunks[kind], at = data, at + 12 + length
        width, height, depth, color = struct.unpack_from(">IIBB", chunks[b"IHDR"])
        self.assertEqual((depth, color, list(chunks)), (1, 0, [b"IHDR", b"IDAT", b"IEND"]))   # grayscale, 1 bit
        raw, stride = zlib.decompress(chunks[b"IDAT"]), 1 + -(-width // 8)
        self.assertEqual(len(raw), stride * height)
        self.assertEqual({raw[y * stride] for y in range(height)}, {0})                      # no filter
        white = {(x, y) for y in range(height) for x in range(width) if raw[y * stride + 1 + x // 8] >> (7 - x % 8) & 1}
        return width, height, white


class Decoding(PictureCase):
    def test_the_last_frame_becomes_a_black_and_white_png(self):
        white = {(0, 0), (9, 11), (3, 7), (4, 8), (5, 0)}      # corners, and either side of a column's byte
        picture = effect_pictures.png(zic((3, 5, {(1, 1)}), (10, 12, white)))
        self.assertEqual(self.pixels(picture), (10, 12, white))

    def test_the_sizes_come_from_the_descriptor(self):
        for frames in ([(74, 97, {(73, 96)})], [(72, 97, set()), (102, 129, {(101, 128), (0, 64)})]):
            width, height, white = frames[-1]
            self.assertEqual(self.pixels(effect_pictures.png(zic(*frames))), (width, height, white))

    def test_what_is_no_picture_gives_none(self):
        good = zic((4, 8, {(1, 1)}))
        for broken in (b"", b"ZBMP", b"ZBMQ" + good[4:], good[:-1], good + b"\0",
                       good[:4] + struct.pack("<I", 4000) + good[8:],            # a descriptor longer than the file
                       zic((0, 0, set())),                                          # no frame
                       zic((4, 0, set()))):                                         # no frame either
            self.assertIsNone(effect_pictures.png(broken), broken[:20])


class Finding(PictureCase):
    def setUp(self) -> None:
        super().setUp()
        backup(self.root, "older", 100.0, True, {"A.ZIC": wide(1), "b.zic": wide(2), "A.ZD2": b"not a picture"})
        backup(self.root, "newest-complete", 200.0, True, {"A.ZIC": wide(3)})
        backup(self.root, "newer-broken-off", 300.0, False, {"A.ZIC": wide(4), "C.ZIC": wide(5)})
        (self.root / "Adapted" / "ms-60bp").mkdir(parents=True)
        for file, width in (("A.ZIC", 6), ("C.ZIC", 7), ("D.ZIC", 8), ("BAD.ZIC", 0)):
            (self.root / "Adapted" / "ms-60bp" / file).write_bytes(wide(width) if width else b"ZBMP")

    def test_the_newest_complete_backup_first_then_the_others_then_the_adapted_effects(self):
        self.assertEqual(effect_pictures.folders(self.root), [
            self.root / "Backups" / "newest-complete" / "files", self.root / "Backups" / "newer-broken-off" / "files",
            self.root / "Backups" / "older" / "files", self.root / "Adapted" / "ms-60bp", self.root])
        pictures = effect_pictures.Pictures(self.root)
        widths = {file: pictures.picture(file) and self.pixels(pictures.picture(file))[0]
                  for file in ("A.ZD2", "b.zd2", "C.ZD2", "D.ZD2", "E.ZD2", "BAD.ZD2")}
        self.assertEqual(widths, {"A.ZD2": 3, "b.zd2": 2, "C.ZD2": 5, "D.ZD2": 8, "E.ZD2": None, "BAD.ZD2": None})

    def test_a_backup_made_meanwhile_counts_a_minute_later(self):
        now = [1000.0]
        with mock.patch.object(effect_pictures, "time", SimpleNamespace(monotonic=lambda: now[0])):
            pictures = effect_pictures.Pictures(self.root)
            self.assertIsNone(pictures.picture("E.ZD2"))
            backup(self.root, "made-meanwhile", 400.0, True, {"E.ZIC": wide(9), "A.ZIC": wide(10)})
            now[0] += effect_pictures.RECHECK_SECONDS / 2
            self.assertEqual((pictures.picture("E.ZD2"), self.pixels(pictures.picture("A.ZD2"))[0]), (None, 3))
            now[0] += effect_pictures.RECHECK_SECONDS
            self.assertEqual([self.pixels(pictures.picture(file))[0] for file in ("E.ZD2", "A.ZD2")], [9, 10])

    def test_a_backup_of_an_older_installer_is_dated_by_its_created_time(self):
        folder = self.root / "Backups" / "older-installer"
        (folder / "files").mkdir(parents=True)
        (folder / "manifest.json").write_text(json.dumps({"created": "2026-11-01T12:00:00", "complete": True}),
                                              encoding="utf-8")
        (folder / "files" / "A.ZIC").write_bytes(wide(11))
        # 2026 is long after the test's created_ts of 100–300 seconds: it is the newest complete backup now
        self.assertEqual(effect_pictures.folders(self.root)[0], folder / "files")
        self.assertEqual(self.pixels(effect_pictures.Pictures(self.root).picture("A.ZD2"))[0], 11)

    def test_without_a_data_folder_there_are_none(self):
        self.assertIsNone(effect_pictures.Pictures(self.root / "missing").picture("A.ZD2"))


class TheRoute(PictureCase):
    """GET /api/picture?id=…: the effect's file from the table (in the bridge's loop), its picture
    from the installer's folder; one the browser may keep for an hour."""

    def setUp(self) -> None:
        super().setUp()
        backup(self.root, "newest", 200.0, True, {"BLACKOPT.ZIC": wide(12)})     # STOCK's file is BLACKOPT.ZD2
        patcher = mock.patch.object(ui, "PICTURES", effect_pictures.Pictures(self.root))
        patcher.start()
        self.addCleanup(patcher.stop)

    def serve(self, bridge_object) -> int:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        ui.serve(bridge_object, {"port": port})
        return port

    def test_a_picture_and_what_has_none(self):
        port = self.serve(SimpleNamespace(call=lambda function, timeout=3.0: {"result": function()}))
        status, body, headers = get(port, f"/api/picture?id={STOCK:08X}")
        self.assertEqual((status, headers["Content-Type"], headers["Cache-Control"]), (200, "image/png", "max-age=3600"))
        self.assertEqual(self.pixels(body)[0], 12)
        self.assertEqual(get(port, f"/api/picture?id={STOCK:08x}")[0], 200)
        for effect_id in (f"{ROOM:08X}", "0F000123", f"{BPM:08X}", "00000000"):   # no picture, unknown, no file, empty
            status, body, headers = get(port, f"/api/picture?id={effect_id}")
            self.assertEqual((status, json.loads(body), headers["Cache-Control"]), (404, {"error": "No picture."}, "no-store"))
        for query in ("?id=12345", "?id=0100002G", ""):
            self.assertEqual(get(port, "/api/picture" + query)[0], 400)

    def test_none_while_no_bridge_runs_or_without_the_pictures(self):
        port = self.serve(bridge.Current())                     # no bridge running (or a busy one): later
        self.assertEqual(get(port, f"/api/picture?id={STOCK:08X}")[0], 503)
        port = self.serve(SimpleNamespace(call=lambda function, timeout=3.0: {"result": function()}))
        with mock.patch.object(ui, "PICTURES", None):           # the Pi: effect_pictures.py is not on its card
            self.assertEqual(get(port, f"/api/picture?id={STOCK:08X}")[0], 404)


class TheEffectsOfEachPreset(PresetCase):
    def test_the_list_of_presets_has_the_ids_of_their_effects_in_chain_order(self):
        world = self.presets()
        world.stored[OTHER] = stored([ROOM, 0, STOCK], "Gaps")              # an empty place in its chain
        state, = self.run_calls(until=1.6, world=world)
        effects = {(entry["bank"], entry["program"]): entry["effects"] for entry in state["presets"]["list"]}
        self.assertEqual(effects[(0, 8)], [f"{STOCK:08X}", f"{CHORUS:08X}"])
        self.assertEqual(effects[OTHER], [f"{ROOM:08X}", f"{STOCK:08X}"])
        self.assertEqual(effects[(9, 9)], [])                               # an empty preset

    def test_none_while_the_presets_are_not_read(self):
        world = self.presets()
        world.answers_presets = False
        state, = self.run_calls(until=1.6, world=world)
        self.assertEqual({tuple(entry["effects"]) for entry in state["presets"]["list"]}, {()})


if __name__ == "__main__":
    unittest.main()
