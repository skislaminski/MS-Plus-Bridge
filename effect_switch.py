"""Switching the effects of the chain on and off from a MIDI controller.

Each of the pedal's six effect places can be given a controller: a footswitch of the expression
controller, a button of the keyboard or a pad of the external sequencer. The assignments, as kept
in controls.json and in the settings file of a bridge without screen under `effects`, by the
number of the effect in the chain:

    {"1": {"cc": 80}, "3": {"cc": 81, "press": true}}

    cc     the controller number, 0–127 (not bank select, sustain or a channel mode message)
    press  false (left out): the value switches, on at 64–127 and off below, for a switch that
           sends both itself. true: every press (a value of 64–127) switches the effect over,
           for a switch that sends a value only while it is held down.

Settings only: bridge.py listens to the controllers and sends, zoom_sysex.py builds the message.
"""
from __future__ import annotations

MAX_EFFECTS = 6                                            # the places in the chain of an MS-60B+
RESERVED_CONTROLS = (0, 32, 64) + tuple(range(120, 128))   # bank select, sustain, channel mode


def _is_int(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def problems(stored) -> list:
    """What is wrong with stored effect switches, as sentences; empty if nothing."""
    if not isinstance(stored, dict):
        return ["effects must list a controller per effect."]
    found, numbers, effects = [], [], []
    for key, switch in stored.items():
        if not (isinstance(key, (str, int)) and not isinstance(key, bool) and str(key).isdecimal()
                and 1 <= int(key) <= MAX_EFFECTS):
            found.append(f"effects: “{key}” is not an effect from 1 to {MAX_EFFECTS}.")
            continue
        effects.append(int(key))
        if not isinstance(switch, dict) or set(switch) - {"cc", "press"}:
            found.append(f"effects: effect {key} must hold cc and, if wanted, press.")
        elif not _is_int(switch.get("cc"), 0, 127) or switch["cc"] in RESERVED_CONTROLS:
            found.append(f"effects: effect {key} needs a controller number from 0 to 127 that is not reserved.")
        elif not isinstance(switch.get("press", False), bool):
            found.append(f"effects: effect {key}: press must be true or false.")
        else:
            numbers.append(switch["cc"])
    if len(set(numbers)) < len(numbers):
        found.append("effects: one controller switches two effects.")
    if len(set(effects)) < len(effects):
        found.append("effects: one effect is listed twice.")
    return found


def read(stored) -> dict:
    """The switches to work with, {slot (effect number - 1): {"cc": number, "press": bool}}.
    Stored ones that cannot be right as a whole are not used."""
    if not stored or problems(stored):
        return {}
    return {int(key) - 1: {"cc": switch["cc"], "press": switch.get("press", False)}
            for key, switch in sorted(stored.items(), key=lambda item: int(item[0]))}


def stored(switches: dict) -> dict:
    """The switches as they are kept in a file: by effect number, press only where it is on."""
    return {str(slot + 1): dict({"cc": switch["cc"]}, **({"press": True} if switch["press"] else {}))
            for slot, switch in sorted(switches.items())}
