#!/usr/bin/env python3
"""MS Plus Bridge: CC from the Chocolate Plus → parameter SysEx to the MS-60B+, with a web UI.
Optionally a MIDI keyboard plays a synth effect on the pedal (section `synth:` in config.yaml):
KeySynth, KeySweep, KeyFM, KeyWave or Pluck with one voice, or KeyPoly and KeyBand with four. Notes
and pitch wheel become their Key knobs (with four the bridge gives each note one of them), the
mod wheel and any other controller can be assigned to their other knobs (learned in the interface,
kept in controls.json). An arpeggiator plays the keys that are down one after another, and a step
sequencer plays a recorded pattern from the key that is down, both at their own tempo or to MIDI
clock. An external sequencer (an MPC) can be a second source of notes and the source of that
clock. Controllers can also switch the effects of the chain on and off (effect_switch.py).

  python bridge.py           bridge + interface in the browser (the address is printed on start)
  python bridge.py --open    also opens the interface in the browser
  python bridge.py --no-ui   bridge only
  python bridge.py --profile NAME
                             a second bridge for a second pedal: its settings, approvals and log
                             are kept in profiles/NAME/ (see README, "Two pedals")
  python bridge.py --settings FOLDER
                             for a computer without screen (Raspberry Pi): runs from the settings
                             file export.py wrote into FOLDER; no interface, nothing is stored
  python bridge.py --settings FOLDER --stick PLACE
                             the same, and a settings file on a USB stick mounted at or below
                             PLACE is taken over into FOLDER; the stick can be removed again

Runs until Ctrl+C; if a device is missing it keeps looking. Only approved message kinds are sent
(`python probe.py approve`), and only values within the ranges the pedal itself reported while
learning.
"""
from __future__ import annotations

import argparse
import copy
import functools
import json
import math
import os
import queue
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from collections import deque
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import NamedTuple, Optional

import mido
import yaml

import arpeggiator
import config_schema as cs
import effect_names
import effect_switch
import scenes as scene_file
import sequencer
import status_led
import synth_controls
import zoom_sysex as zs
from sequencer import note_name

try:
    from rtmidi import RtMidiError      # what listing or opening ports raises while a device is unplugged
except ImportError:                     # mido with another backend
    class RtMidiError(Exception):
        pass

ROOT = Path(__file__).resolve().parent
HOME = ROOT                       # where the settings of this bridge are kept; a profile has a folder of its own
PROFILE: Optional[str] = None     # its name, for the interface
CONFIG = HOME / "config.yaml"
APPROVALS = HOME / "approvals.json"
MEASUREMENTS = HOME / "measurements.json"
CONTROLS = HOME / "controls.json"
EFFECT_CACHE = HOME / "pedal_effects.json"   # effects read from the pedal's own files (effect_names.use_cache)

REQUIRED_KINDS = ("identity_request", "edit_enable", "edit_disable", "set_param", "query_program")
IGNORED_TYPES = ("clock", "active_sensing")
CLOCK_TYPES = ("clock", "start", "stop", "continue")   # arpeggiator and sequencer follow: from the external
                                                       # sequencer's port if one is set up, else the keyboard's
PEDAL_PORTS = "ZOOM MS"    # the interface offers the ports named like this as the pedal: "ZOOM MS Plus Series"
LISTEN_PORT = 80          # the interface of a bridge without screen, for a phone: http://<name of the Pi>.local
ANY_PORT = "*"             # as the keyboard: whichever MIDI input is none of the other devices
# Left alone by "any keyboard": loop-backs (Linux's Midi Through, macOS's IAC driver in any language),
# network sessions, the DAW-control port a keyboard has besides its keys, Zoom's ports, and our own.
NOT_A_KEYBOARD = re.compile(r"midi through|\biac\b|network|\bdaw\b|zoom|rtmidi", re.IGNORECASE)
NOT_A_DEVICE = re.compile(r"midi through|rtmidi", re.IGNORECASE)   # Linux: the loop-back, the bridge's own ports
AMBIGUOUS_TOLD: set = set()  # names of optional controllers that more than one port fit, said once
# said once while no controller is connected: at the start, and when the last one left (Session._no_controller)
NO_CONTROLLER = ("No controller connected – the preset can be edited in the interface; "
                 "a controller joins when it is plugged in")
NO_CONTROLLER_SET_UP = ("No controller set up – the preset can be edited in the interface; "
                        "a controller is chosen under Devices")
RETRY_SECONDS = 2.0        # time between connection attempts
PORT_CHECK_SECONDS = 1.0   # how often to check that the pedal is still there and which controllers are
REPORT_SECONDS = 5.0       # time between statistics lines while sending
SNAPSHOT_SECONDS = 0.1     # how often the state for the interface is refreshed
ACK_TIMEOUT = 0.25         # after this long a value counts as unconfirmed
QUERY_DELAY = 0.2          # wait after a passed-through program change before querying the patch
FILES_READ_AT_ONCE = 20    # effect files read from the pedal at one time (each takes about a second) …
FILES_READ_SECONDS = 20.0  # … and none begun after this long
FILE_READ_LIMIT = 200_000  # … and read no further than this when no table of knobs comes
PRESET_READ_GAP = 0.02     # between reading two stored presets for their names
PRESET_READ_TIMEOUT = 0.5  # … and how long one may take
PRESET_READ_MISSES = 3     # stored presets in a row without an answer: the pedal does not give them
PRESET_CHECK_SECONDS = 0.3 # after a preset was stored: its place is read back
MISSES_BEFORE_SLOWDOWN = 3
ACKS_BEFORE_SPEEDUP = 200
MAX_INTERVAL = 0.1         # never throttle slower than 100 ms
PATCHES_PER_BANK = 10      # measured: display 095 = bank 9, program 4
MAX_TARGETS = 4            # how many parameters one controller (the expression pedal, a knob) sets per preset
MAX_ASSIGNMENTS = 12       # … and all of them together
MAX_EFFECTS = 6            # effects 1–6 and parameters 1–12 can be assigned without learning them
MAX_PARAMS = 12
UNLEARNED_MAX = 0x3FFF     # value limit for a parameter whose range has not been learned
UNCONFIRMED_HINT = 3       # misses in a row before the interface flags a parameter
UNCONFIRMED_LIMIT = 5      # misses before sending stops for a parameter the pedal never confirmed
EXPORT_FORMAT = "expression-bridge/1"
SYNTH_QUERY_SECONDS = 1.0  # while looking for the synth effect: at most one patch query per second
KEY_MISSES_BEFORE_QUERY = 3  # unconfirmed notes before the bridge looks where the effect is now …
KEY_SILENCE_SECONDS = 0.2  # … if the pedal has confirmed none for this long: a fast arpeggio has three in 60 ms
KEY_ACK_TIMEOUT = 0.06     # a note is confirmed after about 10 ms (slowest seen: 20 ms)
KEY_RETRIES = 3            # how often the last note or the gate-off is repeated while unconfirmed
KEY_TROUBLE_SECONDS = 5.0  # while notes stay unconfirmed: warn and look for the effect this often at most
EDIT_REENABLE_SECONDS = 2.0  # while nothing is confirmed: switch edit mode on again this often at most
MAX_BEND_RANGE = 12        # semitones the pitch wheel may be set to bend each way
STICK_SECONDS = 2.0        # how often a bridge without screen looks for a USB stick with other settings
SWITCH_RETRIES = 1         # how often switching an effect is repeated while unconfirmed
SWITCH_QUIET_SECONDS = 1.0  # a controller just learned for an effect is not obeyed for this long (its release)
NAME_SEND_SECONDS = 0.01   # between two characters of a preset's new name
PLACE_SECONDS = 1.0        # after a preset change: how long values wait for the pedal to report its chain
NAME_CHECK_SECONDS = 0.3   # after the last character: the patch is read to see whether the pedal took the name
NAME_CHECK_TIMEOUT = 2.0   # … and given up on if the pedal does not send it within this time
EFFECT_CHECK_SECONDS = 0.3   # after another effect was put into a place: the patch is read to see whether it is there
EFFECT_CHECK_TIMEOUT = 2.0   # … and given up on if the pedal does not send it within this time
# The DSP the effects of a preset may take together, as their files declare it (effect_names.dsp),
# measured on the MS-60B+ (NOTES.md, "DSP-Grenze des MS-60B+ gemessen", 2026-10-22): 269.93 ran,
# 270.04 gave PROCESS OVERFLOW, an effect switched off counting all the same. Over MIDI the pedal
# takes any effect and then overflows, so the bridge keeps to it itself (over_budget).
DSP_BUDGET = 270
# The pedal's categories of effects by the top byte of an id (zoom-explorer, for the MS-60B+). Custom
# and community effects are listed where they are installed, as the pedal lists them (most of the
# custom ones in SFX, where the installer puts them).
EFFECT_GROUPS = {1: "Dynamics", 2: "Filter", 3: "Drive", 4: "Preamp", 5: "Bass amp", 6: "Modulation",
                 7: "Pitch shift", 8: "Synth", 9: "SFX", 10: "Delay", 11: "Reverb"}
CLOCK_TIMEOUT = 0.5        # no MIDI clock for this long: the clock has stopped, arpeggiator and sequencer fall silent
# Controllers that cannot be assigned to a knob: bank select, sustain, the channel mode messages
RESERVED_CONTROLS = (0, 32, 64) + tuple(range(120, 128))

CURVES = {
    "linear": lambda x: x,
    "log": lambda x: math.log1p(9 * x) / math.log(10),   # changes quickly at the start
    "exp": lambda x: (10 ** x - 1) / 9,                   # changes slowly at the start
}

EVENTS: deque = deque(maxlen=8)   # latest status lines, for the interface
HEADLESS = False                  # run from a settings file: lines go to the log only (the service's journal)


class Disconnected(Exception):
    """A device is missing or not answering; the connection is rebuilt."""


class NewSettings(Exception):
    """A USB stick with other settings was put into a bridge without screen; it starts again."""


# Seen on the real setup: a device was unplugged just while the ports were being listed, and
# rtmidi raised InvalidPortError ("the 'portNumber' argument (6) is invalid"), which is no OSError.
# The bridge ended with a traceback. It is a lost connection like any other.
MIDI_ERRORS = (OSError, RtMidiError)


class UserError(Exception):
    """A request from the interface cannot be carried out; the text is shown there."""


def say(text: str) -> None:
    line = f"{time.strftime('%H:%M:%S')}  {text}"
    if not HEADLESS:
        print(line, flush=True)
    EVENTS.append(line)
    zs.log.info("--  %s", text)


def use_profile(name: str) -> None:
    """From here on the settings of a profile are used: config.yaml, approvals, learned ranges,
    controllers and the log in profiles/NAME/. For a second bridge that plays a second pedal."""
    global HOME, PROFILE, CONFIG, APPROVALS, MEASUREMENTS, CONTROLS, EFFECT_CACHE
    HOME, PROFILE = zs.profile_folder(ROOT, name), name
    CONFIG, APPROVALS = HOME / "config.yaml", HOME / "approvals.json"
    MEASUREMENTS, CONTROLS = HOME / "measurements.json", HOME / "controls.json"
    EFFECT_CACHE = HOME / "pedal_effects.json"


def set_config_values(text: str, values: dict, add=()) -> str:
    """The text of config.yaml with other values for some of its settings, given as
    {(section, setting): text}. Everything else stays as it is, comments included. Raises KeyError
    for a setting the file does not have; those listed in add (a config.yaml from before they
    existed lacks them) are put at the end of their section instead."""
    lines, section, todo, ends = text.split("\n"), None, dict(values), {}
    for index, line in enumerate(lines):
        top = re.match(r"([A-Za-z_]+):", line)
        inner = re.match(r"(\s+)([A-Za-z_]+):\s*", line)
        if inner:
            ends[section] = (index, inner.group(1))       # the section's last setting so far, and its indent
        if top:
            section = top.group(1)
        elif inner and (section, inner.group(2)) in todo:
            rest = line[inner.end():]
            if rest[:1] in ("\"", "'"):                   # a quoted value may contain a #
                quote = re.match(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^']|'')*'", rest)
                comment = rest[quote.end():] if quote else ""
            else:
                comment = (re.search(r"\s+#.*$", rest) or re.search(r"^#.*$", rest) or re.search(r"$", rest)).group(0)
            value = json.dumps(todo.pop((section, inner.group(2))), ensure_ascii=False)
            start = f"{inner.group(1)}{inner.group(2)}: {value}"
            column = len(line) - len(comment.lstrip())  # the comment stays where it was, if there is room
            lines[index] = (start.ljust(column - 2) + "  " + comment.lstrip()) if comment.strip() else start
    for setting in sorted((setting for setting in todo if setting in add and setting[0] in ends),
                          key=lambda setting: ends[setting[0]][0], reverse=True):
        index, indent = ends[setting[0]]
        lines.insert(index + 1, f"{indent}{setting[1]}: {json.dumps(todo.pop(setting), ensure_ascii=False)}")
    if todo:
        raise KeyError(", ".join(f"{section}: {setting}" for section, setting in todo))
    return "\n".join(lines)


def parse_key(key) -> tuple[int, int]:
    """Key "bank/program" from config.yaml → (bank, program)."""
    bank, _, program = str(key).rpartition("/")
    patch = (int(bank or 0), int(program))
    if not (0 <= patch[0] <= 0x3FFF and 0 <= patch[1] <= 0x7F):
        raise ValueError(key)
    return patch


def display(patch: tuple[int, int]) -> str:
    """The number under which the pedal shows the patch."""
    return f"{patch[0] * PATCHES_PER_BANK + patch[1] + 1:03d}"


@functools.lru_cache(maxsize=256)
def chain_entry(effect_id: int) -> dict:
    """For the interface: an effect of the chain by its id, with its name and knobs (from the table
    of names, or a synth effect of this project) and the DSP it takes (dsp, None if not known; an
    empty place takes none). A knob whose value the pedal shows as text has "text": true (the
    texts: /api/labels). An empty place has the id 0. Made once per id: the interface's state is
    made ten times a second."""
    if not effect_id:
        return {"name": None, "empty": True, "knobs": [], "dsp": 0}
    known, synth = effect_names.effect(effect_id), zs.key_effect(effect_id)
    if known:
        texts = effect_names.labels(effect_id)
        return {"name": known["name"], "empty": False,
                "knobs": [dict(knob, text=True) if knob["param"] in texts else knob
                          for knob in effect_names.knobs(effect_id)], "dsp": effect_names.dsp(effect_id)}
    if synth:
        return {"name": synth.name, "empty": False,
                "knobs": [{"param": zs.MIN_PARAM + index, "name": name, "max": highest}
                          for index, (name, highest) in enumerate(synth.knobs)], "dsp": None}
    return {"name": None, "empty": False, "knobs": [], "dsp": None}


def over_budget(chain_ids: list, slot: int, effect_id: int) -> Optional[str]:
    """Why that effect cannot go into that place of the chain: the DSP the effects of the other
    places take (on or off, those whose DSP is known) and its own come to more than DSP_BUDGET –
    the pedal would take it all the same and show PROCESS OVERFLOW. None if it fits, or if its own
    is not known. The loads are compared as their files declare them (269.93 fits, 270.04 does
    not), and said rounded, as the interface shows them."""
    load = effect_names.dsp(effect_id)
    if load is None:
        return None
    others = sum(effect_names.dsp(other) or 0 for place, other in enumerate(chain_ids) if place != slot and other)
    if round(others + load, 2) <= DSP_BUDGET:
        return None
    return (f"{effect_label(effect_id)} takes {_shown(load)} of the DSP, {max(0, _shown(DSP_BUDGET - others))} "
            "are free in this preset – the pedal would show PROCESS OVERFLOW")


def _shown(load: float) -> int:
    """A DSP load as the interface shows it: rounded, half up (as Math.round)."""
    return math.floor(round(load, 2) + 0.5)


def plays_other_build(effect_id: int) -> bool:
    """Whether the bridge plays a synth effect with other knobs than the table describes for it
    (a build read from the pedal after the start: zs.use_installed_synths)."""
    synth, entry = zs.key_effect(effect_id), effect_names.effect(effect_id)
    return synth is not None and entry is not None and tuple(map(tuple, entry["knobs"])) != synth.knobs


def can_play_build(effect_id: int) -> bool:
    """Whether the bridge could play the synth effect as the table describes it (zs.installed
    takes it at the start): its Key knobs are there, with as many voices as they are."""
    synth, entry = zs.key_effect(effect_id), effect_names.effect(effect_id)
    if synth is None or entry is None:
        return False
    try:
        zs.SynthEffect(synth.name, synth.number, tuple(map(tuple, entry["knobs"])), lfo=synth.lfo)
    except ValueError:
        return False
    return True


def keys_in_place(effect_id: int) -> bool:
    """Whether the Key knobs the bridge plays the synth effect with are Key knobs in the build the
    table describes (zs.key_knobs; "Key" and "Key1" are the same format): otherwise its notes
    would go into other knobs, and it is not played. KeyBand played as 0.1x on a 0.20 sounds on
    Key1; played as 0.20 on a 0.1x, Key2–Key4 would be its Level, Voice and Chord."""
    synth, entry = zs.key_effect(effect_id), effect_names.effect(effect_id)
    if synth is None or entry is None or not plays_other_build(effect_id):
        return True
    keys = zs.key_knobs(entry["knobs"])
    return all(index in keys for index in range(synth.key_index, synth.key_index + synth.voices))


def _other_build(effect_id: int) -> str:
    """What the keyboard does with a synth effect the bridge plays as another build than the one on
    the pedal: one whose Key knobs are not where it needs them (it cannot play it), or with its own
    lists (MS_PLUS_BRIDGE_SYNTHS=builtin); a build it can play it plays as soon as it is read."""
    played = "plays notes only" if keys_in_place(effect_id) else "leaves it alone"
    if can_play_build(effect_id):
        return f"the keyboard {played} (the bridge goes by its own lists of the knobs)"
    return "the bridge cannot play this build (its Key knobs are not as it expects them)" + (
        "" if keys_in_place(effect_id) else ": the keyboard leaves it alone")


def matches_table(found: zs.PatchEffect) -> bool:
    """Whether what an effect's knobs hold on the pedal fits its entry in zoom_effects.json: no
    knob above its highest value, none beyond its knobs holding anything – and for a synth effect,
    whether the bridge plays the knobs of that entry. An effect the table does not know fits
    (there is nothing to go wrong)."""
    entry = effect_names.effect(found.id)
    if entry is None:
        return True
    if plays_other_build(found.id):
        return False
    if not found.values:
        return True
    knobs = entry["knobs"]
    return all(value <= knobs[index][1] or knobs[index][1] == 0 if index < len(knobs) else not value
               for index, value in enumerate(found.values))


def _without_cursor(patch: Optional[bytes]) -> Optional[bytes]:
    """A patch up to its own length (what follows is left over), without the bytes that say which
    effect is selected on the pedal (PRM2 10–12)."""
    prm2 = zs.patch_chunks(patch).get(b"PRM2") if patch else None
    if prm2 is None:
        return patch
    end = int.from_bytes(patch[4:8], "little")
    return patch[:prm2[0] + 10] + patch[prm2[0] + 13:end]


@functools.lru_cache(maxsize=8)
def effect_choices(effects: frozenset) -> list:
    """For the interface: what a place of the chain can be changed to, the effects the pedal
    reported in its presets that have a name (effect_names.py), by group and name, with the DSP
    each takes (None if not known: it is never refused for it, over_budget). A custom effect
    reported under another id as well (a preset made with an older build of it, in another
    category) is offered once, under the id the table has: the pedal lists it once."""
    order = list(EFFECT_GROUPS.values()) + ["Other"]
    known = effect_names.table()
    described = {effect_id & 0xFFFFFF for effect_id in effects if f"{effect_id:08X}" in known}
    choices = []
    for effect_id in effects:
        number = effect_id & 0xFFFFFF
        if (number in described and f"{effect_id:08X}" not in known
                and number >= effect_names.CUSTOM_FROM and number not in effect_names.ZOOM_OWN):
            continue                                  # the table's id of it is offered
        entry = chain_entry(effect_id)
        if entry["name"]:
            choices.append({"id": f"{effect_id:08X}", "name": entry["name"],
                            "group": EFFECT_GROUPS.get(effect_id >> 24, "Other"), "dsp": entry["dsp"]})
    return sorted(choices, key=lambda choice: (order.index(choice["group"]), choice["name"].lower(), choice["id"]))


def read_synth(cfg: dict) -> Optional[dict]:
    """The optional `synth:` section of config.yaml, checked. None: no keyboard synth set up."""
    section = cfg.get("synth")
    if not section or not (section.get("keyboard") or section.get("external")):
        return None
    unknown = set(section) - {"keyboard", "channel", "external", "external_channel", "effect_id", "poly_effect_id",
                              "low_note", "high_note", "sustain", "bend_range"}
    if unknown:
        sys.exit(f"config.yaml, synth: unknown setting {', '.join(sorted(unknown))}")
    effect_id = lambda name: None if section.get(name) is None else int(section[name])
    try:
        synth = {"keyboard": str(section.get("keyboard") or ""), "channel": int(section.get("channel", 0)),
                 "external": str(section.get("external") or ""),
                 "external_channel": int(section.get("external_channel", 0)),
                 "effect_id": effect_id("effect_id"), "poly_effect_id": effect_id("poly_effect_id"),
                 "low_note": int(section.get("low_note", 0)),
                 "high_note": int(section.get("high_note", 127)), "sustain": bool(section.get("sustain", True)),
                 "bend_range": float(section.get("bend_range", 2))}
    except (TypeError, ValueError):
        sys.exit("config.yaml, synth: a value is not a number.")
    ids = [synth[name] for name in ("effect_id", "poly_effect_id") if synth[name] is not None]
    if not (0 <= synth["channel"] <= 16 and 0 <= synth["external_channel"] <= 16
            and all(0 < number <= 0xFFFFFFFF for number in ids)
            and len(set(ids)) == len(ids)
            and 0 <= synth["low_note"] <= synth["high_note"] <= 127
            and 0 <= synth["bend_range"] <= MAX_BEND_RANGE):
        sys.exit("config.yaml, synth: channel and external_channel must be 0–16 (0 = any), notes 0–127 with "
                 f"low_note ≤ high_note, bend_range 0–{MAX_BEND_RANGE} semitones, effect_id the id "
                 "of the KeySynth effect and poly_effect_id that of KeyPoly (two different ids).")
    for number, effect in synth_effects(synth).items():
        known = zs.key_effect(number)
        if known not in (None, effect):         # it would be played with another effect's knobs
            sys.exit(f"config.yaml, synth: {number:08x} is the id of {known.name}, not of {effect.name}. "
                     f"The bridge knows {known.name} without it – remove the line.")
    if synth["keyboard"] and synth["keyboard"].lower() == synth["external"].lower():
        sys.exit("config.yaml, synth: keyboard and external must be two different ports.")
    # The effects' Key knobs reach C0 to D#8; notes outside that are not played.
    synth["low_note"] = max(synth["low_note"], zs.KEY_LOW_NOTE)
    synth["high_note"] = min(synth["high_note"], zs.KEY_HIGH_NOTE)
    return synth


def synth_effects(synth: Optional[dict]) -> dict:
    """KeySynth and KeyPoly under an id of their own, from config.yaml: {effect id: zs.SynthEffect}.
    Only a build with another id needs that; the bridge knows the effects by zs.key_effect."""
    return {synth[name]: effect for name, effect in (("effect_id", zs.KEYSYNTH), ("poly_effect_id", zs.KEYPOLY))
            if synth and synth[name] is not None}


def read_controls(path: Path) -> dict:
    """Which controller of the keyboard sets what: {"KeyFM/Index" (a knob of one synth effect), or
    the switch of the arpeggiator or the sequencer: CC number}, see synth_controls.py. No file yet:
    nothing is assigned. Anything in the file that cannot be right is dropped."""
    if not path.exists():
        return {}
    try:
        stored = json.loads(path.read_text(encoding="utf-8")).get("controls", {})
    except (OSError, ValueError, AttributeError):
        sys.exit(f"{path.name} is damaged – delete it to start again without assignments.")
    return synth_controls.read(stored)


def _stored(path: Path, section: str):
    """A section of the file that also keeps the controllers; None if the file or the section is not there."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(section)
    except (OSError, ValueError, AttributeError):
        sys.exit(f"{path.name} is damaged – delete it to start again without assignments.")


def read_arp(path: Path) -> dict:
    """The arpeggiator's settings, kept in the same file as the controllers (only what differs from
    the defaults is stored). A setting that cannot be right takes its default."""
    return arpeggiator.read(_stored(path, "arp"))


def read_seq(path: Path) -> list:
    """The step sequencer's pattern, kept in the same file. One that cannot be right is not played."""
    return sequencer.read(_stored(path, "seq"))


def read_switches(path: Path) -> dict:
    """Which controller switches which effect of the chain, kept in the same file:
    {slot: {"cc": number, "press": bool}}."""
    return effect_switch.read(_stored(path, "effects"))


def read_scenes(path: Path) -> dict:
    """The scenes per preset, kept in the same file (scenes.py)."""
    return scene_file.read(_stored(path, "scenes"))


def gather_settings() -> dict:
    """Everything a bridge without screen needs, as its settings file holds it: config.yaml, the
    learned ranges, the approvals, the keyboard's controllers, arpeggiator and pattern, and the
    switches of the effects, from the files of this installation (or profile)."""
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    approvals = {"backup_confirmed": None, "messages": {}}
    if APPROVALS.exists():
        approvals.update(json.loads(APPROVALS.read_text(encoding="utf-8")))
    synth = config.get("synth") or {}
    has_keyboard = bool(synth.get("keyboard") or synth.get("external"))
    controls = read_controls(CONTROLS) if has_keyboard else {}
    arp = read_arp(CONTROLS) if has_keyboard else None
    seq = read_seq(CONTROLS) if has_keyboard else None
    measurements = zs.Measurements(MEASUREMENTS)
    return cs.build(config, measurements.patches, approvals, controls,
                    time.strftime("%Y-%m-%d %H:%M:%S"), arp, seq, read_switches(CONTROLS), read_scenes(CONTROLS),
                    measurements.effects)


PI_WLAN_NAME = "MS Plus Bridge"


def ensure_pi_wlan() -> dict:
    """The name and password of the WLAN a Raspberry Pi opens where no other one it knows is
    around (a phone joins it to reach the interface). Made once, with a password of its own, and
    kept in config.yaml under `pi:`, in front of the assignments."""
    text = CONFIG.read_text(encoding="utf-8")
    config = yaml.safe_load(text) or {}
    wlan = config.get("pi") or {}
    if wlan.get("wlan_name") and wlan.get("wlan_password"):
        return wlan
    if "pi" in config:                                 # half of it: a guess would hide a typo
        sys.exit("config.yaml: complete the “pi:” section (wlan_name and wlan_password) or remove it.")
    import secrets
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"                    # nothing that reads as another letter
    wlan = {"wlan_name": PI_WLAN_NAME, "wlan_password": "".join(secrets.choice(alphabet) for _ in range(10))}
    head, found, rest = text.partition("\nmappings:")
    block = ("\n# The WLAN the Raspberry Pi opens where none it knows is around: join it with the phone and open\n"
             "# http://<name of the Pi>.local for the interface. Written to the card with the settings.\n"
             + yaml.safe_dump({"pi": wlan}, sort_keys=False, allow_unicode=True))
    zs.write_whole(CONFIG, head + block + found + rest if found else text.rstrip("\n") + "\n" + block)
    return wlan


class Keys:
    """Which notes the synth should sound: the last one pressed wins, and when it is released the
    one pressed before it comes back if it is still down. With several voices the same holds for
    the last few notes."""

    def __init__(self, sustain_enabled: bool = True):
        self.sustain_enabled = sustain_enabled
        self.held: list = []          # notes that are down, oldest first
        self.sustained: list = []     # notes released while the sustain pedal was down
        self.pedal_down = False
        self.presses = 0
        self.pressed_as: dict = {}    # note → the how-manyth key press it was
        self.velocities: dict = {}    # note → how hard its key was struck last (1–127; None: not known)

    def note_on(self, note: int, velocity: Optional[int] = None) -> None:
        self.note_off(note, sustain=False)
        self.held.append(note)
        self.presses += 1
        self.pressed_as[note] = self.presses
        self.velocities[note] = velocity

    def note_off(self, note: int, sustain: bool = True) -> None:
        if note in self.sustained:
            self.sustained.remove(note)
        if note in self.held:
            self.held.remove(note)
            if sustain and self.pedal_down:
                self.sustained.append(note)

    def pedal(self, down: bool) -> None:
        self.pedal_down = down and self.sustain_enabled
        if not self.pedal_down:
            self.sustained.clear()

    def clear(self) -> None:
        self.held.clear()
        self.sustained.clear()

    @property
    def note(self) -> Optional[int]:
        """The MIDI note that should sound on a single voice, None for silence."""
        notes = self.notes(1)
        return notes[0] if notes else None

    def notes(self, voices: int) -> list:
        """The MIDI notes that should sound on so many voices, the most important first: keys that
        are down before notes the sustain pedal holds, the latest first in both."""
        return (self.held[::-1] + self.sustained[::-1])[:voices]

    def played(self) -> list:
        """Every note that is down or held by the sustain pedal, in the order the keys were pressed."""
        return sorted(self.held + self.sustained, key=lambda note: self.pressed_as.get(note, 0))


