"""Which controller of the keyboard sets what: a knob of one of the synth effects, or the switch of
the arpeggiator or the step sequencer.

Every synth effect has assignments of its own, so one knob of the keyboard can set Cutoff on
KeySweep and Index on KeyFM: only one synth effect is played at a time. A controller sets at most
one knob of each effect; one that switches the arpeggiator or the sequencer does nothing else. As
kept in controls.json, in the settings file of a bridge without screen and in an export, under
`controls`:

    {"KeySynth/Vib": 1, "KeyFM/Index": 21, "Arp": 14}

A knob named without an effect ("Vib": 1) is how files from before 2026-10-17 kept it: that knob
on every effect that has one. read() turns it into one entry per effect, and an effect under an
earlier name (KeySynF, now KeySweep) into its name of today. An assignment to a knob the effects
no longer have (Mix, the last knob of the builds of 2026-10-20, Level again since those of
2026-10-22; KeyBand's Chord and Oct, gone with its 0.20) is left out, not taken for a mistake;
named without an effect, Oct is that of Pluck. Where the pedal still has a build with Mix (or one
from before 2026-10-20 without Level), that knob is kept and the other left out in the same way.

Settings only: bridge.py listens to the controllers and sends.
"""
from __future__ import annotations

from typing import Optional

import arpeggiator
import sequencer
import zoom_sysex as zs

SWITCHES = (arpeggiator.SWITCH, sequencer.SWITCH)          # what a controller can switch, besides a knob
RESERVED_CONTROLS = (0, 32, 64) + tuple(range(120, 128))   # bank select, sustain, channel mode
# knobs the synth effects had once: Level before 2026-10-20 (and again since 2026-10-22), Mix from
# 2026-10-20 to 2026-10-22, KeyBand's Chord and Oct before its 0.20
REMOVED = {"Level", "Mix", "Chord", "Oct"}


def name(effect: zs.SynthEffect, knob: str) -> str:
    """What an assignment to that knob of that effect is called."""
    return f"{effect.name}/{knob}"


def split(assigned: str) -> tuple[Optional[zs.SynthEffect], str]:
    """(effect, knob) of an assignment, also under an earlier name of the effect, and of the knob
    as the build played calls it (zs.KNOB_RENAMED: Pluck's Bright is its Tone since 0.12, and
    the other way round for an earlier build); (None, the name) for a switch or anything else."""
    effect, slash, knob = assigned.partition("/")
    effect = zs.RENAMED.get(effect, effect)
    effects = {known.name: known for known in zs.KEY_EFFECTS}     # as built at the start (zs.use_installed_synths)
    if not (slash and effect in effects):
        return None, assigned
    found = effects[effect]
    for (owner, earlier), since in zs.KNOB_RENAMED.items():
        other = {earlier: since, since: earlier}.get(knob) if owner == effect else None
        if other is not None and knob not in found.controls and other in found.controls:
            knob = other
    return found, knob


def known(assigned) -> bool:
    """A knob of one of the synth effects or a switch, as it is called today."""
    if not isinstance(assigned, str):
        return False
    effect, knob = split(assigned)
    return knob in effect.controls if effect else assigned in SWITCHES


def removed(assigned) -> bool:
    """An assignment to a knob the synth effects had once (REMOVED) and the builds the bridge goes
    by now do not, in either form."""
    if not isinstance(assigned, str):
        return False
    effect, knob = split(assigned)
    if knob not in REMOVED:
        return False
    return knob not in effect.controls if effect else assigned not in zs.ALL_CONTROLS


def clash(first: str, second: str) -> bool:
    """Whether two assignments may not share a controller: only knobs of two different effects can."""
    one, other = split(first)[0], split(second)[0]
    return one is None or other is None or one is other


def _is_number(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 127 and value not in RESERVED_CONTROLS


def _expand(stored: dict) -> list:
    """(name, number) for every entry, a knob named in the old form once per effect that has it.
    An entry that names the effect comes first and wins over one in the old form."""
    old = lambda key: isinstance(key, str) and key in zs.ALL_CONTROLS
    today = lambda key: name(*split(key)) if isinstance(key, str) and split(key)[0] else key
    named = [(today(key), number) for key, number in stored.items() if not old(key)]
    taken = {key for key, _ in named}
    return named + [(name(effect, key), number) for key, number in stored.items() if old(key)
                    for effect in zs.KEY_EFFECTS if key in effect.controls and name(effect, key) not in taken]


def problems(stored) -> list:
    """What is wrong with stored assignments, as sentences; empty if nothing."""
    if not isinstance(stored, dict):
        return ["controls must list a controller number per name."]
    stored = {key: number for key, number in stored.items() if not removed(key)}
    found = []
    for key, number in stored.items():
        if not (known(key) or isinstance(key, str) and key in zs.ALL_CONTROLS):     # the old form too
            found.append(f"controls: “{key}” is nothing a controller can set on a synth effect.")
        elif not _is_number(number):
            found.append(f"controls: {key} needs a controller number from 0 to 127 that is not reserved.")
    if found:
        return found
    kept: list = []
    for key, number in _expand(stored):
        if any(other_number == number and clash(key, other) for other, other_number in kept):
            return ["controls: one controller is assigned twice."]
        kept.append((key, number))
    return []


def read(stored) -> dict:
    """The assignments to work with, {name: controller number}, names as they are called today.
    Anything that cannot be right is dropped; of two that would share a controller the first stays."""
    controls: dict = {}
    for key, number in _expand(stored) if isinstance(stored, dict) else ():
        if known(key) and _is_number(number) and not any(
                known_number == number and clash(key, other) for other, known_number in controls.items()):
            controls[key] = number
    return controls
