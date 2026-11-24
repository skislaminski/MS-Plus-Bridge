"""What a modified firmware of the MS-60B+ keeps and does itself, and the messages the bridge would
use for it (docs/PEDAL_FIRMWARE.md):

- scene A and B in every preset: the on/off state of chosen effects 1–6 (and, if a firmware keeps
  them, single knob values); a short press switches between them, the pedal shows A or B and a
  star when something differs. The pedal reports the active scene.
- expression targets in every preset: up to a few knobs the expression pedal's CC 11 sets on the
  pedal itself, each with its slot, effect id, param, heel and toe.
- notes over USB: the synth effect played by MIDI notes the pedal receives itself.

A scene, as the bridge keeps it:

    {"effects": {slot: on},                              slot 0–5 (effect 1–6), only those chosen
     "values": {(slot, param): (effect id, value)}}     param as sent to the pedal (2 = first knob)

An expression target: (slot, effect id, param, heel, toe). The effect id goes with every value and
target: if another effect is in that place later, it is not that effect's knob (the firmware leaves
it). The Key knobs of a synth effect are never part of either: they are played live, by notes.

None of these messages is agreed with the firmware yet. Until they are, COMMAND stays None: the
bridge sends none of them, recognizes none, and offers its own scenes and assignments as before.
The tests and the simulated pedal set it to PROVISIONAL, which no real pedal may get.
"""
from __future__ import annotations

from typing import Optional

import effect_names
import zoom_sysex as zs

COMMAND: Optional[int] = None   # the agreed command byte after F0 52 00 <ID>; None: not agreed, nothing sent
PROVISIONAL = 0x7D              # the simulated pedal's: a placeholder, never for a real one
QUERY, READ, WRITE, ACTIVE, EXPRESSION, READ_TARGETS, WRITE_TARGETS = range(7)
# what the firmware says it does, in the answer to QUERY
SCENES, REPORTS_ACTIVE, PLAYS_NOTES, EXPRESSION_TARGETS = 1, 2, 4, 8
NAMES = ("A", "B")
ACTIVE_NAMES = {0: None, 1: "A", 2: "B"}       # 0: no scene, or none that clearly holds
SLOTS = zs.MAX_SLOTS
VALUE_BYTES = 9                 # slot, effect id (5), param, value (LSB, MSB)
TARGET_BYTES = 11               # slot, effect id (5), param, heel (LSB, MSB), toe (LSB, MSB)
MAX_VALUE = 0x3FFF
# the message kinds, for approval; not in zs.KINDS while the bytes are not agreed (no approval for a real pedal)
KINDS = {
    "query_pedal_firmware": "F0 52 00 <ID> <command> 00|03|04 F7 (what the firmware does, the active scene, its "
                            "expression targets) – not agreed yet",
    "read_pedal_scenes": "F0 52 00 <ID> <command> 01 <bank LSB MSB> <program LSB MSB> F7 – not agreed yet",
    "write_pedal_scenes": "F0 52 00 <ID> <command> 02 <bank LSB MSB> <program LSB MSB> <scene A> <scene B> F7 "
                          "– not agreed yet",
    "read_pedal_expression": "F0 52 00 <ID> <command> 05 <bank LSB MSB> <program LSB MSB> F7 – not agreed yet",
    "write_pedal_expression": "F0 52 00 <ID> <command> 06 <bank LSB MSB> <program LSB MSB> <count> <targets> F7 "
                              "– not agreed yet",
    "pass_notes": "the keyboard's notes, pitch wheel and sustain to the pedal as they are (no SysEx), when its "
                  "firmware plays the synth itself – not agreed yet",
    "pass_expression": "the expression pedal as CC 11 to the pedal (no SysEx), when its firmware keeps the targets "
                       "itself – not agreed yet",
}
STATUS = {0: "done", 1: "the pedal has no such preset", 2: "the pedal refused them as written",
          3: "the pedal is busy (a preset being saved or edited on it)", 4: "the pedal could not store them"}


# --- Scenes ---

def empty() -> dict:
    return {name: {"effects": {}, "values": {}} for name in NAMES}


def copy(scenes: dict) -> dict:
    return {name: {"effects": dict(scene["effects"]), "values": dict(scene["values"])}
            for name, scene in scenes.items()}


def _place(bank: int, program: int) -> tuple:
    return (*zs.encode_value(bank), *zs.encode_value(program))


def _encode_scene(scene: dict) -> bytes:
    mask = sum(1 << slot for slot in scene["effects"])
    states = sum(1 << slot for slot, on in scene["effects"].items() if on)
    data = bytearray([mask, states, len(scene["values"])])
    for (slot, param), (effect_id, value) in sorted(scene["values"].items()):
        data += bytes([slot, *zs.effect_bytes(effect_id), param, *zs.encode_value(value)])
    return bytes(data)