class Voice:
    """One Key knob of the synth effect: the note given to it and what the pedal holds."""

    def __init__(self, index: int):
        self.index = index                       # 0 = KeySynth's Key or KeyPoly's Key1, up to Key4
        self.note: Optional[int] = None          # the MIDI note it plays, None = gate closed
        self.note_sent: Optional[int] = None     # the note the last Key value was for
        self.key_sent: Optional[int] = None      # the Key value the pedal holds, as far as we know; None = unknown
        self.awaiting: deque = deque()           # KeySent, ack outstanding
        self.last_note: Optional[int] = None     # the note it played last, sounding or fading out
        self.freed = float("-inf")               # when its last note was taken from it


@dataclass(frozen=True)
class Mapping:
    """A parameter of a preset and the controller that sets it: the expression pedal (cc None) or
    a knob, slider, wheel or pedal of any controller that sends that CC number. invert: heel (or
    the knob at 0) = max, toe (or the knob at full) = min. effect: the id of the effect it was
    made for, so that it can go with it when the effect moves in the chain (None: not known)."""

    slot: int
    param: int
    min: int
    max: int
    invert: bool = False
    curve: str = "linear"
    name: str = ""
    cc: Optional[int] = None
    effect: Optional[int] = None

    def target(self, position: float) -> int:
        """Pedal position 0..1 → parameter value."""
        x = CURVES[self.curve](position)
        if self.invert:
            x = 1.0 - x
        return round(self.min + x * (self.max - self.min))

    def describe(self) -> str:
        low, high = (self.max, self.min) if self.invert else (self.min, self.max)
        return f"effect {self.slot + 1}, parameter {self.param - 1}, {low}–{high}"


def to_mapping(entry: dict) -> Mapping:
    """A mapping from interface input or an import file; raises on incomplete data."""
    cc = entry.get("cc")
    if cc is not None and (isinstance(cc, bool) or not isinstance(cc, int)):
        raise ValueError(cc)
    return Mapping(int(entry["slot"]), int(entry["param"]), int(entry["min"]), int(entry["max"]),
                   invert=bool(entry.get("invert")), curve=str(entry.get("curve", "linear")),
                   name=str(entry.get("name", "")).strip()[:40], cc=cc, effect=effect_id(entry.get("effect")))


def read_mappings(stored) -> dict:
    """(bank, program) → its assignments, as config.yaml or a settings file keeps them; a preset
    without any (written so by a bridge before 2026-10-20) is left out. Raises ValueError for an
    effect that is not an id, with the preset's key as its first argument."""
    found = {}
    for key, entries in (stored or {}).items():
        if not entries:
            continue
        try:
            found[parse_key(key)] = tuple(Mapping(**dict(entry, effect=effect_id(entry.get("effect"))))
                                          for entry in (entries if isinstance(entries, list) else [entries]))
        except ValueError as error:
            raise ValueError(key, error) from None
    return found


def stored_mapping(mapping: Mapping) -> dict:
    """A mapping as config.yaml, the settings file and an export keep it: the controller only
    when it is not the expression pedal (as files from before 2026-10-18 have them), the effect
    as its id in hex, once it is known."""
    entry = asdict(mapping)
    if entry["cc"] is None:
        del entry["cc"]
    if entry["effect"] is None:
        del entry["effect"]
    else:
        entry["effect"] = f"{mapping.effect:08X}"
    return entry


def effect_id(text) -> Optional[int]:
    """An effect id as the files keep it ("0A000020"); None for none. Raises ValueError otherwise."""
    if text is None:
        return None
    if not isinstance(text, str) or not cs.EFFECT_ID.fullmatch(text):
        raise ValueError(text)
    return int(text, 16)


def same_effect(one: Optional[int], other: Optional[int]) -> bool:
    """Two effect ids name the same effect. The whole id: its lower bits alone are shared by
    effects of different categories (BlackOpt 01000020, AnalogDly 0A000020). An empty place (0)
    is no effect."""
    return bool(one) and one == other


def place_of(slot: int, effect: Optional[int], chain: Optional[list]) -> Optional[int]:
    """Where the effect something was assigned to (at slot) is in the chain now: that place while
    it is there (or while the effect or the chain is not known), else the one other place that
    has it. None if it is not in the chain, or in more than one other place."""
    if effect is None or chain is None:
        return slot
    if slot < len(chain) and same_effect(chain[slot], effect):
        return slot
    found = [index for index, other in enumerate(chain) if same_effect(other, effect)]
    return found[0] if len(found) == 1 else None


def effect_label(effect: int, param: Optional[int] = None) -> str:
    """"AnalogDly", or with a knob "AnalogDly · Mix", for an effect that is not in the chain."""
    entry = chain_entry(effect)
    name = entry["name"] or f"Effect {effect:08X}"
    if param is None:
        return name
    knob = next((knob["name"] for knob in entry["knobs"] if knob["param"] == param), f"Parameter {param - 1}")
    return f"{name} · {knob}"


def _effect_or_empty(effect: int) -> str:
    return effect_label(effect) if effect else "an empty place"


def _within_length(patch: bytes) -> bytes:
    """A patch up to its own length: what follows in the buffer is left over, not read."""
    return patch[:int.from_bytes(patch[4:8], "little")]


def _stopped(change: dict) -> str:
    """What an effect change stopped by a preset change was."""
    return {"move": "Moving the effect", "add": "Adding a place",
            "delete": "Deleting the effect"}.get(change["kind"], "Changing the effect") + " stopped: preset changed"


def controller_name(cc: Optional[int]) -> str:
    return "the expression pedal" if cc is None else f"controller {cc}"


class Sent(NamedTuple):
    value: int
    cc_time: float    # arrival of the CC that caused it
    send_time: float
    retry: bool


class KeySent(NamedTuple):
    value: int
    time: float       # arrival of the keyboard message that caused it
    send_time: float
    attempt: int      # 0 for the first try
    quiet: bool       # a step of the pitch wheel or of the arpeggiator: not printed
    struck: bool      # a new note (_strikes), not a bend step: repeated with the knobs it is struck with


class Target:
    """An assigned parameter of the current preset and its send state."""

    def __init__(self, mapping: Mapping, learned: bool):
        self.mapping = mapping
        self.learned = learned                              # its range was reported by the pedal
        self.pending: Optional[tuple[int, float]] = None   # (target value, arrival of the CC)
        self.last_value: Optional[int] = None              # last value sent
        self.awaiting: deque = deque()                      # sent, ack outstanding
        self.misses_in_row = 0
        self.confirmed = False                              # the pedal has acknowledged or reported it
        self.stopped = False                                # not learned and never confirmed: nothing more is sent
        self.blocked = False                                # a synth effect's knob the expression pedal must not write
        self.expression = True                              # False: only a keyboard controller sets it
        self.assigned = False                               # one of the preset's assignments (not only a scene's)
        self.scene = False                                  # only a scene sets it: a value the pedal holds
                                                            # already stays unconfirmed, which is no trouble
        self.away: Optional[str] = None                     # its effect is not in the chain now: which it was
        self.unplaced = False                               # its effect is known, the preset's chain not yet:
                                                            # its values wait for it (Session.refresh)


