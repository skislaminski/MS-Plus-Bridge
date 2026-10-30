"""SysEx for the Zoom MS-60B+: builders, allowlist, parsers and the send function.

The protocol is unofficial (reference: https://github.com/thammer/zoom-explorer, README,
documented on the MS-50G+). Every SysEx message in this project is created by a build_* function
and leaves the program only through ZoomSender.send(), where the allowlist and approvals apply.
"""
from __future__ import annotations

import binascii
import json
import logging
import logging.handlers
import os
import re
import string
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, NamedTuple, Optional

import mido

import effect_names

log = logging.getLogger("sysex")

SOX, EOX = 0xF0, 0xF7
ZOOM = 0x52

IDENTITY_REQUEST = bytes([SOX, 0x7E, 0x7F, 0x06, 0x01, EOX])

# Message kinds on the allowlist and their form (for display and approval).
KINDS = {
    "identity_request": "F0 7E 7F 06 01 F7",
    "edit_enable": "F0 52 00 <ID> 50 F7",
    "edit_disable": "F0 52 00 <ID> 51 F7",
    "set_param": "F0 52 00 <ID> 64 20 00 <slot> <param> <LSB> <MSB> 00 00 00 F7",
    "query_program": "F0 52 00 <ID> 33 F7",
    "query_patch": "F0 52 00 <ID> 64 13 F7",
    "switch_effect": "F0 52 00 <ID> 64 20 00 <slot> 00 <0 or 1> 00 00 00 00 F7",
    "rename_patch": "F0 52 00 <ID> 64 20 00 5F <place 0–27> <character> 00 00 00 00 F7",
    "change_effect": "F0 52 00 <ID> 64 20 00 <slot> 01 <effect id, 7 bits per byte, 5 bytes> F7",
    "move_effect": "F0 52 00 <ID> 28 <the patch the pedal reported last, two neighbouring effects swapped, "
                   "7-bit packed> F7",
    "add_effect": "F0 52 00 <ID> 28 <the patch the pedal reported last, with an empty place inserted, "
                  "7-bit packed> F7",
    "delete_effect": "F0 52 00 <ID> 28 <the patch the pedal reported last, with one effect taken out, "
                     "7-bit packed> F7",
    "read_pedal": "PC mode on/off (F0 52 00 <ID> 52 F7, 53 F7) around the pedal's list of its files: "
                  "F0 52 00 <ID> 60 25|26 00 00 2A 00 F7, then 60 21 40 00 00 00 00 and 60 09 – reading only",
    "read_effect_file": "while the list of files is read: an effect file of that list opened for reading "
                        "(F0 52 00 <ID> 60 20 02 00×9 <name> 00 F7), its status (60 05 00) and its next 512 bytes "
                        "(60 22 14 2F 60 00 0C 00 04 00 00 00) – reading only, a .ZD2 file only",
    "select_preset": "B0 00 00, B0 20 <bank>, C0 <program> (bank select and program change on channel 1, "
                     "no SysEx)",
    "query_preset": "F0 52 00 <ID> 46 00 00 <bank LSB MSB> <program LSB MSB> F7",
    "save_preset": "F0 52 00 <ID> 45 00 00 <bank LSB MSB> <program LSB MSB> <length LSB MSB> <the patch the "
                   "pedal reported last, 7-bit packed> <CRC32, 5 bytes> F7",
}

_FIXED_BODIES = {
    bytes([0x50]): "edit_enable",
    bytes([0x51]): "edit_disable",
    bytes([0x33]): "query_program",
    bytes([0x64, 0x13]): "query_patch",
}

# Safety rule 2: explicitly forbidden (firmware mode, factory reset, file access, overwriting
# patches, system/patch settings, inserting/deleting/moving effects).
FORBIDDEN_PREFIXES = (
    bytes([0x01]),
    bytes([0x04]),
    bytes([0x5B]),
    bytes([0x28]),
    bytes([0x60]),
    bytes([0x64, 0x47]),
    bytes([0x64, 0x20, 0x00, 0x64]),
    bytes([0x32]),                       # store the current patch (original MS series)
    bytes([0x45]),                       # a patch into a memory place: only the one prepared to save
)

# In the slot position these bytes mean something else according to the reference:
# 5F = edit patch name, 64 = patch/system settings.
RESERVED_SLOTS = (0x5F, 0x64)
# The preset's name, as the MS-60B+ reports a name edited on it (measured 2026-10-19): one
# `64 20 00 5F <place> <character> 00 00 00 00` per character, places 0–27, filled up with spaces.
# The patch holds the same 28 characters in its chunk "NAME" (and the first 10 at offset 26).
NAME_SLOT = 0x5F
NAME_LENGTH = 28
# The characters Zoom's manuals list for patch names.
NAME_CHARACTERS = frozenset(string.ascii_letters + string.digits + " !#$%&'()+,-.;=@[]^_`{}~")

# From the zoom-explorer source (src/ZoomDevice.ts, not in the README): param 0 switches the
# effect on/off, param 1 changes the effect type, real parameters start at 2.
MIN_PARAM = 2
SWITCH_PARAM = 0        # written by build_switch_effect only, with 0 or 1, as a message kind of its own
EFFECT_PARAM = 1        # written by build_change_effect only, with the id of an effect the pedal has
EFFECT_ID_TEXT = re.compile(r"[0-9A-Fa-f]{8}")          # an effect id written out, as the patch has it
# The knobs in an effect's EDTB block, after on/off (1 bit) and its id (29): this many bits each,
# from parameter 2 on (zoom-explorer's PTCF_EDTB_PARAM_WIDTHS; knobs past these are not stored).
# Checked against the MS-60B+: 151 knobs turned on it (parameters 2–13) read back the same from the
# next patch; and every knob of the 139 effects in zoom_effects.json fits the bits of its place.
EDTB_WIDTHS = (12, 12, 12, 12, 12, 8, 8, 8, 12, 8, 8, 8, 12, 8, 8, 8, 1, 1, 1)




# KeySynth, KeySweep, KeyFM, KeyPoly, KeyWave, KeyBand and Pluck, DIY effects played from a MIDI
# keyboard: a "Key" knob is gate and pitch in one number, so a note and its pitch bend can never
# arrive apart. KeyPoly and KeyBand (since 0.20) have four voices, Key1–Key4, each in that format;
# the others have one voice and one Key. How many a build has, its knob names say (key_knobs).
# Measured on the MS-60B+: the pedal has 6 effect slots.
MAX_VOICES = 4
KEY_MAX = 1000          # 0 = gate off, n = (n - 1) x 10 cents above KEY_BASE_NOTE
KEY_NAME = re.compile(r"Key\d*")    # what a Key knob is called: "Key", or "Key1" … with several voices
KEY_BASE_NOTE = 12      # the MIDI note of Key 1 (C0)
KEY_STEPS = 10          # Key clicks per semitone
KEY_LOW_NOTE = KEY_BASE_NOTE
KEY_HIGH_NOTE = KEY_BASE_NOTE + (KEY_MAX - 1) // KEY_STEPS    # 111 (D#8)
KEY_BEND_STEP = 9       # the effect takes a larger move of the Key knob for a new note, not a bend
# The knobs of each effect in the order of the pedal, from its manifest.json: name and highest
# value. Knob number k (from 0) is parameter k + 2. The Key knobs stay first: in a patch only knobs
# 1–5 and 9 have 12 bits (0–4095), the others 8 (0–255; EDTB_WIDTHS). The builds of 2026-10-20
# (KeySynth 1.12, KeySweep 0.11, KeyFM 0.11, KeyPoly 0.12, KeyWave 0.11) had Mix as the last knob,
# the bass against the synth; since those of 2026-10-22 (KeySynth 1.13, KeySweep 0.12, KeyFM 0.12,
# KeyPoly 0.14, KeyWave 0.12) it is Level, the synth's level alone (0–100, 80 as loud as Mix 50
# was), and the bass passed unchanged. Those of 2026-10-23 (KeySynth 1.14, KeySweep 0.13, KeyFM
# 0.13, KeyPoly 0.15, KeyWave 0.13) keep these knobs but mute the bass while they are on; nothing
# here relies on hearing it. KeySynth:
SYNTH_KNOBS = (("Key", KEY_MAX), ("Wave1", 9), ("Wave2", 4), ("Pitch", 48), ("Dtune", 100),
               ("Blend", 100), ("Glide", 100), ("Atk", 100), ("Rel", 100), ("LFO", 100),
               ("Rate", 100), ("Level", 100))
# KeySweep (called KeySynF before 2026-10-17): KeySynth's voice through a resonant low-pass with
# its own envelope.
SWEEP_KNOBS = (("Key", KEY_MAX), ("Wave", 9), ("Cutoff", 100), ("Reso", 100), ("FEnv", 100),
               ("FDcy", 100), ("Atk", 100), ("Rel", 100), ("Glide", 100), ("LFO", 100),
               ("Rate", 100), ("Level", 100))
