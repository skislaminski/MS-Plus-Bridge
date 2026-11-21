#!/usr/bin/env python3
"""wt_import.py - turn a WAV file into the wavetable of the KeyWave effect.

Runs on the Mac when the effect is built. Nothing here talks to a pedal.

    wt_import.py <file.wav> [--frame-len 2048] [--frames 8] [--name NAME]
                 [--normalize table|frame|none] [--mix] [--dry-run]
                 [--table-only FILE]

Reads   a WAV file: mono, PCM 16 / 24 / 32 bit or 32 bit float, any sample
        rate (the rate does not matter: a frame is one cycle). --mix takes
        the mean of the channels of a stereo file.
Frames  The file is cut into frames of --frame-len samples (2048 is what
        wavetable editors write); a rest at the end is dropped. --frames of
        them go into the effect, spread evenly from the first to the last.
        A file with fewer frames is stretched: the frames in between are
        blends of their neighbours (so a single-cycle file gives a table
        that sounds the same at every position of Wave).
Levels  Every frame is taken apart into its harmonics (DC is dropped) and
        put together again once per pitch range with only the harmonics
        that fit below 0.45 x the sample rate there: see wt_layout.py. That
        is what keeps high notes from aliasing.
Writes  wt_wave.bin    the table, int16 little endian
        wt_wave.json   where it came from and how it is laid out
        and then runs gen_tables.py, which builds the effect's constants
        from it and brings manifest.json up to date.
        With --table-only FILE only the table is written, to FILE, and the
        effect is left as it is (the harness loads such files to test more
        than the one table that is built in).
Size    The constants of the whole effect may have 12000 bytes. The script
        prints the size and stops before writing anything if a table would
        not fit (--frames is what makes it smaller).
"""
import argparse
import json
import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wt_layout as lay


class ImportError_(Exception):
    pass


def read_wav(path, mix=False):
    """-> (samples as floats -1..1, sample rate, description)"""
    return read_wav_bytes(Path(path).read_bytes(), mix, str(path))


def read_wav_bytes(data, mix=False, path="the file"):
    """read_wav for a file that is already in memory"""
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ImportError_("%s is not a RIFF/WAVE file" % path)
    pos, fmt, body = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        chunk = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            if len(chunk) < 16:
                raise ImportError_("fmt chunk too short")
            tag, ch, rate, _, _, bits = struct.unpack("<HHIIHH", chunk[:16])
            if tag == 0xFFFE and len(chunk) >= 26:          # WAVE_FORMAT_EXTENSIBLE
                tag = struct.unpack("<H", chunk[24:26])[0]
            fmt = (tag, ch, rate, bits)
        elif cid == b"data":
            body = chunk                                    # a length field beyond the file is cut by the slice
        pos += 8 + size + (size & 1)
    if fmt is None or body is None:
        raise ImportError_("%s has no fmt or no data chunk" % path)
    tag, ch, rate, bits = fmt
    if ch < 1:
        raise ImportError_("no channels")
    if ch > 1 and not mix:
        raise ImportError_("%d channels: the table is mono; --mix takes the mean of the channels" % ch)
    if tag == 1 and bits in (16, 24, 32):
        width = bits // 8
        n = len(body) // width
        if bits == 16:
            vals = [v / 32768.0 for v in struct.unpack("<%dh" % n, body[:n * 2])]
        elif bits == 32:
            vals = [v / 2147483648.0 for v in struct.unpack("<%di" % n, body[:n * 4])]
        else:
            vals = [int.from_bytes(body[i:i + 3], "little", signed=True) / 8388608.0
                    for i in range(0, n * 3, 3)]
        kind = "PCM %d bit" % bits
    elif tag == 3 and bits == 32:
        n = len(body) // 4
        vals = list(struct.unpack("<%df" % n, body[:n * 4]))
        kind = "32 bit float"
    else:
        raise ImportError_("format tag %d with %d bits is not supported (PCM 16/24/32 or 32 bit float)" % (tag, bits))
    if any(v != v or v in (float("inf"), float("-inf")) for v in vals):
        raise ImportError_("the file holds NaN or Inf samples")
    if ch > 1:
        vals = [sum(vals[i:i + ch]) / ch for i in range(0, len(vals) - ch + 1, ch)]
        kind += ", %d channels mixed" % ch
    return vals, rate, kind


