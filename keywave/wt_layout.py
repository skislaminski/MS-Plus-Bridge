"""wt_layout.py - how a wavetable is laid out inside the KeyWave effect.

One place for the numbers that the import script, the table generator, the
kernel (through the generated wt_tables.h) and the tests share.

A table is FRAMES frames; the Wave knob moves through them. Every frame is
stored in LEVELS versions, one per pitch range, each with only as many
harmonics as fit below 0.45 x the sample rate at the top of its range:

    level  harmonics  samples  used up to
      0       127       256      156 Hz
      1        63       128      315 Hz
      2        31       128      640 Hz
      3        15        64     1323 Hz
      4         7        32     2835 Hz
      5         3        16     6615 Hz   (the Key knob ends at 5274 Hz)

624 samples per frame, 16 bit each. The samples per level are a power of two
(the kernel masks the index) and at least twice the harmonics (level 0, 1)
or four times (2..5). The kernel reads between the samples with a cubic
through four of them; what that gets wrong comes back as aliasing, and the
less so the more samples a cycle of the highest harmonic has. Measured at
the top of each level for a wave with harmonics falling as 1/k: about
-38 dB against the fundamental at two samples per cycle (nearly all of it
above 10 kHz), -47 to -57 dB at four. A straight line between two samples
would give -33 to -46 dB at four. The harness measures every example table
over all octaves.

With 8 frames: 8 x 624 x 2 = 9984 bytes of waves. The effect's other tables
(pitch, envelope, LFO, glide, level layout) take 1796 bytes, together
11780 of the 12000 bytes the constants may have.
"""

FS = 44100.0
FRAMES_DEFAULT = 8
LEVELS = [(127, 256), (63, 128), (31, 128), (15, 64), (7, 32), (3, 16)]    # (harmonics, samples)
FRAME_SAMPLES = sum(n for _, n in LEVELS)
INC_MAX = 0.45                      # keep every harmonic below this x the sample rate
CONST_LIMIT_BYTES = 12000           # constants of the whole effect, waves included
OTHER_WORDS = 128 + 3 * 101 + 3 * len(LEVELS)      # INC, ENV, LFO, GLD, level layout
FULL_SCALE = 32767


def level_offsets():
    off, out = 0, []
    for _, n in LEVELS:
        out.append(off)
        off += n
    return out


def level_top_inc():
    """phase increment up to which a level may be used"""
    return [INC_MAX / h for h, _ in LEVELS]


def const_bytes(frames):
    samples = frames * FRAME_SAMPLES
    return 4 * (OTHER_WORDS + (samples + 1) // 2)
