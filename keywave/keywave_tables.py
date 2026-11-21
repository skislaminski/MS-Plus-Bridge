#!/usr/bin/env python3
"""keywave_tables.py - put another wavetable into the KeyWave effect, without
building it again. The core of the program "KeyWave Tables"; also a command
line tool. Nothing here talks to a pedal.

    keywave_tables.py <file.wav> [--out DIR] [--name NAME]
                      [--frame-len auto|256|512|1024|2048|4096]
                      [--normalize table|frame|none] [--base KEYWAVE.ZD2]

How: the wavetable is part of the effect's constants. It sits in the code of
the effect (the ELF inside the DATA section of the ZD2), right behind the
effect's other tables (pitch, envelope, LFO, glide, level layout: 1796
bytes that do not depend on the wave). So the program

  1. turns the WAV into a table exactly as tools/wt_import.py does when the
     effect is built (same code: 8 frames, 6 levels, 16 bit),
  2. finds those 1796 bytes in a finished KEYWAVE.ZD2 (exactly once, inside
     DATA, with room for the 9984 bytes of wave behind them),
  3. writes the new wave over the old one, puts the name of the table into
     the description, and sets the container's CRC again.

The code of the effect is not touched: only the 9984 bytes of the wave, the
two description texts and the checksum change. The number of frames is
compiled into the kernel (8), so every table has 8 frames.

Out:    <out>/KEYWAVE.ZD2 and KEYWAVE.ZIC (the picture, unchanged), the pair
        stomphacks' pedal_diy.py installs. Same file name and id as the KeyWave
        on the pedal: take KeyWave out of all patches, uninstall it, then
        install the new file.
"""
import argparse
import hashlib
import json
import math
import os
import re
import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
for p in (HERE, TOOLS):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))
import wt_import                # noqa: E402
import wt_layout as lay         # noqa: E402

FRAMES = 8                      # compiled into the kernel (WT_FRAMES)
WAVE_BYTES = 2 * FRAMES * lay.FRAME_SAMPLES
OTHER_BYTES = 4 * lay.OTHER_WORDS
FRAME_LENS = (256, 512, 1024, 2048, 4096)
NAME_MAX = 24
FILENAME = "KEYWAVE"

# The effect's description, as gen_tables.py writes it (keep the two alike).
DESCRIPTION = ("Wavetable mono synth played from a MIDI keyboard through the MS Plus Bridge, which writes "
               "gate and pitch into the Key knob. Wave moves through the frames of the table built into "
               "the effect (table: %s, %d frames). Level sets the synth. "
               "While the effect is on only the synth is heard; off, the bass passes.")
DESC_RE = re.compile(rb"\(table: ([^,]*), (\d+) frames\)")
# The last sentence of DESCRIPTION. KeyWave 1.00 has it; the prototypes before it
# said "Mix sets bass and synth" or "Level sets the synth; the bass passes
# unchanged" (the bass passed while the effect was on).
DESC_LAST = b"While the effect is on only the synth is heard; off, the bass passes."
INFO_FILE = "TABLE.json"    # where a table came from, written next to KEYWAVE.ZD2


class TableError(Exception):
    """a message for the user: what is wrong and what to do"""


def resource(name):
    """a file the app carries: next to this script (app bundle, or copied by
    packaging/build_mac.sh), else where the effect is built"""
    bundle = Path(getattr(sys, "_MEIPASS", HERE))
    for d in (bundle / "base", HERE / "base", HERE.parent / "install", HERE.parent):
        if (d / name).is_file():
            return d / name
    raise TableError("%s not found (looked in %s/base and %s/install)" % (name, HERE, HERE.parent))


# ---- reading the WAV -------------------------------------------------------

def clm_frame_len(data):
    """the frame length a 'clm ' chunk names (Serum and others write
    '<!>2048 ...'), or None"""
    pos = 12
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if cid == b"clm ":
            m = re.match(rb"<!>(\d+)", data[pos + 8:pos + 8 + min(size, 32)])
            if m:
                return int(m.group(1))
        pos += 8 + size + (size & 1)
    return None


