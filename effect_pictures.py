"""The pictures the pedal shows for its effects, for the interface: each effect file NAME.ZD2 has
its picture in NAME.ZIC beside it. The Zoom MS Installer keeps both in its data folder (the
backups of the pedal, and the effects it adapted for a pedal), so the pictures are taken from
there and given as PNG. The Pi has no such folder: export.py writes the pictures onto its card,
into one folder of ZIC files (see folders)."""
from __future__ import annotations

import datetime
import json
import struct
import threading
import time
import zlib
from pathlib import Path
from typing import Optional

RECHECK_SECONDS = 60.0    # a backup made meanwhile counts after this long, without a restart
# the pictures of our own effects and of the Waveformer effects, for one no backup holds yet
BUNDLED = Path(__file__).resolve().parent / "effect_icons"
KEPT = 512                # pictures kept in memory at most


def png(zic: bytes) -> Optional[bytes]:
    """The largest picture of a .ZIC file (its last frame) as a black and white PNG; None if it is
    no ZIC or does not check out. A ZIC: "ZBMP", the length of its descriptor (u32 at 4), the
    descriptor (width and height of each frame, u16, up to a zero), then the frames, each in
    columns of eight rows: one byte per column, bit 0 its top row, 1 white."""
    if len(zic) < 8 or zic[:4] != b"ZBMP":
        return None
    length = int.from_bytes(zic[4:8], "little")
    frames = []
    for at in range(8, min(8 + length, len(zic)) - 3, 4):
        width, height = struct.unpack_from("<HH", zic, at)
        if not width or not height:
            break
        frames.append((width, height))
    if not frames or 8 + length + sum(width * -(-height // 8) for width, height in frames) != len(zic):
        return None
    width, height = frames[-1]
    frame = zic[len(zic) - width * -(-height // 8):]
    rows = bytearray()
    for y in range(height):
        bits = 0
        for x in range(width):
            bits = bits << 1 | (frame[(y >> 3) * width + x] >> (y & 7)) & 1
        rows += b"\0" + (bits << -width % 8).to_bytes(-(-width // 8), "big")   # filter none, then the row

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))


def _created(info: dict) -> float:
    """When a backup was made: "created_ts", or for an older installer its "created" (ISO time)."""
    stamp = info.get("created_ts")
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
        return float(stamp)
    try:
        return datetime.datetime.fromisoformat(str(info.get("created", ""))).timestamp()
    except ValueError:
        return 0.0


def folders(data_root) -> list:
    """Where the pictures are looked for, the first that has one counts: the files of the newest
    complete backup (as nam_bank.patches_using_bank takes it), those of the other backups from
    the newest on, then the effects adapted for a pedal (Adapted/<model>), then the folder itself
    (the pictures export.py wrote onto the Pi's card), last the ones that come with the program
    (BUNDLED). Custom effects change their ids from one
    backup to the next; their file names stay."""
    backups = []
    for files in Path(data_root, "Backups").glob("*/files"):
        try:
            info = json.loads((files.parent / "manifest.json").read_text(encoding="utf-8"))
            backups.append((info.get("complete") is True, _created(info), files))
        except (OSError, ValueError, TypeError, AttributeError):     # no manifest, or not as the installer writes it
            backups.append((False, 0.0, files))
    newest = max((backup for backup in backups if backup[0]), key=lambda backup: backup[1], default=None)
    backups.sort(key=lambda backup: (backup is newest, backup[1]), reverse=True)
    return [files for *_, files in backups] + sorted(Path(data_root, "Adapted").glob("*")) + [Path(data_root), BUNDLED]


def find(data_root) -> dict:
    """{NAME.ZIC in upper case: the file that counts for it} (see folders)."""
    found: dict = {}
    for folder in folders(data_root):
        try:
            paths = list(folder.iterdir())
        except OSError:
            continue
        for path in paths:
            if path.suffix.upper() == ".ZIC":
                found.setdefault(path.name.upper(), path)
    return found


class Pictures:
    """The pictures in the installer's data folder (data_root) by the name of the effect's file,
    kept in memory. Where they are is looked at again after RECHECK_SECONDS: a new backup, or an
    effect missing until then, counts then. The interface asks from several threads at once."""

    def __init__(self, data_root) -> None:
        self.root = Path(data_root)
        self.lock = threading.Lock()
        self.found: dict = {}                      # NAME.ZIC → its file (find)
        self.found_at = float("-inf")
        self.kept: dict = {}                       # NAME.ZD2 → (its ZIC file or None, the PNG or None)

    def picture(self, file_name: str) -> Optional[bytes]:
        """The PNG for the effect whose file is so called ("DCOMP.ZD2"), None if there is none."""
        key = file_name.upper()
        with self.lock:
            if time.monotonic() - self.found_at > RECHECK_SECONDS:
                self.found, self.found_at = find(self.root), time.monotonic()
            path = self.found.get(Path(key).with_suffix(".ZIC").name)
            if key in self.kept and self.kept[key][0] == path:
                return self.kept[key][1]
            try:
                picture = png(path.read_bytes()) if path else None
            except OSError:
                picture = None
            if len(self.kept) >= KEPT:
                self.kept.clear()
            self.kept[key] = (path, picture)
            return picture