# KeyFM: one sine modulates another.
FM_KNOBS = (("Key", KEY_MAX), ("Ratio", 15), ("Index", 100), ("MEnv", 100), ("MDcy", 100),
            ("Fdbk", 100), ("Atk", 100), ("Rel", 100), ("Glide", 100), ("LFO", 100),
            ("Rate", 100), ("Level", 100))
# KeyPoly: Key1–Key4 in KeySynth's Key format, then eight knobs shared by the voices.
POLY_KNOBS = tuple((f"Key{voice + 1}", KEY_MAX) for voice in range(MAX_VOICES)) + (
    ("Wave", 9), ("Cutoff", 100), ("Reso", 100), ("Atk", 100), ("Rel", 100), ("LFO", 100),
    ("Rate", 100), ("Level", 100))
# KeyWave (the wavetable prototype, WaveTbl before): Wave moves through the frames of a wavetable,
# WEnv pushes it with every note (50 = not at all). Its LFO is a depth (0 = off) on what LDest
# names: Wave, Pitch or Level.
WAVE_KNOBS = (("Key", KEY_MAX), ("Wave", 100), ("WEnv", 100), ("WDcy", 100), ("Sub", 100),
              ("Atk", 100), ("Rel", 100), ("Glide", 100), ("LFO", 100), ("Rate", 100),
              ("LDest", 2), ("Level", 100))
# KeyBand and Pluck (prototypes of 2026-10-21, known by number only) kept Level, the level of what
# they play, and never had Mix; Pluck adds to the bass, KeyBand 0.21 (2026-10-23, the knobs of 0.20)
# mutes it while on. Neither has an LFO knob. KeyBand 0.20 (2026-10-22): a piano, an electric
# piano, strings or brass (Voice) on four voices, Key1–Key4 as KeyPoly's; Vel is how hard a note is
# struck, read for a voice when it strikes and kept by it.
# Its builds 0.1x had one Key, a Chord and an Oct knob: where the table has such a build, it is
# played as that, with one voice (installed).
BAND_KNOBS = tuple((f"Key{voice + 1}", KEY_MAX) for voice in range(MAX_VOICES)) + (
    ("Voice", 3), ("Level", 100), ("Atk", 100), ("Dec", 100), ("Rel", 100), ("Tone", 100),
    ("Move", 100), ("Vel", 100))
# Pluck: Karplus-Strong strings, plucked by every new note on Key when Trig is 0 (Key), or by the
# bass when it is 1 (Bass, its default: then it ignores Key). A string rings out by Decay. Its 0.12
# (2026-10-24) calls knob 5 Tone, the builds before Bright: the same knob (KNOB_RENAMED). Its 0.13
# (2026-10-26) has Dry last, the bass beside the strings (80, its default: the bass as it is; a
# patch saved with an earlier build has 0 there, no bass).
PLUCK_KNOBS = (("Key", KEY_MAX), ("Level", 100), ("Trig", 1), ("Decay", 100), ("Tone", 100),
               ("Pick", 2), ("Oct", 2), ("Dyn", 100), ("Thr", 100), ("Dry", 100))
# An effect id is a category (the top byte) and a number. The category changes with where an
# installer puts the effect on the pedal (KeySweep is 07000f71 as built and 09000f71 in "SFX" of
# an MS-60B+), so the bridge knows the effects by the number alone.
EFFECT_NUMBER = 0xFFFFFF
MAX_SLOTS = 6


def is_effect_param(slot: int, param: int) -> bool:
    """A real effect parameter: not the patch name, a patch/system setting, on/off or effect type."""
    return slot not in RESERVED_SLOTS and param >= MIN_PARAM


class NotAllowed(Exception):
    """The message is not on the allowlist."""


class NotApproved(Exception):
    """The backup confirmation or the approval of the message kind is missing (rules 4 and 5)."""

    def __init__(self, kind: str):
        super().__init__(f"no approval for: {kind}")
        self.kind = kind


class PortError(Exception):
    """No MIDI port, or no unique one, matches a substring."""


@dataclass(frozen=True)
class ParamTarget:
    """A measured parameter; only these may be set, and only within the measured range."""

    slot: int
    param: int
    min: int
    max: int


def to_hex(data: Iterable[int]) -> str:
    return " ".join(f"{b:02X}" for b in data)


# --- Value encoding (7-bit split; confirmed on the MS-60B+ for values up to 100 only) ---

def encode_value(value: int) -> tuple[int, int]:
    if not 0 <= value <= 0x3FFF:
        raise ValueError(f"value {value} does not fit into 14 bits")
    return value & 0x7F, value >> 7


def decode_value(lsb: int, msb: int) -> int:
    return lsb | (msb << 7)


# --- Builders ---

def _zoom(device_id: int, *body: int) -> bytes:
    return bytes([SOX, ZOOM, 0x00, device_id, *body, EOX])


def build_identity_request() -> bytes:
    return IDENTITY_REQUEST


def build_edit_enable(device_id: int) -> bytes:
    return _zoom(device_id, 0x50)


def build_edit_disable(device_id: int) -> bytes:
    return _zoom(device_id, 0x51)


def build_set_param(device_id: int, slot: int, param: int, value: int) -> bytes:
    lsb, msb = encode_value(value)
    return _zoom(device_id, 0x64, 0x20, 0x00, slot, param, lsb, msb, 0x00, 0x00, 0x00)


def build_switch_effect(device_id: int, slot: int, on: bool) -> bytes:
    """An effect of the chain on or off, and nothing else: the "set parameter" message with the
    parameter fixed to 0 and the value 0 or 1 (per the zoom-explorer source). The effect type,
    parameter 1, stays out of reach."""
    if not 0 <= slot < MAX_SLOTS:
        raise ValueError(f"slot {slot} is not one of the {MAX_SLOTS} effect slots")
    return _zoom(device_id, 0x64, 0x20, 0x00, slot, SWITCH_PARAM, 1 if on else 0, 0x00, 0x00, 0x00, 0x00)


def build_name_character(device_id: int, place: int, character: str) -> bytes:
    """One character of the preset's name, as the pedal sends it when the name is edited on it."""
    if not 0 <= place < NAME_LENGTH:
        raise ValueError(f"place {place} is outside the {NAME_LENGTH} characters of a name")
    if character not in NAME_CHARACTERS:
        raise ValueError(f"{character!r} is not a character of a preset name")
    return _zoom(device_id, 0x64, 0x20, 0x00, NAME_SLOT, place, ord(character), 0x00, 0x00, 0x00, 0x00)


def effect_bytes(effect_id: int) -> tuple:
    """An effect's id as the value of parameter 1: 7 bits per byte, the lowest first."""
    return tuple((effect_id >> shift) & 0x7F for shift in (0, 7, 14, 21, 28))


def decode_effect(data: bytes) -> int:
    return sum(byte << 7 * index for index, byte in enumerate(data))


def build_change_effect(device_id: int, slot: int, effect_id: int) -> bytes:
    """Another effect in a place of the chain: the "set parameter" message for parameter 1, the
    effect type, with the effect's whole id as its value (as zoom-explorer sends it). The pedal
    starts the new effect's knobs at their defaults."""
    if not 0 <= slot < MAX_SLOTS:
        raise ValueError(f"slot {slot} is not one of the {MAX_SLOTS} effect slots")
    if not 0 < effect_id <= 0xFFFFFFFF:
        raise ValueError(f"{effect_id} is not an effect id")
    return _zoom(device_id, 0x64, 0x20, 0x00, slot, EFFECT_PARAM, *effect_bytes(effect_id))


def read_effect_ids(texts) -> set:
    """Effect ids written as eight hex digits (a list of them); anything else is left out, also an
    id without a category (its first byte 0: 00000FF0 for BPM, kept by Read Pedal until 2026-10-23)."""
    return {int(text, 16) for text in (texts if isinstance(texts, list) else ())
            if isinstance(text, str) and EFFECT_ID_TEXT.fullmatch(text) and int(text, 16) >> 24}


def build_set_key(device_id: int, slot: int, key: int, voice: int = 0, effect: "SynthEffect" = None) -> bytes:
    """A Key knob of the synth effect in the given slot, and nothing else: voice 0 is KeySynth's
    Key or KeyPoly's Key1, voices 1–3 are Key2–Key4, wherever the effect has them among its knobs
    (KeySynth if none is given). The same message as build_set_param with the parameter fixed to
    one of those, so it can never reach on/off or the effect type."""
    effect = effect or KEYSYNTH
    if not 0 <= slot < MAX_SLOTS:
        raise ValueError(f"slot {slot} is not one of the {MAX_SLOTS} effect slots")
    if not 0 <= voice < effect.voices:
        raise ValueError(f"voice {voice} is not one of the {effect.voices} Key knobs of {effect.name}")
    if not 0 <= key <= KEY_MAX:
        raise ValueError(f"key {key} is outside 0–{KEY_MAX}")
    return build_set_param(device_id, slot, effect.key_param + voice, key)


