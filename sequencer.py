"""The step sequencer of the keyboard synth: a pattern of notes, rests and ties, and which of them
sounds at each step.

Pattern logic only, no timing: the sequencer runs on the arpeggiator's clock in bridge.py and with
the arpeggiator's rate, gate, clock, tempo and latch, and bridge.py asks next_step() at every step.
The pattern, as kept in controls.json and in the settings file of a bridge without screen:

    steps    up to 64 steps, each a MIDI note number, "rest" (silence for a step) or "tie" (the
             note before it sounds on for another step)

In the interface a pattern is written as text: note names, - for a rest and _ for a tie, as in
"C3 E3 - G3 _". It is played from the key that is down: its first note sounds at that key's pitch
and the others keep their distance from it, so the key the pattern starts with plays it as it was
written.

Whether it plays is not a setting: it is switched on in the interface or with a controller of the
keyboard (SWITCH), and it is off whenever the bridge starts.
"""
from __future__ import annotations

import re
from typing import Optional

REST, TIE = "rest", "tie"
MAX_STEPS = 64
SWITCH = "Seq"          # the name under which a controller of the keyboard switches it on (64–127) and off
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
WRITTEN = {REST: "-", TIE: "_"}                 # how a rest and a tie are written in the interface
NOTE = re.compile(r"([A-Ga-g])([#b]?)(-?\d)")   # C3, F#2, Bb4, C-1


def note_name(note: int) -> str:
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


def _is_note(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 127


def problems(settings) -> list:
    """What is wrong with the sequencer's section of a file, as sentences; empty if nothing."""
    if not isinstance(settings, dict):
        return ["seq must be a section with the pattern."]
    found = [f"seq: unknown setting “{key}”." for key in settings if key != "steps"]
    steps = settings.get("steps", [])
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        found.append(f"seq: steps must be a list of at most {MAX_STEPS} steps.")
    elif not all(_is_note(step) or (isinstance(step, str) and step in WRITTEN) for step in steps):
        found.append(f"seq: every step must be a note from 0 to 127, “{REST}” or “{TIE}”.")
    return found


def read(stored) -> list:
    """The pattern to play: the stored one if it is valid, otherwise none."""
    return list(stored.get("steps", [])) if isinstance(stored, dict) and not problems(stored) else []


def parse(text: str) -> list:
    """A pattern written as text → its steps. Raises ValueError with a sentence for the interface."""
    steps = []
    for word in text.replace(",", " ").split():
        match = NOTE.fullmatch(word)
        if match:
            letter, accidental, octave = match.groups()
            note = (int(octave) + 1) * 12 + NOTE_NAMES.index(letter.upper()) + {"#": 1, "b": -1, "": 0}[accidental]
        else:
            note = next((step for step, sign in WRITTEN.items() if word == sign), None)
        if not (_is_note(note) or note in WRITTEN):
            raise ValueError(f"“{word}” is not a step: write a note like C3 or F#2, {WRITTEN[REST]} for a rest "
                             f"or {WRITTEN[TIE]} for a tie.")
        steps.append(note)
    if len(steps) > MAX_STEPS:
        raise ValueError(f"A pattern has at most {MAX_STEPS} steps; this one has {len(steps)}.")
    return steps


def text(steps: list) -> str:
    """The pattern as it is written in the interface."""
    return " ".join(WRITTEN.get(step) or note_name(step) for step in steps)


class Sequencer:
    """Which step of the pattern sounds next, and at what pitch."""

    def __init__(self, steps: list, low_note: int = 0, high_note: int = 127):
        self.steps = steps                  # shared with the bridge: another pattern applies at the next step
        self.low_note, self.high_note = low_note, high_note     # a note moved outside them is a rest
        self.root: Optional[int] = None     # the key the pattern is played from; kept for Latch
        self.reset()

    def reset(self) -> None:
        """The pattern starts from its first step."""
        self.position = 0                           # the step that comes next
        self.step: Optional[int] = None             # the step that plays, counted from 0; None while it rests
        self.sounding: Optional[int] = None         # the note of that step, for a tie after it

    def pressed(self, note: int) -> None:
        """A key was pressed: the pattern moves there, also when the key is up again before the next step."""
        self.root = note

    def clear(self) -> None:
        """All notes off: no key is kept for Latch either."""
        self.root = None
        self.reset()

    def next_step(self, key: Optional[int], latch: bool) -> Optional[tuple]:
        """The next step as (note, tied). note is None for a rest; tied: the step after it is a
        tie, so the note sounds on into it. key: the key that is down, None if none is; with Latch
        the pattern then plays on from the key pressed last. None when there is nothing to play
        (then it starts over)."""
        root = key if key is not None else self.root if latch else None
        first = next((step for step in self.steps if _is_note(step)), None)
        if root is None or first is None:
            self.reset()
            return None
        self.root = root
        index = self.position % len(self.steps)     # the pattern may have become shorter
        step, after = self.steps[index], self.steps[(index + 1) % len(self.steps)]
        self.position, self.step = index + 1, index
        if step == TIE:
            note = self.sounding
        elif step == REST:
            note = None
        else:
            note = step + root - first
            if not self.low_note <= note <= self.high_note:
                note = None
        self.sounding = note
        return note, note is not None and after == TIE