def _decode_scene(data: bytes, at: int) -> tuple:
    """(scene, where the next begins); raises ValueError if the bytes are not a scene."""
    if len(data) < at + 3:
        raise ValueError("a scene is cut short")
    mask, states, count = data[at:at + 3]
    if mask >> SLOTS or states & ~mask:
        raise ValueError("the switches name an effect beyond 6")
    at += 3
    if len(data) < at + count * VALUE_BYTES:
        raise ValueError("a scene's values are cut short")
    values = {}
    for _ in range(count):
        slot, param, lsb, msb = data[at], data[at + 6], data[at + 7], data[at + 8]
        effect_id = zs.decode_effect(data[at + 1:at + 6])
        if slot >= SLOTS or param < zs.MIN_PARAM or not effect_id or (slot, param) in values:
            raise ValueError("a value of a scene is not one of a knob")
        values[(slot, param)] = (effect_id, zs.decode_value(lsb, msb))
        at += VALUE_BYTES
    return {"effects": {slot: bool(states >> slot & 1) for slot in range(SLOTS) if mask >> slot & 1},
            "values": values}, at


def encode(scenes: dict) -> bytes:
    """Scene A and B as the write message and the pedal's answer to reading hold them."""
    return b"".join(_encode_scene(scenes[name]) for name in NAMES)


def decode(data: bytes) -> dict:
    """Scene A and B from their bytes; raises ValueError if they are anything else."""
    scenes, at = {}, 0
    for name in NAMES:
        scenes[name], at = _decode_scene(data, at)
    if at != len(data):
        raise ValueError("something follows scene B")
    return scenes


# --- Expression targets ---

def encode_targets(targets: list) -> bytes:
    data = bytearray([len(targets)])
    for slot, effect_id, param, heel, toe in targets:
        data += bytes([slot, *zs.effect_bytes(effect_id), param, *zs.encode_value(heel), *zs.encode_value(toe)])
    return bytes(data)


def decode_targets(data: bytes) -> list:
    """The targets from their bytes; raises ValueError if they are anything else."""
    if not data or len(data) != 1 + data[0] * TARGET_BYTES:
        raise ValueError("the targets are cut short or something follows them")
    targets = []
    for at in range(1, len(data), TARGET_BYTES):
        part = data[at:at + TARGET_BYTES]
        slot, effect_id, param = part[0], zs.decode_effect(part[1:6]), part[6]
        if slot >= SLOTS or param < zs.MIN_PARAM or not effect_id:
            raise ValueError("a target is not a knob of an effect")
        targets.append((slot, effect_id, param, zs.decode_value(part[7], part[8]), zs.decode_value(part[9], part[10])))
    return targets


# --- Messages ---

def build_query(device_id: int, what: int = QUERY) -> bytes:
    """A question: what the firmware does (QUERY), the active scene (ACTIVE), its targets (EXPRESSION)."""
    return zs._zoom(device_id, COMMAND, what)


def build_read(device_id: int, bank: int, program: int, what: int = READ) -> bytes:
    return zs._zoom(device_id, COMMAND, what, *_place(bank, program))


def build_write(device_id: int, bank: int, program: int, scenes: dict) -> bytes:
    return zs._zoom(device_id, COMMAND, WRITE, *_place(bank, program), *encode(scenes))


def build_write_targets(device_id: int, bank: int, program: int, targets: list) -> bytes:
    return zs._zoom(device_id, COMMAND, WRITE_TARGETS, *_place(bank, program), *encode_targets(targets))


def classify(body: bytes) -> Optional[str]:
    """The kind of a message to the pedal with this body (after F0 52 00 <ID>), None if it is none of
    these; raises zs.NotAllowed for one that names no preset of the pedal or holds what it should not."""
    if COMMAND is None or len(body) < 2 or body[0] != COMMAND:
        return None
    if body[1:] in (bytes([QUERY]), bytes([ACTIVE]), bytes([EXPRESSION])):
        return "query_pedal_firmware"
    if body[1] not in (READ, WRITE, READ_TARGETS, WRITE_TARGETS) or len(body) < 6:
        raise zs.NotAllowed(f"not a message about the pedal's scenes or targets: {zs.to_hex(body)}")
    bank, program = zs.decode_value(body[2], body[3]), zs.decode_value(body[4], body[5])
    if not zs.is_preset(bank, program):
        raise zs.NotAllowed(f"no preset {bank}/{program} on the pedal")
    if body[1] in (READ, READ_TARGETS):
        if len(body) != 6:
            raise zs.NotAllowed(f"not a message about the pedal's scenes or targets: {zs.to_hex(body)}")
        return "read_pedal_scenes" if body[1] == READ else "read_pedal_expression"
    try:
        found = problems(decode(body[6:])) if body[1] == WRITE else target_problems(decode_targets(body[6:]))
    except ValueError as problem:
        raise zs.NotAllowed(f"not {'scenes' if body[1] == WRITE else 'targets'} to write: {problem}")
    if found:
        raise zs.NotAllowed(f"not {'scenes' if body[1] == WRITE else 'targets'} to write: {found[0]}")
    return "write_pedal_scenes" if body[1] == WRITE else "write_pedal_expression"