def key_for(note: int, bend: float = 0.0) -> int:
    """The Key value that sounds a MIDI note, bent by so many semitones. The result stays inside
    1–KEY_MAX, so a bend at the very top or bottom of the range is cut short."""
    if not KEY_LOW_NOTE <= note <= KEY_HIGH_NOTE:
        raise ValueError(f"note {note} is outside {KEY_LOW_NOTE}–{KEY_HIGH_NOTE}")
    return min(KEY_MAX, max(1, round((note - KEY_BASE_NOTE + bend) * KEY_STEPS) + 1))


def key_pitch(key: int) -> tuple[int, int]:
    """The nearest MIDI note of a Key value above 0, and how many cents the value is away from it."""
    clicks = key - 1
    note = (clicks + KEY_STEPS // 2) // KEY_STEPS
    return KEY_BASE_NOTE + note, (clicks - note * KEY_STEPS) * (100 // KEY_STEPS)


# What a controller of the keyboard can be assigned to, as name → (knob, value at rest, value at
# full travel): every knob but the Key knobs over its whole range, and the two halves of the LFO
# knob on their own. The LFO knob is depth and kind in one (Vib50 … Vib1, Off, Trm1 … Trm50), so
# a wheel on "LFO" would have Off in the middle of its travel. "Vib" and "Trm" run from Off to the
# deepest vibrato or tremolo instead: at rest the LFO is off, as with the mod wheel of a
# hardware synth. On KeySweep and KeyPoly the upper half moves the filter: "Flt" in place of "Trm".
# KeyWave's LFO knob is a plain depth from 0 = off, so it has no halves; KeyBand and Pluck have none.
LFO_OFF = 50


def key_knobs(knobs) -> range:
    """Where a build of a synth effect has its Key knobs, by their names: the first "Key" (one
    voice), or the first "Key1" and the "Key2" … right after it (one voice each, at most
    MAX_VOICES: KeyPoly, KeyBand 0.20); empty if it has neither."""
    names = [knob for knob, _ in knobs]
    first = next((index for index, name in enumerate(names) if name in ("Key", "Key1")), None)
    if first is None:
        return range(0)
    count = 1
    while (names[first] == "Key1" and count < MAX_VOICES
           and names[first + count:first + count + 1] == [f"Key{count + 1}"]):
        count += 1
    return range(first, first + count)


class SynthEffect:
    """A synth effect the keyboard plays: its Key knobs, one per voice next to each other ("Key",
    or "Key1" to "Key4"), wherever they are among its knobs; a controller of the keyboard can be
    assigned to any of the others (`controls`). Some read knobs at the moment a note is struck,
    which the bridge sends right before such a note: `velocity` is the knob that takes how hard
    the key was struck (KeyBand's Vel), `needs` the knobs the keyboard needs at a value to be heard
    at all, as knob → (value, what the pedal shows for it) (Pluck's Trig on Key)."""

    def __init__(self, name: str, number: int, knobs: tuple, lfo: Optional[str] = "Trm",
                 velocity: Optional[str] = None, needs: Optional[dict] = None):
        """As many voices as its Key knobs (key_knobs: KeyBand 0.1x one, 0.20 four). lfo: what the
        upper half of an LFO knob with Off in the middle is called; None for an LFO knob that is a
        plain depth, or for none. velocity and needs: left out where the build has no such knob (or
        that value is beyond it)."""
        keys = key_knobs(knobs)
        if not keys:
            raise ValueError(f"{name}: no Key knob")
        if any(index >= len(EDTB_WIDTHS) or EDTB_WIDTHS[index] < 10 for index in keys):
            # a patch keeps 0–KEY_MAX only in a place of 12 bits: what the bridge reads back must hold
            raise ValueError(f"{name}: its Key knobs must be on knobs with 12 bits")
        if any(KEY_NAME.fullmatch(knob) for index, (knob, _) in enumerate(knobs) if index not in keys):
            # a Key knob that is no voice (a Key3 elsewhere, a Key5) would be a control: notes from a wheel
            raise ValueError(f"{name}: its Key knobs must be next to each other, at most {MAX_VOICES}")
        self.name, self.number, self.knobs, self.voices, self.lfo = name, number, knobs, len(keys), lfo
        self.key_index = keys.start
        self.key_param = MIN_PARAM + self.key_index           # the first Key knob; Key2–Key4 follow it
        others = {knob: top for index, (knob, top) in enumerate(knobs) if index not in keys}
        self.controls = {knob: (knob, 0, top) for knob, top in others.items()}
        if lfo is not None:
            self.controls.update({"Vib": ("LFO", LFO_OFF, 0), lfo: ("LFO", LFO_OFF, 2 * LFO_OFF)})
        self.velocity = velocity if velocity in others else None
        self.needs = {knob: (value, shown) for knob, (value, shown) in (needs or {}).items()
                      if knob in others and 0 <= value <= others[knob]}

    def __repr__(self) -> str:
        return self.name


def installed(name: str, number: int, knobs: tuple, lfo: Optional[str] = "Trm", velocity: Optional[str] = None,
              needs: Optional[dict] = None) -> SynthEffect:
    """A synth effect with the knobs of the build on the pedal. Which build that is decides where
    its knobs are and how many voices it has (KeyBand 0.1x one, 0.20 four: key_knobs), so the knob
    list comes from the table of the pedal's effect files (zoom_effects.json, see effect_names.py)
    when it has the effect and its Key knobs are where a patch keeps them; otherwise from the lists
    above, the builds of 2026-10-22 (Pluck: of 2026-10-21). The tests
    set MS_PLUS_BRIDGE_SYNTHS=builtin to run against the lists above whatever is installed."""
    entry = None if os.environ.get("MS_PLUS_BRIDGE_SYNTHS") == "builtin" else effect_names.effect(0x09000000 | number)
    if entry and entry["knobs"]:
        try:
            return SynthEffect(name, number, tuple((knob, top) for knob, top in entry["knobs"]), lfo=lfo,
                               velocity=velocity, needs=needs)
        except ValueError:
            pass                        # not a build the bridge can play: its own list
    return SynthEffect(name, number, knobs, lfo=lfo, velocity=velocity, needs=needs)


def _synths() -> tuple:
    return (installed("KeySynth", 0x000F61, SYNTH_KNOBS), installed("KeySweep", 0x000F71, SWEEP_KNOBS, lfo="Flt"),
            installed("KeyFM", 0x000F72, FM_KNOBS), installed("KeyPoly", 0x000F73, POLY_KNOBS, lfo="Flt"),
            installed("KeyWave", 0x000F7C, WAVE_KNOBS, lfo=None),
            installed("KeyBand", 0x000F94, BAND_KNOBS, lfo=None, velocity="Vel"),
            installed("Pluck", 0x000F8F, PLUCK_KNOBS, lfo=None, needs={"Trig": (0, "Key")}))


KEY_EFFECTS = _synths()
KEYSYNTH, KEYSWEEP, KEYFM, KEYPOLY, KEYWAVE, KEYBAND, PLUCK = KEY_EFFECTS
KEY_NAMES = "KeySynth, KeySweep, KeyFM, KeyPoly, KeyWave, KeyBand or Pluck"
RENAMED = {"KeySynF": "KeySweep"}       # earlier names of the effects, as stored assignments may have them
# A knob another build calls otherwise, the same knob in the same place: (effect, its name in the
# earlier builds) → its name since. An assignment to either goes to the one the build played has.
KNOB_RENAMED = {("Pluck", "Bright"): "Tone"}
# Every name a controller can be assigned to, on one of the effects. A name several have (Level,
# Atk, Rel, LFO, Rate, Wave …) sets that knob on whichever of them is in the preset; KeySweep's Wave
# has ten settings and KeyWave's a hundred, each effect spreads the controller over its own.
ALL_CONTROLS: dict = {}


def _collect_controls() -> None:
    ALL_CONTROLS.clear()
    for effect in KEY_EFFECTS:
        for name, control in effect.controls.items():
            ALL_CONTROLS.setdefault(name, control)


_collect_controls()


def use_installed_synths(only: Optional[set] = None) -> None:
    """The synth effects again, with the knobs of the builds the table describes now: once the
    effect files read from the pedal itself are taken (effect_names.use_cache), at the start of
    the bridge before anything plays them, and whenever Read Pedal has read one (the bridge plays
    it from then on). only: the names of the effects to take anew (those whose file was read just
    now); the others stay as they are. An effect whose build is the same keeps its object: what
    plays it is not disturbed."""
    global KEY_EFFECTS, KEYSYNTH, KEYSWEEP, KEYFM, KEYPOLY, KEYWAVE, KEYBAND, PLUCK
    KEY_EFFECTS = tuple(old if old.knobs == new.knobs or (only is not None and old.name not in only) else new
                        for old, new in zip(KEY_EFFECTS, _synths()))
    KEYSYNTH, KEYSWEEP, KEYFM, KEYPOLY, KEYWAVE, KEYBAND, PLUCK = KEY_EFFECTS
    _collect_controls()


def key_effect(effect_id: int) -> Optional[SynthEffect]:
    """The synth effect with that id on the pedal, in whichever category it was installed."""
    return next((effect for effect in KEY_EFFECTS if effect.number == effect_id & EFFECT_NUMBER), None)


def synth_knob(name: str, effect: Optional[SynthEffect] = None) -> tuple[int, int]:
    """Parameter number and highest value of a knob of the synth effect (KeySynth if none)."""
    effect = effect or KEYSYNTH
    for index, (knob, top) in enumerate(effect.knobs):
        if knob == name:
            return MIN_PARAM + index, top
    raise ValueError(f"{effect.name} has no knob called {name}")


def synth_targets(slot: int, effect: Optional[SynthEffect] = None) -> tuple:
    """The allowlist entries for the synth effect in that slot: every knob with its own range."""
    effect = effect or KEYSYNTH
    return tuple(ParamTarget(slot, MIN_PARAM + index, 0, top) for index, (_, top) in enumerate(effect.knobs))


def cc_to_knob(value: int, top: int) -> int:
    """A controller value 0–127 spread evenly over a knob's 0–top (at most 127)."""
    return min(top, value * (top + 1) // 128)


def control_value(name: str, value: int, effect: Optional[SynthEffect] = None) -> int:
    """The knob value for a controller value 0–127 on one of the effect's controls."""
    effect = effect or KEYSYNTH
    _, rest, full = effect.controls[name]
    steps = cc_to_knob(value, abs(full - rest))
    return rest + steps if full >= rest else rest - steps


def build_query_program(device_id: int) -> bytes:
    return _zoom(device_id, 0x33)


def build_query_patch(device_id: int) -> bytes:
    return _zoom(device_id, 0x64, 0x13)


# --- Allowlist ---

# What the installer sends to read the list of the pedal's files, and nothing else of its file
# access: PC mode on and off, the first and the next entry of the list of everything ("*"), and the
# two messages that end the file session. Allowed only while the bridge reads the list.
FILE_LIST_BODIES = (b"\x52", b"\x53", b"\x60\x25\x00\x00*\x00", b"\x60\x26\x00\x00*\x00",
                    b"\x60\x21\x40\x00\x00\x00\x00", b"\x60\x09")


# Reading an effect file, as the installer reads one for its backup: opened (twice, as zoomzt2
# does), then per 512 bytes its status, the request for the next ones and its status again, which
# answers with them (`60 04 22 01 00 <length LSB MSB> <bytes, 7-bit packed> <CRC32, 5 bytes>`); the
# file session is ended as after the list. Checked against 117 files the installer read while the
# bridge listened (logs of 2026-10-17 to 21): no CRC wrong, 18 effect files byte for byte as the
# installer's own copies. Only the one .ZD2 file of the list just read that is being read now.
FILE_OPEN_READ = b"\x60\x20\x02" + bytes(9)
FILE_STATUS = b"\x60\x05\x00"
FILE_READ_NEXT = b"\x60\x22\x14\x2F\x60\x00\x0C\x00\x04\x00\x00\x00"
EFFECT_FILE = re.compile(r"[A-Z0-9_]{1,8}\.ZD2")
# … and how the pedal answers (the installer's backup of 2026-10-21 02:20): a file opened
# `60 04 20 …` (the second open is answered `60 03 7F 7F 7F 7F 0F` at times); a status, a close
# and a request past the file's end with a result `60 03 <5 bytes>`; a request for the next bytes
# `60 04 22 00 …`, the status after it with them `60 04 22 01 …`; the end of the file session
# `60 05 00`, about 0.6 s after it was asked.
FILE_OPENED = b"\x60\x04\x20"
FILE_RESULT = b"\x60\x03"
FILE_REQUESTED = b"\x60\x04\x22\x00"
FILE_BLOCK = b"\x60\x04\x22\x01"
FILE_SESSION_ENDED = b"\x60\x05\x00"


def classify(msg: bytes, device_id: Optional[int] = None,
             targets: Iterable[ParamTarget] = (), switchable: Iterable[int] = (),
             changeable: Iterable[int] = (), effects=frozenset(), upload: Optional[bytes] = None,
             upload_kind: str = "move_effect", listing: bool = False, file_name: Optional[str] = None) -> str:
    """Returns the message kind or raises NotAllowed. switchable: the slots whose effect may be
    switched on and off, those the pedal itself reported for the current preset. changeable: the
    slots whose effect may be changed (those it reported an effect in), to one of effects (ids
    the pedal reported in a preset, so it has them). upload: the one patch that may be sent
    (ZoomSender.upload, made by swap_effects, insert_empty or remove_effect from the pedal's own),
    of the kind upload_kind; any other is refused."""
    msg = bytes(msg)
    if len(msg) < 2 or msg[0] != SOX or msg[-1] != EOX or any(b > 0x7F for b in msg[1:-1]):
        raise NotAllowed(f"not a valid SysEx message: {to_hex(msg)}")
    if msg == IDENTITY_REQUEST:
        return "identity_request"
    if device_id is None:
        raise NotAllowed("device ID unknown: only the identity request is allowed")
    if msg[1:4] != bytes([ZOOM, 0x00, device_id]):
        raise NotAllowed(f"no Zoom header for device ID {device_id:02X}: {to_hex(msg)}")

    body = msg[4:-1]
    if listing and body in FILE_LIST_BODIES:
        return "read_pedal"
    if listing and file_name and EFFECT_FILE.fullmatch(file_name) and (
            body == FILE_OPEN_READ + file_name.encode("ascii") + b"\0" or body in (FILE_STATUS, FILE_READ_NEXT)):
        return "read_effect_file"
    if body[:1] == bytes([PATCH_UPLOAD]) and upload is not None and msg == upload and upload_kind != "save_preset":
        problem = check_patch(unpack_7bit(body[1:]))
        if problem:
            raise NotAllowed(f"not a patch to send back: {problem}")
        return upload_kind
    if body[:1] == bytes([STORED_PATCH]) and upload is not None and msg == upload and upload_kind == "save_preset":
        stored = parse_stored_preset(body)
        if stored is None or not is_preset(stored[0], stored[1]):
            raise NotAllowed("not a preset to store: its place, length or CRC is wrong")
        problem = check_patch(stored[3])
        if problem:
            raise NotAllowed(f"not a preset to store: {problem}")
        return "save_preset"
    if len(body) == 7 and body[:3] == bytes([QUERY_STORED, 0, 0]):
        bank, program = decode_value(body[3], body[4]), decode_value(body[5], body[6])
        if not is_preset(bank, program):
            raise NotAllowed(f"no preset {bank}/{program} on the pedal: {to_hex(msg)}")
        return "query_preset"
    for prefix in FORBIDDEN_PREFIXES:
        if body.startswith(prefix):
            raise NotAllowed(f"explicitly forbidden command {to_hex(prefix)}: {to_hex(msg)}")
    if body in _FIXED_BODIES:
        return _FIXED_BODIES[body]
    if len(body) == 10 and body[:3] == b"\x64\x20\x00" and body[3] < MAX_SLOTS and body[4] == EFFECT_PARAM:
        effect_id = decode_effect(body[5:])
        if body[3] not in tuple(changeable):
            raise NotAllowed(f"effect {body[3] + 1} cannot be changed: the pedal reported no effect there")
        if effect_id not in effects:
            raise NotAllowed(f"effect {effect_id:08X} has not been reported by the pedal: {to_hex(msg)}")
        return "change_effect"
    if len(body) == 10 and body[:3] == b"\x64\x20\x00" and body[7:] == b"\x00\x00\x00":
        slot, param, lsb, msb = body[3:7]
        if (param == SWITCH_PARAM and slot < MAX_SLOTS and slot in tuple(switchable)
                and (lsb, msb) in ((0, 0), (1, 0))):
            return "switch_effect"
        if slot == NAME_SLOT and param < NAME_LENGTH and msb == 0 and chr(lsb) in NAME_CHARACTERS:
            return "rename_patch"
        _check_param(slot, param, decode_value(lsb, msb), targets)
        return "set_param"
    raise NotAllowed(f"not on the allowlist: {to_hex(msg)}")


def _check_param(slot: int, param: int, value: int, targets: Iterable[ParamTarget]) -> None:
    if slot in RESERVED_SLOTS:
        raise NotAllowed(f"slot byte {slot:02X} is not an effect slot")
    if param < MIN_PARAM:
        raise NotAllowed(f"param {param} is blocked (effect on/off or effect type)")
    for target in targets:
        if (target.slot, target.param) == (slot, param):
            if target.min <= value <= target.max:
                return
            raise NotAllowed(f"value {value} is outside the measured range "
                             f"{target.min}–{target.max} (slot {slot}, param {param})")
    raise NotAllowed(f"slot {slot}, param {param} has not been measured")


# --- Measurements ---

def write_whole(path: Path, text: str) -> None:
    """Writes a file in full under another name and only then gives it its own: the bridge
    stopped or the power cut in between leaves the file as it was, never half of it."""
    path = Path(path)
    written = path.with_name(path.name + ".new")
    with open(written, "w", encoding="utf-8") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    os.replace(written, path)


class Measurements:
    """Value ranges the pedal itself reported while a knob was turned, per patch (bank, program)
    and parameter. Only what is recorded here may be set (rule 1). And the effects it reported in
    its presets: only these may be put into a place of the chain."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.patches: dict = {}
        self.effects: set = set()
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.patches, self.effects = data.get("patches", {}), read_effect_ids(data.get("effects"))

    def get(self, patch: tuple[int, int], slot: int, param: int) -> Optional[dict]:
        return self.patches.get(f"{patch[0]}/{patch[1]}", {}).get(f"{slot}/{param}")

    def for_patch(self, patch: tuple[int, int]) -> list:
        return list(self.patches.get(f"{patch[0]}/{patch[1]}", {}).values())

    def add(self, patch: tuple[int, int], slot: int, param: int, values: Iterable[int]) -> dict:
        """Records reported values (merged with earlier ones) and saves."""
        known = self.get(patch, slot, param) or {}
        merged = sorted(set(known.get("values", [])) | set(values))
        entry = {"slot": slot, "param": param, "min": merged[0], "max": merged[-1], "values": merged}
        self.patches.setdefault(f"{patch[0]}/{patch[1]}", {})[f"{slot}/{param}"] = entry
        self._write()
        return entry

    def found(self, effect_ids: Iterable[int]) -> bool:
        """Records the effects of a patch the pedal reported (0 is an empty place) and saves if
        one is new. Returns whether one was."""
        new = {effect_id for effect_id in effect_ids if effect_id} - self.effects
        if new:
            self.effects |= new
            self._write()
        return bool(new)

    def _write(self) -> None:
        data = {"patches": self.patches}
        if self.effects:
            data["effects"] = [f"{effect_id:08X}" for effect_id in sorted(self.effects)]
        write_whole(self.path, json.dumps(data) + "\n")

    def move(self, patch: tuple, moves: dict) -> bool:
        """The learned ranges of effects that moved in the chain go with them (moves: place → new
        place); a range left in a new place by an effect that is gone makes way for one that moves
        in on the same parameter, and stays otherwise (an assignment kept there may need it).
        Returns whether anything changed."""
        key = f"{patch[0]}/{patch[1]}"
        entries = self.patches.get(key, {})
        if not any(entry["slot"] in moves for entry in entries.values()):
            return False
        kept = {name: entry for name, entry in entries.items() if entry["slot"] not in moves}
        for entry in entries.values():
            if entry["slot"] in moves:
                entry = dict(entry, slot=moves[entry["slot"]])
                kept[f"{entry['slot']}/{entry['param']}"] = entry
        self.patches[key] = kept
        self._write()
        return True

    def preset(self, patch: tuple):
        """A copy of the learned ranges of a preset, None if it has none."""
        entries = self.patches.get(f"{patch[0]}/{patch[1]}")
        return {name: dict(entry) for name, entry in entries.items()} if entries else None

    def set_preset(self, patch: tuple, entries) -> None:
        """Puts back what preset() gave."""
        key = f"{patch[0]}/{patch[1]}"
        if entries:
            self.patches[key] = {name: dict(entry) for name, entry in entries.items()}
        else:
            self.patches.pop(key, None)
        self._write()

    def copy(self, source: tuple, target: tuple) -> None:
        """The learned ranges of a preset for another one too (a preset stored into another place
        on the pedal); that place's own go."""
        entries, key = self.patches.get(f"{source[0]}/{source[1]}"), f"{target[0]}/{target[1]}"
        if entries:
            self.patches[key] = {name: dict(entry) for name, entry in entries.items()}
        else:
            self.patches.pop(key, None)
        self._write()

    def replace_effects(self, effect_ids: Iterable[int]) -> None:
        """Exactly these effects can be chosen from now on (read from the pedal's list of its files)."""
        self.effects.clear()
        self.effects.update(effect_id for effect_id in effect_ids if effect_id)
        self._write()

    def forget(self, patch: tuple, places) -> bool:
        """Takes the learned ranges of (slot, param) places away: another effect's, where an
        assignment moved in without one of its own. Returns whether there was any."""
        entries = self.patches.get(f"{patch[0]}/{patch[1]}", {})
        gone = [name for name in (f"{slot}/{param}" for slot, param in places) if name in entries]
        for name in gone:
            del entries[name]
        self._write()
        return bool(gone)


# --- Approvals ---

class Approvals:
    """Persistent approvals: backup confirmation (rule 4) and message kinds (rule 5)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._data = {"backup_confirmed": None, "messages": {}}
        if self.path.exists():
            self._data.update(json.loads(self.path.read_text(encoding="utf-8")))

    @property
    def backup_confirmed(self) -> bool:
        return bool(self._data["backup_confirmed"])

    def confirm_backup(self) -> None:
        self._data["backup_confirmed"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._save()

    def is_approved(self, kind: str) -> bool:
        return kind in self._data["messages"]

    def approve(self, kind: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown message kind: {kind}")
        self._data["messages"][kind] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._save()

    def _save(self) -> None:
        write_whole(self.path, json.dumps(self._data, indent=2) + "\n")


# --- Sending ---

class ZoomSender:
    """The only way SysEx leaves the program."""

    def __init__(self, port, approvals: Approvals, device_id: Optional[int] = None,
                 targets: Iterable[ParamTarget] = ()):
        self.port = port
        self.approvals = approvals
        self.device_id = device_id
        self.targets = tuple(targets)
        self.switchable: tuple = ()          # the slots whose effect may be switched on and off
        self.changeable: tuple = ()          # the slots whose effect may be changed …
        self.effects = frozenset()           # … to one of these (Measurements.effects)
        self.upload: Optional[bytes] = None  # the one patch that may be sent, once …
        self.upload_kind = "move_effect"     # … as this kind of message
        self.listing = False                 # the pedal's list of its files is being read
        self.file_name: Optional[str] = None # … and this effect file of it

    def send(self, msg: bytes) -> str:
        kind = classify(msg, self.device_id, self.targets, self.switchable, self.changeable, self.effects,
                        self.upload, self.upload_kind, self.listing, self.file_name)
        if not self.approvals.backup_confirmed:
            raise NotApproved("backup")
        if not self.approvals.is_approved(kind):
            raise NotApproved(kind)
        log.info("TX  %s  (%s)", to_hex(msg), kind)
        self.port.send(mido.Message("sysex", data=msg[1:-1]))
        if kind in ("move_effect", "add_effect", "delete_effect", "save_preset"):
            self.upload = None
        return kind

    def select_preset(self, bank: int, program: int) -> None:
        """Bank select and program change, as the pedal sends them when a preset is chosen on it:
        it loads the preset as stored. An MS Plus pedal stores nothing on a program change
        (zoom-explorer measured it; the original MS series stored unsaved changes then)."""
        if not is_preset(bank, program):
            raise NotAllowed(f"no preset {bank}/{program} on the pedal")
        if not self.approvals.backup_confirmed:
            raise NotApproved("backup")
        if not self.approvals.is_approved("select_preset"):
            raise NotApproved("select_preset")
        for message in (mido.Message("control_change", control=0, value=0),
                        mido.Message("control_change", control=32, value=bank),
                        mido.Message("program_change", program=program)):
            log.info("TX  %s  (select_preset)", message)
            self.port.send(message)


# --- Parsers ---

class Identity(NamedTuple):
    device_id: int
    family: bytes
    model: bytes
    version: str


def parse_identity_reply(msg: Optional[bytes]) -> Optional[Identity]:
    """F0 7E <ch> 06 02 52 <family LSB MSB> <model LSB MSB> <version ASCII> F7"""
    if msg is None or len(msg) < 11 or msg[1] != 0x7E or msg[3:6] != bytes([0x06, 0x02, ZOOM]):
        return None
    return Identity(device_id=msg[6], family=bytes(msg[6:8]), model=bytes(msg[8:10]),
                    version=bytes(msg[10:-1]).decode("ascii", "replace"))


def zoom_body(msg: Optional[bytes], device_id: int) -> Optional[bytes]:
    """The content of a Zoom SysEx between `F0 52 00 <ID>` and `F7`, otherwise None."""
    if msg is None or len(msg) < 6 or msg[:4] != bytes([SOX, ZOOM, 0x00, device_id]):
        return None
    return bytes(msg[4:-1])


def is_ack(body: Optional[bytes]) -> bool:
    return body == b"\x00\x00"


class ParamMessage(NamedTuple):
    ack: bool  # False: 64 20 00 (change), True: 64 20 01 (the pedal's confirmation)
    slot: int
    param: int
    lsb: int
    msb: int
    tail: bytes

    @property
    def value(self) -> int:
        return decode_value(self.lsb, self.msb)


def parse_param(body: Optional[bytes]) -> Optional[ParamMessage]:
    if body is None or len(body) != 10 or body[:2] != b"\x64\x20" or body[2] not in (0, 1):
        return None
    return ParamMessage(bool(body[2]), body[3], body[4], body[5], body[6], bytes(body[7:]))


def parse_file_entry(body: Optional[bytes]) -> Optional[str]:
    """`60 04 25 … <name> …`: one file of the pedal's own list → its name ("HOGI.ZD2"); None for
    anything else. The name has 12 places (8.3), filled up with zeros; a name of 12 characters
    fills them all, and the byte after them is 01 (measured 2026-10-21: "160_COMP.ZD2")."""
    if body is None or len(body) < 24 or body[:3] != b"\x60\x04\x25":
        return None
    name = body[11:23].split(b"\0", 1)[0]
    return name.decode("ascii") if name and all(32 < byte < 127 for byte in name) else None


def file_entry_size(body: Optional[bytes]) -> Optional[int]:
    """The size of the file an entry of the pedal's list names, 7 bits per byte from its 27th byte
    (measured 2026-10-21: 160_COMP.ZD2 `23 7C` = 15907, SLAMDRV.ZD2 `49 09 02` = 33993)."""
    if parse_file_entry(body) is None or len(body) < 31:
        return None
    return sum(byte << (7 * index) for index, byte in enumerate(body[26:31]))


def build_file_open(device_id: int, name: str) -> bytes:
    return _zoom(device_id, *FILE_OPEN_READ, *name.encode("ascii"), 0)


def build_file_status(device_id: int) -> bytes:
    return _zoom(device_id, *FILE_STATUS)


def build_file_read_next(device_id: int) -> bytes:
    return _zoom(device_id, *FILE_READ_NEXT)


def parse_file_block(body: Optional[bytes]) -> Optional[bytes]:
    """`60 04 22 01 00 <length LSB MSB> <bytes, 7-bit packed> <CRC32, 5 bytes>` → the bytes of the
    file (empty at its end); None if it is none or its CRC is wrong."""
    if body is None or len(body) < 12 or body[:4] != b"\x60\x04\x22\x01":
        return None
    length = body[5] | body[6] << 7
    if length == 0:
        return b""
    data = unpack_7bit(body[7:7 + length + length // 7 + 1])[:length]
    crc = sum(byte << (7 * index) for index, byte in enumerate(body[-5:]))
    if len(data) != length or (binascii.crc32(data) ^ 0xFFFFFFFF) != crc:
        return None
    return data


def parse_program_info(body: Optional[bytes]) -> Optional[tuple[int, int]]:
    """`64 26 00 00 <bank LSB MSB> <prog LSB MSB>` → (bank, program). The MS-60B+ sends this on
    every patch change, before bank select and program change."""
    if body is None or len(body) != 8 or body[:4] != b"\x64\x26\x00\x00":
        return None
    return decode_value(body[4], body[5]), decode_value(body[6], body[7])


class PatchEffect(NamedTuple):
    id: int
    enabled: Optional[bool]       # None if the dump carries no settings for the effect
    first_param: Optional[int]    # the value of its first knob
    values: tuple = ()            # the values of its knobs, from parameter 2 on (EDTB_WIDTHS)


class PatchDump(NamedTuple):
    name: str
    effects: tuple                # one PatchEffect per slot, in chain order
    data: bytes = b""             # the whole patch, unpacked


def unpack_7bit(packed: bytes) -> bytes:
    """Eight bytes on the wire carry seven data bytes: the first holds their top bits, bit 6 for
    the first data byte down to bit 0 for the seventh (as in zoom-zt2's zoomzt2.py)."""
    data = bytearray()
    for start in range(0, len(packed), 8):
        high = packed[start]
        for index, byte in enumerate(packed[start + 1:start + 8]):
            data.append(byte | ((high >> (6 - index)) & 1) << 7)
    return bytes(data)


def pack_7bit(data: bytes) -> bytes:
    """unpack_7bit the other way round: seven data bytes become eight, a byte with their top bits first."""
    packed = bytearray()
    for start in range(0, len(data), 7):
        group = data[start:start + 7]
        packed.append(sum((byte >> 7) << (6 - index) for index, byte in enumerate(group)))
        packed += bytes(byte & 0x7F for byte in group)
    return bytes(packed)


# --- Moving an effect: the pedal's own patch, two neighbouring effects swapped ---
# The MS-60B+ has no message that moves an effect (measured 2026-10-19). zoom-explorer sends the
# whole patch into the edit buffer instead (`28 <patch, 7-bit packed>`); the reference warns that a
# malformed one can hang a pedal. The bridge sends back only the patch the pedal reported last,
# with the ids, the 24-byte EDTB blocks and the PRM2 bits of two neighbouring places swapped. For
# all 23 moves made on the pedal (logs of 2026-10-19) that gave the patch it reported next, byte for
# byte, apart from on/off and the effect selected on it (PRM2 bytes 10–12, which stay as they are).
PATCH_UPLOAD = 0x28
# PRM2 holds a bit per place for some kinds of effect (zoom-explorer's reading): (byte, shift) of a
# six-bit field spread over two bytes, and which ids set it. In all 1223 patches of the MS-60B+ in
# the logs these matched the chain exactly; a patch where they do not is not sent back.
SLOT_FLAGS = {
    "unknown effect": ((2, 6), lambda effect_id: False),
    "amp": ((20, 1), lambda effect_id: effect_id >> 24 == 0x05),                    # Bass amp, AMPG SVT …
    "BPM": ((21, 4), lambda effect_id: effect_id & 0xFFFFFF == 0xFF0),
    "line selector": ((22, 7), lambda effect_id: effect_id & 0xFFFFFF == 0xF00),
}


def patch_chunks(data: bytes) -> dict:
    """The chunks after the ids of a patch: tag → (start of the content, its size)."""
    count = int.from_bytes(data[12:16], "little")
    position, found = 36 + 4 * count, {}
    while position + 8 <= len(data):
        tag, size = bytes(data[position:position + 4]), int.from_bytes(data[position + 4:position + 8], "little")
        found.setdefault(tag, (position + 8, size))
        position += 8 + size
    return found


def _flag_field(data, start: int, byte: int, shift: int) -> int:
    word = data[start + byte] | data[start + byte + 1] << 8
    return (word >> shift) & 0x3F


def check_patch(data: bytes) -> Optional[str]:
    """Why a patch may not be sent to the pedal; None if it is laid out as the pedal's own are:
    PTCF, 1–6 effects, an EDTB block per effect that names its id, and the PRM2 bits per place
    matching the effects."""
    if len(data) < 36 or data[:4] != b"PTCF":
        return "not a patch"
    count = int.from_bytes(data[12:16], "little")
    if not 1 <= count <= MAX_SLOTS or len(data) < 36 + 4 * count:
        return "not 1 to 6 effects"
    ids = [int.from_bytes(data[36 + 4 * slot:40 + 4 * slot], "little") for slot in range(count)]
    found = patch_chunks(data)
    edtb, prm2 = found.get(b"EDTB"), found.get(b"PRM2")
    if edtb is None or edtb[1] != 24 * count or edtb[0] + edtb[1] > len(data):
        return "no settings block per effect"
    for slot, effect_id in enumerate(ids):
        block = int.from_bytes(data[edtb[0] + 24 * slot:edtb[0] + 24 * slot + 24], "little")
        if (block >> 1) & 0x1FFFFFFF != effect_id & 0x1FFFFFFF:
            return f"the settings of effect {slot + 1} are not its own"
    if prm2 is None or prm2[1] < 24 or prm2[0] + prm2[1] > len(data):
        return "no PRM2"
    for name, ((byte, shift), sets) in SLOT_FLAGS.items():
        wanted = sum(1 << slot for slot, effect_id in enumerate(ids) if sets(effect_id))
        if _flag_field(data, prm2[0], byte, shift) != wanted:
            return f"the {name} bits of PRM2 do not match the effects"
    return None


def swap_effects(data: bytes, slot: int) -> bytes:
    """The patch with the effects in slot and slot + 1 swapped, with their settings and PRM2 bits.
    Raises ValueError for a patch that is not one to send back (check_patch), before or after."""
    problem = check_patch(data)
    if problem:
        raise ValueError(problem)
    count = int.from_bytes(data[12:16], "little")
    if not 0 <= slot < count - 1:
        raise ValueError(f"effects {slot + 1} and {slot + 2} are not both in the chain")
    swapped = bytearray(data)
    for start, size in ((36, 4), (patch_chunks(data)[b"EDTB"][0], 24)):
        one, other = start + size * slot, start + size * (slot + 1)
        swapped[one:one + size], swapped[other:other + size] = data[other:other + size], data[one:one + size]
    prm2 = patch_chunks(data)[b"PRM2"][0]
    for (byte, shift), _ in SLOT_FLAGS.values():
        field = _flag_field(data, prm2, byte, shift)
        low, high = (field >> slot) & 1, (field >> (slot + 1)) & 1
        field = field & ~(3 << slot) | high << slot | low << (slot + 1)
        word = swapped[prm2 + byte] | swapped[prm2 + byte + 1] << 8
        word = word & ~(0x3F << shift) | field << shift
        swapped[prm2 + byte], swapped[prm2 + byte + 1] = word & 0xFF, word >> 8
    problem = check_patch(bytes(swapped))
    if problem:
        raise ValueError(problem)
    return bytes(swapped)


# An empty place, as the MS-60B+ writes it when one is added on it: switched on, id 0, every knob 0.
# Adding one inserts its id (4 bytes) and this block (24) and grows the patch's own length by 28;
# the patch buffer keeps its size (what follows the patch's length is left over, not read). For
# all 19 places added on the pedal (logs of 2026-10-18 to 10-20) that gave the patch it reported
# next, apart from the effect selected on it (PRM2 bytes 10–12, left as they are) and knobs turned
# in between.
EMPTY_BLOCK = b"\x01" + bytes(23)
ADDED_BYTES = 4 + 24


def insert_empty(data: bytes, slot: int) -> bytes:
    """The patch with an empty place inserted before the effect in slot (slot = the number of
    effects: at the end). The PRM2 bits of the places from there on move up with them. Raises
    ValueError for a patch that is not one to send back (check_patch), before or after."""
    problem = check_patch(data)
    if problem:
        raise ValueError(problem)
    count, length = int.from_bytes(data[12:16], "little"), int.from_bytes(data[4:8], "little")
    if count >= MAX_SLOTS:
        raise ValueError(f"the chain has {MAX_SLOTS} effects already")
    if not 0 <= slot <= count:
        raise ValueError(f"place {slot + 1} is not in a chain of {count}")
    if length + ADDED_BYTES > len(data):
        raise ValueError("the patch has no room for another place")
    edtb, size = patch_chunks(data)[b"EDTB"]
    added = bytearray(data[:36 + 4 * slot] + bytes(4) + data[36 + 4 * slot:edtb - 4]
                      + (size + 24).to_bytes(4, "little") + data[edtb:edtb + 24 * slot] + EMPTY_BLOCK
                      + data[edtb + 24 * slot:])[:len(data)]
    added[4:8] = (length + ADDED_BYTES).to_bytes(4, "little")
    added[12:16] = (count + 1).to_bytes(4, "little")
    prm2 = patch_chunks(bytes(added))[b"PRM2"][0]
    for (byte, shift), _ in SLOT_FLAGS.values():
        field = _flag_field(added, prm2, byte, shift)
        field = (field & ((1 << slot) - 1) | (field >> slot) << (slot + 1)) & 0x3F
        word = added[prm2 + byte] | added[prm2 + byte + 1] << 8
        word = word & ~(0x3F << shift) | field << shift
        added[prm2 + byte], added[prm2 + byte + 1] = word & 0xFF, word >> 8
    problem = check_patch(bytes(added))
    if problem:
        raise ValueError(problem)
    return bytes(added)


# The places PRM2 bytes 10–12 name (read as one little-endian number): bits 0–4 as they are (12 in
# every patch of the MS-60B+), then four bits per place from bit 5 – the effect selected on the
# pedal, followed by those after it up to the last, as many as fit. In the 1223 patches of the logs
# that was the form nearly always; the pedal leaves a place in it now and then that is gone.
SELECTED = 10
SELECTED_BITS = 24


def _selected_places(data: bytes, prm2: int) -> list:
    word = int.from_bytes(data[prm2 + SELECTED:prm2 + SELECTED + 3], "little") >> 5
    places = []
    while word:
        places.append(word & 0xF)
        word >>= 4
    return places or [0]


def remove_effect(data: bytes, slot: int) -> bytes:
    """The patch with the effect in slot taken out: its id (4 bytes) and its EDTB block (24), the
    patch's own length 28 shorter; the buffer keeps its size (zeros at its end, what follows the
    patch's length is not read). The PRM2 bits of the places after it move down. The effect
    selected on the pedal stays selected, one before it taken out or not; if it was the one taken
    out, the one after it is, or the last. For the 4 effects deleted on the pedal (logs of
    2026-10-19) that gave the patch it reported next, apart from places it left in PRM2 10–12
    that are gone, and knobs turned in between. Raises ValueError for a patch that is not one to
    send back (check_patch), before or after, and for the only effect of a chain."""
    problem = check_patch(data)
    if problem:
        raise ValueError(problem)
    count, length = int.from_bytes(data[12:16], "little"), int.from_bytes(data[4:8], "little")
    if not 0 <= slot < count:
        raise ValueError(f"effect {slot + 1} is not in a chain of {count}")
    if count == 1:
        raise ValueError("a chain keeps at least one effect")
    edtb, size = patch_chunks(data)[b"EDTB"]
    removed = bytearray(data[:36 + 4 * slot] + data[40 + 4 * slot:edtb - 4] + (size - 24).to_bytes(4, "little")
                        + data[edtb:edtb + 24 * slot] + data[edtb + 24 * (slot + 1):])
    removed += bytes(len(data) - len(removed))
    removed[4:8] = (length - ADDED_BYTES).to_bytes(4, "little")
    removed[12:16] = (count - 1).to_bytes(4, "little")
    prm2 = patch_chunks(bytes(removed))[b"PRM2"][0]
    for (byte, shift), _ in SLOT_FLAGS.values():
        field = _flag_field(removed, prm2, byte, shift)
        field = (field & ((1 << slot) - 1) | (field >> (slot + 1)) << slot) & 0x3F
        word = removed[prm2 + byte] | removed[prm2 + byte + 1] << 8
        word = word & ~(0x3F << shift) | field << shift
        removed[prm2 + byte], removed[prm2 + byte + 1] = word & 0xFF, word >> 8
    selected = _selected_places(removed, prm2)[0]
    selected = min(selected - (slot < selected), count - 2)
    word = int.from_bytes(removed[prm2 + SELECTED:prm2 + SELECTED + 3], "little") & 0x1F
    for index, place in enumerate(range(selected, count - 1)):
        if (place << 5 + 4 * index) >> SELECTED_BITS:
            break
        word |= place << 5 + 4 * index
    removed[prm2 + SELECTED:prm2 + SELECTED + 3] = word.to_bytes(3, "little")
    problem = check_patch(bytes(removed))
    if problem:
        raise ValueError(problem)
    return bytes(removed)


# --- Presets: chosen, read from their places, stored ---
# The MS-60B+ keeps 100 presets in ten banks of ten (the number it shows: bank × 10 + program + 1).
# When a preset is saved on it, it sends the stored patch as `45 00 00 <bank LSB MSB> <program LSB
# MSB> <length LSB MSB> <patch, 7-bit packed> <CRC32, 5 bytes>`; all 9 such messages in the logs
# (2026-10-18 and 19) are made again byte for byte by build_save_preset from their bank, program and
# patch. zoom-explorer writes a preset into a place in that form (the pedal answers `00 00`) and
# reads one with `46 00 00 <bank LSB MSB> <program LSB MSB>`, which the pedal answers in it too.
PRESET_BANKS, PRESETS_PER_BANK = 10, 10
STORED_PATCH = 0x45
QUERY_STORED = 0x46


def is_preset(bank: int, program: int) -> bool:
    return 0 <= bank < PRESET_BANKS and 0 <= program < PRESETS_PER_BANK


def build_query_preset(device_id: int, bank: int, program: int) -> bytes:
    if not is_preset(bank, program):
        raise ValueError(f"no preset {bank}/{program}")
    return _zoom(device_id, QUERY_STORED, 0, 0, *encode_value(bank), *encode_value(program))


def build_save_preset(device_id: int, bank: int, program: int, data: bytes) -> bytes:
    """The patch into the preset's place, as the pedal itself reports a preset saved on it."""
    problem = check_patch(data)
    if problem:
        raise ValueError(problem)
    if not is_preset(bank, program):
        raise ValueError(f"no preset {bank}/{program}")
    crc = binascii.crc32(data) ^ 0xFFFFFFFF
    return _zoom(device_id, STORED_PATCH, 0, 0, *encode_value(bank), *encode_value(program),
                 *encode_value(len(data)), *pack_7bit(data), *((crc >> 7 * index) & 0x7F for index in range(5)))


def parse_stored_preset(body: Optional[bytes]) -> Optional[tuple]:
    """`45 00 00 …` → (bank, program, PatchDump, the patch); None if it is none or does not check out."""
    if body is None or len(body) < 14 or body[:3] != bytes([STORED_PATCH, 0, 0]):
        return None
    dump = parse_patch_dump(b"\x64\x12\x01" + body[7:])
    if dump is None:
        return None
    return decode_value(body[3], body[4]), decode_value(body[5], body[6]), dump, dump.data


def build_patch_upload(device_id: int, data: bytes) -> bytes:
    """A patch into the pedal's edit buffer, as zoom-explorer sends it: `28 <patch, 7-bit packed>`."""
    problem = check_patch(data)
    if problem:
        raise ValueError(problem)
    return _zoom(device_id, PATCH_UPLOAD, *pack_7bit(data))


def edtb_values(block: int) -> tuple:
    """The knob values of an EDTB block (read as one little-endian number), from parameter 2 on."""
    values, bit = [], 30
    for width in EDTB_WIDTHS:
        values.append((block >> bit) & ((1 << width) - 1))
        bit += width
    return tuple(values)


def parse_patch_dump(body: Optional[bytes]) -> Optional[PatchDump]:
    """The reply to query_patch, `64 12 01 <length LSB MSB> <patch, 7-bit packed> <CRC32, 5 bytes>`
    → the effects of the current patch in slot order. None if it is not a patch dump or does
    not check out. Layout of the patch ("PTCF") as in zoom-zt2's decode_preset.py, verified
    against dumps of the MS-60B+ (firmware 1.20): effect count at 12, name at 26 (10 characters),
    ids from 36, then tagged chunks; "EDTB" holds 24 bytes of settings per effect, "NAME" the
    whole name (28 characters filled up with spaces, then four zeros)."""
    if body is None or len(body) < 10 or body[:2] != b"\x64\x12":
        return None
    length = decode_value(body[3], body[4])
    data = unpack_7bit(body[5:-5])
    crc = sum(byte << (7 * index) for index, byte in enumerate(body[-5:]))
    if len(data) != length or (binascii.crc32(data) ^ 0xFFFFFFFF) != crc:
        return None
    if data[:4] != b"PTCF" or length < 36:
        return None
    count = int.from_bytes(data[12:16], "little")
    if count > MAX_SLOTS or length < 36 + 4 * count:
        return None
    ids = [int.from_bytes(data[36 + 4 * slot:40 + 4 * slot], "little") for slot in range(count)]

    settings: list = [None] * count
    name = data[26:36]
    position = 36 + 4 * count
    while position + 8 <= length:                # chunks: 4-byte tag, 4-byte length, content
        tag, size = data[position:position + 4], int.from_bytes(data[position + 4:position + 8], "little")
        if tag == b"EDTB" and size >= 24 * count and position + 8 + 24 * count <= length:
            for slot in range(count):
                # 24 bytes read as one little-endian number: bit 0 on/off, 29 bits id, then
                # the knobs, as many bits each as EDTB_WIDTHS says
                block = int.from_bytes(data[position + 8 + 24 * slot:position + 32 + 24 * slot], "little")
                if (block >> 1) & 0x1FFFFFFF == ids[slot] & 0x1FFFFFFF:
                    values = edtb_values(block)
                    settings[slot] = (bool(block & 1), values[0], values)
        elif tag == b"NAME" and 0 < size <= length - position - 8:
            name = data[position + 8:position + 8 + size].split(b"\0")[0]
            break
        position += 8 + size
    return PatchDump(
        name=name.decode("ascii", "replace").rstrip(),   # leading spaces keep the places of the characters
        effects=tuple(PatchEffect(effect_id, *(settings[slot] or (None, None, ())))
                      for slot, effect_id in enumerate(ids)),
        data=bytes(data))


# --- Logging and ports ---

class _ConsoleFormatter(logging.Formatter):
    """Shortens long lines (patch dumps) on the console; the log file stays complete."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        if len(line) <= 240:
            return line
        return f"{line[:200]} … (truncated, complete in the log file)"


def tolerant_console() -> None:
    """Never fail on printing: a Windows console may not know characters such as → or –."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def setup_logging(log_dir: Optional[Path], console: bool = True) -> Optional[Path]:
    """Everything sent and received goes to logs/sysex.log (rotating), optionally to the console.
    Without a folder it goes to the console only (a service's journal, on a read-only system)."""
    path = None
    fmt = "%(asctime)s.%(msecs)03d  %(message)s"
    for handler in log.handlers:                # the file of an earlier setup is closed (tests set up many)
        handler.close()
    log.handlers[:] = []
    if log_dir is not None:
        log_dir.mkdir(exist_ok=True)
        path = log_dir / "sysex.log"
        to_file = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3,
                                                       encoding="utf-8")
        to_file.setFormatter(logging.Formatter(fmt, "%Y-%m-%d %H:%M:%S"))
        log.addHandler(to_file)
    if console:
        to_console = logging.StreamHandler()
        to_console.setFormatter(_ConsoleFormatter(fmt, "%H:%M:%S"))
        log.addHandler(to_console)
    log.setLevel(logging.INFO)
    log.propagate = False
    return path


def log_rx(message: mido.Message) -> Optional[bytes]:
    """Logs an incoming message; for SysEx returns the raw bytes (F0 … F7)."""
    if message.type == "sysex":
        raw = bytes(message.bytes())
        log.info("RX  %s", to_hex(raw))
        return raw
    log.info("RX  %s", message)
    return None


def profile_folder(root: Path, name: str) -> Path:
    """Where a profile keeps its settings: profiles/NAME/ in the installation. A second pedal gets
    a bridge of its own, with its own config.yaml, approvals, learned ranges, controllers and log."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", name):
        sys.exit("A profile name is made of letters, digits, - and _ (at most 32), for example --profile second.")
    folder = root / "profiles" / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# On Linux (ALSA) every port name is "client:port client-number:port-number", for example
# "KOMPLETE KONTROL A61:KOMPLETE KONTROL A61 MIDI 1 24:0"; on macOS it is the device's name alone.
# The settings are made on a Mac and travel to a Raspberry Pi, so a name has to find its port on both.
ALSA_PORT = re.compile(r"^(?P<client>.*?):(?P<port>.*) (?P<number>\d+):(?P<index>\d+)$")


def _alsa(name: str):
    return ALSA_PORT.match(name)


def port_candidates(names: Iterable[str], needle: str) -> list:
    """The ports a name from the settings may mean, case-insensitive: the port with exactly that
    name; else, on Linux, the ports whose client or port name is exactly that; else, for a Linux
    name chosen in the interface, the ports of that client and port under other numbers (they
    change with the order devices are plugged in or connect over Bluetooth); else every port
    whose name contains it."""
    bare = lambda name: ":".join(_alsa(name).group("client", "port")).lower() if _alsa(name) else None
    chosen = bare(needle)
    needle, names = needle.lower(), sorted(set(names))
    parts = lambda name: (name.lower(),) + (tuple(_alsa(name).group("client", "port")) if _alsa(name) else ())
    for test in (lambda name: needle == name.lower(),
                 lambda name: needle in (part.lower() for part in parts(name)),
                 lambda name: chosen is not None and bare(name) == chosen,
                 lambda name: needle in name.lower()):
        found = [name for name in names if test(name)]
        if found:
            return found
    return []


def find_port(names: Iterable[str], needle: str) -> str:
    """The one port a name from the settings means (port_candidates). So "ZOOM MS Plus Series"
    still names one port when a "ZOOM MS Plus Series 2" is connected as well. On Linux a device
    with several ports (a keyboard with a DAW port besides its keys) is one client: there its
    first port is the one, as it is the first one macOS lists for the device."""
    if not needle:
        raise PortError("port name missing in config.yaml (run `python probe.py ports` first)")
    names = list(names)
    matches = port_candidates(names, needle)
    if len(matches) > 1 and all(_alsa(name) for name in matches) \
            and len({_alsa(name).group("number") for name in matches}) == 1:
        matches = [min(matches, key=lambda name: int(_alsa(name).group("index")))]
    if len(matches) != 1:
        found = ", ".join(matches) if matches else "none"
        there = sorted({_alsa(name).group("client", "port")[1] if _alsa(name) else name for name in names})
        raise PortError(f"no unique port matching “{needle}” (found: {found})"
                        + ("" if matches else f" – ports there are: {', '.join(there) or 'none'}"))
    return matches[0]