class Session:
    """One connection to the pedal and the controllers there are, from opening the ports to closing them."""

    def __init__(self, bridge: "Bridge"):
        cfg = bridge.cfg
        self.synth = bridge.synth                           # None: no keyboard synth set up
        self.effects = synth_effects(self.synth)            # ids from config.yaml → zs.SynthEffect
        inputs, outputs = mido.get_input_names(), mido.get_output_names()
        self.zoom_in_name = zs.find_port(inputs, cfg["ports"]["zoom"])
        self.zoom_out_name = zs.find_port(outputs, cfg["ports"]["zoom"])
        # Every controller is optional: the pedal alone is enough to edit its preset in the
        # interface, and a controller may join or leave while the session runs (_check_ports).
        self.chocolate_name = self._present(inputs, cfg["ports"].get("chocolate"))
        if self.synth is None:
            self.keyboard_name = self.external_name = None
        else:
            self.external_name = self._present(inputs, self.synth["external"])
            self.keyboard_name = self._keyboard_port(inputs, cfg)
        self.zoom_in = self.zoom_out = self.chocolate = self.keyboard = self.external = None
        try:
            self.zoom_in = mido.open_input(self.zoom_in_name)
            self.zoom_out = mido.open_output(self.zoom_out_name)
            self.chocolate = mido.open_input(self.chocolate_name) if self.chocolate_name else None
            self.keyboard = mido.open_input(self.keyboard_name) if self.keyboard_name else None
            self.external = mido.open_input(self.external_name) if self.external_name else None
        except MIDI_ERRORS:
            # A device unplugged between listing and opening: what is open already is closed, or
            # every new attempt would leave another MIDI client behind (ALSA has room for a few).
            self._close_ports()
            raise

        self.bridge = bridge
        self.device_id = cfg["zoom"]["device_id"]
        self.expression = cfg["expression"]
        self.deadband = cfg["bridge"].get("deadband", 0)
        self.passthrough = cfg["bridge"].get("passthrough", False)
        self.min_interval = cfg["bridge"]["min_interval_ms"] / 1000
        self.interval = self.min_interval
        self.sender = zs.ZoomSender(self.zoom_out, bridge.approvals)
        self.edit_enabled = self.ready = False

        self.bank_msb = self.bank_lsb = 0
        self.bank_selected = False                          # bank select came last: a program change counts
        self.fresh = False                                  # the preset was just selected: the next patch is as saved
        self.patch_since = 0.0                              # when the preset on the pedal was last selected
        self.unplaced_given_up = False                      # its chain did not come: assignments go by their place
        self.patch: Optional[tuple[int, int]] = None
        self.targets: list = []                             # one Target per assigned parameter of the preset
        self.turn = 0                                       # whose turn it is to send
        self.learning: Optional[dict] = None                # (slot, param) → reported values, while learning
        self.last_cc: Optional[int] = None
        self.last_send = 0.0
        self.misses_in_row = self.acks_in_row = 0
        self.sent = self.acked = self.missed = 0            # since the last statistics line
        self.totals = {"sent": 0, "acked": 0, "missed": 0}
        self.latencies: list = []
        self.recent_latencies: deque = deque(maxlen=50)
        self.next_report = self.next_port_check = 0.0
        self.query_at: Optional[float] = None               # when a patch query is due

        # the synth effect of the preset: the keyboard writes its Key knobs
        self.keys = Keys(self.synth["sustain"] if self.synth else True)
        self.bend = 0.0                                     # semitones, from the pitch wheel
        self.controls: dict = {}                            # CC number → (Target, knob in the effect's controls)
        self.cc_targets: dict = {}                          # CC number → the preset's parameters it sets
        self.told: set = set()                              # remarks made about the assignments of this preset
        self.synth_slot: Optional[int] = None               # where the effect sits in the current preset
        self.synth_effect: Optional[zs.SynthEffect] = None  # which of the two it is
        self.synth_known = False                            # a patch dump has told us whether it is there
        self.voices: list = []                              # one Voice per Key knob of the effect
        self.bend_turn = -1                                 # the voice that followed the pitch wheel last
        # The arpeggiator and the step sequencer: while one of them is on (bridge.arp_on, seq_on),
        # it decides what sounds instead of the keys. Both run on the same clock.
        low_note, high_note = (self.synth["low_note"], self.synth["high_note"]) if self.synth else (0, 127)
        self.arp = arpeggiator.Arpeggiator(bridge.arp_settings, high_note)
        self.seq = sequencer.Sequencer(bridge.seq_steps, low_note, high_note)
        self.arp_note: Optional[int] = None                 # the note that one sounds now
        self.next_step: Optional[float] = None              # their own clock: when the next step is due
        self.gate_off_at: Optional[float] = None            # when the note that sounds ends
        self.ticks = 0                                      # MIDI clocks since Start
        self.clock_running = True                           # False from Stop until Start or Continue
        self.tick_times: deque = deque(maxlen=25)           # arrival of the last MIDI clocks, for its tempo
        self.key_misses = 0
        self.key_silent_since = 0.0                         # when the first of these unconfirmed notes was sent
        self.key_totals = {"sent": 0, "acked": 0, "missed": 0, "overtaken": 0}
        self.key_latencies: deque = deque(maxlen=50)
        self.patch_query_at: Optional[float] = None         # when to look for the effect (after a preset change)
        self.patch_query_wanted = False     # … and that query was put off for more than the synth effect
        self.last_patch_query = float("-inf")
        self.last_key_trouble = float("-inf")
        self.last_reenable = float("-inf")
        self.last_heard = time.monotonic()                 # when anything last came from the pedal
        self.can_query_patch = bridge.approvals.is_approved("query_patch")
        # The effects of the chain, on or off: only those the pedal itself reported for the
        # preset are switched, so it takes the patch query as well as its own approval.
        self.switching = self.can_query_patch and bridge.approvals.is_approved("switch_effect")
        # Patch dumps are read whenever they may be asked for: for the synth effect, the switches,
        # and the names of the effects in the interface.
        self.watching = self.can_query_patch
        # Renaming the preset: read back from the patch, so it takes the patch query as well.
        self.renaming = self.can_query_patch and bridge.approvals.is_approved("rename_patch")
        self.name_queue: list = []                          # (place, character) of a new name still to send
        self.name_wanted: Optional[str] = None              # the name being sent, until the pedal's patch is read
        self.name_sent = float("-inf")
        self.name_check_at: Optional[float] = None          # when to read the patch after the last character
        self.name_checking = False                          # the next patch dump tells whether it was taken
        self.name_acks = 0                                  # characters the pedal confirmed
        self.name_result: Optional[dict] = None             # the last renaming: {"ok", "text"}, for the interface
        # Another effect in a place of the chain: only one the pedal reported in a preset (so it has
        # it), into a place it reported an effect in, and read back from the patch.
        self.changing = self.can_query_patch and bridge.approvals.is_approved("change_effect")
        self.sender.effects = bridge.measurements.effects
        # Moving an effect: the patch it reports is sent back with two effects swapped.
        self.moving = self.can_query_patch and bridge.approvals.is_approved("move_effect")
        self.patch_data: Optional[bytes] = None             # the patch the pedal reported last, unpacked
        # The editor: what each knob of the chain holds (from the patch, then from what the pedal
        # reports and confirms), and the values set in the interface still to be sent.
        self.knob_values: list = []                         # per effect: {parameter: value}
        self.edits: dict = {}                               # (slot, parameter) → value
        self.edit_awaiting: dict = {}                       # (slot, parameter) → (value, when sent, which try, effect)
        self.strike_awaiting: dict = {}                     # (slot, parameter) → values sent before a note, ack due
        # Effects whose knobs on the pedal hold what their entry in zoom_effects.json cannot: another
        # build is installed than the table describes. Their knobs go by number, not by the table.
        self.unmatched: set = set()
        self.unreadable: set = set()                        # (name, size) of effect files not read in this run
        self.reading_files_now = False                      # effect files are being read from the pedal
        self.recheck = False                                # the table changed: the next patch checks every effect again
        self.recall_waiting: Optional[int] = None           # a scene recalled before the chain was known
        # Adding an empty place: as moving, the patch sent back, with the place inserted.
        self.adding = self.can_query_patch and bridge.approvals.is_approved("add_effect")
        # Deleting an effect: as moving, the patch sent back, with the effect taken out.
        self.deleting = self.can_query_patch and bridge.approvals.is_approved("delete_effect")
        # Presets: chosen from the interface (bank select and program change), their names read
        # from their places, and the preset on the pedal stored into its own place or another one.
        self.selecting = bridge.approvals.is_approved("select_preset")
        self.reading_presets = bridge.approvals.is_approved("query_preset")
        self.saving = (self.can_query_patch and self.reading_presets
                       and bridge.approvals.is_approved("save_preset"))
        self.preset_names: dict = {}                        # (bank, program) → its name as stored on the pedal
        self.preset_queue: list = []                        # places whose name is still to be read
        self.preset_awaiting: Optional[tuple] = None        # (place, when it was asked for)
        self.preset_misses = 0                              # places in a row that drew no answer
        self.preset_read_at = 0.0                           # when the next one may be asked for
        self.preset_save: Optional[dict] = None             # a preset being stored: place, phase, the patch
        self.preset_result: Optional[dict] = None           # what came of the last one: {"ok", "text"}
        self.loading_stored = False                         # chosen here: the pedal loads the preset as stored
        self.reading_pedal = bridge.approvals.is_approved("read_pedal")
        self.reading_files = self.reading_pedal and bridge.approvals.is_approved("read_effect_file")
        # at the start the pedal's list of files is read once by itself – when nothing would be lost;
        # not again when the pedal is connected anew (a cable pulled while playing: no PC mode then)
        self.auto_read = (self.reading_pedal and self.reading_presets and self.can_query_patch
                          and not bridge.read_at_start)
        self.auto_read_asked: Optional[float] = None        # when the patch to compare with was asked for
        self.patch_at = float("-inf")                       # when the pedal last sent its patch
        self.listing: Optional[list] = None                 # the files of the pedal, while its list is read
        self.read_result: Optional[dict] = None             # what came of the last reading: {"ok", "text"}
        self.preset_data: dict = {}                         # (bank, program) → the patch stored there, as last read
        self.preset_undo: Optional[dict] = None             # the last save: place, what was there, what was sent
        self.effect_change: Optional[dict] = None           # a change or move on its way: kind, phase, slot …
        self.effect_result: Optional[dict] = None           # the last change: {"ok", "text"}, for the interface
        self.chain: Optional[list] = None                   # per effect of the preset: on, off, or None (unknown)
        self.chain_ids: Optional[list] = None               # per effect of the preset: its id, from the patch dump
        self.patch_name: Optional[str] = None               # the preset's name, from the patch dump
        self.switch_awaiting: dict = {}                     # slot → (on or off, when sent, which try)
        self.switch_quiet: tuple = (None, 0.0)              # the controller learned last, not obeyed until then
        self.scene_targets: dict = {}                       # (slot, param) → Target, for the scenes of the preset
        self.scene_active: Optional[int] = None             # the scene recalled last in this preset
        self.capture_effects: dict = {}                     # slot → on, switched while a scene is captured

    @property
    def playable(self) -> bool:
        """Something that plays notes is connected: the keyboard or the external sequencer."""
        return self.keyboard is not None or self.external is not None

    @property
    def controllers(self) -> list:
        """The ports of the controllers connected: the expression controller, the keyboard and
        the external sequencer. Empty: only the pedal, whose preset is edited in the interface."""
        return [name for name in (self.chocolate_name, self.keyboard_name, self.external_name) if name]

    def _no_controller(self) -> str:
        """What is said while no controller is connected: with none set up none can join, and
        without an interface (a bridge without screen whose settings cannot be changed) nothing
        is edited."""
        set_up = self.bridge.cfg["ports"].get("chocolate") or self.synth is not None   # a keyboard or a sequencer
        if self.bridge.interface:
            return NO_CONTROLLER if set_up else NO_CONTROLLER_SET_UP
        return ("No controller connected – a controller joins when it is plugged in" if set_up
                else "No controller set up – there is nothing to play the pedal with")

    @staticmethod
    def _present(names, needle: str) -> Optional[str]:
        """The port for an optional controller, or None while it is not connected – or while more
        than one port fits its name (two identical devices on Linux): then it is left out, said
        once, and the others go on."""
        if not needle or not zs.port_candidates(names, needle):
            return None
        try:
            return zs.find_port(names, needle)
        except zs.PortError as error:
            if needle not in AMBIGUOUS_TOLD:
                AMBIGUOUS_TOLD.add(needle)
                say(f"WARNING: {error} – it is left out until only one is there")
            return None

    def _keyboard_port(self, names, cfg: dict) -> Optional[str]:
        """The keyboard's port: by its name, or for "any keyboard" (ANY_PORT) the first input that
        is not the pedal and not what the expression controller or the external sequencer is
        called, connected or not."""
        needle = self.synth["keyboard"]
        if needle != ANY_PORT:
            return self._present(names, needle)
        others = {self.zoom_in_name}
        for name in (cfg["ports"].get("chocolate"), self.synth["external"]):
            others.update(zs.port_candidates(names, name) if name else ())
        free = [name for name in sorted(names) if name not in others
                and not NOT_A_KEYBOARD.search(name)]
        return free[0] if free else None

    # --- Setup and teardown ---

    def start(self) -> None:
        self.sender.send(zs.build_identity_request())
        identity = self._wait(zs.parse_identity_reply, 2.0)
        if identity is None:
            raise Disconnected("no identity reply from the MS-60B+")
        if identity.device_id != self.device_id:
            sys.exit(f"Device ID {identity.device_id:02X} does not match config.yaml – nothing further sent.")
        self.sender.device_id = self.device_id

        self.sender.send(zs.build_edit_enable(self.device_id))
        self.edit_enabled = True
        if not self._wait(lambda raw: zs.is_ack(zs.zoom_body(raw, self.device_id)), 1.0):
            raise Disconnected("no ack for edit enable")

        self.sender.send(zs.build_query_program(self.device_id))
        self._wait(lambda raw: None, 0.5)   # the reply (bank select + program change) is handled by _on_zoom
        if self.patch is None:
            say("Current preset unknown – expression is ignored until the pedal reports a preset.")
        if self.synth is not None and not self.can_query_patch:
            say("Keyboard synth: the patch query is not approved (`python probe.py approve query_patch`) "
                "– the keyboard is ignored")
        if self.bridge.switches and not self.switching:
            say("Effect switches: not approved (`python probe.py approve switch_effect query_patch`) "
                "– they are ignored")
        if self.watching and self.can_query_patch:
            self.patch_query_at = None
            self._look_for_synth(time.monotonic(), force=True)
            self._wait(lambda raw: self.chain is not None, 1.0)
        self.ready = True
        if self.reading_presets:
            self.preset_queue = [(bank, program) for bank in range(zs.PRESET_BANKS)
                                 for program in range(zs.PRESETS_PER_BANK)]
        say(f"Ready: {' + '.join(self.controllers) or 'no controller'} → {self.zoom_out_name}, "
            f"firmware {identity.version}")
        if not self.controllers:
            say(self._no_controller())

    def close(self) -> None:
        try:
            if self.edit_enabled:
                if any(voice.key_sent for voice in self.voices):    # never leave a note hanging
                    self.keys.clear()
                    self._arp_silence()
                    if self.rearranging and self._synth_stays():
                        self.effect_change = None   # the gate-off goes now, into the place the synth keeps
                    self._play(time.monotonic())
                    self._wait(lambda raw: not any(voice.awaiting for voice in self.voices), 0.3)
                self.sender.send(zs.build_edit_disable(self.device_id))
                self._wait(lambda raw: zs.is_ack(zs.zoom_body(raw, self.device_id)), 0.5)
        except MIDI_ERRORS:
            pass   # the device is already gone
        finally:
            self._close_ports()

    def _close_ports(self) -> None:
        for port in (self.zoom_in, self.zoom_out, self.chocolate, self.keyboard, self.external):
            try:
                if port is not None:
                    port.close()
            except MIDI_ERRORS:
                pass   # the others are still closed

    def _wait(self, match, timeout: float):
        """Processes incoming Zoom messages until match(raw) returns something truthy for a SysEx."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for message in self.zoom_in.iter_pending():
                raw = self._on_zoom(message, time.monotonic())
                result = raw and match(raw)
                if result:
                    return result
            time.sleep(0.001)
        return None

    # --- One pass of the main loop ---

    def step(self, now: float) -> None:
        for port, external in ((self.keyboard, False), (self.external, True)):
            if port is not None:                # notes first: they are never delayed or merged
                for message in port.iter_pending():
                    self._on_keyboard(message, now, external)
        if self.synth is not None:
            self._arp_tick(now)
        if self.chocolate is not None:
            for message in self.chocolate.iter_pending():
                self._on_chocolate(message, now)
        for message in self.zoom_in.iter_pending():
            self._on_zoom(message, now)
        self._flush(now)
        self._send_name(now)
        self._check_effect_change(now)
        self._check_preset_save(now)
        self._read_presets(now)
        self._read_pedal_at_start(now)
        self._check_edits(now)
        self._check_timeouts(now)
        if self.query_at is not None and now >= self.query_at:
            self.query_at = None
            self.sender.send(zs.build_query_program(self.device_id))
        if (self.chain_ids is None and not self.unplaced_given_up and now - self.patch_since >= PLACE_SECONDS
                and any(target.unplaced for target in self.targets)):
            self.unplaced_given_up = True           # as without the patch query: by place
            say("The pedal has not reported the effects of this preset – what is assigned goes by its place")
            self.refresh()
            if self.recall_waiting is not None:
                index, self.recall_waiting = self.recall_waiting, None
                self.recall_scene(index, now)
        if self.patch_query_at is not None and now >= self.patch_query_at:
            wanted, self.patch_query_at, self.patch_query_wanted = self.patch_query_wanted, None, False
            # Known to be absent: only keep looking while a key is down (the player is waiting for it).
            # A query put off for something else (a name, a switch, an effect chosen on the pedal) goes.
            if (wanted or self.synth_slot is not None or not self.synth_known or self.keys.note is not None
                    or self.chain is None):
                self._look_for_synth(now, force=True)
        self._report(now)
        if now >= self.next_port_check:
            self._check_ports()
            self.next_port_check = now + PORT_CHECK_SECONDS

    def _check_ports(self) -> None:
        inputs, outputs = mido.get_input_names(), mido.get_output_names()
        self.bridge.port_names = (inputs, outputs)
        for name, names in ((self.zoom_in_name, inputs), (self.zoom_out_name, outputs)):
            if name not in names:
                raise Disconnected(f"{name} is gone")
        # The controllers: one may leave or join while the others keep working. Without any the
        # session stays with the pedal, whose preset can still be edited in the interface.
        controlled = bool(self.controllers)
        if self.chocolate_name is not None and self.chocolate_name not in inputs:
            say(f"{self.chocolate_name} is gone")
            self._drop("chocolate")
        for attribute in ("keyboard", "external"):
            name = getattr(self, attribute + "_name")
            if name is not None and name not in inputs:
                say(f"{name} is gone")
                self._drop(attribute)
                self.keys.clear()                   # whoever held them: no note is left hanging …
                self.keys.pedal(False)              # … nor held by a sustain pedal unplugged with it
                self.bend = 0.0
                self.arp.clear()
                self.seq.clear()
                self._arp_silence()
                self._play(time.monotonic())        # gate off
        joining = [("chocolate", self.bridge.cfg["ports"].get("chocolate"))]
        if self.synth is not None:
            joining += [("external", self.synth["external"]), ("keyboard", self.synth["keyboard"])]
        for attribute, needle in joining:
            if getattr(self, attribute) is None:
                name = (self._keyboard_port(inputs, self.bridge.cfg) if attribute == "keyboard"
                        else self._present(inputs, needle))
                if name is not None:
                    setattr(self, attribute, mido.open_input(name))
                    setattr(self, attribute + "_name", name)
                    say(f"{name} connected")
        if controlled and not self.controllers:     # said when the last one left, not at every check
            say(self._no_controller())

    def _drop(self, attribute: str) -> None:
        port = getattr(self, attribute)
        setattr(self, attribute, None)
        setattr(self, attribute + "_name", None)
        try:
            port.close()
        except MIDI_ERRORS:
            pass   # the device is already gone

    # --- Chocolate → target values ---

    def _on_chocolate(self, message, now: float) -> None:
        expression = self.expression
        is_pedal = message.type == "control_change" and (message.control, message.channel + 1) == (
            expression["cc"], expression["channel"])
        if self.bridge.param_learning is not None and message.type == "control_change" and (
                is_pedal or message.control not in RESERVED_CONTROLS):
            self.bridge.learned_param(None if is_pedal else message.control)
            return
        if is_pedal:
            # While learning the user turns knobs on the pedal; nothing is sent against that.
            if self._accept(message.value) and self.learning is None:
                span = expression["max"] - expression["min"]
                position = min(1.0, max(0.0, (message.value - expression["min"]) / span))
                if expression["invert"]:
                    position = 1.0 - position
                for target in self.targets:
                    if target.expression and not (target.stopped or target.blocked):
                        target.pending = (target.mapping.target(position), now)   # an older value is dropped
        elif self.passthrough and (message.type == "program_change" or (
                message.type == "control_change" and message.control in (0, 32))):
            zs.log.info("TX  %s  (passed through)", message)
            self.zoom_out.send(message)
            if message.type == "program_change":
                self._left_preset(now)
        elif message.type == "control_change" and message.control not in RESERVED_CONTROLS:
            if not self._on_switch(message.control, message.value, now):    # a footswitch for an effect
                self._set_by_controller(message.control, message.value, now)

    def _left_preset(self, now: float) -> None:
        """A program change went to the pedal (passed through, or a preset chosen in the
        interface): no parameter goes out until it reports the new patch, and nothing of the old
        chain is taken for the new one."""
        self.patch = None
        self.chain, self.chain_ids, self.patch_name, self.patch_data = None, None, None, None
        self.knob_values, self.edits, self.edit_awaiting, self.strike_awaiting = [], {}, {}, {}
        self.sender.switchable = self.sender.changeable = ()
        if self.synth is not None:  # no Key goes out until the new preset's chain is known
            self.synth_slot, self.synth_effect, self.synth_known, self.voices = None, None, False, []
        if self.effect_change is not None:
            self._effect_done(False, _stopped(self.effect_change), play=False)
        if self.preset_save is not None and self.preset_save["phase"] == "reading":
            self._save_done(False, "Saving stopped: preset changed")
        if self.name_wanted is not None:            # the rest of the name would go into the next one
            self._name_done(False, "Renaming stopped: preset changed")
        self.refresh()
        self.query_at = now + QUERY_DELAY

    # --- Presets: choosing, their names, storing ---

    def select_preset(self, bank: int, program: int, now: float) -> Optional[str]:
        """Chooses a preset on the pedal, as its footswitches do: it loads it as stored, so what was
        changed in the one on the pedal and not saved is gone (the same one chosen again: back to
        how it is stored). Returns why it was not done."""
        if not self.selecting:
            return "Choosing presets is not approved (`python probe.py approve select_preset`)."
        if not zs.is_preset(bank, program):
            return f"The pedal has no preset {display((bank, program))}."
        if self.effect_change is not None or self.preset_save is not None or self.name_wanted is not None:
            return "Something is being changed on the pedal: try again in a moment."
        self.sender.select_preset(bank, program)
        self.last_send = time.monotonic()
        self._left_preset(now)
        self.loading_stored = True
        return None

    def save_preset(self, bank: int, program: int, now: float) -> Optional[str]:
        """Stores the preset on the pedal, as it is now, into its own place or another one (whose
        preset it replaces). The patch is read afresh, sent into the place in the form the pedal
        itself reports a saved preset in, and the place is read back. Into another place: what the
        bridge keeps for this preset goes to that one, which the pedal is switched to (when
        choosing presets is approved). Returns why it was not done."""
        if not self.saving:
            return ("Saving presets is not approved "
                    "(`python probe.py approve save_preset query_preset query_patch`).")
        if self.patch is None or self.chain_ids is None:
            self._look_for_synth(now)
            return "The pedal has not reported this preset yet."
        if not zs.is_preset(bank, program):
            return f"The pedal has no preset {display((bank, program))}."
        if self.effect_change is not None or self.preset_save is not None or self.name_wanted is not None:
            return "Something is being changed on the pedal: try again in a moment."
        self.preset_save = {"place": (bank, program), "from": self.patch, "phase": "reading",
                            "check_at": now + EFFECT_CHECK_TIMEOUT}
        self.preset_result = None
        self._look_for_synth(now, force=True)        # the patch as it is now, knobs turned on the pedal too
        return None

    def _send_save(self, now: float) -> None:
        save = self.preset_save
        if self.patch != save["from"]:             # the patch of another preset: not into that place
            self._save_done(False, "Saving stopped: preset changed")
            return
        try:
            message = zs.build_save_preset(self.device_id, *save["place"], self.patch_data)
        except ValueError as problem:
            self._save_done(False, f"Not saved: the pedal's patch is not one to store ({problem})")
            return
        save["before"] = self.preset_data.get(save["place"])       # to go back to
        save["before_name"] = self.preset_names.get(save["place"])
        self.sender.upload, self.sender.upload_kind = message, "save_preset"
        self.sender.send(message)
        self.last_send = time.monotonic()
        save.update(phase="sent", patch=self.patch_data, check_at=now + PRESET_CHECK_SECONDS)

    def _check_preset_save(self, now: float) -> None:
        save = self.preset_save
        if save is None or now < save["check_at"]:
            return
        if save["phase"] == "reading":
            self._save_done(False, "The pedal did not send its patch: nothing was saved")
        elif save["phase"] == "sent":
            if self.preset_awaiting is not None:   # a name being read first: its answer is on its way
                return
            self.sender.send(zs.build_query_preset(self.device_id, *save["place"]))
            save.update(phase="checking", check_at=now + EFFECT_CHECK_TIMEOUT)
        else:
            self._save_done(False, f"The pedal did not answer: whether preset {display(save['place'])} holds it "
                                   "is not known")

    def _on_stored(self, stored: tuple, now: float) -> None:
        """A preset as stored in its place: the answer to reading it, or what the pedal reports
        when one is saved on it (or by the bridge)."""
        bank, program, dump, data = stored
        place = (bank, program)
        self.preset_names[place] = dump.name
        self.preset_data[place] = data
        undo = self.preset_undo
        if (undo is not None and undo["place"] == place and self.preset_save is None
                and _within_length(data) != _within_length(undo["written"])):
            self.preset_undo = None                  # saved over on the pedal since: nothing to go back to
        if self.preset_awaiting is not None and self.preset_awaiting[0] == place:
            self.preset_awaiting, self.preset_misses, self.preset_read_at = None, 0, now + PRESET_READ_GAP
        save = self.preset_save
        if save is not None and save["phase"] in ("sent", "checking") and save["place"] == place:
            same = _within_length(data) == _within_length(save["patch"])
            zs.log.info("--  preset %s holds the patch %s", display(place), "as sent" if same else "otherwise than sent")
            self._preset_saved(same, now)

    def _preset_saved(self, same: bool, now: float) -> None:
        save = self.preset_save
        place, source = save["place"], save["from"]
        if not same:
            self._save_done(False, f"Preset {display(place)} holds another patch than was sent – look at it on "
                                   "the pedal")
            return
        if save.get("undo"):
            undone, self.preset_undo = save["undo"], None
            try:
                self.bridge.restore_kept(place, undone["kept"])
            except UserError as error:
                zs.log.info("--  what the bridge kept for preset %s could not be restored: %s", display(place), error)
            self._save_done(True, f"Preset {display(place)} is as it was before")
        else:
            kept = self.bridge.kept_for(place)
            if place == source:
                text = f"Preset {display(place)} saved"
            else:
                text = f"Saved as preset {display(place)}"
                try:
                    self.bridge.copy_preset(source, place)
                except UserError as error:
                    text += f" (what the bridge keeps for preset {display(source)} did not go along: {error})"
                text += " – the pedal shows it now" if self.selecting else " – choose it on the pedal"
            self._save_done(True, text)
            # what this save replaced; not known (its place not read yet): nothing to undo, not an
            # earlier save, which would write another place back
            self.preset_undo = None if save.get("before") is None else {
                "place": place, "before": save["before"], "written": save["patch"], "kept": kept,
                "name": save.get("before_name")}
        if place != source or save.get("undo"):
            if self.selecting:
                self.sender.select_preset(*place)
                self.last_send = time.monotonic()
                self._left_preset(now)
                self.loading_stored = True

    def read_pedal(self, now: float) -> Optional[str]:
        """Reads the list of the pedal's files, as the installer does (PC mode, the list of
        everything, the end of the file session, PC mode off), and lets exactly the effects of
        those files be chosen from then on. Effect files the table does not describe are read
        meanwhile, as far as their knobs (read_effect_file, if approved). Returns why it was not
        done. The pedal is out of its normal mode for the second or two it takes (about a second
        more per effect file read); PC mode is always left again."""
        if not self.reading_pedal:
            return "Reading the pedal is not approved (`python probe.py approve read_pedal`)."
        if (self.effect_change is not None or self.preset_save is not None or self.name_wanted is not None
                or self.learning is not None):
            return "Something is being changed on the pedal: try again in a moment."
        self.listing, names, ok, problem, read = [], None, False, None, []
        self.sender.listing = True
        self.read_result = None
        session = False                              # a file session the pedal may still be in
        try:
            self.sender.send(zs.build_edit_disable(self.device_id))
            self._wait(lambda raw: zs.is_ack(zs.zoom_body(raw, self.device_id)), 0.5)
            self.edit_enabled = False
            self.sender.send(bytes([zs.SOX, zs.ZOOM, 0, self.device_id, 0x52, zs.EOX]))
            if not self._wait(lambda raw: True, 1.0):
                raise Disconnected("the pedal did not take PC mode")
            session = True
            for number in range(5000):
                first = number == 0
                self.sender.send(bytes([zs.SOX, zs.ZOOM, 0, self.device_id, 0x60, 0x25 if first else 0x26,
                                        0, 0, 0x2A, 0, zs.EOX]))
                answer = self._wait(lambda raw: raw if (zs.zoom_body(raw, self.device_id) or b"")[:1] == b"\x60"
                                    else None, 1.0)
                if answer is None:
                    raise Disconnected("the pedal stopped answering while its files were listed")
                body = zs.zoom_body(answer, self.device_id)
                if body[1:2] != b"\x04" or zs.parse_file_entry(body) is None:
                    ok = True                                  # an answer that names no file: the end (as the installer)
                    break
            session = False
            self._end_file_session()
            entries = list(self.listing)
            names = [name for name, _ in entries]
            if ok and len(entries) >= 10:            # a complete list: what was read of files gone or changed goes
                effect_names.forget_unlisted([entry for entry in entries if entry[0].upper().endswith(".ZD2")])
                chain_entry.cache_clear()            # … and the names, knobs and texts made of it
                effect_choices.cache_clear()
                if self.reading_files:               # the effect files the table does not describe
                    read = self._read_effect_files(entries)
        except Disconnected as error:
            problem = str(error)
        finally:
            try:
                if session:                          # as the installer leaves one after a failure
                    self._end_file_session()
                self.sender.send(bytes([zs.SOX, zs.ZOOM, 0, self.device_id, 0x53, zs.EOX]))
                self.sender.listing = False
                self.listing = None
                self._wait(lambda raw: True, 0.5)
                self.sender.send(zs.build_edit_enable(self.device_id))
                self.edit_enabled = True
                self._wait(lambda raw: zs.is_ack(zs.zoom_body(raw, self.device_id)), 0.5)
            except MIDI_ERRORS:
                pass
            finally:
                self.sender.listing = False
                self.listing = None
        if read:                                     # what was read is kept, also when the rest went wrong
            read = self._take_read(read)
        if problem:
            self.read_result = {"ok": False, "text": f"{problem}: the effects to choose are as before"}
            say(self.read_result["text"])
            self._look_for_synth(now, force=True)
            return None
        effects = {effect_names.by_file(name) for name in names or () if name.upper().endswith(".ZD2")}
        effects.discard(None)
        if not ok or len(names) < 10 or not effects:
            text = f"The pedal's list of files was not complete ({len(names or [])} files): the effects to choose are as before"
            self.read_result = {"ok": False, "text": text}
            say(text)
            self._look_for_synth(now, force=True)
            return None
        # … and those in the pedal itself (BPM, under the whole id the pedal reported it with before)
        keep = {effect for effect in (self.chain_ids or []) if effect} | {
            effect for effect in self.bridge.measurements.effects if effect & 0xFFFFFF in effect_names.BUILT_IN}
        self.bridge.set_pedal_effects(effects | keep)
        unknown = sorted(name for name in names if name.upper().endswith(".ZD2") and effect_names.by_file(name) is None)
        text = (f"The pedal has {len(effects)} effects that are described here; they are the ones to choose from"
                + (f" ({len(read)} of them read from the pedal now: {', '.join(effect['name'] for effect in read)})"
                   if read else "")
                + (f" ({len(unknown)} more files are not in the table: {', '.join(unknown[:6])}"
                   f"{' …' if len(unknown) > 6 else ''}"
                   f"{'' if self.reading_files else ' – `python probe.py approve read_effect_file` reads them'})"
                   if unknown else ""))
        self.read_result = {"ok": True, "text": text}
        say(text)
        self._look_for_synth(now, force=True)
        return None

    def _end_file_session(self) -> None:
        """As the installer ends one: the file closed, the session ended (answered after about 0.6 s)."""
        for body, answer, seconds in ((b"\x60\x21\x40\x00\x00\x00\x00", zs.FILE_RESULT, 1.0),
                                      (b"\x60\x09", zs.FILE_SESSION_ENDED, 2.0)):
            self.sender.send(bytes([zs.SOX, zs.ZOOM, 0, self.device_id]) + body + bytes([zs.EOX]))
            self._file_answer((answer,), seconds)

    def _file_answer(self, starts: tuple, seconds: float = 1.0) -> Optional[bytes]:
        """The body of the pedal's next answer that starts as one of these (zs.FILE_…); others are
        passed over, so a late answer to an earlier message cannot be taken for this one."""
        return self._wait(lambda raw: next((body for body in [zs.zoom_body(raw, self.device_id) or b""]
                                            if body.startswith(starts)), None), seconds)

    def _read_effect_files(self, entries: list) -> list:
        """The effect files of the list that are not described (effect_names.described), read from
        the pedal: at most FILES_READ_AT_ONCE, none begun after FILES_READ_SECONDS, those not tried
        before first. A file the pedal stops answering for ends the reading for now. Returns what
        was read, with {file, size, id: None, unread: True} for each that could not be read."""
        unread = [(name, size) for name, size in entries
                  if zs.EFFECT_FILE.fullmatch(name.upper()) and not effect_names.described(name, size)]
        unread.sort(key=lambda entry: entry in self.unreadable or effect_names.tried(*entry))   # stable
        batch = unread[:FILES_READ_AT_ONCE]
        if not batch:
            return []
        say(f"Reading {len(batch)} effect file{'s' if len(batch) > 1 else ''} the table does not describe from "
            f"the pedal (about {len(batch)} s, the pedal is in PC mode meanwhile): {', '.join(name for name, _ in batch)}")
        self.reading_files_now = True
        read, started = [], time.monotonic()
        try:
            for name, size in batch:
                if time.monotonic() - started > FILES_READ_SECONDS:
                    break
                self.bridge._publish()               # the interface sees what is going on meanwhile
                if size == 0:                        # an empty file (an install broken off): no effect in it
                    read.append({"file": name.upper(), "size": 0, "id": None})
                    continue
                effect, lost = None, False
                try:
                    effect = self._read_effect_file(name, size)
                except Disconnected as error:
                    say(f"WARNING: {name} was not read: {error}")
                    lost = True
                if effect is None:
                    self.unreadable.add((name, size))
                    read.append({"file": name.upper(), "size": size, "id": None, "unread": True})
                else:
                    read.append(effect)
                if lost:
                    break                            # the pedal is out of step: no further file now
        finally:
            self.reading_files_now = False
        done = sum(1 for effect in read if not effect.get("unread"))
        if len(unread) > done:
            say(f"{len(unread) - done} effect file{'s' if len(unread) - done > 1 else ''} not read yet: "
                "the next Read Pedal tries again")
        return read

    def _take_read(self, read: list) -> list:
        """What was read from the pedal's effect files into the table, and what follows from it.
        Returns the effects that are in the table now."""
        effect_names.remember(read)
        effect_names.table()                         # … which says which of them make way for another
        chain_entry.cache_clear()
        effect_choices.cache_clear()
        files = {effect["file"] for effect in read}
        for conflict in effect_names.CONFLICTS:
            if any(f"({file})" in conflict for file in files):
                say(f"WARNING: {conflict}")
        effects = [effect for effect in read
                   if effect["id"] is not None and effect_names.by_file(effect["file"]) == effect["id"]]
        if effects:
            say("Read from the pedal: " + ", ".join(f"{effect['name']} ({effect['file']})" for effect in effects))
        for effect in read:
            if effect["id"] is None and not effect.get("unread"):
                say(f"{effect['file']} holds no effect")
        self._play_builds({synth.name for synth in (zs.key_effect(effect["id"]) for effect in effects) if synth})
        for effect in effects:
            if plays_other_build(effect["id"]):
                say(f"{effect['name']} on the pedal is another build than the bridge plays: " + _other_build(effect["id"]))
        if self.can_query_patch:                     # the patch read next checks every effect again
            self.unmatched.clear()
            self.recheck = True
        return effects

    def _play_builds(self, names: set) -> None:
        """The synth effects of those names as the builds read from the pedal just now: played from
        now on. (A file not read again – forgotten for another size, or not read in this pass – leaves
        its effect as it is played until it is read.) The effect in the chain is found again with the
        patch read next (_find_synth), its voices as the build has them."""
        if not names:
            return
        changed = self.bridge.synths_from_builds(names)
        if changed:
            self.effects = synth_effects(self.synth)
            if self.synth is not None:
                for name in changed:
                    say(f"{name}: the keyboard plays it as the build on the pedal from now on")

    def _read_effect_file(self, name: str, size: Optional[int]) -> Optional[dict]:
        """The beginning of an effect file on the pedal, as far as its table of knobs goes, as the
        installer reads a file for its backup (and as far as the DSP it takes, which comes before)
        → {id, name, knobs, file, size, dsp and labels if any}, {file, size, id: None} if it holds
        no effect, None if it could not be read (a part not as sent, or not opened). A lost answer
        ends the reading: raises Disconnected. The file is always closed again."""
        self.sender.file_name = name.upper()
        data, done, effect = bytearray(), False, None
        try:
            for attempt in range(2):                 # opened twice, as zoomzt2 and the installer do
                self.sender.send(zs.build_file_open(self.device_id, name.upper()))
                answer = self._file_answer((zs.FILE_OPENED, zs.FILE_RESULT))
                if answer is None:
                    raise Disconnected(f"the pedal did not answer opening {name}")
                if attempt == 0 and not answer.startswith(zs.FILE_OPENED):
                    zs.log.info("--  %s: the pedal did not open it", name)
                    return None
            while not done and (size is None or len(data) < size) and len(data) <= FILE_READ_LIMIT:
                for message, answers in ((zs.build_file_status(self.device_id), (zs.FILE_RESULT,)),
                                         (zs.build_file_read_next(self.device_id), (zs.FILE_REQUESTED, zs.FILE_RESULT)),
                                         (zs.build_file_status(self.device_id), (zs.FILE_BLOCK, zs.FILE_RESULT))):
                    self.sender.send(message)
                    body = self._file_answer(answers)
                    if body is None:
                        raise Disconnected(f"the pedal stopped answering while {name} was read")
                if body.startswith(zs.FILE_RESULT):      # no bytes: the end of the file
                    break
                block = zs.parse_file_block(body)
                if block is None:
                    zs.log.info("--  %s: a part of it was not as sent (CRC) – not read", name)
                    return None
                if not block:
                    break
                data += block
                done, effect = effect_names.read_zd2_part(bytes(data))
        finally:
            self.sender.file_name = None
            self._end_file_session()
        if not done and data and (size is None or len(data) >= size):   # read to its end: its knobs went on to it
            effect = effect_names.read_zd2(bytes(data))
        elif not done:                               # it ended before its size: not read
            zs.log.info("--  %s ended after %d of its %s bytes – not read", name, len(data), size)
            return None
        if effect is None:
            if not data:
                return None
            zs.log.info("--  %s holds no effect (%d bytes read)", name, len(data))
            return {"file": name.upper(), "size": size, "id": None}
        return dict(effect, file=name.upper(), size=size)

    def _read_pedal_at_start(self, now: float) -> None:
        """Once the stored presets are read: the list of the pedal's files, so that the effects to
        choose are those it has. Only when the preset on the pedal is as it is stored (reading
        loads it again as stored): otherwise not, and that is said."""
        if not self.auto_read or self.preset_queue or self.preset_awaiting is not None:
            return
        if (self.patch is None or self.patch_data is None or self.effect_change is not None
                or self.preset_save is not None or self.name_wanted is not None or self.learning is not None):
            return
        # compared with the patch as it is now: a knob turned since the last one came (on the pedal,
        # in the editor, by an assignment) is only in a fresh one (one taken in the pass that asked
        # for it came before the question)
        if self.auto_read_asked is None or self.patch_at <= self.auto_read_asked:
            if self.auto_read_asked is None:
                self.auto_read_asked = now
                self._look_for_synth(now, force=True)
                return
            if now - self.auto_read_asked < EFFECT_CHECK_TIMEOUT:
                return
            self.auto_read, self.bridge.read_at_start = False, True
            say("The pedal's effects were not read at the start: it did not send its patch to compare "
                "(Read Pedal reads them when you like)")
            return
        self.auto_read, self.bridge.read_at_start = False, True
        stored = self.preset_data.get(self.patch)
        if stored is None:
            return                                    # its place was not read: nothing is risked
        if _without_cursor(stored) != _without_cursor(self.patch_data):
            say(f"The pedal's effects were not read at the start: preset {display(self.patch)} has changes that are "
                "not saved, which reading would undo (Read Pedal reads them when you like)")
            return
        self.read_pedal(now)

    def undo_save(self, now: float) -> Optional[str]:
        """Writes back what the last save replaced (the place as the bridge read it before), and
        what the bridge kept for the place. Returns why it was not done."""
        undo = self.preset_undo
        if not self.saving:
            return "Saving presets is not approved (`python probe.py approve save_preset query_preset query_patch`)."
        if undo is None:
            return "There is no save to undo."
        if self.effect_change is not None or self.preset_save is not None or self.name_wanted is not None:
            return "Something is being changed on the pedal: try again in a moment."
        try:
            message = zs.build_save_preset(self.device_id, *undo["place"], undo["before"])
        except ValueError as problem:
            return f"The preset as it was cannot be written back ({problem})."
        self.preset_save = {"place": undo["place"], "from": undo["place"], "phase": "sent", "undo": undo,
                            "patch": undo["before"], "check_at": now + PRESET_CHECK_SECONDS}
        self.preset_result = None
        self.sender.upload, self.sender.upload_kind = message, "save_preset"
        self.sender.send(message)
        self.last_send = time.monotonic()
        return None

    def _save_done(self, ok: bool, text: str) -> None:
        say(text)
        self.preset_result = {"ok": ok, "text": text}
        self.preset_save = None
        self.sender.upload = None

    def _read_presets(self, now: float) -> None:
        """The names of the stored presets, one place at a time between everything else: for
        choosing one. Not while something is being changed on the pedal."""
        if self.preset_awaiting is not None:
            if now - self.preset_awaiting[1] < PRESET_READ_TIMEOUT:
                return
            self.preset_awaiting = None
            self.preset_misses += 1
            if self.preset_misses >= PRESET_READ_MISSES:
                self.preset_queue = []
                say("WARNING: the pedal does not answer for its stored presets – they are listed without names")
                return
        if (not self.preset_queue or now < self.preset_read_at or self.rearranging
                or self.preset_save is not None or self.name_wanted is not None):
            return
        place = self.preset_queue.pop(0)
        self.sender.send(zs.build_query_preset(self.device_id, *place))
        self.preset_awaiting = (place, now)

    # --- Keyboard → the synth effect's knobs ---

    def _on_keyboard(self, message, now: float, external: bool = False) -> None:
        """A message from the keyboard, or from the external sequencer: both play the same keys."""
        synth = self.synth
        if message.type in CLOCK_TYPES:
            # One clock only: the external sequencer's if one is set up, otherwise the keyboard's.
            if external == bool(synth["external"]):
                self._on_clock(message.type, now)
            return
        if message.type in IGNORED_TYPES or not hasattr(message, "channel"):
            return
        channel = synth["external_channel"] if external else synth["channel"]
        if channel and message.channel + 1 != channel:
            return
        if message.type in ("note_on", "note_off"):
            if not synth["low_note"] <= message.note <= synth["high_note"]:
                return
            if message.type == "note_on" and message.velocity > 0:
                alone = not self.keys.held
                self.keys.note_on(message.note, message.velocity)
                self.arp.pressed(message.note, alone)
                self.seq.pressed(message.note)
                self.bridge.record_step(message.note)       # while a pattern is recorded: its next step
                if self._stepping() and self.next_step is None and self._arp_clock() == "internal":
                    self._arp_step(now, now)        # it starts with the key, not on the next beat
                    return
            else:                                   # note on with velocity 0 is a note off
                self.keys.note_off(message.note)
        elif message.type == "pitchwheel":
            # Only remembered here: _bend_step walks the Key knob there, a few clicks at a time.
            self.bend = message.pitch / 8192 * synth["bend_range"]
            return
        elif message.type == "control_change" and message.control == 64:
            self.keys.pedal(message.value >= 64)
        elif message.type == "control_change" and message.control in (120, 123):
            self.keys.clear()                       # all sound off, all notes off
            self.arp.clear()
            self.seq.clear()
            self._arp_silence()
        elif message.type == "control_change":
            self._on_control(message.control, message.value, now)
            return
        else:
            return
        self._play(now)

    def _on_control(self, number: int, value: int, now: float) -> None:
        """A controller of the keyboard other than sustain: while the interface is learning it is
        assigned to a knob, otherwise it sets the knob it is assigned to. One that switches an
        effect of the chain does that."""
        if self.bridge.control_learning is not None:    # whatever the controller did before
            self.bridge.learned_control(number)
            return
        if self.bridge.param_learning is not None:
            self.bridge.learned_param(number)
            return
        if self._on_switch(number, value, now):
            return
        if number == self.bridge.controls.get(arpeggiator.SWITCH):
            self.bridge.switch_arp(value >= 64)     # like the sustain pedal: on from 64
            return
        if number == self.bridge.controls.get(sequencer.SWITCH):
            self.bridge.switch_seq(value >= 64)
            return
        target, name = self.controls.get(number, (None, None))
        if target is not None and not (target.stopped or target.blocked):
            target.pending = (zs.control_value(name, value, self.synth_effect), now)   # an older value is dropped
        self._set_by_controller(number, value, now)

    def _set_by_controller(self, number: int, value: int, now: float) -> None:
        """A knob, slider or pedal assigned to parameters of the preset sets them, from its whole
        travel (0–127) to their range, as the expression pedal does."""
        if self.learning is not None:                # while knobs are turned on the pedal, nothing is sent
            return
        for target in self.cc_targets.get(number, ()):
            if not (target.stopped or target.blocked):
                target.pending = (target.mapping.target(value / 127), now)

    # --- The effects of the chain, on and off ---

    def _on_switch(self, number: int, value: int, now: float) -> bool:
        """A controller from any of the controllers. True if it was taken for the switch of an
        effect: learned just now, or assigned before."""
        bridge = self.bridge
        if bridge.effect_learning is not None or bridge.scene_learning is not None:
            (bridge.learned_switch if bridge.effect_learning is not None else bridge.learned_scene)(number)
            self.switch_quiet = (number, now + SWITCH_QUIET_SECONDS)    # letting go must not switch it at once
            return True
        slot = next((slot for slot, switch in bridge.switches.items() if switch["cc"] == number), None)
        scene = next((index for index, scene in enumerate(self._scenes()) if scene["cc"] == number), None)
        if slot is None and scene is None:
            return False
        if scene is not None:                       # a press recalls it; letting go does nothing
            if value >= 64 and not (self.switch_quiet[0] == number and now < self.switch_quiet[1]):
                self.recall_scene(scene, now)
            return True
        if self.switch_quiet[0] == number and now < self.switch_quiet[1]:
            return True
        if not bridge.switches[slot]["press"]:
            self.switch_effect(slot, value >= 64, now)       # like the sustain pedal: on from 64
        elif value >= 64:
            self.switch_effect(slot, None, now)              # every press switches it over
        return True

    def _scenes(self) -> list:
        """The scenes of the preset on the pedal."""
        return self.bridge.scenes.get(self.patch, []) if self.patch else []

    def recall_scene(self, index: int, now: float) -> Optional[str]:
        """Sets the parameters of a scene and switches its effects. The values take their turn
        like those of the expression pedal, one per minimum interval. Returns why it was not
        done, None if it was."""
        scenes = self._scenes()
        if index >= len(scenes):
            return "This preset has no such scene."
        if self.learning is not None:
            return "Not while a parameter or a scene is being learned."
        scene = scenes[index]
        if scene.get("chain") and (self.rearranging or (
                self.chain_ids is None and self.watching and not self.unplaced_given_up)):
            # its effects may sit elsewhere in a moment: recalled once the pedal has said where
            self.recall_waiting = index
            return None
        for place, value in scene["values"].items():
            target = self.scene_targets.get(place)
            if target is not None and not (target.stopped or target.blocked):
                target.pending = (value, now)
        for slot, on in scene["effects"].items():
            if place_of(slot, scene.get("chain", {}).get(slot), self.chain_ids) == slot:
                self.switch_effect(slot, on, now)
        self.scene_active = index
        say(f"Scene “{scene['name']}”")
        return None

    def switch_effect(self, slot: int, on: Optional[bool], now: float) -> Optional[str]:
        """Switches an effect of the chain on or off; on None: over to the other. Only an effect
        the pedal reported for this preset is switched. Returns why it was not done, None if it
        was (or is as wanted already)."""
        if not self.switching:
            return "Switching effects is not approved (`python probe.py approve switch_effect query_patch`)."
        if self.rearranging:                        # the place may hold another effect in a moment
            return "An effect is being changed or moved: try again in a moment."
        if self.chain is None:
            self._look_for_synth(now)
            return "The pedal has not reported the effects of this preset yet."
        if slot >= len(self.chain):
            text = f"This preset has no effect {slot + 1}"
            if text not in self.told:               # once per preset, not with every press
                self.told.add(text)
                say(f"{text} – its switch is ignored")
            return text + "."
        state = self.chain[slot]
        if on is None:
            on = state is False                     # unknown counts as on, as effects mostly are
        if on != state:
            self._send_switch(slot, on, now)
        return None

    def _send_switch(self, slot: int, on: bool, now: float, attempt: int = 0) -> None:
        self.sender.send(zs.build_switch_effect(self.device_id, slot, on))
        self.chain[slot] = on
        if self.learning is not None:
            self.capture_effects[slot] = on         # part of the scene that is being captured
        self.last_send = time.monotonic()           # every other value keeps its distance
        self.switch_awaiting[slot] = (on, now, attempt)

    def rename(self, name: str, now: float) -> Optional[str]:
        """Gives the preset on the pedal another name, the way the pedal reports a name edited on
        it: one message per character, the 28 places filled up with spaces, NAME_SEND_SECONDS
        apart. Afterwards the patch is read to see whether the pedal took it. The pedal keeps it
        once the preset is saved on it (or with Auto Save). Returns why it was not done."""
        if not self.renaming:
            return "Renaming presets is not approved (`python probe.py approve rename_patch query_patch`)."
        if self.patch is None:
            return "The pedal has not reported its preset yet."
        if self.name_wanted is not None:
            return "The preset is being renamed already."
        if self.preset_save is not None or self.effect_change is not None:
            return "Something is being changed on the pedal: try again in a moment."
        self.name_queue = list(enumerate(name.ljust(zs.NAME_LENGTH)))
        self.name_wanted, self.name_acks, self.name_result = name, 0, None
        self._send_name(now)
        return None

    def _send_name(self, now: float) -> None:
        if self.name_queue and now - self.name_sent >= NAME_SEND_SECONDS:
            place, character = self.name_queue.pop(0)
            self.sender.send(zs.build_name_character(self.device_id, place, character))
            self.name_sent = self.last_send = time.monotonic()   # other values keep their distance
            if not self.name_queue:
                self.name_check_at = now + NAME_CHECK_SECONDS
        if self.name_check_at is not None and now >= self.name_check_at:
            if not self.name_checking:
                self.name_checking, self.name_check_at = True, now + NAME_CHECK_TIMEOUT
                self._look_for_synth(now, force=True)
            else:
                self._name_done(False, f"The pedal did not send its patch: whether it took the name "
                                       f"“{self.name_wanted}” is not known")

    def _name_checked(self) -> None:
        confirmed = f"{self.name_acks} of {zs.NAME_LENGTH} characters confirmed"
        if self.patch_name == self.name_wanted:
            self._name_done(True, f"Preset {display(self.patch)} is called “{self.name_wanted}” now ({confirmed}) – "
                                  "save the preset on the pedal to keep the name")
        else:
            self._name_done(False, f"The pedal did not take the name “{self.name_wanted}”: it reports "
                                   f"“{self.patch_name or ''}” ({confirmed})")

    def _name_done(self, ok: bool, text: str) -> None:
        say(text)
        self.name_result = {"ok": ok, "text": text}
        self.name_queue, self.name_wanted, self.name_check_at, self.name_checking = [], None, None, False

    def _on_name_character(self, change: zs.ParamMessage, now: float) -> None:
        """The pedal confirmed a character of a name sent to it, or reports one edited on it. A
        name it gives a preset by itself does not come complete: an empty preset that gets an
        effect is "USER-012" then, of which it reported U, S, E, R and 1 and spaces in places that
        had them already (measured 2026-10-21) – so the patch is read afterwards, and its name is
        the one shown."""
        if change.ack:
            self.name_acks += 1
            return
        if self.patch_name is not None and change.param < zs.NAME_LENGTH and 0x20 <= change.value < 0x7F:
            name = list(self.patch_name.ljust(zs.NAME_LENGTH))
            name[change.param] = chr(change.value)
            self.patch_name = "".join(name).rstrip() or None
        self._look_for_synth(now)                       # at most once a second, the last one after the last character

    def edit_knob(self, slot: int, param: int, value: int, effect_id: int) -> Optional[str]:
        """The editor: a knob of an effect in the chain to a value, within the range its effect file
        gives (effect_names.py); sent with the next free turn. effect_id: the effect the slider was
        made for; if another one is in that place now, nothing is sent. The Key knobs of a synth
        effect are not set here (they are notes). Returns why it is not done."""
        if self.patch is None or self.chain_ids is None or slot >= len(self.chain_ids) or (
                slot >= len(self.knob_values)):
            return "The pedal has not reported the effects of this preset yet."
        if self.effect_change is not None:
            return "An effect is being changed or moved: try again in a moment."
        if self.chain_ids[slot] != effect_id:
            return f"Effect {slot + 1} is {effect_label(self.chain_ids[slot])} now: nothing was sent."
        if effect_id in self.unmatched:
            return f"{effect_label(effect_id)} on the pedal is another build than the table describes."
        knob = next((knob for knob in effect_names.knobs(effect_id) if knob["param"] == param), None)
        if knob is None:
            return f"Effect {slot + 1} has no knob {param - 1} that is known."
        if not 0 <= value <= knob["max"]:
            return f"{knob['name']} goes from 0 to {knob['max']}."
        if param in self.key_params(slot):
            return f"{knob['name']} is a note: the keyboard plays it."
        self.edits[(slot, param)] = (value, effect_id)
        return None

    def key_params(self, slot: int) -> range:
        """The Key knobs of the effect in that place if it is a synth effect, played or not: only
        notes write them."""
        effect_id = self.chain_ids[slot] if self.chain_ids and slot < len(self.chain_ids) else 0
        synth = self.effects.get(effect_id) or zs.key_effect(effect_id) if effect_id else None
        return range(synth.key_param, synth.key_param + synth.voices) if synth else range(0)

    def change_effect(self, slot: int, effect_id: int, now: float) -> Optional[str]:
        """Puts another effect into a place of the chain: one the pedal reported in a preset, so it
        has it, into a place it reported an effect in, and one that fits into the DSP beside the
        others (over_budget: the pedal does not check that over MIDI). The pedal starts its knobs
        at their defaults. Afterwards the patch is read to see whether the pedal took it; it keeps
        it once the preset is saved on it. Returns why it was not done, None if it was (or is
        there already)."""
        if not self.changing:
            return "Changing effects is not approved (`python probe.py approve change_effect query_patch`)."
        if self.chain_ids is None:
            self._look_for_synth(now)
            return "The pedal has not reported the effects of this preset yet."
        if slot >= len(self.chain_ids):             # an effect, or an empty place
            return f"This preset has no effect {slot + 1}."
        if effect_id not in self.bridge.measurements.effects:
            return "Only an effect the pedal has (in its list of files, or reported in a preset) can be chosen."
        if self.effect_change is not None:
            return "An effect is being changed already."
        if self.chain_ids[slot] == effect_id:
            return None
        refused = over_budget(self.chain_ids, slot, effect_id)
        if refused:
            return refused
        self.sender.send(zs.build_change_effect(self.device_id, slot, effect_id))
        self.last_send = time.monotonic()           # every other value keeps its distance
        self.effect_change = {"kind": "change", "phase": "sent", "slot": slot, "id": effect_id,
                              "check_at": now + EFFECT_CHECK_SECONDS, "checking": False}
        self.effect_result = None
        return None

    def move_effect(self, slot: int, to: int, now: float) -> Optional[str]:
        """Moves an effect to another place in the chain; those in between move up by one. The
        pedal has no message for it: its patch is read afresh, sent back with the effect swapped
        with its neighbour place by place (zs.swap_effects, nothing else changed), and read again
        to see whether it took it; it keeps it once the preset is saved on it. Returns why it was
        not done."""
        if not self.moving:
            return "Moving effects is not approved (`python probe.py approve move_effect query_patch`)."
        if self.chain_ids is None:
            self._look_for_synth(now)
            return "The pedal has not reported the effects of this preset yet."
        if to == slot or not (0 <= slot < len(self.chain_ids) and 0 <= to < len(self.chain_ids)):
            return f"Effect {slot + 1} cannot move to place {to + 1}."
        if self.effect_change is not None:
            return "An effect is being changed already."
        self.effect_change = {"kind": "move", "phase": "reading", "slot": slot, "to": to,
                              "id": self.chain_ids[slot], "check_at": now + EFFECT_CHECK_TIMEOUT, "checking": True}
        self.effect_result = None
        self._look_for_synth(now, force=True)        # the patch as it is now, knobs turned on the pedal too
        return None

    def add_effect(self, slot: Optional[int], now: float) -> Optional[str]:
        """Inserts an empty place into the chain, before the effect in slot (None: at the end), to
        put an effect into with change_effect. As with moving: the patch is read afresh, sent back
        with the place inserted (zs.insert_empty, nothing else changed), and read again. Returns
        why it was not done."""
        if not self.adding:
            return "Adding effects is not approved (`python probe.py approve add_effect query_patch`)."
        if self.chain_ids is None:
            self._look_for_synth(now)
            return "The pedal has not reported the effects of this preset yet."
        slot = len(self.chain_ids) if slot is None else slot
        if len(self.chain_ids) >= zs.MAX_SLOTS:
            return f"The preset has {zs.MAX_SLOTS} effects already."
        if not 0 <= slot <= len(self.chain_ids):
            return f"There is no place {slot + 1} to add one at."
        if self.effect_change is not None:
            return "An effect is being changed already."
        self.effect_change = {"kind": "add", "phase": "reading", "slot": slot, "to": slot, "id": 0,
                              "check_at": now + EFFECT_CHECK_TIMEOUT, "checking": True}
        self.effect_result = None
        self._look_for_synth(now, force=True)        # the patch as it is now, knobs turned on the pedal too
        return None

    def delete_effect(self, slot: int, effect_id: int, now: float) -> Optional[str]:
        """Takes the effect in slot (or an empty place) out of the chain; those after it move up by
        one. effect_id: the effect the interface showed there (0: an empty place); if another one
        is there by now, nothing is done. As with moving: the patch is read afresh, sent back with
        the effect taken out (zs.remove_effect, nothing else changed), and read again; the pedal
        keeps it once the preset is saved on it. Returns why it was not done."""
        if not self.deleting:
            return "Deleting effects is not approved (`python probe.py approve delete_effect query_patch`)."
        if self.chain_ids is None:
            self._look_for_synth(now)
            return "The pedal has not reported the effects of this preset yet."
        if not 0 <= slot < len(self.chain_ids):
            return f"This preset has no effect {slot + 1}."
        if self.chain_ids[slot] != effect_id:
            return f"Effect {slot + 1} is {_effect_or_empty(self.chain_ids[slot])} now: nothing was deleted."
        if len(self.chain_ids) == 1:
            return "A preset keeps at least one effect: choose another one for it instead."
        if self.effect_change is not None:
            return "An effect is being changed already."
        self.effect_change = {"kind": "delete", "phase": "reading", "slot": slot, "id": self.chain_ids[slot],
                              "check_at": now + EFFECT_CHECK_TIMEOUT, "checking": True}
        self.effect_result = None
        self._look_for_synth(now, force=True)        # the patch as it is now, knobs turned on the pedal too
        return None

    def _send_delete(self, now: float) -> None:
        change = self.effect_change
        slot = change["slot"]
        if slot >= len(self.chain_ids) or self.chain_ids[slot] != change["id"]:
            self._effect_done(False, "Not deleted: the chain on the pedal has changed meanwhile")
            return
        try:
            patch = zs.remove_effect(self.patch_data, slot)
        except ValueError as problem:
            self._effect_done(False, f"Not deleted: the pedal's patch is not one to send back ({problem})")
            return
        ids = list(self.chain_ids)
        del ids[slot]
        self.sender.upload, self.sender.upload_kind = zs.build_patch_upload(self.device_id, patch), "delete_effect"
        self.sender.send(self.sender.upload)
        self.last_send = time.monotonic()
        change.update(phase="sent", ids=ids, patch=patch, check_at=now + EFFECT_CHECK_SECONDS, checking=False)

    def _send_add(self, now: float) -> None:
        change = self.effect_change
        slot = change["slot"]
        try:
            patch = zs.insert_empty(self.patch_data, slot)
        except ValueError as problem:
            self._effect_done(False, f"Not added: the pedal's patch is not one to send back ({problem})")
            return
        ids = list(self.chain_ids)
        ids.insert(slot, 0)
        self.sender.upload, self.sender.upload_kind = zs.build_patch_upload(self.device_id, patch), "add_effect"
        self.sender.send(self.sender.upload)
        self.last_send = time.monotonic()
        change.update(phase="sent", ids=ids, patch=patch, check_at=now + EFFECT_CHECK_SECONDS, checking=False)

    def _on_file_listed(self, name: str) -> None:
        """A file in the pedal's list of its files went by (the installer asked for it): an effect
        file means the pedal has that effect, so it can be chosen. Nothing is taken off the
        choice: the installer lists while it replaces effects, after deleting and before
        writing them again, so a list does not show what is missing for good."""
        effect_id = effect_names.by_file(name) if name.upper().endswith(".ZD2") else None
        if effect_id is not None and self.bridge.found_effects([effect_id]):
            zs.log.info("--  %s is on the pedal: it can be chosen", effect_label(effect_id))

    def _send_move(self, now: float) -> None:
        change = self.effect_change
        slot, to = change["slot"], change["to"]
        if slot >= len(self.chain_ids) or to >= len(self.chain_ids) or self.chain_ids[slot] != change["id"]:
            self._effect_done(False, "Not moved: the chain on the pedal has changed meanwhile")
            return
        patch = self.patch_data
        try:
            for place in (range(slot, to) if to > slot else range(slot - 1, to - 1, -1)):
                patch = zs.swap_effects(patch, place)
        except ValueError as problem:
            self._effect_done(False, f"Not moved: the pedal's patch is not one to send back ({problem})")
            return
        ids = list(self.chain_ids)
        ids.insert(to, ids.pop(slot))
        self.sender.upload, self.sender.upload_kind = zs.build_patch_upload(self.device_id, patch), "move_effect"
        self.sender.send(self.sender.upload)
        self.last_send = time.monotonic()
        change.update(phase="sent", ids=ids, patch=patch, check_at=now + EFFECT_CHECK_SECONDS, checking=False)

    @property
    def rearranging(self) -> bool:
        """An effect is being changed, moved, added or deleted and the pedal has not reported it
        yet: values for the places of the chain wait (they might reach another effect), notes too."""
        return self.effect_change is not None and self.effect_change["phase"] == "sent"

    def _synth_stays(self) -> bool:
        """While an effect is changed, moved, added or deleted: whether the synth effect is in the
        same place before and after it."""
        change, slot = self.effect_change, self.synth_slot
        if change is None or slot is None or not self.chain_ids or slot >= len(self.chain_ids):
            return False
        if change["kind"] == "change":
            return change["slot"] != slot
        ids = change.get("ids")
        return bool(ids) and slot < len(ids) and ids[slot] == self.chain_ids[slot]

    def _check_edits(self, now: float) -> None:
        """An editor value the pedal has not confirmed goes once more (it confirms only a change, so
        a value it holds already draws no ack: then that is the end of it)."""
        for key, (value, sent_at, attempt, effect_id) in list(self.edit_awaiting.items()):
            if now - sent_at > ACK_TIMEOUT:
                del self.edit_awaiting[key]
                if attempt == 0 and key not in self.edits:
                    self.edits[key] = (value, effect_id)
                    self.edit_awaiting[key] = (value, now, 0, effect_id)

    def _check_effect_change(self, now: float) -> None:
        change = self.effect_change
        if change is None or now < change["check_at"]:
            return
        if not change["checking"]:
            change["checking"], change["check_at"] = True, now + EFFECT_CHECK_TIMEOUT
            self._look_for_synth(now, force=True)
        elif change["phase"] == "reading":
            self._effect_done(False, "The pedal did not send its patch: nothing was "
                                     + {"add": "added", "delete": "deleted"}.get(change["kind"], "moved"))
        else:
            # What is where is not known now: nothing that was meant for a place goes out, no
            # note is written, and the patch is asked for once more.
            if change["kind"] == "delete":
                where = f"whether effect {change['slot'] + 1} ({_effect_or_empty(change['id'])}) is deleted"
            else:
                what = "an empty place" if change["kind"] == "add" else _effect_or_empty(change["id"])
                where = f"whether {what} is effect {change.get('to', change['slot']) + 1} now"
            self._effect_done(False, f"The pedal did not send its patch: {where} is not known", play=False)
            self._look_for_synth(now, force=True)

    def _effect_checked(self, now: float) -> None:
        """A patch came while an effect was being changed, moved, added or deleted: for all but a
        change, the patch to send back; then done if the chain is as it should be; if not, and it
        is the patch read for it, the pedal did not take it."""
        change = self.effect_change
        if change["phase"] == "reading":
            {"add": self._send_add, "delete": self._send_delete}.get(change["kind"], self._send_move)(now)
        elif change["kind"] in ("add", "delete"):
            if self.chain_ids == change["ids"]:
                same = _without_cursor(self.patch_data) == _without_cursor(change["patch"])
                zs.log.info("--  the pedal holds the patch %s", "as sent" if same else "otherwise than sent")
                self._effect_done(True, f"Effect {change['slot'] + 1} is an empty place now – choose an effect "
                                        "for it, and save the preset on the pedal to keep it"
                                  if change["kind"] == "add" else
                                  f"Effect {change['slot'] + 1} ({_effect_or_empty(change['id'])}) is deleted – "
                                  "save the preset on the pedal to keep it")
            elif change["checking"]:
                self._effect_done(False, "The pedal did not take the new place" if change["kind"] == "add" else
                                         f"The pedal did not delete effect {change['slot'] + 1} "
                                         f"({_effect_or_empty(change['id'])})")
        elif change["kind"] == "move":
            name = effect_label(change["id"]) if change["id"] else "An empty place"
            if self.chain_ids == change["ids"]:
                same = _without_cursor(self.patch_data) == _without_cursor(change["patch"])
                zs.log.info("--  the pedal holds the patch %s", "as sent" if same else "otherwise than sent")
                self._effect_done(True, f"{name} is effect {change['to'] + 1} now – save the preset on the pedal "
                                        "to keep it")
            elif change["checking"]:
                # an empty place is not told from another one: where it went is not said
                self._effect_done(False, f"The pedal did not take the move: {name} is effect "
                                         f"{self.chain_ids.index(change['id']) + 1 if change['id'] in self.chain_ids else '–'}"
                                  if change["id"] else
                                  f"The pedal did not move the empty place {change['slot'] + 1}")
        else:
            slot, wanted = change["slot"], change["id"]
            there = self.chain_ids[slot] if slot < len(self.chain_ids) else 0
            if there == wanted:
                self._effect_done(True, f"Effect {slot + 1} is {effect_label(wanted)} now – save the preset on the "
                                        "pedal to keep it")
            elif change["checking"]:
                self._effect_done(False, f"The pedal did not take {effect_label(wanted)}: effect {slot + 1} is "
                                         + (effect_label(there) if there else "empty"))

    def _effect_done(self, ok: bool, text: str, play: bool = True) -> None:
        say(text)
        self.effect_result = {"ok": ok, "text": text}
        self.effect_change = None
        self.sender.upload = None
        self.edits, self.edit_awaiting = {}, {}     # meant for the chain as it was
        if play and self.synth_slot is not None:    # notes held meanwhile, a key released too
            self._play(time.monotonic(), quiet=True)

    def _on_switched(self, change: zs.ParamMessage, now: float) -> None:
        """The pedal confirmed a switch of ours, or reports that an effect was switched on it."""
        on = bool(change.value)
        if not change.ack:
            if self.chain is not None and change.slot < len(self.chain):
                self.chain[change.slot] = on
                if self.learning is not None:
                    self.capture_effects[change.slot] = on
                say(f"Effect {change.slot + 1} {'on' if on else 'off'}, switched on the pedal")
            self.switch_awaiting.pop(change.slot, None)
        elif self.switch_awaiting.get(change.slot, (None,))[0] == on:
            del self.switch_awaiting[change.slot]
            say(f"Effect {change.slot + 1} {'on' if on else 'off'}")

    def _check_switches(self, now: float) -> None:
        """A switch the pedal has not confirmed is sent once more (the pedal ignores what it holds
        already); after that the preset is read again, which says what the effect is now."""
        for slot, (on, sent_at, attempt) in list(self.switch_awaiting.items()):
            if now - sent_at <= ACK_TIMEOUT:
                continue
            del self.switch_awaiting[slot]
            zs.log.info("--  no ack for effect %d %s", slot + 1, "on" if on else "off")
            if self.chain is None or slot >= len(self.chain) or self.rearranging:
                continue        # the chain has changed since (or is changing): the switch is not repeated into it
            if attempt < SWITCH_RETRIES:
                self._send_switch(slot, on, now, attempt + 1)
            else:
                say(f"WARNING: the pedal did not confirm effect {slot + 1} {'on' if on else 'off'} "
                    "– reading the preset again")
                self._reenable_edit(now)
                self._look_for_synth(now)

    def _wanted_key(self, voice: Voice) -> int:
        """The Key value for the voice's note and the pitch wheel; 0 = gate off."""
        return 0 if voice.note is None else zs.key_for(voice.note, self.bend)

    def _assign(self, now: float) -> None:
        """Gives every note that should sound a voice. A note that sounds keeps its voice, so a
        key released or added leaves the others alone. A new note takes the free voice that last
        played it, otherwise the one that has been free longest (its release has faded most).
        With more notes than voices the oldest gives its voice up and gets it back when a newer
        one is released, as a single voice does."""
        wanted = self._sounding()
        for voice in self.voices:
            if voice.note is not None and voice.note not in wanted:
                voice.note, voice.freed = None, now
        sounding = {voice.note for voice in self.voices}
        for note in wanted:
            if note not in sounding:
                voice = min((voice for voice in self.voices if voice.note is None),
                            key=lambda voice: (voice.last_note != note, voice.freed))
                voice.note = voice.last_note = note

    def _play(self, now: float, quiet: bool = False) -> None:
        """Brings the pedal's Key knobs in line with the keys that are down (or with the
        arpeggiator or the sequencer). A note goes out at once: it is not spaced, delayed or
        merged, only an unchanged value is skipped (a new note may have one or two knobs the
        effect reads at that moment right before it, _before_note). quiet: not printed (their notes)."""
        if self.synth_slot is None:
            if self.keys.note is not None:          # the effect may have been added just now
                self._look_for_synth(now, synth_only=True)
            return
        if self.rearranging:                        # sent once the pedal has reported its chain
            return
        self._assign(now)
        for voice in self.voices:
            if voice.note is not None and voice.note == voice.note_sent and voice.key_sent:
                continue                            # the same note still sounds: _bend_step follows the wheel
            if voice.note is None and voice.key_sent is None:
                continue                            # never played and not known: left as it is until it is needed
            key = self._wanted_key(voice)
            if key != voice.key_sent:
                struck = self._strikes(voice, key)
                remarks = self._before_note(voice, key)
                self._send_key(voice, key, now, quiet=quiet, struck=struck)
                for text in remarks:                # once the note is out
                    say(text)
            voice.note_sent = voice.note

    def _before_note(self, voice: Voice, key: int) -> list:
        """Before a Key value that strikes a new note (from 0, or a jump the effect takes for a new
        note and not for a bend), the knobs the effect reads at that moment, through the same path
        with nothing in between: those the keyboard needs (Pluck's Trig on Key) and how hard the
        key was struck (KeyBand's Vel; left alone when that is not known). Not a knob something
        assigned in this preset sets (that wins), nor a value the pedal holds as far as the bridge
        knows (from the patch, the knobs turned on the pedal and what it sent). The pedal confirms
        them like any value of ours (`64 20 01`), which only updates knob_values (_on_zoom); until
        then they go again with the Key if that is repeated (_key_miss). Returns what to say once
        the note is out."""
        effect, slot = self.synth_effect, self.synth_slot
        if not (effect.needs or effect.velocity) or not self._strikes(voice, key):
            return []                               # a gate-off or a bend
        held = self.knob_values[slot] if slot < len(self.knob_values) else {}
        remarks = []
        for knob, value in self._strike_values(voice).items():
            param = zs.synth_knob(knob, effect)[0]
            if held.get(param) == value or not self._strike_knob(param, value):
                continue
            if knob in effect.needs:
                remarks.append(f"{effect.name}: {knob} set to {effect.needs[knob][1]}, so the keyboard plays it – "
                               "save the preset on the pedal to keep it")
        return remarks

    def _strikes(self, voice: Voice, key: int) -> bool:
        """Whether a Key value strikes a new note on the voice: from 0, or a jump the effect takes
        for a new note and not for a bend."""
        return bool(key) and not (voice.key_sent and abs(key - voice.key_sent) <= zs.KEY_BEND_STEP)

    def _strike_values(self, voice: Voice) -> dict:
        """The knobs the synth effect reads when the voice strikes its note, at the values for that
        note (_before_note): knob → value. With several voices each strikes with the velocity of
        its own note; the effect keeps it for that voice."""
        effect = self.synth_effect
        wanted = {knob: value for knob, (value, _) in effect.needs.items()}
        velocity = self._velocity(voice.note)
        if effect.velocity is not None and velocity:
            top = zs.synth_knob(effect.velocity, effect)[1]
            wanted[effect.velocity] = max(1, round(velocity * top / 127))
        return wanted

    def _strike_knob(self, param: int, value: int) -> bool:
        """Sends a knob of the synth effect that it reads when a note is struck (_before_note), and
        takes it for what the pedal holds now. Not when something assigned in this preset sets it
        (that wins), nor when the allowlist does not have it: another build than the table
        describes, where the keyboard plays notes only, also right after Read Pedal until the
        next patch has renewed the allowlist. Returns whether it was sent."""
        slot = self.synth_slot
        if any((target.mapping.slot, target.mapping.param) == (slot, param) and not (target.away or target.blocked)
               for target in self.targets) or not any(
                (item.slot, item.param) == (slot, param) and item.min <= value <= item.max
                for item in self.sender.targets):
            return False
        self.sender.send(zs.build_set_param(self.device_id, slot, param, value))
        if slot < len(self.knob_values):
            self.knob_values[slot][param] = value   # what the pedal holds now, as far as the bridge knows
        awaited = self.strike_awaiting.setdefault((slot, param), [])
        if awaited[-1:] != [value]:
            awaited.append(value)                   # in the order sent, as the pedal confirms them
        return True

    def _velocity(self, note: int) -> Optional[int]:
        """How hard the key a note comes from was struck: the key itself, the key the sequencer
        plays its pattern from, or the key of the chord the arpeggiator took it from (the note
        itself before the one an octave below). None if that is not known."""
        if self.bridge.seq_on:
            key = self.seq.root
        elif self.bridge.arp_on:
            chord = self.arp.chord(self.keys.played())
            key = next((note - 12 * octave for octave in range(self.bridge.arp_settings["octaves"])
                        if note - 12 * octave in chord), None)
        else:
            key = note
        return self.keys.velocities.get(key)

    def _bend_step(self, now: float) -> bool:
        """While notes sound, moves one Key knob towards where the pitch wheel wants it, at most
        KEY_BEND_STEP clicks per message; the voices take turns. The effect takes a larger move
        for a new note and, with KeySynth's Glide on, would slide to it instead of following the
        wheel."""
        count = len(self.voices)
        for offset in range(1, count + 1):
            voice = self.voices[(self.bend_turn + offset) % count]
            if voice.note is None or voice.note != voice.note_sent or not voice.key_sent:
                continue
            wanted = self._wanted_key(voice)
            if wanted == voice.key_sent:
                continue
            step = max(-zs.KEY_BEND_STEP, min(zs.KEY_BEND_STEP, wanted - voice.key_sent))
            self._send_key(voice, voice.key_sent + step, now, quiet=True)
            self.bend_turn = voice.index
            return True
        return False

    def _send_key(self, voice: Voice, key: int, now: float, played: Optional[float] = None,
                  attempt: int = 0, quiet: bool = False, struck: bool = False) -> None:
        self.sender.send(zs.build_set_key(self.device_id, self.synth_slot, key, voice.index, self.synth_effect))
        voice.key_sent = key
        self.last_send = time.monotonic()           # every other value keeps its distance from a note
        voice.awaiting.append(KeySent(key, now if played is None else played, now, attempt, quiet, struck))
        self.key_totals["sent"] += 1

    def _on_key_ack(self, voice: Voice, value: int, now: float) -> None:
        if all(sent.value != value for sent in voice.awaiting):
            return
        while True:                                 # acks arrive in send order
            sent = voice.awaiting.popleft()
            if sent.value == value:
                break
            # Seen on the real pedal: of two messages 1 ms apart (two keys almost together) only
            # the second is confirmed. The first was replaced before it could matter.
            zs.log.info("--  %s %d was overtaken by the next message", self._key_name(voice), sent.value)
            self.key_totals["overtaken"] += 1
        self.key_misses = 0
        self.key_totals["acked"] += 1
        self.key_latencies.append(now - sent.time)
        if sent.quiet or HEADLESS:
            return                                  # a moving wheel or an arpeggio would flood the terminal
        if value:
            note, cents = zs.key_pitch(value)
            what = f"{note_name(note):>4} ({note:3d})" + (f" {cents:+d} c" if cents else "")
        else:
            what = "       off"
        print(f"{time.strftime('%H:%M:%S')}  {what} → {self._key_name(voice)} {value:4d}   "
              f"ack {1000 * (now - sent.time):5.1f} ms", flush=True)

    def _key_name(self, voice: Voice) -> str:
        """"Key" on KeySynth, "Key1" to "Key4" on KeyPoly."""
        return self.synth_effect.knobs[self.synth_effect.key_index + voice.index][0] if self.synth_effect else "Key"

    def _key_miss(self, voice: Voice, sent: KeySent, now: float) -> None:
        """No ack for a note in time. Seen on the real pedal: while the player is in its effect
        menu a message can get lost."""
        zs.log.info("--  no ack for %s %d", self._key_name(voice), sent.value)
        # Not while an effect is changed, moved, added or deleted: the place may hold another one by
        # then; the patch read afterwards says what the Key holds, and _play sends what is missing.
        if (not voice.awaiting and sent.value == voice.key_sent == self._wanted_key(voice)
                and sent.attempt < KEY_RETRIES and not self.rearranging):
            # The last note, the end of a bend or the gate-off must not get lost. Repeating is
            # harmless: the pedal ignores a value it already holds. A note goes again with the
            # knobs it is struck with (KeyBand's Vel, Pluck's Trig) where they are not confirmed
            # either, at its own values – another voice may have struck with another Vel since.
            # A bend step goes again alone: the effect does not read them then.
            if sent.struck:
                slot = self.synth_slot
                held = self.knob_values[slot] if slot < len(self.knob_values) else {}
                for knob, value in self._strike_values(voice).items():
                    param = zs.synth_knob(knob, self.synth_effect)[0]
                    if held.get(param) != value or self.strike_awaiting.get((slot, param)):
                        self._strike_knob(param, value)
            self._send_key(voice, sent.value, now, played=sent.time, attempt=sent.attempt + 1, quiet=sent.quiet,
                           struck=sent.struck)
        if sent.attempt:
            return          # counted once, with the first try
        self.key_totals["missed"] += 1
        self.key_misses += 1
        if self.key_misses == 1:
            self.key_silent_since = sent.send_time
        # Seen on the real pedal: while one of its knobs is turned it confirms nothing for about a
        # tenth of a second and then carries on. At 1/32 and 200 BPM that is six notes; they are
        # no reason to send it more.
        if self.key_misses >= KEY_MISSES_BEFORE_QUERY and now - self.key_silent_since >= KEY_SILENCE_SECONDS:
            self.key_misses = 0
            self._reenable_edit(now)
            # Usually the player is turning a knob or browsing effects on the pedal, which keeps
            # it from answering for a moment. Do not pile more work on it: ask where the effect
            # is only now and then.
            if now - self.last_key_trouble >= KEY_TROUBLE_SECONDS:
                self.last_key_trouble = now
                say("WARNING: the pedal does not confirm notes – switching edit mode on again and "
                    "looking for the synth effect")
                self._look_for_synth(now, synth_only=True)

    def _reenable_edit(self, now: float) -> None:
        """Parameters that stay unconfirmed can mean the pedal has left edit mode. Seen on the
        real pedal: its USB cable was pulled and plugged back in within two seconds, the port
        check did not notice, and afterwards the pedal answered queries but confirmed no
        parameter. Sending edit enable again is harmless. The pedal answers it every time: when
        nothing at all came from it since the last one, the connection is dead (on Linux, after
        its cable was out and in again between two port checks, the port has the same name but
        no longer delivers) and is made anew."""
        if now - self.last_reenable < EDIT_REENABLE_SECONDS:
            return
        if self.last_heard < self.last_reenable and now - self.last_reenable < 5 * EDIT_REENABLE_SECONDS:
            raise Disconnected("the pedal answers nothing")
        self.last_reenable = now
        self.sender.send(zs.build_edit_enable(self.device_id))

    def _look_for_synth(self, now: float, force: bool = False, synth_only: bool = False) -> None:
        """Asks the pedal for the current patch; _on_patch reads the answer: where the synth
        effect is, and which effects are on. Unless forced, at most once per SYNTH_QUERY_SECONDS:
        a query that comes too early is put off, not dropped. synth_only: asked only to find the
        synth effect (a key pressed), which a preset known to have none does not need."""
        if not self.can_query_patch:
            return
        if not force and now - self.last_patch_query < SYNTH_QUERY_SECONDS:
            if self.patch_query_at is None:
                self.patch_query_at = self.last_patch_query + SYNTH_QUERY_SECONDS
            self.patch_query_wanted = self.patch_query_wanted or not synth_only
            return
        self.last_patch_query = now
        self.sender.send(zs.build_query_patch(self.device_id))

    def _on_patch(self, dump: zs.PatchDump, now: float) -> None:
        before = self.chain_ids
        self.patch_at = now
        self.chain = [found.enabled for found in dump.effects]
        self.chain_ids, self.patch_name = [found.id for found in dump.effects], dump.name or None
        if self.name_checking:
            self._name_checked()
        for slot, (on, _, _) in self.switch_awaiting.items():      # a switch on its way is newer than the dump
            if slot < len(self.chain):
                self.chain[slot] = on
        self.sender.switchable = tuple(range(len(self.chain))) if self.switching else ()
        self.sender.changeable = tuple(range(len(self.chain_ids))) if self.changing else ()   # empty ones too
        self.patch_data = dump.data
        self.knob_values = [{zs.MIN_PARAM + index: value for index, value in enumerate(found.values)}
                            for found in dump.effects]
        if self.chain_ids != before:                # meant for effects that are elsewhere now
            self.edits, self.edit_awaiting, self.strike_awaiting = {}, {}, {}
        self.bridge.found_effects(self.chain_ids)
        for found in dump.effects:
            if found.id and found.id not in self.unmatched and not matches_table(found):
                self.unmatched.add(found.id)
                if plays_other_build(found.id):
                    say(f"WARNING: {effect_label(found.id)} on the pedal is another build than the bridge plays – "
                        + _other_build(found.id))
                else:
                    say(f"WARNING: {effect_label(found.id)} on the pedal is another build than zoom_effects.json "
                        "describes – its knobs go by number, the editor and the keyboard's controllers leave it "
                        "alone; Read Pedal reads its file from the pedal (read_effect_file), or read the table "
                        "again after installing effects (effect_names.py)")
        fresh, self.fresh = self.fresh, False
        changed = self.synth is not None and self._find_synth(dump)
        # What is assigned follows its effect, once the synth effect's place is known (its knobs
        # are sent under rules of their own); with other effects, what is assigned may wait.
        renew = self.bridge.follow_effects(self.patch, self.chain_ids, fresh) if self.patch else False
        if changed or renew or self.chain_ids != before or self.recheck:
            self.recheck = False
            self.refresh()
        if self.effect_change is not None:          # once the synth's place and the allowlist are the new ones
            self._effect_checked(now)
        if self.preset_save is not None and self.preset_save["phase"] == "reading":
            self._send_save(now)
        if self.recall_waiting is not None and not self.rearranging:
            index, self.recall_waiting = self.recall_waiting, None
            self.recall_scene(index, now)
        if self.synth is not None and (self.keys.note is not None or self.arp_note is not None or any(
                voice.key_sent and voice.note not in self._sounding() for voice in self.voices)):
            # also: keep looking while a key is down, and close a gate that nothing plays any more
            # (a note left sounding while the cable was out, or released while an effect moved)
            self._play(now, quiet=self._stepping())

    def _find_synth(self, dump: zs.PatchDump) -> bool:
        """Where the synth effect is in the chain the pedal reported. Returns whether that changed."""
        # The first synth effect in the chain is played; a second one is left alone.
        slot, effect = next(((index, effect) for index, effect in enumerate(
            self.effects.get(found.id) or zs.key_effect(found.id) for found in dump.effects)
            if effect and keys_in_place(dump.effects[index].id)), (None, None))   # notes not into other knobs
        moved = (slot, effect) != (self.synth_slot, self.synth_effect)
        changed = moved or not self.synth_known
        if moved:                                   # what stood in the old place says nothing about the new one
            self.targets, self.told = [], set()
            self.voices = [Voice(index) for index in range(effect.voices)] if effect else []
        self.synth_slot, self.synth_effect, self.synth_known = slot, effect, True
        if slot is None:
            if changed:
                say(f"No {zs.KEY_NAMES} in this preset – the keyboard is ignored")
        else:
            # What each Key knob holds now, wherever the effect has them (the dump has every knob).
            values = dump.effects[slot].values
            for voice in self.voices:
                held = values[effect.key_index + voice.index] if values else None
                if not voice.awaiting and held != voice.key_sent:
                    voice.key_sent, voice.note_sent = held, None
            if changed:
                voices = f" with {effect.voices} voices" if effect.voices > 1 else ""
                say(f"{effect.name} is effect {slot + 1} – the keyboard plays it{voices}")
        return changed

    # --- The arpeggiator and the step sequencer ---

    def _stepping(self) -> bool:
        """One of the two is on and decides what sounds."""
        return self.bridge.arp_on or self.bridge.seq_on

    def _sounding(self) -> list:
        """The notes that should sound now, the most important first: the one note of the
        arpeggiator or the sequencer while one of them is on, otherwise the keys that are down."""
        if self._stepping():
            return [] if self.arp_note is None else [self.arp_note]
        return self.keys.notes(len(self.voices))

    def _next_note(self) -> Optional[tuple]:
        """What the one that is on plays at the next step, as (note, tied): None for a rest, and
        tied if the note sounds on into the step after it. None when there is nothing to play."""
        if self.bridge.seq_on:
            return self.seq.next_step(self.keys.note, self.bridge.arp_settings["latch"])
        note = self.arp.next_note(self.keys.played())
        return None if note is None else (note, False)

    def _arp_clock(self) -> str:
        return self.bridge.arp_settings["clock"]

    def _step_length(self) -> float:
        """Seconds per step: from the tempo of the MIDI clock while it runs, otherwise the set tempo."""
        clocks = arpeggiator.RATES[self.bridge.arp_settings["rate"]]
        if self._arp_clock() == "midi" and len(self.tick_times) > 1 and self.tick_times[-1] > self.tick_times[0]:
            return (self.tick_times[-1] - self.tick_times[0]) / (len(self.tick_times) - 1) * clocks
        return 60 / self.bridge.arp_settings["tempo"] * clocks / 24

    def _arp_step(self, now: float, due: float) -> None:
        """One step: the next note of the chord or the pattern, with a gate-off scheduled for it.
        With nothing to play it rests; on its own clock it starts again with the next key."""
        step = self._next_note()
        length = self._step_length()
        self.next_step = None
        if step is not None and self._arp_clock() == "internal":
            # From when the step was due, so the tempo does not drift; after a stall, from now.
            self.next_step = due + length if due + length > now else now + length
        note, tied = step or (None, False)
        self.arp_note, self.gate_off_at = note, None
        # A gate-off and the next note on the same Key knob keep the minimum interval apart:
        # of two messages 1 ms apart the pedal confirms only the second.
        gate = self.bridge.arp_settings["gate"]
        if note is not None and not tied and gate < 100 and length >= 2 * self.min_interval:
            self.gate_off_at = now + max(self.min_interval, min(length * gate / 100, length - self.min_interval))
        self._play(now, quiet=True)

    def _arp_tick(self, now: float) -> None:
        """Gate-offs and steps that are due (its own clock); silence once the MIDI clock has stopped."""
        if self.gate_off_at is not None and now >= self.gate_off_at:
            self.gate_off_at, self.arp_note = None, None
            self._play(now, quiet=True)
        if self.next_step is not None and now >= self.next_step:
            if self._stepping() and self._arp_clock() == "internal":
                self._arp_step(now, self.next_step)
            else:
                self.next_step = None
        if (self.arp_note is not None and self._arp_clock() == "midi"
                and (not self.tick_times or now - self.tick_times[-1] > CLOCK_TIMEOUT)):
            self._arp_silence()
            self._play(now, quiet=True)

    def _on_clock(self, kind: str, now: float) -> None:
        """MIDI clock (24 per beat) and its Start, Stop and Continue. On the MIDI clock the
        arpeggiator and the sequencer step every so many clocks, counted from Start, so they stay
        on the beat."""
        if kind == "start":
            self.clock_running, self.ticks = True, 0
            return
        if kind == "continue":
            self.clock_running = True
            return
        if kind == "stop":
            self.clock_running = False
            if self._arp_clock() == "midi":
                self.arp.reset()                     # Start begins at the first note again
                self.seq.reset()
                if self.arp_note is not None:
                    self._arp_silence()
                    self._play(now, quiet=True)
            return
        if self.tick_times and now - self.tick_times[-1] > CLOCK_TIMEOUT:
            self.tick_times.clear()                  # its tempo is measured afresh
        self.tick_times.append(now)
        if not self.clock_running:
            return
        if (self._stepping() and self._arp_clock() == "midi"
                and self.ticks % arpeggiator.RATES[self.bridge.arp_settings["rate"]] == 0):
            self._arp_step(now, now)
        self.ticks += 1

    def _arp_silence(self) -> None:
        """No note of the arpeggiator or the sequencer any more and none planned; _play then closes the gate."""
        self.arp_note = self.gate_off_at = self.next_step = None

    def switch_arp(self, now: float) -> None:
        """The arpeggiator or the sequencer was switched on or off: it starts afresh. On its own
        clock it starts at once with the keys that are down; with both off, they sound as usual."""
        self._latch_keys()
        self._arp_restart(now)

    def arp_changed(self, now: float, before: dict) -> None:
        """The settings changed while one of the two is on; before: what they were. It plays on,
        every step reads them. Only another clock needs a new start, and Latch switched on keeps
        the keys that are down now."""
        settings = self.bridge.arp_settings
        if settings["latch"] and not before["latch"]:
            self._latch_keys()
        if settings["clock"] != before["clock"]:
            self._arp_restart(now)

    def _latch_keys(self) -> None:
        """Latch keeps what is down now, not an older chord or key."""
        self.arp.latched = self.keys.played()
        self.seq.root = self.keys.note

    def _arp_restart(self, now: float) -> None:
        """The chord or the pattern from its beginning: on its own clock at once if there is
        something to play, on the MIDI clock with its next step."""
        self._arp_silence()
        self.arp.reset()
        self.seq.reset()
        if self._stepping() and self._arp_clock() == "internal":
            self._arp_step(now, now)                # with nothing to play it rests until a key comes
        else:
            self._play(now)

    def _going(self) -> bool:
        """On the MIDI clock the two only go on while clocks arrive."""
        return self._arp_clock() == "internal" or (
            bool(self.tick_times) and time.monotonic() - self.tick_times[-1] <= CLOCK_TIMEOUT)

    def arp_state(self) -> dict:
        """For the interface: the note the arpeggiator is at, also in the gap before the next one
        (None while it rests), and the tempo of the MIDI clock arriving."""
        bpm, span = None, (self.tick_times[-1] - self.tick_times[0]) if self.tick_times else 0
        if span > 0 and time.monotonic() - self.tick_times[-1] <= CLOCK_TIMEOUT:
            bpm = round(60 * (len(self.tick_times) - 1) / (24 * span), 1)
        playing = self.arp.last is not None and self._going()
        return {"note": note_name(self.arp.last) if playing else None, "clock_bpm": bpm,
                "clock_running": self.clock_running}

    def seq_step(self) -> Optional[int]:
        """For the interface: the step of the pattern the sequencer is at, counted from 0; None while it rests."""
        return self.seq.step if self._going() else None

    def _accept(self, value: int) -> bool:
        """Deadband: ignore small changes, but always let the end stops through."""
        at_end = value in (self.expression["min"], self.expression["max"])
        if self.last_cc is None or abs(value - self.last_cc) > self.deadband or (
                at_end and value != self.last_cc):
            self.last_cc = value
            return True
        return False

    # --- Sending and acks ---

    def _flush(self, now: float) -> None:
        """At most one message per minimum interval; the parameters take turns."""
        if self.rearranging:
            return
        # The pitch wheel comes before any other knob and keeps the configured pace: notes have
        # their own check for missing acks, and a parameter the pedal does not confirm (which
        # slows the others down) must not make a bend drag.
        if now - self.last_send >= self.min_interval and self._bend_step(now):
            return
        if now - self.last_send < self.interval:
            return
        for _ in self.targets:
            self.turn = (self.turn + 1) % len(self.targets)
            target = self.targets[self.turn]
            if target.pending is None or target.unplaced or target.blocked or target.stopped:
                continue
            if not (target.learned or target.confirmed) and target.awaiting:
                continue   # unproven parameter: one message at a time until the pedal confirms it
            (value, cc_time), target.pending = target.pending, None
            if value != target.last_value:
                self._transmit(target, value, cc_time, now)
                return
        while self.edits and self.patch is not None:   # the editor's, after the assignments' values
            (slot, param), (value, effect_id) = next(iter(self.edits.items()))
            del self.edits[(slot, param)]
            if not self.chain_ids or slot >= len(self.chain_ids) or self.chain_ids[slot] != effect_id:
                continue                            # another effect is there now: not for it
            self.sender.send(zs.build_set_param(self.device_id, slot, param, value))
            self.last_send = now
            # shown once the pedal confirms it (or reports the knob); without an ack it goes once more
            known = self.edit_awaiting.get((slot, param))
            attempt = known[2] + 1 if known and known[0] == value else 0
            self.edit_awaiting[(slot, param)] = (value, now, attempt, effect_id)
            for target in self.targets:             # what they sent last is not what the knob holds now
                if (target.mapping.slot, target.mapping.param) == (slot, param):
                    target.last_value = None
                    target.awaiting.clear()
            if self.learning is not None and zs.is_effect_param(slot, param):
                self.learning.setdefault((slot, param), []).append(value)    # a scene being captured
            return

    def _transmit(self, target: Target, value: int, cc_time: float, now: float,
                  retry: bool = False) -> None:
        mapping = target.mapping
        self.sender.send(zs.build_set_param(self.device_id, mapping.slot, mapping.param, value))
        target.last_value = value
        self.last_send = time.monotonic()   # the real send time, so a slow loop pass cannot shorten the spacing
        target.awaiting.append(Sent(value, cc_time, now, retry))
        self.sent += 1
        self.totals["sent"] += 1

    def _on_ack(self, target: Target, value: int, now: float) -> None:
        if all(sent.value != value for sent in target.awaiting):
            return
        while True:   # acks arrive in send order; anything skipped stayed unconfirmed
            sent = target.awaiting.popleft()
            if sent.value == value:
                break
            self._miss(target, sent, now)
        self.acked += 1
        self.totals["acked"] += 1
        self.latencies.append(now - sent.cc_time)
        self.recent_latencies.append(now - sent.cc_time)
        self.misses_in_row = target.misses_in_row = 0
        target.confirmed = True
        self.acks_in_row += 1
        if self.acks_in_row >= ACKS_BEFORE_SPEEDUP and self.interval > self.min_interval:
            self.interval = max(self.interval / 2, self.min_interval)
            self.acks_in_row = 0
            say(f"Acks are reliable again – minimum interval {self.interval * 1000:g} ms")

    def _check_timeouts(self, now: float) -> None:
        for target in self.targets:
            while target.awaiting and now - target.awaiting[0].send_time > ACK_TIMEOUT:
                self._miss(target, target.awaiting.popleft(), now)
        for voice in self.voices:
            while voice.awaiting and now - voice.awaiting[0].send_time > KEY_ACK_TIMEOUT:
                self._key_miss(voice, voice.awaiting.popleft(), now)
        self._check_switches(now)

    def _miss(self, target: Target, sent: Sent, now: float) -> None:
        """No ack. The pedal only confirms changes, so a single miss is normal."""
        if target.scene:        # a scene recalled twice, or a value the pedal holds already
            zs.log.info("--  no ack for value %d (scene)", sent.value)
            return
        self.missed += 1
        self.totals["missed"] += 1
        self.misses_in_row += 1
        target.misses_in_row += 1
        self.acks_in_row = 0
        zs.log.info("--  no ack for value %d", sent.value)
        if not (target.learned or target.confirmed) and target.misses_in_row >= UNCONFIRMED_LIMIT:
            # Wrong effect or parameter number, or a range the pedal rejects: stop sending to it.
            target.stopped, target.pending = True, None
            say(f"Preset {display(self.patch)}: the pedal does not accept {target.mapping.describe()} "
                "– sending stopped")
            return
        if self.misses_in_row >= MISSES_BEFORE_SLOWDOWN:
            self._reenable_edit(now)
        if self.misses_in_row >= MISSES_BEFORE_SLOWDOWN and self.interval < MAX_INTERVAL:
            self.interval = min(self.interval * 2, MAX_INTERVAL)
            self.misses_in_row = 0
            say(f"WARNING: acks are missing – minimum interval now {self.interval * 1000:g} ms")
        # Repeat the final value of a movement once, so the pedal does not stay on an old value
        if (not sent.retry and not target.awaiting and target.pending is None and not (target.blocked or target.stopped)
                and sent.value == target.last_value):
            if self.rearranging:    # the place may hold another effect now: it goes once the chain is reported
                target.pending, target.last_value = (sent.value, sent.cc_time), None
            else:
                self._transmit(target, sent.value, sent.cc_time, now, retry=True)

    def _report(self, now: float) -> None:
        if now < self.next_report:
            return
        self.next_report = now + REPORT_SECONDS
        if not self.sent:
            return
        latency = ""
        if self.latencies:
            in_ms = [1000 * seconds for seconds in self.latencies]
            latency = f", latency CC→ack median {statistics.median(in_ms):.1f} ms / max {max(in_ms):.1f} ms"
        zs.log.info("--  %d sent, %d confirmed, %d unconfirmed%s", self.sent, self.acked, self.missed, latency)
        self.sent = self.acked = self.missed = 0
        self.latencies = []

    # --- MS-60B+ → patch, acks, knob movements ---

    def _on_zoom(self, message, now: float) -> Optional[bytes]:
        self.last_heard = now
        if message.type in IGNORED_TYPES:
            return None
        raw = zs.log_rx(message)
        if message.type == "control_change" and message.control == 0:
            self.bank_msb = message.value
        elif message.type == "control_change" and message.control == 32:
            self.bank_lsb, self.bank_selected = message.value, True
        elif message.type == "program_change":
            # A preset change comes with bank select. While a name is edited on it, the MS-60B+
            # sends bursts of "program change 0" on their own (measured 2026-10-19): no change.
            if self.bank_selected:
                self._set_patch(zs.decode_value(self.bank_lsb, self.bank_msb), message.program)
            self.bank_selected = False
        elif raw is not None and zs.parse_file_entry(zs.zoom_body(raw, self.device_id)):
            name = zs.parse_file_entry(zs.zoom_body(raw, self.device_id))
            if self.listing is not None:
                self.listing.append((name, zs.file_entry_size(zs.zoom_body(raw, self.device_id))))
            self._on_file_listed(name)
        elif raw is not None:
            body = zs.zoom_body(raw, self.device_id)
            change, program_info = zs.parse_param(body), zs.parse_program_info(body)
            dump = zs.parse_patch_dump(body) if self.watching else None
            stored = zs.parse_stored_preset(body)
            if stored:
                self._on_stored(stored, now)
            elif program_info:
                self._set_patch(*program_info, fresh=True)
            elif dump:
                self._on_patch(dump, now)
            elif (change and change.slot == self.synth_slot
                  and 0 <= change.param - self.synth_effect.key_param < len(self.voices)):
                voice = self.voices[change.param - self.synth_effect.key_param]
                if change.ack:
                    self._on_key_ack(voice, change.value, now)
                else:
                    voice.key_sent, voice.note_sent = change.value, None   # a Key knob was turned on the pedal
            elif change and change.param == zs.SWITCH_PARAM and change.slot < zs.MAX_SLOTS:
                self._on_switched(change, now)
            elif change and change.slot == zs.NAME_SLOT:
                self._on_name_character(change, now)
            elif change and change.param == zs.EFFECT_PARAM and change.slot < zs.MAX_SLOTS:
                if not change.ack:          # another effect, chosen on the pedal: what is there now?
                    self._look_for_synth(now)
            elif change:
                awaited, overtaken = self.strike_awaiting.get((change.slot, change.param), []), False
                if not change.ack:
                    awaited.clear()                 # turned on the pedal: what was sent before a note is past
                elif change.value in awaited:       # sent before a note: the pedal confirms in the order sent
                    del awaited[:awaited.index(change.value) + 1]
                    overtaken = bool(awaited)       # a newer value of ours follows: that is what it holds then
                if (not overtaken and change.slot < len(self.knob_values)
                        and change.param in self.knob_values[change.slot]):
                    self.knob_values[change.slot][change.param] = change.value    # turned, or taken
                if self.edit_awaiting.get((change.slot, change.param), (None,))[0] == change.value:
                    del self.edit_awaiting[(change.slot, change.param)]
                target = next((t for t in self.targets if (t.mapping.slot, t.mapping.param) == (
                    change.slot, change.param) and not (t.away or t.blocked)), None)     # the one that sends
                if not change.ack:
                    self._on_knob(change, target)
                elif target is not None:
                    self._on_ack(target, change.value, now)
        return raw

    def _on_knob(self, change: zs.ParamMessage, target: Optional[Target]) -> None:
        """A knob was turned on the pedal."""
        if self.learning is not None and zs.is_effect_param(change.slot, change.param):
            self.learning.setdefault((change.slot, change.param), []).append(change.value)
        if target is not None:
            target.last_value = None   # send the next target value in any case
            target.confirmed, target.stopped, target.misses_in_row = True, False, 0

    def _set_patch(self, bank: int, program: int, fresh: bool = False) -> None:
        """fresh: the player selected it on the pedal, which loads it as it was saved (at the
        start, the pedal may hold changes to it that are not saved). A preset chosen in the
        interface is loaded as stored too."""
        fresh, self.loading_stored = fresh or self.loading_stored, False
        if (bank, program) == self.patch:
            return
        self.patch, self.fresh = (bank, program), fresh
        self.patch_since, self.unplaced_given_up = time.monotonic(), False
        if self.learning is not None or self.bridge.capturing is not None:
            self.learning = None
            self.bridge.capturing = None
            say("Learning cancelled: preset changed")
        if self.bridge.param_learning is not None:
            self.bridge.param_learning = None
            say("Learning a controller cancelled: preset changed")
        self.scene_active, self.recall_waiting = None, None
        self.chain, self.sender.switchable = None, ()   # another chain: nothing is switched until it is known
        self.chain_ids = self.patch_name = self.patch_data = None
        self.knob_values, self.edits, self.edit_awaiting, self.strike_awaiting = [], {}, {}, {}
        self.sender.changeable = ()
        self.switch_awaiting.clear()
        if self.name_wanted is not None:
            self._name_done(False, "Renaming stopped: preset changed")
        if self.effect_change is not None:
            self._effect_done(False, _stopped(self.effect_change), play=False)
        if self.preset_save is not None and self.preset_save["phase"] == "reading":
            self._save_done(False, "Saving stopped: preset changed")   # its patch would be this one's
        if self.synth is not None:   # the effect may sit elsewhere now, or not be there at all
            self.synth_slot, self.synth_effect, self.synth_known, self.voices = None, None, False, []
        if self.watching:
            self.patch_query_at = time.monotonic() + QUERY_DELAY
        self.targets, self.told = [], set()   # another preset: nothing is carried over, remarks are made afresh
        self.refresh()
        expression = [target for target in self.targets if target.expression]
        if not expression:
            say(f"Preset {display(self.patch)}: no assignment, expression is ignored")
        else:
            say(f"Preset {display(self.patch)}: expression → "
                + " and ".join(target.mapping.describe() for target in expression))

    def refresh(self) -> None:
        """Re-derive the assignments and allowlist targets for the current patch."""
        # A parameter that stays what it was keeps the value last sent to it, so that learning
        # a controller for one knob does not make all the others send their values again.
        before = {(target.mapping, target.expression): target for target in self.targets}

        def carry(target: Target) -> Target:
            old = before.get((target.mapping, target.expression))
            if old is not None:
                target.last_value, target.confirmed, target.pending = old.last_value, old.confirmed, old.pending
            return target

        def remark(text: str) -> None:              # once per preset and place of the effect
            if text not in self.told:
                self.told.add(text)
                say(text)

        self.targets, self.controls, self.cc_targets, allowed, knobs = [], {}, {}, [], {}
        effect = self.synth_effect
        known_build = self.synth_slot is not None and self.chain_ids is not None and (
            self.chain_ids[self.synth_slot] not in self.unmatched)
        if self.synth_slot is not None:
            allowed += [item for item in zs.synth_targets(self.synth_slot, effect)    # another build: notes only
                        if known_build or effect.key_param <= item.param < effect.key_param + effect.voices]
            knobs = {zs.MIN_PARAM + index: knob for index, knob in enumerate(effect.knobs)}
        for mapping in (self.bridge.mappings.get(self.patch, ()) if self.patch else ()):
            learned = self.bridge.measurements.get(self.patch, mapping.slot, mapping.param) is not None
            target = carry(Target(mapping, learned))
            target.assigned, target.expression = True, mapping.cc is None
            if mapping.cc is not None:
                self.cc_targets.setdefault(mapping.cc, []).append(target)
            self.targets.append(target)
            knob = knobs.get(mapping.param) if mapping.slot == self.synth_slot else None
            if place_of(mapping.slot, mapping.effect, self.chain_ids) != mapping.slot:
                target.away, target.blocked = effect_label(mapping.effect, mapping.param), True
                remark(f"Nothing is sent to {target.away}: that effect is not in this preset now")
            elif (mapping.effect is not None and self.chain_ids is None and self.watching
                  and not self.unplaced_given_up):
                # just selected: until the pedal reports the chain, its effect may sit elsewhere
                # (moved before without saving); the values wait for that, a moment
                target.unplaced = True
            elif knob is None and mapping.param in self.key_params(mapping.slot):
                target.blocked = True   # a synth effect the keyboard does not play: its Key is a note all the same
                remark(f"Nothing is sent to {mapping.describe()}: that is a Key knob of a synth effect")
            elif knob is None:
                allowed.append(zs.ParamTarget(mapping.slot, mapping.param, mapping.min, mapping.max))
            elif effect.key_param <= mapping.param < effect.key_param + effect.voices:
                target.blocked = True   # the keyboard owns the Key knobs; expression must not write notes
                remark(f"Nothing is sent to {mapping.describe()}: that is the {effect.name}'s {knob[0]} knob")
            elif mapping.max > knob[1]:
                target.blocked = True   # a range learned from another effect or an older version
                remark(f"Nothing is sent to {mapping.describe()}: the {effect.name}'s {knob[0]} knob "
                       f"only goes up to {knob[1]} – learn it again")
        self.scene_targets = {}
        for scene in self._scenes():        # what the scenes of this preset set, one Target per parameter
            for (slot, param), value in scene["values"].items():
                limits = self.bridge.measurements.get(self.patch, slot, param)
                low, high = (limits["min"], limits["max"]) if limits else (value, value)
                where = f"effect {slot + 1}, parameter {param - 1}"
                made_for = scene.get("chain", {}).get(slot)     # the effect the value was captured on
                if place_of(slot, made_for, self.chain_ids) != slot:
                    remark(f"Scene “{scene['name']}” does not set {effect_label(made_for, param)}: "
                           "that effect is not in this preset now")
                    continue
                if slot == self.synth_slot:
                    remark(f"Scene “{scene['name']}” does not set {where}: that is the {effect.name}")
                    continue
                if param in self.key_params(slot):
                    remark(f"Scene “{scene['name']}” does not set {where}: that is a Key knob of a synth effect")
                    continue
                if not low <= value <= high or not (limits or (
                        slot < MAX_EFFECTS and zs.MIN_PARAM <= param < zs.MIN_PARAM + MAX_PARAMS)):
                    remark(f"Scene “{scene['name']}” does not set {where}: {value} is outside what was learned")
                    continue
                target = self.scene_targets.get((slot, param)) or next(
                    (t for t in self.targets if (t.mapping.slot, t.mapping.param) == (slot, param)
                     and not t.away), None)
                if target is None:
                    target = Target(Mapping(slot, param, low, high), learned=limits is not None)
                    target.expression, target.scene = False, True
                    self.targets.append(carry(target))
                if made_for is not None and self.chain_ids is None and self.watching and not self.unplaced_given_up:
                    target.unplaced = True      # as with assignments: until the pedal reports the chain
                self.scene_targets[(slot, param)] = target
                allowed.append(zs.ParamTarget(slot, param, min(low, value), max(high, value)))
        if self.synth_slot is not None and known_build:   # the keyboard's controllers, one Target per knob
            for name, number in self.bridge.controls.items():
                owner, name = synth_controls.split(name)
                if owner is not effect or name not in effect.controls:
                    continue            # a switch, a knob of another synth effect, or of another build
                param, top = zs.synth_knob(effect.controls[name][0], effect)
                # one Target per knob – not one that sends nothing (made for an effect not here now,
                # or with a range this knob does not have): the knob's own then
                target = next((t for t in self.targets if (t.mapping.slot, t.mapping.param) == (
                    self.synth_slot, param) and not (t.away or t.blocked)), None)
                if target is None:
                    target = Target(Mapping(self.synth_slot, param, 0, top), learned=True)
                    target.expression = False
                    self.targets.append(carry(target))
                self.controls[number] = (target, name)
        for slot, effect_id in enumerate(self.chain_ids or ()):     # the editor: every known knob, its whole range
            if effect_id in self.unmatched:
                continue
            keys = self.key_params(slot)                            # (not a synth effect's Key knobs)
            allowed += [zs.ParamTarget(slot, knob["param"], 0, knob["max"])
                        for knob in effect_names.knobs(effect_id) if knob["param"] not in keys]
        for target in self.targets:         # a value that waited for an effect that is gone, or is not to be sent
            if target.blocked or target.stopped:
                target.pending = None
                target.awaiting.clear()
        widest: dict = {}                   # a parameter of both a scene and the pedal: the wider range counts
        for item in allowed:
            known = widest.get((item.slot, item.param))
            widest[(item.slot, item.param)] = item if known is None else zs.ParamTarget(
                item.slot, item.param, min(item.min, known.min), max(item.max, known.max))
        self.sender.targets = tuple(widest.values())