def energy(x, kmax):
    """energy of x in harmonics 1..kmax of its length"""
    n = len(x)
    cos_t = [math.cos(2.0 * math.pi * i / n) for i in range(n)]
    sin_t = [math.sin(2.0 * math.pi * i / n) for i in range(n)]
    out = []
    for k in range(1, kmax + 1):
        a = b = 0.0
        j = 0
        for v in x:
            a += v * cos_t[j]
            b += v * sin_t[j]
            j += k
            if j >= n:
                j -= n
        out.append(a * a + b * b)
    return out


def guess_frame_len(samples, data=b""):
    """-> (frame length, how it was found)

    A 'clm ' chunk says it (Serum, Vital and others write one), if it names 8
    to 4096 samples. A short file that fits no usual length is one cycle (AKWF
    files have 600 samples). Otherwise the largest L of 256 .. 2048 that
    divides the file - unless its first frame that is not silent holds div
    cycles of L/div: then it has (nearly) nothing below harmonic div, and most
    of its energy sits on the multiples of div. WaveEdit writes 64 frames of
    256, which also divides into 8 of 2048. A bright frame (buzz, noise, a
    narrow pulse) also has little at the bottom, but its energy is spread
    over all harmonics, so it stays one cycle of L."""
    c = clm_frame_len(data) if data else None
    n = len(samples)
    if c and 8 <= c <= min(n, max(FRAME_LENS)):
        return c, "as the file says"
    fits = [L for L in FRAME_LENS[:-1] if n % L == 0]
    if not fits:
        if 8 <= n <= max(FRAME_LENS):
            return n, "a single cycle"
        return 2048, "default; the length of the file fits no usual frame length"
    L = max(fits)
    s, step = 0, FRAME_LENS[0]      # leading silent frames say nothing: skip them
    while s + L < n and not any(samples[s:s + step]):
        s += step
    s = min(s, n - L)
    e = energy(samples[s:s + L], min(256, L // 2))
    tot = sum(e)
    if tot <= 0.0:
        return L, "detected"
    for div in (8, 4, 2):           # the shortest frames first
        if (L // div >= 256 and sum(e[:div // 2]) < 0.02 * tot
                and sum(e[div - 1::div]) > 0.7 * tot):
            return L // div, "detected"
    return L, "detected"


def load(data, frame_len="auto", normalize="table", label="the file"):
    """WAV bytes -> (table as int16 list, info dict). Stereo is mixed."""
    try:
        samples, rate, kind = wt_import.read_wav_bytes(data, mix=True, path=label)
    except wt_import.ImportError_ as e:
        raise TableError(str(e))
    if frame_len in (None, "auto", 0, ""):
        fl, how = guess_frame_len(samples, data)
    else:
        try:
            fl = int(frame_len)
        except (TypeError, ValueError, OverflowError):
            fl = 0
        if not 8 <= fl <= max(FRAME_LENS):
            raise TableError("frame length: auto, or 8 to %d samples (not %s)" % (max(FRAME_LENS), frame_len))
        how = "chosen by hand"
    if normalize not in ("table", "frame", "none"):
        raise TableError("normalize: table, frame or none")
    try:
        table, info = wt_import.build_table(samples, fl, FRAMES, normalize)
    except wt_import.ImportError_ as e:
        raise TableError(str(e))
    info.update({"source_format": "%s, %d Hz" % (kind, rate), "frame_len_how": how,
                 "source_samples": len(samples)})
    return table, info


def preview(table):
    """per frame: the cycle of level 0 (256 samples, -1..1) and its harmonics
    1..127 as (cos, sin) pairs; what the window draws and plays"""
    n0 = lay.LEVELS[0][1]
    out = []
    for f in range(FRAMES):
        cyc = [v / lay.FULL_SCALE for v in table[f * lay.FRAME_SAMPLES:f * lay.FRAME_SAMPLES + n0]]
        harm = wt_import.harmonics(cyc, lay.LEVELS[0][0])
        out.append({"wave": [round(v, 5) for v in cyc],
                    "cos": [round(a, 6) for a, _ in harm],
                    "sin": [round(b, 6) for _, b in harm]})
    return out


# ---- the ZD2 ---------------------------------------------------------------

def sections(b):
    """-> list of (tag, payload offset, length); tiles the file from ICON to the trailer"""
    i = b.find(b"ICON")
    if b[:4] != b"ZDLF" or i < 0:
        raise TableError("not a ZD2 file")
    out = []
    while i + 8 <= len(b) - 16:
        tag = b[i:i + 4]
        n = struct.unpack("<I", b[i + 4:i + 8])[0]
        if i + 8 + n > len(b) - 16:
            raise TableError("ZD2: section %s runs past the end of the file" % tag.decode("ascii", "replace"))
        out.append((tag, i + 8, n))
        i += 8 + n
    if i != len(b) - 16:
        raise TableError("ZD2: the sections do not fill the file up to its end")
    return out


def crc(b):
    """the ZD2 checksum: CRC32 over data[12:-16], stored inverted"""
    return zlib.crc32(b[12:-16]) ^ 0xFFFFFFFF


def find_wave(base, anchor):
    """-> offset of the wave in the ZD2"""
    secs = sections(base)
    if struct.unpack_from("<I", base, 8)[0] != crc(base):
        raise TableError("the checksum of the ZD2 is wrong: is the file damaged?")
    data = [(o, n) for t, o, n in secs if t == b"DATA"]
    if len(data) != 1:
        raise TableError("the ZD2 has %d DATA sections (expected: one)" % len(data))
    o, n = data[0]
    if base[o:o + 4] != b"\x7fELF":
        raise TableError("the DATA section of the ZD2 is not an ELF")
    hits = []
    i = base.find(anchor, o, o + n)
    while i >= 0:
        hits.append(i)
        i = base.find(anchor, i + 1, o + n)
    if len(hits) != 1:
        raise TableError("the tables of KeyWave are in the ZD2 %d times (expected: once). "
                         "Is it another effect or another version of KeyWave?" % len(hits))
    w = hits[0] + len(anchor)
    if w + WAVE_BYTES > o + n:
        raise TableError("there is no room for %d bytes of wave behind the tables" % WAVE_BYTES)
    txe = [(o, n) for t, o, n in secs if t == b"TXE1"]
    m = DESC_RE.search(base[txe[0][0]:txe[0][0] + txe[0][1]]) if txe else None
    if not m or int(m.group(2)) != FRAMES:
        raise TableError("the ZD2 does not say that it has %d frames" % FRAMES)
    return w


WINDOWS_DEVICES = re.compile(r"(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..*)?$", re.I)


def clean_name(name):
    """the table's name, also the name of its folder: letters, digits, spaces and
    _ . + -; no dots or spaces at either end (".." and "." are no folder of
    their own, ".x" would be hidden, Windows drops them at the end), and not a
    name Windows keeps for a device (CON, NUL ...)"""
    name = re.sub(r"[^A-Za-z0-9 _.+-]", "", name or "").strip(" .")[:NAME_MAX].strip(" .")
    if WINDOWS_DEVICES.match(name):
        name = "_" + name[:NAME_MAX - 1].rstrip(" .")
    return name or "table"


def patch(base, table, name, anchor):
    """-> the new ZD2 bytes"""
    if len(table) != FRAMES * lay.FRAME_SAMPLES:
        raise TableError("the table has %d samples, expected %d" % (len(table), FRAMES * lay.FRAME_SAMPLES))
    if any(not -32768 <= v <= 32767 for v in table):
        raise TableError("a sample is outside the 16 bit range")
    w = find_wave(base, anchor)
    # Older prototypes of KeyWave have Mix as their last knob, which cuts the bass
    # above 50, or let the bass pass while they are on. The description written
    # below says what 1.00 does (on, only the synth), so it may only go into that.
    if DESC_LAST not in base:
        raise TableError("the base KEYWAVE.ZD2 is an older KeyWave (it has Mix instead of Level, or "
                         "lets the bass pass while it is on): use the base file that comes with the "
                         "app, or KeyWave 1.00 or later")
    b = bytearray(base)
    b[w:w + WAVE_BYTES] = struct.pack("<%dh" % len(table), *table)
    text = (DESCRIPTION % (clean_name(name), FRAMES)).encode("ascii") + b"\0"
    out = bytearray(b[:sections(base)[0][1] - 8])          # header up to the first section
    for tag, o, n in sections(bytes(b)):
        payload = text if tag in (b"TXE1", b"TXJ1") else bytes(b[o:o + n])
        out += tag + struct.pack("<I", len(payload)) + payload
    out += b[-16:]
    struct.pack_into("<I", out, 8, crc(out))
    return bytes(out)


def table_in(zd2, anchor):
    """the wave as int16 list, and the table's name from the description"""
    w = find_wave(zd2, anchor)
    tab = list(struct.unpack("<%dh" % (WAVE_BYTES // 2), zd2[w:w + WAVE_BYTES]))
    m = DESC_RE.search(zd2)
    return tab, (m.group(1).decode("ascii", "replace") if m else "?")


def base_files(base_path=None):
    """-> (ZD2 bytes, ZIC bytes, anchor bytes, description)"""
    zd2 = Path(base_path) if base_path else resource(FILENAME + ".ZD2")
    zic = zd2.with_suffix(".ZIC")
    if not zic.is_file():
        zic = resource(FILENAME + ".ZIC")
    anchor = resource("wt_tables.bin").read_bytes()[:OTHER_BYTES]
    if len(anchor) != OTHER_BYTES:
        raise TableError("wt_tables.bin is too short")
    return zd2.read_bytes(), zic.read_bytes(), anchor, str(zd2)


def write_effect(out_dir, zd2, zic, info=None):
    """KEYWAVE.ZIC, TABLE.json and KEYWAVE.ZD2 into out_dir. Each is written
    to <name>.tmp next to it first and takes the place of the old file only
    when it is complete, KEYWAVE.ZD2 last: a full disk or a pulled stick leaves
    the old files (or none), never a cut-off KEYWAVE.ZD2."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = [(FILENAME + ".ZIC", zic, None)]
    if info is not None:
        # UTF-8 everywhere: Windows would otherwise take its ANSI code page, which
        # cannot hold every character a source file name may have
        files.append((INFO_FILE, json.dumps(info, indent=2, ensure_ascii=False) + "\n", "utf-8"))
    files.append((FILENAME + ".ZD2", zd2, None))
    tmps = []
    try:
        for name, body, encoding in files:
            tmp = out / (name + ".tmp")
            tmps.append(tmp)
            with (open(tmp, "w", encoding=encoding) if encoding else open(tmp, "wb")) as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
        for (name, _, _), tmp in zip(files, tmps):
            os.replace(str(tmp), str(out / name))
    finally:
        for tmp in tmps:
            try:
                if tmp.is_file():
                    tmp.unlink()
            except OSError:
                pass
    return out / (FILENAME + ".ZD2")


def main(argv=None):
    ap = argparse.ArgumentParser(description="WAV -> KEYWAVE.ZD2 with that wavetable")
    ap.add_argument("wav")
    ap.add_argument("--out", default=None, help="folder for KEYWAVE.ZD2/.ZIC (default: KeyWave-<name> next to the WAV)")
    ap.add_argument("--name", default=None)
    ap.add_argument("--frame-len", default="auto")
    ap.add_argument("--normalize", default="table", choices=["table", "frame", "none"])
    ap.add_argument("--base", default=None, help="another KEYWAVE.ZD2 to start from")
    a = ap.parse_args(argv)
    try:
        data = Path(a.wav).read_bytes()
        table, info = load(data, a.frame_len, a.normalize, a.wav)
        zd2, zic, anchor, src = base_files(a.base)
        name = clean_name(a.name or Path(a.wav).stem)
        new = patch(zd2, table, name, anchor)
    except (TableError, OSError) as e:
        print("keywave_tables: %s" % e, file=sys.stderr)
        return 1
    info.update({"name": name, "source": Path(a.wav).name, "base": Path(src).name,
                 "base_sha256": hashlib.sha256(zd2).hexdigest(), "zd2_sha256": hashlib.sha256(new).hexdigest()})
    out = Path(a.out) if a.out else Path(a.wav).resolve().parent / ("KeyWave-" + name)
    try:
        p = write_effect(out, new, zic, info)
    except OSError as e:
        print("keywave_tables: could not write into %s: %s" % (out, e.strerror or e), file=sys.stderr)
        return 1
    print("%s: %d samples, frames of %d (%s), taken at %s" % (info["source"], info["source_samples"],
          info["source_frame_len"], info["frame_len_how"], info["taken_at"]))
    print("wrote %s and KEYWAVE.ZIC (table '%s')" % (p, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