def parse(body: Optional[bytes]) -> Optional[tuple]:
    """An answer or report of the pedal, None if it is none of these (or holds what it should not):

        ("query", version, most values per scene, features)
        ("active", (bank, program), "A" | "B" | None, differs)     also sent by the pedal on a press
        ("expression", most targets, controller number)
        ("read", status, (bank, program), scenes or None)          and ("read_targets", …, targets or None)
        ("write", status, (bank, program))                         and ("write_targets", …)
    """
    if COMMAND is None or not body or len(body) < 2 or body[0] != COMMAND:
        return None
    op, data = body[1], body[2:]
    if op == QUERY:
        return ("query", *data) if len(data) == 3 else None
    if op == ACTIVE:
        if len(data) != 6 or data[4] not in ACTIVE_NAMES or data[5] > 1:
            return None
        return "active", (zs.decode_value(data[0], data[1]), zs.decode_value(data[2], data[3])), \
            ACTIVE_NAMES[data[4]], bool(data[5])
    if op == EXPRESSION:
        return ("expression", *data) if len(data) == 2 else None
    if op not in (READ, WRITE, READ_TARGETS, WRITE_TARGETS) or len(data) < 5:
        return None
    status, place = data[0], (zs.decode_value(data[1], data[2]), zs.decode_value(data[3], data[4]))
    name = {READ: "read", WRITE: "write", READ_TARGETS: "read_targets", WRITE_TARGETS: "write_targets"}[op]
    if op in (WRITE, WRITE_TARGETS):
        return (name, status, place) if len(data) == 5 else None
    if status:
        return (name, status, place, None) if len(data) == 5 else None
    try:
        return name, status, place, decode(data[5:]) if op == READ else decode_targets(data[5:])
    except ValueError:
        return None


# --- What may go in ---

def key_knob(effect_id: int, param: int) -> bool:
    """Whether that knob is a Key knob of a synth effect: played live, never part of a scene or target."""
    synth = zs.key_effect(effect_id)
    if synth and synth.key_param <= param < synth.key_param + synth.voices:
        return True
    knob = next((knob for knob in effect_names.knobs(effect_id) if knob["param"] == param), None)
    return bool(knob and zs.KEY_NAME.fullmatch(knob["name"]))


def _knob_problem(where: str, slot: int, effect_id: int, param: int, values) -> Optional[str]:
    """Why a knob of an effect with these values cannot be kept, None if it can. A knob of an effect
    the table of names knows must be one of its knobs, with values up to its highest."""
    known = effect_names.knobs(effect_id)
    knob = next((knob for knob in known if knob["param"] == param), None)
    if not 0 <= slot < SLOTS or param < zs.MIN_PARAM or not 0 < effect_id <= 0xFFFFFFFF:
        return f"{where}: not a knob of an effect."
    if key_knob(effect_id, param):
        return f"{where}: a Key knob of a synth effect is played, not kept."
    wrong = [value for value in values if not 0 <= value <= (knob["max"] if knob else MAX_VALUE)]
    if wrong or (known and not knob):
        return f"{where}, parameter {param - 1}: {(wrong or values)[0]} is not a value of that knob."
    return None


def problems(scenes: dict, most: Optional[int] = None) -> list:
    """What is wrong with scene A and B, as sentences; empty if nothing. most: the values a scene may
    hold (0: the firmware keeps only the switches)."""
    found = []
    for name in NAMES:
        scene = scenes.get(name)
        if not isinstance(scene, dict):
            found.append(f"Scene {name} is missing.")
            continue
        if any(not 0 <= slot < SLOTS for slot in scene["effects"]):
            found.append(f"Scene {name} switches an effect beyond 6.")
        if most is not None and len(scene["values"]) > most:
            found.append(f"Scene {name} holds {len(scene['values'])} values, the pedal keeps "
                         + (f"{most}." if most else "only the switches of the effects."))
        for (slot, param), (effect_id, value) in scene["values"].items():
            problem = _knob_problem(f"Scene {name}, effect {slot + 1}", slot, effect_id, param, (value,))
            if problem:
                found.append(problem)
    return found


def target_problems(targets: list, most: Optional[int] = None) -> list:
    """What is wrong with the expression targets of a preset, as sentences; empty if nothing."""
    found = []
    if most is not None and len(targets) > most:
        found.append(f"{len(targets)} targets, the pedal keeps {most}.")
    if len({(slot, param) for slot, _, param, _, _ in targets}) < len(targets):
        found.append("One knob is a target twice.")
    for slot, effect_id, param, heel, toe in targets:
        problem = _knob_problem(f"Target effect {slot + 1}", slot, effect_id, param, (heel, toe))
        if problem:
            found.append(problem)
    return found