def harmonics(frame, kmax):
    """cosine and sine parts of harmonics 1..kmax of one cycle (plain DFT of those bins)"""
    n = len(frame)
    kmax = min(kmax, (n - 1) // 2)
    cos_t = [math.cos(2.0 * math.pi * i / n) for i in range(n)]
    sin_t = [math.sin(2.0 * math.pi * i / n) for i in range(n)]
    out = []
    for k in range(1, kmax + 1):
        a = b = 0.0
        j = 0
        for v in frame:
            a += v * cos_t[j]
            b += v * sin_t[j]
            j += k
            if j >= n:
                j -= n
        out.append((2.0 * a / n, 2.0 * b / n))
    return out


def synth(harm, kmax, length):
    """one cycle of `length` samples from the harmonics 1..kmax"""
    cos_t = [math.cos(2.0 * math.pi * i / length) for i in range(length)]
    sin_t = [math.sin(2.0 * math.pi * i / length) for i in range(length)]
    out = [0.0] * length
    for k, (a, b) in enumerate(harm[:kmax], start=1):
        if a == 0.0 and b == 0.0:
            continue
        for i in range(length):
            j = (k * i) % length
            out[i] += a * cos_t[j] + b * sin_t[j]
    return out


def pick_frames(count, want, frame):
    """`want` sets of harmonics spread evenly over the `count` source frames,
    blends in between. frame(i) gives the harmonics of source frame i; only
    the frames that are needed are asked for."""
    if count == 1 or want == 1:
        return [frame(0)] * want, [0.0] * want
    out, where = [], []
    for j in range(want):
        x = j * (count - 1) / (want - 1)
        i = min(int(x), count - 2)
        f = x - i
        if f < 1e-9:
            out.append(frame(i))
        elif f > 1.0 - 1e-9:
            out.append(frame(i + 1))
        else:
            out.append([((1 - f) * a0 + f * a1, (1 - f) * b0 + f * b1)
                        for (a0, b0), (a1, b1) in zip(frame(i), frame(i + 1))])
        where.append(round(x, 4))
    return out, where


def build_table(samples, frame_len, want, normalize):
    """-> (list of int16, info dict)"""
    if frame_len < 8:
        raise ImportError_("--frame-len has to be at least 8")
    count = len(samples) // frame_len
    if count < 1:
        raise ImportError_("the file has %d samples, less than one frame of %d" % (len(samples), frame_len))
    kmax = max(h for h, _ in lay.LEVELS)
    done = {}

    def frame(i):
        if i not in done:
            h = harmonics(samples[i * frame_len:(i + 1) * frame_len], kmax)
            done[i] = h + [(0.0, 0.0)] * (kmax - len(h))
        return done[i]

    harm, where = pick_frames(count, want, frame)
    waves = [[synth(h, hm, n) for hm, n in lay.LEVELS] for h in harm]
    peaks = [max(max(abs(v) for v in lvl) for lvl in fr) for fr in waves]
    top = max(peaks)
    if top < 1.0e-9:                # what the sums leave of a constant is rounding noise, not a wave
        raise ImportError_("the frames hold nothing but DC or silence")
    out = []
    for fr, pk in zip(waves, peaks):
        if normalize == "frame":
            # the same limit as for the whole table: below it a frame holds only
            # what the sums leave of DC (rounding noise), which must stay silent
            g = lay.FULL_SCALE / pk if pk >= 1.0e-9 else 0.0
        elif normalize == "table":
            g = lay.FULL_SCALE / top
        else:
            g = float(lay.FULL_SCALE)
        for lvl in fr:
            for v in lvl:
                q = int(round(v * g))
                out.append(max(-lay.FULL_SCALE, min(lay.FULL_SCALE, q)))
    info = {
        "frames": want,
        "source_frames": count,
        "source_frame_len": frame_len,
        "dropped_samples": len(samples) - count * frame_len,
        "taken_at": where,
        "normalize": normalize,
        "frame_peaks": [round(p, 6) for p in peaks],
        "levels": [{"harmonics": h, "samples": n} for h, n in lay.LEVELS],
        "frame_samples": lay.FRAME_SAMPLES,
        "wave_bytes": 2 * len(out),
    }
    return out, info


def main(argv=None):
    ap = argparse.ArgumentParser(description="WAV -> wavetable of the KeyWave effect")
    ap.add_argument("wav")
    ap.add_argument("--frame-len", type=int, default=2048)
    ap.add_argument("--frames", type=int, default=lay.FRAMES_DEFAULT)
    ap.add_argument("--name", default=None, help="name of the table (default: the file name)")
    ap.add_argument("--normalize", choices=["table", "frame", "none"], default="table")
    ap.add_argument("--mix", action="store_true", help="take the mean of the channels of a stereo file")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--out-dir", default=None, help="the effect folder (default: the one this script is in)")
    ap.add_argument("--table-only", default=None, metavar="FILE", help="write only the table, to FILE")
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent.parent
    name = args.name or Path(args.wav).stem
    try:
        if not 2 <= args.frames <= 64:
            raise ImportError_("--frames has to be 2..64")
        total = lay.const_bytes(args.frames)
        print("constants of the effect with %d frames: %d bytes (waves %d, other tables %d); limit %d"
              % (args.frames, total, 2 * args.frames * lay.FRAME_SAMPLES, 4 * lay.OTHER_WORDS, lay.CONST_LIMIT_BYTES))
        if total > lay.CONST_LIMIT_BYTES:
            most = (lay.CONST_LIMIT_BYTES // 4 - lay.OTHER_WORDS) * 2 // lay.FRAME_SAMPLES
            raise ImportError_("too large by %d bytes; at most %d frames fit" % (total - lay.CONST_LIMIT_BYTES, most))
        samples, rate, kind = read_wav(args.wav, args.mix)
        table, info = build_table(samples, args.frame_len, args.frames, args.normalize)
    except ImportError_ as e:
        print("wt_import: %s" % e, file=sys.stderr)
        return 1
    info.update({"name": name, "source": Path(args.wav).name, "source_format": "%s, %d Hz" % (kind, rate),
                 "const_bytes": total, "const_limit": lay.CONST_LIMIT_BYTES})
    print("%s: %s, %d Hz, %d samples = %d frames of %d (%d samples dropped)"
          % (info["source"], kind, rate, len(samples), info["source_frames"], args.frame_len, info["dropped_samples"]))
    print("table '%s': %d frames x %d samples x 16 bit = %d bytes, taken at source frames %s"
          % (name, args.frames, lay.FRAME_SAMPLES, info["wave_bytes"], info["taken_at"]))
    if args.dry_run:
        print("dry run: nothing written")
        return 0
    if args.table_only:
        Path(args.table_only).write_bytes(struct.pack("<%dh" % len(table), *table))
        print("wrote  %s" % args.table_only)
        return 0
    if not (out_dir / "gen_tables.py").is_file() or not (out_dir / "manifest.json").is_file():
        print("wt_import: %s is not the effect folder (no gen_tables.py, no manifest.json); nothing written.\n"
              "           --table-only FILE writes just the table." % out_dir, file=sys.stderr)
        return 1
    (out_dir / "wt_wave.bin").write_bytes(struct.pack("<%dh" % len(table), *table))
    (out_dir / "wt_wave.json").write_text(json.dumps(info, indent=2) + "\n")
    print("wrote  wt_wave.bin, wt_wave.json")
    sys.path.insert(0, str(out_dir))
    import gen_tables
    return gen_tables.build(out_dir).main([])


if __name__ == "__main__":
    sys.exit(main())