class Bridge:
    """Configuration, assignments and the current connection. run() runs in the main thread; the
    interface reads state() and queues changes into the loop through call()."""

    def __init__(self, settings: Optional[dict] = None, folder: Optional[Path] = None,
                 effects_file: Optional[Path] = None) -> None:
        """settings: the content of a settings file (config_schema.py), already validated. Then
        nothing is read from or written to this installation's own files. folder: where that
        file is, for a bridge without screen whose interface is reached over the network: what
        is changed there is written back into the file. Without it the settings cannot be changed.
        effects_file: where a bridge without screen keeps the effects read from the pedal's files
        (beside its settings file); only read from there without folder."""
        self.fixed = settings is not None
        self.folder = folder if self.fixed else None
        self.interface = not self.fixed or self.folder is not None    # someone may look at the state
        self.led: Optional[status_led.StatusLed] = None    # the status light of a bridge without screen
        self.news = None                                   # there: says whether a stick with other settings is in
        self.fallback = False                              # running with the previous settings file
        self.read_at_start = False                         # the pedal's files were read (or not) at the start
        if self.fixed:
            self.cfg = settings
            self.approvals = cs.Approvals(settings["approvals"])
            self.measurements = cs.Measurements(settings.get("learned") or {},
                                                zs.read_effect_ids(settings.get("pedal_effects")))
        else:
            if not CONFIG.exists():
                sys.exit("config.yaml is missing – see “First-time setup” in README.md.")
            self.cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
            self.approvals = zs.Approvals(APPROVALS)
            self.measurements = zs.Measurements(MEASUREMENTS)
        # what was read from the pedal's own effect files: kept beside the settings (on the Pi in
        # the folder of its settings file; without one, for this run only)
        if self.fixed:
            effects_file = effects_file or (self.folder / cs.EFFECTS_NAME if self.folder is not None else None)
            effect_names.use_cache(effects_file, keep=self.folder is not None)
        else:
            effect_names.use_cache(EFFECT_CACHE)
        zs.use_installed_synths()                    # a synth effect's build read from the pedal is played
        chain_entry.cache_clear()
        effect_choices.cache_clear()
        missing = [] if self.approvals.backup_confirmed else ["backup"]
        missing += [kind for kind in REQUIRED_KINDS if not self.approvals.is_approved(kind)]
        if missing:
            sys.exit(f"Approval missing: {', '.join(missing)} – see `python probe.py approve`.")
        self.synth = read_synth(self.cfg)
        # Someone without an expression controller (only a keyboard, or only the pedal to edit its
        # presets in the interface) has none to measure.
        expression = bool(self.cfg["ports"].get("chocolate"))
        if self.cfg["zoom"]["device_id"] is None or (expression and self.cfg["expression"].get("cc") is None):
            sys.exit("config.yaml is incomplete – see “First-time setup” in README.md.")
        self.controls: dict = {}                      # what a keyboard controller sets → CC number
        self.arp_settings = arpeggiator.read({})      # changed in place, the session's arpeggiator shares it
        self.seq_steps: list = []                     # the sequencer's pattern, changed in place as well
        self.port_names: tuple = ([], [])             # the MIDI inputs and outputs there are, for the interface
        if self.synth:
            self._load_keyboard_settings(settings)
        # which controller switches which effect of the chain: slot → {"cc": number, "press": bool}
        self.switches: dict = effect_switch.read(settings.get("effects")) if self.fixed else read_switches(CONTROLS)
        self.effect_learning: Optional[int] = None    # the effect (slot) waiting for a controller to be moved
        # scenes per preset: (bank, program) → [{"name", "cc", "values": {(slot, param): value}, "effects"}]
        self.scenes: dict = scene_file.read(settings.get("scenes")) if self.fixed else read_scenes(CONTROLS)
        self.scene_learning: Optional[tuple] = None   # (preset, index) of the scene waiting for a controller
        self.capturing: Optional[tuple] = None        # (preset, index) of the scene whose values are being captured
        self.arp_on = False                           # live, never stored: the bridge starts with it off
        self.seq_on = False                           # the same for the sequencer; only one of the two is on
        self.recording: Optional[list] = None         # the steps played so far while a pattern is recorded
        self.control_learning: Optional[str] = None   # the knob waiting for a controller to be moved
        # (preset, slot, param, the new assignment or None) waiting for its controller to be moved
        self.param_learning: Optional[tuple] = None
        self.param_result: Optional[dict] = None      # how the last one went: {"ok", "text"}, for the interface

        try:
            self.mappings: dict = read_mappings(self.cfg.get("mappings"))   # (bank, program) → its mappings
        except ValueError as error:
            sys.exit(f"Mapping {error.args[0]}: “{error.args[1]}” is not an effect id (8 hex digits).")
        for patch, mappings in self.mappings.items():
            problem = self.check(patch, mappings)
            if problem:
                sys.exit(f"Mapping {patch[0]}/{patch[1]}: {problem}")
        # a bridge without screen: its settings file as last written (or read), to go back to
        # when a change cannot be kept
        self._kept: Optional[dict] = copy.deepcopy(settings) if self.fixed else None

        self.session: Optional[Session] = None
        self._pi_wlan_seen: tuple = (None, None)      # (time config.yaml was changed, its `pi:` section)
        self.status = "Starting…"
        self.commands: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._snapshot: dict = {}
        self._publish()   # the interface gets a complete state from the very start

    def _load_keyboard_settings(self, settings: Optional[dict] = None) -> None:
        """What belongs to the keyboard synth: its controllers, the arpeggiator's settings and the
        sequencer's pattern, from the settings file or from controls.json."""
        fixed = settings is not None
        self.controls = synth_controls.read(settings.get("controls") or {}) if fixed else read_controls(CONTROLS)
        self.arp_settings.update(arpeggiator.read(settings.get("arp")) if fixed else read_arp(CONTROLS))
        self.seq_steps[:] = sequencer.read(settings.get("seq")) if fixed else read_seq(CONTROLS)

    def check(self, patch: tuple[int, int], mappings: tuple, incoming: Optional[dict] = None) -> Optional[str]:
        """The reason a preset's assignments are not acceptable, otherwise None.
        incoming: ranges per (slot, param) from an import file that are not applied yet."""
        if not 1 <= len(mappings) <= MAX_ASSIGNMENTS:
            return f"A preset can have 1 to {MAX_ASSIGNMENTS} parameters."
        if len({(mapping.slot, mapping.param) for mapping in mappings}) < len(mappings):
            return "The same parameter is assigned twice."
        for cc in {mapping.cc for mapping in mappings}:
            if cc is not None and not (0 <= cc <= 127 and cc not in RESERVED_CONTROLS):
                return f"Controller {cc} cannot set a parameter (0–127, not bank select, sustain or channel mode)."
            if sum(mapping.cc == cc for mapping in mappings) > MAX_TARGETS:
                return f"{controller_name(cc).capitalize()} can set at most {MAX_TARGETS} parameters of a preset."
        for mapping in mappings:
            where = f"effect {mapping.slot + 1}, parameter {mapping.param - 1}"
            limits = (incoming or {}).get((mapping.slot, mapping.param)) or self.measurements.get(
                patch, mapping.slot, mapping.param)
            if limits is None:   # not learned: only the fixed grid, with a range the user states
                if not (0 <= mapping.slot < MAX_EFFECTS
                        and zs.MIN_PARAM <= mapping.param < zs.MIN_PARAM + MAX_PARAMS):
                    return (f"{where.capitalize()} is outside effects 1–{MAX_EFFECTS} / parameters "
                            f"1–{MAX_PARAMS} and has not been learned in preset {display(patch)}.")
                if not 0 <= mapping.min <= mapping.max <= UNLEARNED_MAX:
                    return f"{mapping.min}–{mapping.max} is not a valid range ({where})."
            elif not limits["min"] <= mapping.min <= mapping.max <= limits["max"]:
                return (f"{mapping.min}–{mapping.max} is outside the learned range "
                        f"{limits['min']}–{limits['max']} ({where}).")
            if mapping.curve not in CURVES:
                return f"Unknown curve {mapping.curve} (available: {', '.join(CURVES)})."
        return None

    # --- Main loop ---

    def run(self) -> None:
        next_try = next_snapshot = next_ports = 0.0
        next_news = time.monotonic() + STICK_SECONDS
        waiting = False
        try:
            while True:
                now = time.monotonic()
                self._run_commands()
                if self.news is not None and now >= next_news:
                    next_news = now + STICK_SECONDS
                    if self.news():
                        raise NewSettings
                try:
                    # The interface offers the ports to choose from. While connected, the session's
                    # own check of its ports keeps the list.
                    if self.session is None and now >= next_ports and self.interface:
                        self.port_names = (mido.get_input_names(), mido.get_output_names())
                        next_ports = now + PORT_CHECK_SECONDS
                    if self.session is not None:
                        try:
                            self.session.step(now)
                        except (zs.NotAllowed, zs.NotApproved) as error:
                            say(f"WARNING: not sent – {error}")    # a refusal of the allowlist: nothing went out
                        except UserError as error:                 # learned, but not kept (the settings file)
                            say(f"WARNING: {error}")
                    elif now >= next_try:
                        try:
                            self.session = Session(self)
                        except zs.PortError as error:
                            if not waiting:
                                say(f"Waiting for devices: {error}")
                            waiting, self.status = True, f"Waiting for devices: {error}"
                            next_try = now + RETRY_SECONDS
                        else:
                            waiting = False
                            self.session.start()
                            self.status = "Ready"
                except (Disconnected,) + MIDI_ERRORS as error:
                    say(f"Connection lost: {error}")
                    self.status = f"Connection lost: {error}"
                    self._close_session()
                    next_try = time.monotonic() + RETRY_SECONDS
                if now >= next_snapshot:
                    if self.interface:               # without screen and network nobody reads it
                        self._publish()
                    if self.led is not None:
                        # The Pi without screen is for playing: ready once a controller is there too
                        ready = self.session is not None and self.session.ready and bool(self.session.controllers)
                        self.led.show(("fallback" if self.fallback else "ready") if ready else "waiting")
                    next_snapshot = now + SNAPSHOT_SECONDS
                time.sleep(0.001 if self.session else 0.05)
        except KeyboardInterrupt:
            pass
        finally:
            self._close_session()
            self.status = "Stopped"
            self._publish()
            say("Stopped.")

    def _close_session(self) -> None:
        session, self.session = self.session, None
        self.capturing = None                       # a capture lives in the session it was started in
        if session is not None:
            session.close()

    # --- Access from the interface (another thread) ---

    def state(self) -> dict:
        with self._lock:
            return self._snapshot

    def call(self, function, timeout: float = 3.0) -> dict:
        """Runs function() in the main loop; returns {"result": …} or {"error": text}."""
        done, box = threading.Event(), {}
        self.commands.put((function, done, box))
        if not done.wait(timeout):
            box["late"] = True                  # not run when the loop gets to it: the person was told it failed
            return {"error": "The bridge is not responding right now."}
        return box

    def _run_commands(self) -> None:
        while True:
            try:
                function, done, box = self.commands.get_nowait()
            except queue.Empty:
                return
            if box.get("late"):                 # given up on (a long Read Pedal held the loop)
                done.set()
                continue
            try:
                box["result"] = function()
            except UserError as error:
                box["error"] = str(error)
            except Exception as error:      # a fault in handling a request must not end the bridge
                zs.log.error("--  request failed\n%s", traceback.format_exc())
                say(f"ERROR in a request from the interface: {error!r} – see the log")
                box["error"] = "That went wrong inside the bridge; it keeps running (details in the log)."
            finally:
                self._publish()
                done.set()

    def _publish(self) -> None:
        session = self.session
        patch = session.patch if session else None
        latencies = [1000 * seconds for seconds in session.recent_latencies] if session else []
        effect = session.synth_effect if session else None
        learning = None
        if session and session.learning is not None and self.capturing is None:
            learning = [{"slot": slot, "param": param, "min": min(values), "max": max(values),
                         "count": len(values)} for (slot, param), values in session.learning.items()]
        chain = session.chain_ids if session else None

        def scene_away(scene: dict, slot: int, param: Optional[int] = None) -> Optional[str]:
            effect = scene.get("chain", {}).get(slot)
            return effect_label(effect, param) if place_of(slot, effect, chain) != slot else None

        state = {
            "connected": bool(session and session.ready),
            "status": self.status,
            "profile": PROFILE,
            "devices": self._devices(),
            "pi_wlan": self._pi_wlan(),                      # the Pi's own WLAN, once a card has been written
            "fixed": self.fixed,                             # a bridge without screen, run from its settings file
            "patch": {"key": f"{patch[0]}/{patch[1]}", "display": display(patch),
                      "name": session.patch_name} if patch else None,
            # renaming the preset on the pedal: approved, and a new name on its way
            "rename": {"approved": self.approvals.is_approved("rename_patch") and self.approvals.is_approved("query_patch"),
                       "busy": bool(session and session.name_wanted is not None),
                       "result": session.name_result if session else None},
            # the effects of the preset on the pedal, with their names, knobs and the DSP they take
            # (null while unknown); name null: an effect the table of names does not know (effect_names.py)
            "chain_effects": [{"slot": slot, "id": f"{effect_id:08X}", **chain_entry(effect_id),
                               # another build than the table describes: its knobs go by number
                               **({"knobs": [], "unmatched": True} if effect_id in session.unmatched else {}),
                               # what its knobs hold now, for the editor: {parameter: value}
                               "values": session.knob_values[slot] if slot < len(session.knob_values) else {},
                               "keys": list(session.key_params(slot))}         # Key knobs: notes, not set
                              for slot, effect_id in enumerate(session.chain_ids)]
            if session and session.chain_ids is not None else None,
            "pedal": session.last_cc if session else None,
            # the preset's assignments, in their order: what was sent last and whether it is taken
            "targets": [{"value": target.last_value, "stopped": (target.stopped or target.blocked) and not target.away,
                         "away": target.away,
                         "unconfirmed": target.misses_in_row >= UNCONFIRMED_HINT}
                        for target in session.targets if target.assigned] if session else [],
            # the parameter waiting for its controller to be moved, in the preset on the pedal
            "param_learning": {"slot": self.param_learning[1], "param": self.param_learning[2],
                               "new": self.param_learning[3] is not None}
            if self.param_learning and patch and self.param_learning[0] == patch else None,
            "param_result": self.param_result,
            "effects": {
                "approved": self.approvals.is_approved("switch_effect") and self.approvals.is_approved("query_patch"),
                # per effect of the preset on the pedal: on, off, or null while that is unknown
                "chain": list(session.chain) if session and session.chain is not None else None,
                "switches": [dict(self.switches.get(slot) or {"cc": None, "press": False}, slot=slot)
                             for slot in range(effect_switch.MAX_EFFECTS)],
                "learning": self.effect_learning,
                "controller": bool(session and session.ready and session.controllers),   # to learn a switch from
                # another effect in a place: approved, one on its way, and what came of the last
                "change": {"approved": self.approvals.is_approved("change_effect")
                           and self.approvals.is_approved("query_patch"),
                           "busy": bool(session and session.effect_change is not None),
                           "result": session.effect_result if session else None},
                # what a place can be changed to: the effects the pedal reported in its presets
                "choices": effect_choices(frozenset(self.measurements.effects)),
                # the DSP the effects of a preset may take together (chain_effects' and choices' "dsp")
                "dsp_budget": DSP_BUDGET,
                # changes with the table and with what was read from the pedal: the texts of the
                # knobs' values (/api/labels) are asked for again
                "labels_version": effect_names.version(),
                # moving an effect one place: approved (on its way and done: as "change")
                "move": {"approved": self.approvals.is_approved("move_effect")
                         and self.approvals.is_approved("query_patch")},
                # adding an empty place: approved (on its way and done: as "change")
                "add": {"approved": self.approvals.is_approved("add_effect")
                        and self.approvals.is_approved("query_patch")},
                # reading the pedal's list of files: approved, and what came of the last
                "read": {"approved": self.approvals.is_approved("read_pedal"),
                         "result": session.read_result if session else None,
                         "busy": bool(session and session.reading_files_now)},   # effect files are being read
                # deleting an effect: approved (on its way and done: as "change")
                "delete": {"approved": self.approvals.is_approved("delete_effect")
                           and self.approvals.is_approved("query_patch")},
            },
            # the presets of the pedal: choosing one, their names (null: not read), storing the one on it
            "presets": {
                "select": bool(self.approvals.is_approved("select_preset")),
                "save": all(self.approvals.is_approved(kind) for kind in ("save_preset", "query_preset", "query_patch")),
                "list": [{"bank": bank, "program": program, "display": display((bank, program)),
                          "name": session.preset_names.get((bank, program)) if session else None}
                         for bank in range(zs.PRESET_BANKS) for program in range(zs.PRESETS_PER_BANK)],
                "reading": bool(session and (session.preset_queue or session.preset_awaiting)),
                "busy": bool(session and session.preset_save is not None),
                "undo": ({"display": display(session.preset_undo["place"]), "name": session.preset_undo["name"]}
                         if session and session.preset_undo else None),
                "result": session.preset_result if session else None,
            },
            # the scenes of the preset on the pedal
            "scenes": {
                # away: the effect a value or switch was made for is not in the chain now (its name)
                "list": [{"name": scene["name"], "cc": scene["cc"],
                          "values": [{"slot": slot, "param": param, "value": value,
                                      "away": scene_away(scene, slot, param)}
                                     for (slot, param), value in sorted(scene["values"].items())],
                          "effects": [{"slot": slot, "on": on, "away": scene_away(scene, slot)}
                                      for slot, on in sorted(scene["effects"].items())]}
                         for scene in (self.scenes.get(patch, []) if patch else [])],
                "active": session.scene_active if session else None,
                "learning": self.scene_learning[1] if self.scene_learning and self.scene_learning[0] == patch else None,
                "capturing": self.capturing[1] if self.capturing and self.capturing[0] == patch else None,
                "seen": (len(session.learning) + len(session.capture_effects)) if session and self.capturing
                and session.learning is not None else 0,
                # what the scene being captured takes so far: the last value of each knob turned, the switches
                "captured": {"values": [{"slot": slot, "param": param, "value": reported[-1]}
                                        for (slot, param), reported in sorted(session.learning.items())],
                             "effects": [{"slot": slot, "on": on} for slot, on in sorted(session.capture_effects.items())]}
                if session and self.capturing and session.learning is not None else {"values": [], "effects": []},
                "max": scene_file.MAX_SCENES,
                "max_name": scene_file.MAX_NAME,
            },
            "synth": None if self.synth is None else {
                "keyboard": bool(session and session.keyboard is not None),
                # the external sequencer: None if none is set up, otherwise whether it is connected
                "external": bool(session and session.external is not None) if self.synth["external"] else None,
                "playable": bool(session and session.playable),
                "expression": bool(session and session.chocolate is not None),
                "effects": [known.name for known in zs.KEY_EFFECTS],
                "slot": session.synth_slot if session else None,
                "effect": effect.name if effect else None,
                "key_param": effect.key_param if effect else None,       # its first Key knob; the others follow
                "key": session.voices[0].key_sent if session and session.voices else None,
                "voices": [None if voice.note is None else note_name(voice.note) for voice in session.voices]
                if session else [],
                "bend_range": self.synth["bend_range"],
                # every knob of every synth effect a controller can be assigned to
                "knobs": [{"name": synth_controls.name(known, label), "effect": known.name, "label": label,
                           "knob": knob, "rest": rest, "full": full,
                           "cc": self.controls.get(synth_controls.name(known, label))}
                          for known in zs.KEY_EFFECTS for label, (knob, rest, full) in known.controls.items()],
                "learning": self.control_learning,
                "arp": dict(self.arp_settings, on=self.arp_on, cc=self.controls.get(arpeggiator.SWITCH),
                            **(session.arp_state() if session else {"note": None, "clock_bpm": None,
                                                                    "clock_running": True})),
                "seq": {"on": self.seq_on, "cc": self.controls.get(sequencer.SWITCH),
                        "pattern": sequencer.text(self.seq_steps), "steps": len(self.seq_steps),
                        "max_steps": sequencer.MAX_STEPS, "step": session.seq_step() if session else None,
                        "recording": None if self.recording is None else sequencer.text(self.recording)},
                "totals": dict(session.key_totals) if session else None,
                "latency_ms": round(statistics.median(1000 * seconds for seconds in session.key_latencies), 1)
                if session and session.key_latencies else None,
            },
            "totals": dict(session.totals) if session else None,
            "latency_ms": round(statistics.median(latencies), 1) if latencies else None,
            "learning": learning,
            "mappings": [{"key": f"{p[0]}/{p[1]}", "display": display(p),
                          "targets": [stored_mapping(mapping) for mapping in mappings]}
                         for p, mappings in sorted(self.mappings.items())],
            "learned": {key: self._choices(parse_key(key)) for key in self.measurements.patches},
            "limits": {"targets": MAX_TARGETS, "assignments": MAX_ASSIGNMENTS, "effects": MAX_EFFECTS, "params": MAX_PARAMS,
                       "value": UNLEARNED_MAX, "per_bank": PATCHES_PER_BANK},
            "events": list(EVENTS),
        }
        with self._lock:
            self._snapshot = state

    def _pi_wlan(self) -> Optional[dict]:
        """The Pi's own WLAN as config.yaml has it; export.py may add it while this bridge runs, so
        the file is read again whenever it has changed."""
        if self.fixed:
            return None                             # the Pi's interface is open to its network: not there
        try:
            changed = CONFIG.stat().st_mtime
            if changed != self._pi_wlan_seen[0]:
                self._pi_wlan_seen = (changed, (yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}).get("pi"))
        except (OSError, yaml.YAMLError, AttributeError):
            return None
        return self._pi_wlan_seen[1] or None

    def _devices(self) -> dict:
        """For the interface: the name each device is looked for under (as config.yaml has it),
        the port that was found for it, and the ports there are to choose from. keyboard and
        external are None while config.yaml has no synth section. Only ports named like an MS Plus
        pedal are offered as the pedal: the bridge sends to that port, and to nothing else by a
        slip of the mouse. Any other name can still be entered in config.yaml."""
        session, (inputs, outputs), synth = self.session, self.port_names, self.cfg.get("synth")
        pedals = [name for name in inputs if name in outputs and PEDAL_PORTS.lower() in name.lower()]
        others = [name for name in inputs if name not in pedals and not NOT_A_DEVICE.search(name)]
        return {
            "pedal": {"name": self.cfg["ports"]["zoom"], "port": session.zoom_in_name if session else None,
                      "choices": pedals},
            "keyboard": {"name": synth.get("keyboard") or "", "port": session.keyboard_name if session else None,
                         "choices": others} if isinstance(synth, dict) else None,
            "external": {"name": synth.get("external") or "", "port": session.external_name if session else None,
                         "choices": others} if isinstance(synth, dict) else None,
            "expression": {"name": self.cfg["ports"].get("chocolate") or "",
                           "port": session.chocolate_name if session else None, "choices": others},
        }

    def _choices(self, patch: tuple[int, int]) -> list:
        """The learned parameters of a preset that the interface offers to choose from."""
        return [{"slot": entry["slot"], "param": entry["param"], "min": entry["min"], "max": entry["max"]}
                for entry in self.measurements.for_patch(patch)]

    # --- Changes from the interface (run in the main loop through call()) ---

    def learn(self, action: str) -> Optional[dict]:
        """start: record knob movements on the pedal. stop: take the most-moved knob for the
        current preset. cancel: discard."""
        self._changeable()
        session = self.session
        if session is None or not session.ready or session.patch is None:
            raise UserError("The MS-60B+ is not connected or has not reported a preset yet.")
        if self.capturing is not None:
            raise UserError("A scene is being captured – apply or cancel that first.")
        if action == "start":
            session.learning, self.param_learning = {}, None
            for target in session.targets:
                target.pending = None
            return None
        seen, session.learning = session.learning, None
        if action == "cancel":
            return None
        if seen is None:
            raise UserError("Learning was cancelled because the preset changed.")
        if not seen:
            raise UserError("No knob movement received. Turn the knob on the MS-60B+ and try again.")
        (slot, param), values = max(seen.items(), key=lambda item: len(item[1]))
        if len(set(values)) < 2:
            raise UserError("The knob was barely moved. Please turn it once from minimum to maximum.")
        limits = self.measurements.add(session.patch, slot, param, values)
        current = self.mappings.get(session.patch, ())
        known = any((mapping.slot, mapping.param) == (slot, param) for mapping in current)
        added = not known and len(current) < MAX_ASSIGNMENTS and sum(
            mapping.cc is None for mapping in current) < MAX_TARGETS
        if added:   # a free place: assign it right away, with the whole learned range
            self.mappings[session.patch] = current + (Mapping(slot, param, limits["min"], limits["max"]),)
            self._store()
        elif known:   # already assigned: keep its range inside what the pedal just reported
            self.mappings[session.patch] = tuple(
                self._fit(mapping, limits) if (mapping.slot, mapping.param) == (slot, param) else mapping
                for mapping in current)
            self._store()
        elif self.fixed:   # only the range: on the computer measurements.json holds it already
            self._write_settings()
        say(f"Learned for preset {display(session.patch)}: effect {slot + 1}, parameter {param - 1}, "
            f"{limits['min']}–{limits['max']}")
        return {"slot": slot, "param": param, "min": limits["min"], "max": limits["max"],
                "added": added, "known": known}

    def devices(self, data: dict) -> None:
        """From the interface: the ports of the pedal ("pedal"), the keyboard ("keyboard"), the
        external sequencer ("external") and the expression controller ("expression"), each the
        name of a port; the last three may be "" for none, all of them too (the pedal alone, its
        preset edited in the interface). They are written to config.yaml, and the connection is
        made anew."""
        self._changeable()
        chosen = {device: data[device] for device in ("pedal", "keyboard", "external", "expression")
                  if device in data}
        if not chosen or not all(isinstance(name, str) and len(name) <= 200 for name in chosen.values()):
            raise UserError("Choose a port.")
        ports, synth = self.cfg["ports"], self.cfg.get("synth")
        if ("keyboard" in chosen or "external" in chosen) and not isinstance(synth, dict):
            raise UserError("config.yaml has no “synth:” section – copy it from config.example.yaml to set up "
                            "a keyboard.")
        pedal = chosen.get("pedal", ports["zoom"])
        expression = chosen.get("expression", ports.get("chocolate") or "")
        keyboard = chosen.get("keyboard", (synth or {}).get("keyboard") or "")
        external = chosen.get("external", (synth or {}).get("external") or "")
        if not pedal:
            raise UserError("The pedal needs a port.")
        if keyboard and keyboard == external:
            raise UserError("The keyboard and the external sequencer need two different ports.")
        if expression and self.cfg["expression"]["cc"] is None:
            raise UserError("The expression pedal has not been measured yet: enter its port under “ports:” in "
                            "config.yaml and run `python probe.py cc` (README, “First-time setup”).")
        now = {("ports", "zoom"): ports["zoom"], ("ports", "chocolate"): ports.get("chocolate") or "",
               ("synth", "keyboard"): (synth or {}).get("keyboard") or "",
               ("synth", "external"): (synth or {}).get("external") or ""}
        wanted = {("ports", "zoom"): pedal, ("ports", "chocolate"): expression, ("synth", "keyboard"): keyboard,
                  ("synth", "external"): external}
        values = {setting: name for setting, name in wanted.items() if name != now[setting]}
        if not values:
            return                                    # as it is already
        cfg = dict(self.cfg, ports=dict(ports, zoom=pedal, chocolate=expression))
        if isinstance(synth, dict):
            cfg["synth"] = dict(synth, keyboard=keyboard, external=external)
        try:
            synth_now = read_synth(cfg)               # a keyboard needs the rest of its section to be right
            text = None if self.fixed else set_config_values(CONFIG.read_text(encoding="utf-8"), values,
                                                             add=[("synth", "external")])
        except SystemExit as refused:
            raise UserError(str(refused)) from None
        except KeyError as missing:
            raise UserError(f"config.yaml has no setting {missing.args[0]} – compare it with "
                            "config.example.yaml.") from None
        old_cfg = self.cfg
        self.cfg = cfg
        try:
            if self.fixed:
                self._write_settings()
            else:
                zs.write_whole(CONFIG, text)
        except UserError:
            self.cfg = old_cfg
            raise
        had_keyboard, self.synth, self.control_learning = self.synth is not None, synth_now, None
        self.effect_learning = self.scene_learning = self.capturing = None
        if synth_now is not None and not had_keyboard:
            self._load_keyboard_settings(self.cfg if self.fixed else None)
        devices = [("pedal", pedal), ("keyboard", keyboard), ("expression controller", expression)]
        if external or "external" in chosen:          # not named where there has never been one
            devices.insert(2, ("external sequencer", external))
        say("Devices: " + ", ".join(f"{device} “{name}”" if name else f"no {device}" for device, name in devices)
            + " – connecting anew")
        self._close_session()                         # the main loop opens the ports again at once
        self.status = "Connecting…"

    def synth_control(self, action: str, knob: Optional[str] = None) -> None:
        """learn: the next controller moved on the keyboard will set this knob ("KeyFM/Index") or
        switch ("Arp", "Seq"). cancel: stop waiting for one. clear: no controller sets it any more."""
        self._changeable()
        if self.synth is None:
            raise UserError("No keyboard synth is set up in config.yaml.")
        if action == "cancel":
            self.control_learning = None
            return
        if not synth_controls.known(knob):         # anything can arrive over HTTP
            raise UserError("Unknown knob.")
        if action == "clear":
            self.control_learning = None
            if self.controls.pop(knob, None) is not None:
                self._store_controls()
        elif action == "learn":
            if self.session is None or not self.session.playable:
                raise UserError("The keyboard is not connected.")
            self.control_learning, self.effect_learning, self.scene_learning = knob, None, None
            self.param_learning = None
        else:
            raise UserError("Unknown action.")

    def parameter(self, data: dict) -> None:
        """From the interface, for a parameter of the preset on the pedal ("slot", "param"):
        "learn" its controller, the next knob, slider, wheel or pedal moved on any controller (the
        expression pedal gives it back to that); "cancel" stops waiting. A parameter not assigned
        yet comes with its assignment ("new": min, max, invert, curve, name) and is assigned once
        its controller has been moved."""
        self._changeable()
        action = data.get("action")
        if action == "cancel":
            self.param_learning = None
            return
        if action != "learn":
            raise UserError("Unknown action.")
        session = self.session
        if session is None or not session.ready or session.patch is None:
            raise UserError("The MS-60B+ is not connected or has not reported a preset yet.")
        if session.learning is not None:
            raise UserError("A knob of the pedal is being learned – apply or cancel that first.")
        slot, param, new = data.get("slot"), data.get("param"), data.get("new")
        if not all(isinstance(number, int) and not isinstance(number, bool) for number in (slot, param)):
            raise UserError("Say which parameter.")      # "0" would never match the mapping made of it
        current = self.mappings.get(session.patch, ())
        if any((mapping.slot, mapping.param) == (slot, param) for mapping in current):
            new = None
        elif isinstance(new, dict):
            try:
                new = to_mapping(dict(new, slot=slot, param=param, cc=None))
            except (KeyError, TypeError, ValueError):
                raise UserError("The details are incomplete.") from None
            # with a controller no other parameter has: whether there is room, and the range is right
            unused = next(number for number in range(1, 120) if number not in RESERVED_CONTROLS
                          and all(mapping.cc != number for mapping in current))
            problem = self.check(session.patch, current + (replace(new, cc=unused),))
            if problem:
                raise UserError(problem)
        else:
            raise UserError("This preset has no such parameter.")
        self.param_learning = (session.patch, slot, param, new)
        self.param_result = None
        self.control_learning = self.effect_learning = self.scene_learning = None

    def learned_param(self, number: Optional[int]) -> None:
        """The session saw a controller move while a parameter was waiting for one (None: the
        expression pedal). What the controller switched so far it does no longer."""
        waiting, self.param_learning = self.param_learning, None
        if waiting is None or (number is not None and number in RESERVED_CONTROLS):
            return
        patch, slot, param, new = waiting
        current = self.mappings.get(patch, ())
        changed = tuple(replace(mapping, cc=number) if (mapping.slot, mapping.param) == (slot, param) else mapping
                        for mapping in current) + ((replace(new, cc=number),) if new else ())
        problem = self.check(patch, changed)
        if problem:
            say(f"Not learned: {problem}")
            self.param_result = {"ok": False, "text": f"Not learned: {problem}"}
            return
        if number is not None:                      # a switch with it is taken away; in other presets
            self._free(number, scenes_of=patch, keep_parameters=True)   # their scenes keep it
        self.mappings[patch] = changed
        self._store()
        self._store_controls()
        mapping = next(mapping for mapping in changed if (mapping.slot, mapping.param) == (slot, param))
        say(f"Preset {display(patch)}: {controller_name(number)} now sets {mapping.describe()}")
        self.param_result = {"ok": True, "text": f"{controller_name(number).capitalize()} now sets {effect_label(mapping.effect, mapping.param) if mapping.effect else mapping.describe()}"}

    def learned_control(self, number: int) -> None:
        """The session saw a controller move while a knob was waiting for one."""
        knob = self.control_learning
        if knob is None or number in RESERVED_CONTROLS:
            return
        self.control_learning = None
        if not synth_controls.known(knob):        # a knob of a build read since: none to set
            return
        self._free(number, knob)                  # one controller sets one knob of each synth effect
        self.controls[knob] = number
        self._store_controls()
        effect, name = synth_controls.split(knob)
        if effect is None:
            name = "Arpeggiator" if knob == arpeggiator.SWITCH else "Step sequencer"
            say(f"{name}: controller {number} now switches it on and off")
            return
        say(f"{effect.name}: controller {number} now sets {name}")

    def _free(self, number: int, keep: Optional[str] = None, scenes_of: Optional[tuple] = None,
              keep_parameters: bool = False) -> None:
        """Whatever the controller did so far, it does no longer; with keep, a knob that is to get
        it, the knobs it sets on the other synth effects stay; with scenes_of, a preset one of whose
        scenes is to get it, the scenes of the other presets keep it. A knob of a synth effect and
        parameters of a preset can share one controller (it moves them together): with keep or
        keep_parameters the parameters it sets stay, and with keep_parameters its synth knobs."""
        for other in [name for name, known in self.controls.items() if known == number and not (
                keep_parameters and synth_controls.split(name)[0] is not None)
                      and (keep is None or synth_controls.clash(keep, name))]:
            del self.controls[other]
        if keep is None or synth_controls.split(keep)[0] is None:    # a switch: parameters let go of it
            if not keep_parameters:
                self._drop_controller(number, scenes_of)
        for other in [slot for slot, switch in self.switches.items() if switch["cc"] == number]:
            del self.switches[other]
        for patch, entries in self.scenes.items():
            for scene in entries:
                if scene["cc"] == number and scenes_of in (None, patch):
                    scene["cc"] = None

    def _drop_controller(self, number: int, only: Optional[tuple] = None) -> None:
        """The parameters controller number set (in the preset only, or in all) are set by nothing
        any more: their assignments are taken away."""
        for patch in [patch for patch in self.mappings if only in (None, patch)]:
            kept = tuple(mapping for mapping in self.mappings[patch] if mapping.cc != number)
            if len(kept) != len(self.mappings[patch]):
                if kept:
                    self.mappings[patch] = kept
                else:
                    del self.mappings[patch]
                self._store()

    def preset(self, data: dict) -> None:
        """From the interface: "select" a preset on the pedal ("bank", "program"), or "save" the one
        on the pedal into its own place or another ("bank", "program")."""
        action, place = data.get("action"), (data.get("bank"), data.get("program"))
        if action == "undo":
            place = (0, 0)
        if not all(isinstance(number, int) and not isinstance(number, bool) for number in place):
            raise UserError("Say which preset.")
        session = self.session
        if session is None or not session.ready:
            raise UserError("The MS-60B+ is not connected.")
        if action == "select":
            refused = session.select_preset(*place, time.monotonic())
        elif action == "save":
            refused = session.save_preset(*place, time.monotonic())
        elif action == "undo":
            refused = session.undo_save(time.monotonic())
        else:
            raise UserError("Unknown action.")
        if refused:
            raise UserError(refused)

    def kept_for(self, place: tuple) -> dict:
        """What the bridge keeps for a preset: its parameters, scenes and learned ranges."""
        return {"mappings": self.mappings.get(place), "scenes": copy.deepcopy(self.scenes.get(place)),
                "learned": self.measurements.preset(place)}

    def restore_kept(self, place: tuple, kept: dict) -> None:
        """Puts back what kept_for gave, for a preset written back as it was."""
        self._changeable()
        for held, value in ((self.mappings, kept["mappings"]), (self.scenes, kept["scenes"])):
            if value:
                held[place] = value
            else:
                held.pop(place, None)
        self.measurements.set_preset(place, kept["learned"])
        self._store()
        self._store_controls()

    def copy_preset(self, source: tuple, target: tuple) -> None:
        """A preset stored into another place on the pedal: what the bridge keeps for it (its
        parameters, scenes and learned ranges) is that place's too; what that place had goes, with
        the preset it was made for."""
        self._changeable()
        if self.mappings.get(source):
            self.mappings[target] = self.mappings[source]
        else:
            self.mappings.pop(target, None)
        if self.scenes.get(source):
            self.scenes[target] = copy.deepcopy(self.scenes[source])
        else:
            self.scenes.pop(target, None)
        self.measurements.copy(source, target)
        self._store()
        self._store_controls()

    def effects(self, data: dict) -> None:
        """From the interface, for the effect in "slot": "learn", the next controller moved on any
        of the controllers will switch it (“cancel” stops waiting); "clear", none does any more;
        "press", how its controller switches (true: every press switches it over, false: its
        value does, on from 64); "switch", switch the effect now ("on": true or false, left out:
        over to the other); "change", put another effect there ("effect": its id, one of the
        state's choices; refused if it does not fit into the DSP beside the others, over_budget);
        "move", move it to another place ("to": its new place); "knob", set
        one of its knobs ("param", "value"; "effect": the id of the effect meant), as the editor does;
        "delete", take it out of the chain ("effect": the id of the one meant; those after it move
        up); "add", an empty place at the
        end of the chain (or before effect "to"), without "slot"."""
        action = data.get("action")
        if action == "cancel":
            self.effect_learning = None
            return
        session = self.session
        if action == "read_pedal":                  # the list of the pedal's files, for the effects to choose
            if session is None or not session.ready:
                raise UserError("The MS-60B+ is not connected.")
            refused = session.read_pedal(time.monotonic())
            if refused:
                raise UserError(refused)
            return
        if action == "add":                         # an empty place, at the end or before effect "to"
            at = data.get("to")
            if at is not None and not (isinstance(at, int) and not isinstance(at, bool)):
                raise UserError("Say where to.")
            if session is None or not session.ready:
                raise UserError("The MS-60B+ is not connected.")
            refused = session.add_effect(at, time.monotonic())
            if refused:
                raise UserError(refused)
            return
        slot = data.get("slot")
        if not (isinstance(slot, int) and not isinstance(slot, bool) and 0 <= slot < effect_switch.MAX_EFFECTS):
            raise UserError("Unknown effect.")
        if action == "switch":
            if data.get("on") is not None and not isinstance(data["on"], bool):
                raise UserError("on must be true or false.")
            if session is None or not session.ready:
                raise UserError("The MS-60B+ is not connected.")
            refused = session.switch_effect(slot, data.get("on"), time.monotonic())
            if refused:
                raise UserError(refused)
            return
        if action == "knob":
            param, value = data.get("param"), data.get("value")
            try:
                made_for = effect_id(data.get("effect"))
            except ValueError:
                made_for = None
            if made_for is None or not all(isinstance(number, int) and not isinstance(number, bool)
                                           for number in (param, value)):
                raise UserError("Say which effect, knob and value.")
            if session is None or not session.ready:
                raise UserError("The MS-60B+ is not connected.")
            refused = session.edit_knob(slot, param, value, made_for)
            if refused:
                raise UserError(refused)
            return
        if action == "move":
            to = data.get("to")
            if not (isinstance(to, int) and not isinstance(to, bool)):
                raise UserError("Say where to.")
            if session is None or not session.ready:
                raise UserError("The MS-60B+ is not connected.")
            refused = session.move_effect(slot, to, time.monotonic())
            if refused:
                raise UserError(refused)
            return
        if action == "delete":
            try:
                shown = effect_id(data.get("effect"))
            except ValueError:
                shown = None
            if shown is None:
                raise UserError("Say which effect.")
            if session is None or not session.ready:
                raise UserError("The MS-60B+ is not connected.")
            refused = session.delete_effect(slot, shown, time.monotonic())
            if refused:
                raise UserError(refused)
            return
        if action == "change":
            try:
                chosen = effect_id(data.get("effect"))
            except ValueError:
                chosen = None
            if not chosen:
                raise UserError("Choose an effect.")
            if session is None or not session.ready:
                raise UserError("The MS-60B+ is not connected.")
            refused = session.change_effect(slot, chosen, time.monotonic())
            if refused:
                raise UserError(refused)
            return
        self._changeable()
        if action == "learn":
            if not all(self.approvals.is_approved(kind) for kind in cs.SWITCH_KINDS):
                raise UserError("Switching effects is not approved (`python probe.py approve switch_effect query_patch`).")
            if session is None or not session.ready or not session.controllers:
                raise UserError("No controller is connected.")
            self.effect_learning, self.control_learning, self.scene_learning = slot, None, None
            self.param_learning = None
        elif action == "clear":
            self.effect_learning = None
            if self.switches.pop(slot, None) is not None:
                self._store_controls()
        elif action == "press":
            if slot not in self.switches or not isinstance(data.get("press"), bool):
                raise UserError("Learn a controller for the effect first.")
            self.switches[slot]["press"] = data["press"]
            self._store_controls()
        else:
            raise UserError("Unknown action.")

    def rename_preset(self, data: dict) -> None:
        """From the interface: the preset on the pedal gets the "name" given, 1 to 28 of the
        characters a Zoom pedal offers for names."""
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise UserError("Enter a name.")
        name = name.strip()
        if len(name) > zs.NAME_LENGTH:
            raise UserError(f"A name has at most {zs.NAME_LENGTH} characters.")
        wrong = sorted(set(name) - zs.NAME_CHARACTERS)
        if wrong:
            raise UserError(f"Not possible in a name: {' '.join(wrong)} – letters, digits, spaces and "
                            "! # $ % & ' ( ) + , - . ; = @ [ ] ^ _ ` { } ~ are.")
        session = self.session
        if session is None or not session.ready:
            raise UserError("The MS-60B+ is not connected.")
        refused = session.rename(name, time.monotonic())
        if refused:
            raise UserError(refused)

    def scene(self, data: dict) -> None:
        """From the interface, for the preset on the pedal: "add" a scene ("name"); for the scene
        "index": "remove", "rename" ("name"), "recall", "learn" a controller for it ("cancel"
        stops waiting, "clear" takes it away), "capture" its values (the knobs turned and the
        effects switched on the pedal from now on), then "apply" or "discard", and "drop" one
        of its values ("slot", "param") or effects ("slot")."""
        action = data.get("action")
        session = self.session
        if action == "cancel":
            self.scene_learning = None
            return
        if session is None or not session.ready or session.patch is None:
            raise UserError("The MS-60B+ is not connected or has not reported a preset yet.")
        patch = session.patch
        entries = self.scenes.setdefault(patch, [])
        name = data.get("name")
        named = isinstance(name, str) and 0 < len(name.strip()) <= scene_file.MAX_NAME
        if action == "add":
            self._changeable()
            if not named:
                raise UserError(f"A scene needs a name of at most {scene_file.MAX_NAME} characters.")
            if len(entries) >= scene_file.MAX_SCENES:
                raise UserError(f"A preset can have {scene_file.MAX_SCENES} scenes.")
            entries.append({"name": name.strip(), "cc": None, "values": {}, "effects": {}, "chain": {}})
            self._store_controls()
            return
        index = data.get("index")
        if not (isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(entries)):
            raise UserError("This preset has no such scene.")
        chosen = entries[index]
        if action == "recall":
            refused = session.recall_scene(index, time.monotonic())
            if refused:
                raise UserError(refused)
            return
        self._changeable()
        if action == "remove":
            del entries[index]
            if self.capturing is not None:
                session.learning = None
            self.scene_learning = self.capturing = None
            session.scene_active = None
        elif action == "rename":
            if not named:
                raise UserError(f"A scene needs a name of at most {scene_file.MAX_NAME} characters.")
            chosen["name"] = name.strip()
        elif action == "learn":
            self.scene_learning, self.effect_learning, self.control_learning = (patch, index), None, None
            self.param_learning = None
            return
        elif action == "clear":
            self.scene_learning, chosen["cc"] = None, None
        elif action == "capture":
            if session.learning is not None:
                raise UserError("Something is being learned already – finish that first.")
            session.learning, session.capture_effects, self.capturing = {}, {}, (patch, index)
            for target in session.targets:
                target.pending = None           # nothing is sent against the knobs being turned
            return
        elif action in ("apply", "discard"):
            if self.capturing != (patch, index) or session.learning is None:
                if action == "discard":
                    self.capturing = None
                    return
                raise UserError("This scene is not being captured.")
            seen = session.learning
            if action == "discard":
                session.learning, self.capturing = None, None
                return
            # refused: the capture goes on (turn or switch something, take a value out), as it was
            if not seen and not session.capture_effects:
                raise UserError("Nothing was turned or switched on the pedal.")
            known = chosen.setdefault("chain", {})
            chain = session.chain_ids or []
            touched = {slot for slot, _ in seen} | set(session.capture_effects)
            # the scene's entries for an effect that is not in its place now make way – before they
            # are counted
            replaced = {slot for slot in touched if known.get(slot) is not None and slot < len(chain)
                        and chain[slot] and not same_effect(known[slot], chain[slot])}
            values = {place: value for place, value in chosen["values"].items() if place[0] not in replaced}
            values.update({place: reported[-1] for place, reported in seen.items()})
            if len(values) > scene_file.MAX_VALUES:
                raise UserError(f"A scene holds at most {scene_file.MAX_VALUES} values.")
            session.learning, self.capturing = None, None
            for (slot, param), reported in seen.items():      # what the pedal reported counts as learned
                self.measurements.add(patch, slot, param, reported)
            for slot in touched:
                if slot in replaced:
                    chosen["effects"].pop(slot, None)
                if slot < len(chain) and chain[slot]:
                    known[slot] = chain[slot]
            chosen["values"] = values
            chosen["effects"].update(session.capture_effects)
            say(f"Scene “{chosen['name']}”: {len(values)} value{'s' if len(values) != 1 else ''}"
                + (f", {len(chosen['effects'])} effect switch{'es' if len(chosen['effects']) != 1 else ''}"
                   if chosen["effects"] else ""))
        elif action == "drop":
            slot, param = data.get("slot"), data.get("param")
            number = lambda value: isinstance(value, int) and not isinstance(value, bool)
            if number(slot) and number(param) and (slot, param) in chosen["values"]:
                del chosen["values"][(slot, param)]
            elif number(slot) and param is None and slot in chosen["effects"]:
                del chosen["effects"][slot]
            else:
                raise UserError("The scene does not set that.")
        else:
            raise UserError("Unknown action.")
        self._store_controls()

    def learned_scene(self, number: int) -> None:
        """The session saw a controller move while a scene was waiting for one."""
        waiting, self.scene_learning = self.scene_learning, None
        if waiting is None or number in RESERVED_CONTROLS:
            return
        patch, index = waiting
        if index >= len(self.scenes.get(patch, [])):
            return
        self._free(number, scenes_of=patch)     # in other presets it may recall a scene of theirs as well
        self.scenes[patch][index]["cc"] = number
        self._store_controls()
        say(f"Scene “{self.scenes[patch][index]['name']}”: controller {number} now recalls it")

    def learned_switch(self, number: int) -> None:
        """The session saw a controller move while an effect was waiting for one."""
        slot = self.effect_learning
        if slot is None or number in RESERVED_CONTROLS:
            return
        self.effect_learning = None
        press = self.switches.get(slot, {}).get("press", False)    # how it switches stays as it was set
        self._free(number)
        self.switches[slot] = {"cc": number, "press": press}
        self._store_controls()
        say(f"Effect {slot + 1}: controller {number} now switches it on and off")

    def _changeable(self) -> None:
        if self.fixed and self.folder is None:
            raise UserError("The settings come from a settings file; change them on the computer "
                            "they were exported from.")

    def _current_settings(self) -> dict:
        """The settings file as it would be written now, from what this bridge holds."""
        config = {key: value for key, value in self.cfg.items() if key in cs.SECTIONS}
        config["mappings"] = {f"{patch[0]}/{patch[1]}": [stored_mapping(mapping) for mapping in mappings]
                              for patch, mappings in sorted(self.mappings.items()) if mappings}
        return cs.build(config, self.measurements.patches, self.cfg["approvals"], self.controls,
                        self.cfg.get("exported_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
                        self.arp_settings, self.seq_steps, self.switches, self.scenes, self.measurements.effects)

    def _write_settings(self) -> None:
        """A bridge without screen, changed from its interface: everything into its settings file,
        which the next start reads (written whole and then renamed, as export.py writes it). The
        same content is not written again, so the previous file stays the one before the change.
        A change that cannot be kept is undone: what the bridge holds goes back to the file."""
        settings = self._current_settings()
        problems = cs.validate(settings)
        if problems:
            self._restore_settings()
            raise UserError("Not kept: " + " ".join(problems))
        text = cs.dump(settings, from_interface=True)
        try:
            if (self.folder / cs.FILE_NAME).read_text(encoding="utf-8") == text:
                return
        except (OSError, UnicodeDecodeError):
            pass
        try:
            cs.store(text, self.folder)
        except OSError as error:
            self._restore_settings()
            raise UserError(f"The settings file could not be written ({error.strerror or error}).") from None
        self._kept, self.cfg = copy.deepcopy(settings), copy.deepcopy(settings)

    def _restore_settings(self) -> None:
        """Back to the settings file as last written: assignments, learned ranges, the keyboard's
        controllers, arpeggiator and pattern, the switches of the effects and the scenes."""
        kept = self._kept
        self.cfg = copy.deepcopy(kept)
        self.mappings = read_mappings(kept.get("mappings"))
        self.measurements.patches = cs.Measurements(kept.get("learned") or {}).patches
        self._load_keyboard_settings(kept)
        self.switches = effect_switch.read(kept.get("effects"))
        self.scenes = scene_file.read(kept.get("scenes"))
        if self.session is not None:
            self.session.refresh()

    def set_pedal_effects(self, effect_ids) -> None:
        """Exactly these effects can be chosen from now on: the pedal's own list of files."""
        self.measurements.replace_effects(effect_ids)
        if self.fixed and self.folder is not None:
            try:
                self._write_settings()
            except UserError as error:
                say(f"WARNING: {error}")

    def synths_from_builds(self, names: Optional[set] = None) -> list:
        """The synth effects (those of these names; None: all) again, as the builds described now
        (zs.use_installed_synths): played from now on. The keyboard's controllers go along under the
        names those builds have (Pluck's Bright and Tone); one to a knob a build no longer has is
        left out, as at the start, and a knob waiting for its controller that the build no longer
        has waits no longer. Returns the names of the effects whose build changed."""
        before = {effect.name: effect for effect in zs.KEY_EFFECTS}
        zs.use_installed_synths(names)
        changed = [effect.name for effect in zs.KEY_EFFECTS if effect is not before.get(effect.name)]
        if changed:
            self.controls = synth_controls.read(self.controls)
            if self.control_learning is not None and not synth_controls.known(self.control_learning):
                self.control_learning = None
            if self.session is not None:
                self.session.refresh()
        return changed

    def found_effects(self, effect_ids) -> bool:
        """Effects the pedal has (in a preset it reported, or in its list of files): they can be
        chosen from now on. On the computer measurements.json keeps them at once; a bridge without
        screen whose settings can be changed writes them into its file. Returns whether any was new."""
        if not self.measurements.found(effect_ids):
            return False
        if self.fixed and self.folder is not None:
            try:
                self._write_settings()
            except UserError as error:
                say(f"WARNING: {error}")
        return True

    def switch_arp(self, on: bool) -> None:
        """The arpeggiator on or off, from the interface or a controller of the keyboard. On, it
        takes over from the sequencer."""
        self._switch(arp=on, seq=self.seq_on and not on)

    def switch_seq(self, on: bool) -> None:
        """The step sequencer on or off, in the same way. On, it takes over from the arpeggiator."""
        self._switch(arp=self.arp_on and not on, seq=on)

    def _switch(self, arp: bool, seq: bool) -> None:
        if (arp or seq) and self.recording is not None:
            self._end_recording(keep=True)            # “On” right after playing the steps: that is the pattern
        if (arp, seq) == (self.arp_on, self.seq_on):
            return
        changed = [(on, name) for name, was, on in (("Arpeggiator", self.arp_on, arp),
                                                    ("Step sequencer", self.seq_on, seq)) if on != was]
        for on, name in sorted(changed):              # the one that goes off is named first
            say(f"{name} {'on' if on else 'off'}")
        self.arp_on, self.seq_on = arp, seq
        if self.session is not None:
            self.session.switch_arp(time.monotonic())

    def arp(self, data: dict) -> None:
        """From the interface: switch the arpeggiator ("on": true or false) and change its settings."""
        if self.synth is None:
            raise UserError("No keyboard synth is set up in config.yaml.")
        changes = {key: value for key, value in data.items() if key != "on"}
        if "on" in data and not isinstance(data["on"], bool):
            raise UserError("on must be true or false.")
        if changes:
            self._changeable()
            problems = arpeggiator.problems(changes)
            if problems:
                raise UserError(" ".join(problem.removeprefix("arp: ") for problem in problems))
            before = dict(self.arp_settings)
            self.arp_settings.update(changes)
            self._store_controls()
        if "on" in data and data["on"] != self.arp_on:
            self.switch_arp(data["on"])
        elif changes and (self.arp_on or self.seq_on) and self.session is not None:
            self.session.arp_changed(time.monotonic(), before)

    def seq(self, data: dict) -> None:
        """From the interface: switch the step sequencer ("on": true or false), give it a pattern
        written as text ("pattern"), or record one from the keyboard ("record": start, then rest,
        tie and back between the keys, and stop or cancel)."""
        if self.synth is None:
            raise UserError("No keyboard synth is set up in config.yaml.")
        if "on" in data and not isinstance(data["on"], bool):
            raise UserError("on must be true or false.")
        if "pattern" in data:
            self._changeable()
            if not isinstance(data["pattern"], str):
                raise UserError("The pattern must be a text.")
            try:
                steps = sequencer.parse(data["pattern"])
            except ValueError as error:
                raise UserError(str(error)) from None
            self.recording = None                     # what was being recorded is replaced, too
            self._set_pattern(steps)
        if "record" in data:
            self._record(data["record"])
        if "on" in data:
            self.switch_seq(data["on"])

    def _record(self, action) -> None:
        self._changeable()
        if action == "start":
            if self.session is None or not self.session.playable:
                raise UserError("The keyboard is not connected.")
            self._switch(arp=False, seq=False)        # the keys play as usual, so every step is heard
            self.recording = []
        elif action not in ("rest", "tie", "back", "stop", "cancel"):
            raise UserError("Unknown action.")
        elif self.recording is None:
            raise UserError("No pattern is being recorded.")
        elif action == "back":
            del self.recording[-1:]
        elif action in ("stop", "cancel"):
            self._end_recording(keep=action == "stop")
        else:
            self.record_step(action)                  # "rest" or "tie", as the pattern holds them

    def record_step(self, step) -> None:
        """While a pattern is recorded: a key was pressed, or a rest or a tie added in the
        interface. That is its next step; with the last step it can have, recording ends."""
        if self.recording is None:
            return
        self.recording.append(step)
        if len(self.recording) >= sequencer.MAX_STEPS:
            self._end_recording(keep=True)

    def _end_recording(self, keep: bool) -> None:
        recorded, self.recording = self.recording, None
        if not keep:
            return
        if not any(isinstance(step, int) for step in recorded):
            say("Step sequencer: no note was played, the pattern stays as it was")
            return
        self._set_pattern(recorded)
        say(f"Step sequencer: recorded {len(recorded)} step{'s' if len(recorded) != 1 else ''}")

    def _set_pattern(self, steps: list) -> None:
        self.seq_steps[:] = steps                     # in place: the session's sequencer plays it from its next step
        self._store_controls()

    def _store_controls(self) -> None:
        """Writes controls.json: the controllers, the arpeggiator's settings that are not the
        defaults, the sequencer's pattern, and the switches of the effects."""
        self._changeable()
        self._remember_effects()
        stored = {"controls": self.controls}
        if arpeggiator.changed(self.arp_settings):
            stored["arp"] = arpeggiator.changed(self.arp_settings)
        if self.seq_steps:
            stored["seq"] = {"steps": self.seq_steps}
        if self.switches:
            stored["effects"] = effect_switch.stored(self.switches)
        if scene_file.stored(self.scenes):
            stored["scenes"] = scene_file.stored(self.scenes)
        if self.fixed:
            self._write_settings()
        else:
            zs.write_whole(CONTROLS, json.dumps(stored, indent=2) + "\n")
        if self.session is not None:
            self.session.refresh()

    @staticmethod
    def _fit(mapping: Mapping, limits: dict) -> Mapping:
        """The mapping narrowed to the learned range; the whole range if nothing is left."""
        low, high = max(mapping.min, limits["min"]), min(mapping.max, limits["max"])
        if low > high:
            low, high = limits["min"], limits["max"]
        return replace(mapping, min=low, max=high)

    def save_mapping(self, key: str, data: dict) -> None:
        self._changeable()
        try:
            patch = parse_key(key)
            mappings = tuple(to_mapping(entry) for entry in data["targets"])
        except (KeyError, TypeError, ValueError):
            raise UserError("The details are incomplete.") from None
        problem = self.check(patch, mappings)
        if problem:
            raise UserError(problem)
        self.mappings[patch] = mappings
        self._forget_waiting()
        self._store()

    def _forget_waiting(self) -> None:
        """A parameter waiting for its controller that is no longer assigned waits no longer."""
        if self.param_learning is not None:
            patch, slot, param, new = self.param_learning
            if new is None and not any((mapping.slot, mapping.param) == (slot, param)
                                       for mapping in self.mappings.get(patch, ())):
                self.param_learning = None

    def delete_mapping(self, key: str) -> None:
        self._changeable()
        try:
            self.mappings.pop(parse_key(key), None)
        except ValueError:
            raise UserError("Unknown preset.") from None
        self._forget_waiting()
        self._store()

    def export_data(self, key: Optional[str] = None) -> dict:
        """Assignments together with learned ranges, for all presets or for one. With all presets
        the keyboard's part goes along: its controllers, the arpeggiator's settings and the
        sequencer's pattern. So do the switches of the effects, if there are any."""
        try:
            chosen = sorted(self.mappings) if key is None else [parse_key(key)]
        except ValueError:
            raise UserError("Unknown preset.") from None
        if any(patch not in self.mappings for patch in chosen):
            raise UserError("There is no assignment for this preset.")
        data = {
            "format": EXPORT_FORMAT,
            "firmware": self.cfg["zoom"]["firmware"],
            "presets": {f"{patch[0]}/{patch[1]}": {
                "display": display(patch),
                "targets": [stored_mapping(mapping) for mapping in self.mappings[patch]],
                "learned": self._choices(patch),
            } for patch in chosen},
        }
        if key is None and self.synth is not None:
            data["keyboard"] = {"controls": dict(self.controls), "arp": arpeggiator.changed(self.arp_settings),
                                "seq": {"steps": list(self.seq_steps)}}
        if key is None and self.switches:
            data["effects"] = effect_switch.stored(self.switches)
        if key is None and scene_file.stored(self.scenes):
            data["scenes"] = scene_file.stored(self.scenes)
        return data

    def settings_file(self) -> str:
        """For the interface: the settings file for a bridge without screen, as export.py writes
        it to the Pi's card. Everything is in it, the devices and the approvals too. Not from the Pi
        itself: its interface is open to its network, and the file holds the password of its own WLAN."""
        if self.fixed:
            raise UserError("The Pi keeps its settings file on its card: make one on the computer, under "
                            "Devices there (Export all here carries what was changed on the Pi).")
        self._changeable()
        settings = gather_settings()
        problems = cs.validate(settings)
        if problems:
            raise UserError("The settings are not complete yet: " + " ".join(problems))
        return cs.dump(settings)

    @staticmethod
    def value_labels(effect_id: int) -> dict:
        """For the interface: the texts the pedal shows for the values of an effect's knobs, {param
        (as a string): [text per value 0..highest]}, empty if none or the effect is not known. In
        the bridge's own loop: the table changes there when the pedal's files are read."""
        return {str(param): texts for param, texts in effect_names.labels(effect_id).items()}

    @staticmethod
    def _keyboard_problem(keyboard) -> Optional[str]:
        """What is wrong with the keyboard's part of an import file, None if nothing."""
        if not isinstance(keyboard, dict) or set(keyboard) - {"controls", "arp", "seq"}:
            return "the keyboard's part is not as an export writes it"
        found = synth_controls.problems(keyboard.get("controls", {}))
        if any("assigned twice" in problem for problem in found):
            return "one controller of the keyboard is assigned twice"
        if found:
            return "a controller of the keyboard is assigned to something unknown, or has no usable number"
        found = arpeggiator.problems(keyboard.get("arp", {})) + sequencer.problems(keyboard.get("seq", {}))
        return found[0].rstrip(".") if found else None

    def import_data(self, data) -> list:
        """Applies an export file: its presets replace existing ones with the same number, and
        its ranges count as learned. If it holds the keyboard's part, that replaces the
        controllers, the arpeggiator's settings and the sequencer's pattern; if it holds the
        switches of the effects, those replace the ones there are. Returns the display numbers
        of the presets."""
        self._changeable()
        if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT or not isinstance(
                data.get("presets"), dict) or not (data["presets"] or "keyboard" in data or "effects" in data
                                                   or "scenes" in data):
            raise UserError("That is not an MS Plus Bridge export file.")
        if "scenes" in data and scene_file.problems(data["scenes"]):
            raise UserError("Scenes: " + scene_file.problems(data["scenes"])[0].removeprefix("scenes: ").rstrip(".")
                            + ".")
        keyboard, switches = data.get("keyboard"), data.get("effects")
        if keyboard is not None and self._keyboard_problem(keyboard):
            raise UserError(f"Keyboard settings: {self._keyboard_problem(keyboard)}.")
        if switches is not None:
            found = effect_switch.problems(switches)
            taken = set((keyboard or {}).get("controls", {}).values()) if isinstance(keyboard, dict) else set()
            if not found and isinstance(switches, dict) and taken & {switch["cc"] for switch in switches.values()}:
                found = ["one controller both switches an effect and is assigned on the keyboard."]
            if found:
                raise UserError(f"Effect switches: {found[0].removeprefix('effects: ').rstrip('.')}.")
        presets = []
        for key, preset in data["presets"].items():
            try:
                patch = parse_key(key)
                learned = [(int(e["slot"]), int(e["param"]), int(e["min"]), int(e["max"]))
                           for e in preset["learned"]]
                mappings = tuple(to_mapping(entry) for entry in preset["targets"])
            except (KeyError, TypeError, ValueError):
                raise UserError(f"Preset {key}: the file is incomplete or damaged.") from None
            for slot, param, low, high in learned:
                if not (zs.is_effect_param(slot, param) and 0 <= slot <= 0x7F and param <= 0x7F
                        and 0 <= low <= high <= 0x3FFF):
                    raise UserError(f"Preset {display(patch)}: invalid range in the file.")
            problem = self.check(patch, mappings, {(slot, param): {"min": low, "max": high}
                                                   for slot, param, low, high in learned})
            if problem:
                raise UserError(f"Preset {display(patch)}: {problem}")
            presets.append((patch, learned, mappings))
        # Nothing is changed before this point. Everything is taken first and then written at once:
        # a bridge without screen checks the whole, and what it cannot keep goes back as a whole
        # (not the presets kept and the rest refused).
        done = []
        for patch, learned, mappings in presets:
            for slot, param, low, high in learned:
                self.measurements.add(patch, slot, param, [low, high])
            self.mappings[patch] = mappings
        if presets:
            done.append(f"preset {', '.join(display(patch) for patch, _, _ in presets)}")
        if keyboard is not None:
            self._switch(arp=False, seq=False)        # they would play on with half the old settings
            self.recording = self.control_learning = None
            self.controls = synth_controls.read(keyboard.get("controls", {}))
            self.arp_settings.update(arpeggiator.read(keyboard.get("arp")))
            self.seq_steps[:] = sequencer.read(keyboard.get("seq"))
            if switches is None:                      # one controller sets one thing: the file's wins
                self.switches = {slot: switch for slot, switch in self.switches.items()
                                 if switch["cc"] not in self.controls.values()}
            done.append("the keyboard's controllers, the arpeggiator's settings and the sequencer's pattern")
        if switches is not None:
            self.effect_learning = None
            self.switches = effect_switch.read(switches)
            for number in [switch["cc"] for switch in self.switches.values()]:
                for other in [name for name, known in self.controls.items() if known == number]:
                    del self.controls[other]          # one controller sets one thing
            done.append("the switches of the effects")
        if "scenes" in data:
            self.scene_learning = self.capturing = None
            self.scenes = scene_file.read(data["scenes"])
            done.append("the scenes")
        if presets:
            self._store()
        if keyboard is not None or switches is not None or "scenes" in data:
            self._store_controls()
        for text in done:
            say(f"Imported: {text}")
        return [display(patch) for patch, _, _ in presets]

    def follow_effects(self, patch: tuple, chain: list, fresh: bool) -> bool:
        """The chain of the preset on the pedal, as the pedal reported it. What was assigned to an
        effect that is in another place now goes with it: its parameters, the values and switches
        of the scenes, the learned ranges. An effect in its own place keeps that place; one that
        is in more than one other place, or whose new place another one of the same kind holds,
        stays where it was and waits (Session.refresh sends nothing there). What was assigned
        before effects were remembered learns its effect from the preset when it has just been
        selected (fresh), as it was saved. Returns whether anything changed."""
        mappings = self.mappings.get(patch, ())
        scenes = self.scenes.get(patch, [])
        changed = False
        if fresh:
            effect_at = lambda slot: chain[slot] if slot < len(chain) and chain[slot] else None
            adopted = tuple(replace(mapping, effect=effect_at(mapping.slot)) if mapping.effect is None else mapping
                            for mapping in mappings)
            changed, mappings = adopted != mappings, adopted
            for scene in scenes:
                known = scene.setdefault("chain", {})
                for slot in {slot for slot, _ in scene["values"]} | set(scene["effects"]):
                    if slot not in known and effect_at(slot):
                        known[slot], changed = effect_at(slot), True
        assigned = {(mapping.slot, mapping.effect) for mapping in mappings if mapping.effect} | {
            (slot, effect) for scene in scenes for slot, effect in scene.get("chain", {}).items()}
        home = {slot for slot, effect in assigned if slot < len(chain) and same_effect(chain[slot], effect)}
        wanted: dict = {}                                   # new place → the (place, effect) that want it
        for slot, effect in assigned:
            new = place_of(slot, effect, chain)
            if new is not None and new != slot and new not in home:
                wanted.setdefault(new, set()).add((slot, effect))
        moves = {origin: new for new, origins in wanted.items() if len(origins) == 1 for origin in origins}
        # an assignment that moves in without a learned range of its own: a range left in its new
        # place by an effect that is gone (an assignment of it made way, or was removed) goes
        arriving = {(moves[(mapping.slot, mapping.effect)], mapping.param) for mapping in mappings
                    if (mapping.slot, mapping.effect) in moves
                    and self.measurements.get(patch, mapping.slot, mapping.param) is None}
        if moves:
            changed = True
            for _, new in sorted(moves.items()):
                say(f"Preset {display(patch)}: {effect_label(chain[new])} is effect {new + 1} now – "
                    "what is assigned to it goes along")
            moved = [replace(mapping, slot=moves[(mapping.slot, mapping.effect)])
                     if (mapping.slot, mapping.effect) in moves else mapping for mapping in mappings]
            # an assignment of an effect that is not there makes way for one that moved into its place
            here = {(mapping.slot, mapping.param) for mapping in moved
                    if place_of(mapping.slot, mapping.effect, chain) == mapping.slot}
            mappings = tuple(mapping for mapping in moved
                             if place_of(mapping.slot, mapping.effect, chain) == mapping.slot
                             or (mapping.slot, mapping.param) not in here)
            for mapping in moved:
                if mapping not in mappings:
                    say(f"Preset {display(patch)}: {effect_label(mapping.effect, mapping.param)} is assigned no "
                        "longer – another effect took its place")
            for scene in scenes:
                known = scene.get("chain", {})
                new_of = lambda slot: moves.get((slot, known[slot]), slot) if slot in known else slot
                present = {slot for slot in known if new_of(slot) != slot
                           or place_of(slot, known[slot], chain) == slot}
                landed = {new_of(slot) for slot in present}
                keep = lambda slot: slot in present or slot not in landed     # an absent effect's entries make way
                for slot in sorted(slot for slot in known if not keep(slot)):
                    say(f"Preset {display(patch)}: scene “{scene['name']}” holds nothing for {effect_label(known[slot])} "
                        "any longer – another effect took its place")
                scene["values"] = {(new_of(slot), param): value for (slot, param), value in scene["values"].items()
                                   if keep(slot)}
                scene["effects"] = {new_of(slot): on for slot, on in scene["effects"].items() if keep(slot)}
                scene["chain"] = {new_of(slot): effect for slot, effect in known.items() if keep(slot)}
            places: dict = {}
            for (slot, _), new in moves.items():
                places[slot] = new if places.get(slot, new) == new else None   # two effects from one place: unclear
            self.measurements.move(patch, {slot: new for slot, new in places.items() if new is not None})
            self.measurements.forget(patch, arriving)
        if not changed:
            return False
        if mappings:
            self.mappings[patch] = mappings
        else:                                       # scenes only: no empty entry for the preset
            self.mappings.pop(patch, None)
        try:            # kept; with settings that cannot be changed, only until the next start
            self._store()
            self._store_controls()
        except UserError:
            pass
        return True

    def _remember_effects(self) -> None:
        """The assignments and scene entries of the preset on the pedal that do not know their
        effect yet get the one there now: what was just made is made for what the pedal shows."""
        session = self.session
        if session is None or session.patch is None or session.chain_ids is None:
            return
        chain, patch = session.chain_ids, session.patch
        effect_at = lambda slot: chain[slot] if slot < len(chain) and chain[slot] else None
        if patch in self.mappings:
            self.mappings[patch] = tuple(replace(mapping, effect=effect_at(mapping.slot))
                                         if mapping.effect is None else mapping for mapping in self.mappings[patch])
        for scene in self.scenes.get(patch, []):
            known = scene.setdefault("chain", {})
            for slot in {slot for slot, _ in scene["values"]} | set(scene["effects"]):
                if slot not in known and effect_at(slot):
                    known[slot] = effect_at(slot)

    def _store(self) -> None:
        """Rewrites the `mappings:` section at the end of config.yaml and applies it at once."""
        self._changeable()
        self._remember_effects()
        if self.fixed:
            self._write_settings()
            if self.session is not None:
                self.session.refresh()
            return
        head, found, _ = CONFIG.read_text(encoding="utf-8").partition("\nmappings:")
        if not found:
            raise UserError("config.yaml has no “mappings:” section.")
        lines = []
        for patch, mappings in sorted(self.mappings.items()):
            if not mappings:
                continue
            lines.append(f'  "{patch[0]}/{patch[1]}":   # preset {display(patch)}')
            lines += ["    - " + yaml.safe_dump(stored_mapping(mapping), default_flow_style=True, sort_keys=False,
                                                allow_unicode=True, width=1000).strip()
                      for mapping in mappings]
        block = "\nmappings:\n" + "\n".join(lines) + "\n" if lines else "\nmappings: {}\n"
        zs.write_whole(CONFIG, head + block)
        if self.session is not None:
            self.session.refresh()


def interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


def _wait_forever() -> None:
    while True:
        time.sleep(0.5)


def safe_mode(led: status_led.StatusLed, reason: str, news=None) -> bool:
    """Nothing is sent to the pedal any more; the light says so until the service is stopped, or,
    with news(), until a USB stick with other settings is put in. True: there is one."""
    say(f"SAFE MODE – nothing is sent to the pedal: {reason}")
    led.show("safe")
    try:
        if news is None:
            _wait_forever()
            return False
        while not news():
            time.sleep(STICK_SECONDS)
        return True
    except KeyboardInterrupt:
        return False


def headless_settings(folder: Path, stick: Optional[str], refused: set) -> tuple:
    """What a bridge without screen runs with: (settings, where they are from, what was wrong, the
    text of the file in use). stick is the text of the settings file on a USB stick, if one is in.
    If it is valid and not what the folder holds already, it is stored there, and the stick can
    be removed. One that breaks a rule is said once (refused remembers it) and not used."""
    try:
        stored = (folder / cs.FILE_NAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        stored = None
    wrong = []
    if stick is not None and stick != stored and stick not in refused:
        settings, problems = cs.read(stick)
        if settings is not None:
            try:
                cs.store(stick, folder)
                say("Settings taken over from the USB stick – it can be removed")
            except OSError as error:
                say(f"Settings taken from the USB stick, but they could not be stored ({error.strerror or error}): "
                    "leave the stick in")
            return settings, "stick", [], stick
        refused.add(stick)
        wrong = [f"on the USB stick, not used: {problem}" for problem in problems]
    settings, source, problems = cs.load_with_fallback(folder)
    return settings, source, wrong + problems, stored if source == "current" else None


class Current:
    """The bridge the interface talks to on a computer without screen: a new one is made whenever
    other settings come in, and while there is none (safe mode) the interface says why."""

    def __init__(self) -> None:
        self.bridge: Optional[Bridge] = None
        self.reason = "Starting…"

    def state(self) -> dict:
        bridge = self.bridge                         # it may be replaced while this runs (another thread)
        return bridge.state() if bridge else {"unavailable": self.reason, "events": list(EVENTS)}

    def call(self, function, timeout: float = 3.0) -> dict:
        bridge = self.bridge
        if bridge is None:
            return {"error": f"The bridge is not running: {self.reason}"}
        return bridge.call(function, timeout)

    def __getattr__(self, name: str):
        """What the interface asks of the bridge (only inside call(), in the bridge's own loop)."""
        bridge = self.bridge
        if bridge is None:
            raise UserError(f"The bridge is not running: {self.reason}")
        return getattr(bridge, name)


INSTALLED = Path("/var/lib/expression-bridge/installed")    # written by pi/install.sh when it has worked


def pi_update(settings: dict, script: Path = ROOT / "pi" / "install.sh", installed: Path = INSTALLED,
              folder: Optional[Path] = None) -> bool:
    """On the Raspberry Pi: the card was written anew and its program needs more of the system
    than was set up (a higher LEVEL in pi/install.sh), or the Pi's own WLAN changed. Then
    install.sh runs again, as a unit of its own outside this service's read-only view of the
    system; it restarts the bridge when it is done. True if it was started. folder: where the
    settings files are, which install.sh reads the WLAN from."""
    if not sys.platform.startswith("linux") or os.geteuid() != 0 or not script.exists() or not installed.exists():
        return False
    wanted = re.search(r"^LEVEL=(\d+)", script.read_text(encoding="utf-8"), re.M)
    marker = installed.read_text(encoding="utf-8", errors="replace")
    level = re.search(r"^level=(\d+)", marker, re.M)
    wlan = re.search(r"^wlan=(\S*)", marker, re.M)
    up_to_date = (int(level.group(1)) if level else 1) >= (int(wanted.group(1)) if wanted else 1)
    if up_to_date and (wlan.group(1) if wlan else "") == cs.wlan_fingerprint(settings):
        return False
    if up_to_date and folder is not None and cs.wlan_fingerprint(cs.load_with_fallback(folder)[0]) != \
            cs.wlan_fingerprint(settings):
        # install.sh notes the WLAN of the file on the card: for settings that are not that one (a
        # USB stick's that could not be stored) it would run again at every start
        say("The Pi's own WLAN of these settings is set up once they are stored on the card")
        return False
    say("The program on the card needs more of the system – setting it up (pi/install.sh), the bridge "
        "starts again when that is done")
    try:
        subprocess.run(["systemd-run", "--unit", "expression-bridge-update", "--collect", "--no-block",
                        "/bin/bash", str(script), "--update"], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as error:
        say(f"The setup could not be started: {error}")
        return False
    return True


def run_headless(folder: Path, stick: Optional[Path] = None, listen: bool = False) -> None:
    """The bridge on a computer without screen: settings from the file export.py wrote into the
    folder (or the previous one), everything logged to the console (the service's journal), and
    the status light instead of a display. stick: where a USB stick is mounted; a settings file
    on it is taken over, at the start or whenever it is put in. listen: the interface is reached
    over the network (a phone in the same WLAN), and what is changed there is written into the
    settings file; without it there is no interface and nothing is stored."""
    global HEADLESS
    HEADLESS = True
    zs.setup_logging(None, console=True)
    led = status_led.StatusLed.find()
    in_use, refused = None, set()
    current = Current()
    if listen:
        import ui
        try:
            say(f"Interface: {ui.serve(current, {'port': LISTEN_PORT}, listen=True)}")
        except OSError as error:
            say(f"The interface cannot be started ({error.strerror or error}) – the bridge runs without it")

    def other_settings() -> bool:
        text = cs.stick_text(stick)
        return text is not None and text != in_use and text not in refused

    news = other_settings if stick is not None else None
    try:
        if led.problem:
            say(f"The status light cannot be used: {led.problem}")
        while True:
            led.show("waiting")
            # the effects read from the pedal before (written by export.py or by this bridge), so that
            # the settings are checked against the synth builds it plays
            effect_names.use_cache(folder / cs.EFFECTS_NAME, keep=False)
            zs.use_installed_synths()
            settings, source, problems, in_use = headless_settings(folder, cs.stick_text(stick), refused)
            for problem in problems:
                say(f"Settings: {problem}")
            stopped = None if settings is not None else "there is no usable settings file"
            if stopped is None:
                pi_update(settings, folder=folder)
                try:
                    bridge = Bridge(settings, folder if listen else None, folder / cs.EFFECTS_NAME)
                    bridge.led, bridge.fallback, bridge.news = led, source == "previous", news
                    current.bridge = bridge
                    say(f"Settings exported {settings.get('exported_at', 'at an unknown time')}"
                        + (" – this is the PREVIOUS file, the current one is not usable" if bridge.fallback else ""))
                    bridge.run()
                    return
                except NewSettings:
                    say("A USB stick with other settings was put in – starting again with them")
                    continue
                except SystemExit as refusal:        # settings the bridge cannot start with, or the
                    stopped = str(refusal)           # pedal is not the one they are for
                finally:
                    current.bridge = None
            current.reason = f"SAFE MODE – nothing is sent to the pedal: {stopped}"
            if not safe_mode(led, stopped, news):
                return
    finally:
        led.close()


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-ui", action="store_true", help="start without the interface")
    parser.add_argument("--open", action="store_true", help="open the interface in the browser")
    parser.add_argument("--settings", metavar="FOLDER", type=Path,
                        help="run from the settings file in this folder (made by export.py), without interface")
    parser.add_argument("--profile", metavar="NAME",
                        help="use the settings in profiles/NAME/: a second bridge for a second pedal")
    parser.add_argument("--stick", metavar="PLACE", type=Path,
                        help="with --settings: where a USB stick is mounted; a settings file on it is taken over")
    parser.add_argument("--listen", action="store_true",
                        help="the interface for the network as well (a phone or tablet in the same WLAN; also "
                             "`listen: true` under `ui:` in config.yaml); with --settings, changes made there are "
                             "written into the settings file")
    args = parser.parse_args(argv)

    zs.tolerant_console()
    for name in ("SIGTERM", "SIGHUP", "SIGBREAK"):   # also stop cleanly when the window is closed
        if hasattr(signal, name):                    # SIGHUP is missing on Windows, SIGBREAK elsewhere
            signal.signal(getattr(signal, name), interrupt)
    if args.settings is not None:
        run_headless(args.settings, args.stick, args.listen)
        return
    if args.profile is not None:
        use_profile(args.profile)
    bridge = Bridge()
    zs.setup_logging(HOME / "logs", console=False)
    if not args.no_ui:
        import ui
        settings = bridge.cfg.get("ui") or {}
        listen = args.listen or settings.get("listen") is True
        try:
            address = ui.serve(bridge, settings, listen=listen)
        except OSError as error:                     # two bridges cannot share a port
            port = int(settings.get("port", ui.DEFAULT_PORT))
            running = ui.running_bridge(port)
            if running is not None and running.get("profile") == PROFILE:
                # This bridge is running already (started earlier, in another window): its
                # interface is what was asked for, and a second one would find the pedal taken.
                print(f"The bridge is running already – its interface: http://127.0.0.1:{port}", flush=True)
                webbrowser.open(f"http://127.0.0.1:{port}")
                return
            sys.exit(f"The interface cannot be started ({error.strerror or error}). If another bridge is running, "
                     f"give this one a port of its own under “ui:” in {CONFIG}.")
        say(f"Interface: {address}")
        port = int(settings.get("port", ui.DEFAULT_PORT))
        if listen:                                   # for a phone or tablet: by name, or by address
            here = ui.network_address()
            say(f"On a phone or tablet in the same WLAN: {address}" + (f" or http://{here}:{port}" if here else "")
                + " – everyone in this WLAN can use it")
        if args.open:
            webbrowser.open(f"http://127.0.0.1:{port}" if listen else address)
    bridge.run()


if __name__ == "__main__":
    main()
