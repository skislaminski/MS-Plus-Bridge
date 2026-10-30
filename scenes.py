"""Scenes: sets of parameter values, and effects on or off, that one press of a button recalls.

A scene belongs to a preset, because effect and parameter numbers mean something else in every
preset. As kept in controls.json and in the settings file of a bridge without screen under
`scenes`, by preset ("bank/program", as in the assignments):

    {"9/7": [{"name": "Solo", "cc": 21,
              "values": [{"slot": 1, "param": 2, "value": 30}],
              "effects": {"4": true},
              "chain": {"2": "0A000020", "4": "01000020"}}]}

    name     shown in the interface, at most 24 characters
    cc       the controller that recalls it (a press: a value of 64–127), or left out
    values   the parameters it sets: slot = effect number - 1, param = parameter number + 1
    effects  the effects it switches, by effect number: true = on, false = off
    chain    by effect number, the effect (its id in hex) that was there when the scene was made,
             so that its values and switches go with an effect that moves; left out where not known

Settings only: bridge.py recalls a scene with the same "set parameter" and "switch effect"
messages the expression pedal and the effect switches use, under the same rules.
"""
from __future__ import annotations

import re
from typing import Optional

MAX_SCENES = 8                  # per preset
MAX_VALUES = 8                  # parameters per scene
MAX_NAME = 24
MAX_EFFECTS = 6
MAX_VALUE = 0x3FFF
EFFECT_ID = re.compile(r"[0-9A-Fa-f]{8}")
RESERVED_CONTROLS = (0, 32, 64) + tuple(range(120, 128))   # bank select, sustain, channel mode


def _is_int(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def preset(key) -> Optional[tuple]:
    """"bank/program" → (bank, program), or None if it is not a preset key."""
    bank, _, program = str(key).rpartition("/")
    try:
        patch = (int(bank or 0), int(program))
    except ValueError:
        return None
    return patch if 0 <= patch[0] <= 0x3FFF and 0 <= patch[1] <= 0x7F else None


def problems(stored) -> list:
    """What is wrong with stored scenes, as sentences; empty if nothing."""
    if not isinstance(stored, dict):
        return ["scenes must list the scenes per preset."]
    found = []
    for key, entries in stored.items():
        if preset(key) is None or not isinstance(entries, list) or len(entries) > MAX_SCENES:
            found.append(f"scenes: “{key}” must be a preset with at most {MAX_SCENES} scenes.")
            continue
        numbers = []
        for entry in entries:
            where = f"scenes: preset {key}"
            if not isinstance(entry, dict) or set(entry) - {"name", "cc", "values", "effects", "chain"}:
                found.append(f"{where} has an entry that is not a scene.")
                continue
            values, effects = entry.get("values", []), entry.get("effects", {})
            if not isinstance(entry.get("name"), str) or not 0 < len(entry["name"]) <= MAX_NAME:
                found.append(f"{where}: a scene needs a name of at most {MAX_NAME} characters.")
            if "cc" in entry and (not _is_int(entry["cc"], 0, 127) or entry["cc"] in RESERVED_CONTROLS):
                found.append(f"{where}: a scene needs a controller number from 0 to 127 that is not reserved.")
            elif "cc" in entry:
                numbers.append(entry["cc"])
            if not (isinstance(values, list) and len(values) <= MAX_VALUES and all(
                    isinstance(value, dict) and set(value) == {"slot", "param", "value"}
                    and _is_int(value["slot"], 0, MAX_EFFECTS - 1) and _is_int(value["param"], 2, 0x7F)
                    and _is_int(value["value"], 0, MAX_VALUE) for value in values)):
                found.append(f"{where}: a scene holds at most {MAX_VALUES} values, each with slot, param and value.")
            elif len({(value["slot"], value["param"]) for value in values}) < len(values):
                found.append(f"{where}: a scene sets one parameter twice.")
            if not (isinstance(effects, dict) and all(
                    str(number).isdecimal() and 1 <= int(number) <= MAX_EFFECTS and isinstance(on, bool)
                    for number, on in effects.items())):
                found.append(f"{where}: effects must say true or false for effects 1 to {MAX_EFFECTS}.")
            chain = entry.get("chain", {})
            if not (isinstance(chain, dict) and all(
                    str(number).isdecimal() and 1 <= int(number) <= MAX_EFFECTS
                    and isinstance(effect, str) and EFFECT_ID.fullmatch(effect) for number, effect in chain.items())):
                found.append(f"{where}: chain must name an effect id (8 hex digits) for effects 1 to {MAX_EFFECTS}.")
        if len(set(numbers)) < len(numbers):
            found.append(f"scenes: preset {key}: one controller recalls two scenes.")
    return found


def read(stored) -> dict:
    """The scenes to work with: {(bank, program): [{"name", "cc" (or None), "values":
    {(slot, param): value}, "effects": {slot: on}}]}. Stored ones that cannot be right as a
    whole are not used."""
    if not stored or problems(stored):
        return {}
    return {preset(key): [{"name": entry["name"], "cc": entry.get("cc"),
                           "values": {(value["slot"], value["param"]): value["value"]
                                      for value in entry.get("values", [])},
                           "effects": {int(number) - 1: on for number, on in entry.get("effects", {}).items()},
                           "chain": {int(number) - 1: int(effect, 16)
                                     for number, effect in entry.get("chain", {}).items()}}
                          for entry in entries]
            for key, entries in stored.items() if entries}


def stored(scenes: dict) -> dict:
    """The scenes as they are kept in a file."""
    found = {}
    for patch, entries in sorted(scenes.items()):
        if not entries:
            continue
        found[f"{patch[0]}/{patch[1]}"] = [dict(
            {"name": scene["name"]}, **({"cc": scene["cc"]} if scene["cc"] is not None else {}),
            values=[{"slot": slot, "param": param, "value": value}
                    for (slot, param), value in sorted(scene["values"].items())],
            effects={str(slot + 1): on for slot, on in sorted(scene["effects"].items())},
            **({"chain": {str(slot + 1): f"{effect:08X}" for slot, effect in sorted(scene["chain"].items())}}
               if scene.get("chain") else {})) for scene in entries]
    return found
