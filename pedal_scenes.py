"""Pedal scenes: scene A and B that a modified firmware of the MS-60B+ keeps in every preset itself
(its menu "Scenes"; a short press switches between A and B). Each holds the on/off state of chosen
effects 1–6 and, if chosen, single knob values. The bridge reads them, lets them be edited and
writes them back into the preset, then reads them again to see that the pedal holds them.

A scene, as the bridge keeps it:

    {"effects": {slot: on},                              slot 0–5 (effect 1–6), only those chosen
     "values": {(slot, param): (effect id, value)}}     param as sent to the pedal (2 = first knob)

The effect id goes with every value: if another effect is in that place later, the value is not
its knob's (the firmware leaves it). The Key knobs of a synth effect are never part of a scene:
they are played live, by notes.

The messages are not agreed with the firmware yet (docs/PEDAL_SCENES.md). Until they are, COMMAND
stays None: then the bridge sends none of them, recognizes none, and offers its own scenes
(scenes.py). The tests and webmidi/sim_server.py set it to PROVISIONAL for the simulated pedal,
which is the only one that knows that form.
"""
from __future__ import annotations

from typing import Optional

import effect_names
import zoom_sysex as zs

COMMAND: Optional[int] = None   # the agreed command byte after F0 52 00 <ID>; None: not agreed, nothing sent
PROVISIONAL = 0x7D              # the simulated pedal's: a placeholder, never for a real one
QUERY, READ, WRITE = 0x00, 0x01, 0x02
NAMES = ("A", "B")
SLOTS = zs.MAX_SLOTS
VALUE_BYTES = 9                 # slot, effect id (5), param, value (LSB, MSB)
MAX_VALUE = 0x3FFF
# the message kinds, for approval; not in zs.KINDS while the bytes are not agreed (no approval for a real pedal)
KINDS = {
    "query_pedal_scenes": "F0 52 00 <ID> <scene command> 00 F7 (whether the firmware keeps scenes) – not agreed yet",
    "read_pedal_scenes": "F0 52 00 <ID> <scene command> 01 <bank LSB MSB> <program LSB MSB> F7 – not agreed yet",
    "write_pedal_scenes": "F0 52 00 <ID> <scene command> 02 <bank LSB MSB> <program LSB MSB> <scene A> <scene B> F7 "
                          "– not agreed yet",
}
STATUS = {0: "done", 1: "the pedal has no such preset", 2: "the pedal refused the scenes as written",
          3: "the pedal is busy (a preset being saved or edited on it)", 4: "the pedal could not store them"}


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


def build_query(device_id: int) -> bytes:
    return zs._zoom(device_id, COMMAND, QUERY)


def build_read(device_id: int, bank: int, program: int) -> bytes:
    return zs._zoom(device_id, COMMAND, READ, *_place(bank, program))


def build_write(device_id: int, bank: int, program: int, scenes: dict) -> bytes:
    return zs._zoom(device_id, COMMAND, WRITE, *_place(bank, program), *encode(scenes))


def classify(body: bytes) -> Optional[str]:
    """The kind of a message to the pedal with this body (after F0 52 00 <ID>), None if it is none of
    these; raises zs.NotAllowed for one that names no preset of the pedal or holds no scenes."""
    if COMMAND is None or len(body) < 2 or body[0] != COMMAND:
        return None
    if body[1:] == bytes([QUERY]):
        return "query_pedal_scenes"
    if body[1] not in (READ, WRITE) or len(body) < 6:
        raise zs.NotAllowed(f"not a scene message: {zs.to_hex(body)}")
    bank, program = zs.decode_value(body[2], body[3]), zs.decode_value(body[4], body[5])
    if not zs.is_preset(bank, program):
        raise zs.NotAllowed(f"no preset {bank}/{program} on the pedal")
    if body[1] == READ:
        if len(body) != 6:
            raise zs.NotAllowed(f"not a scene message: {zs.to_hex(body)}")
        return "read_pedal_scenes"
    try:
        scenes = decode(body[6:])
    except ValueError as problem:
        raise zs.NotAllowed(f"not scenes to write: {problem}")
    found = problems(scenes)
    if found:
        raise zs.NotAllowed(f"not scenes to write: {found[0]}")
    return "write_pedal_scenes"


def parse(body: Optional[bytes]) -> Optional[tuple]:
    """An answer of the pedal: ("query", version, most values per scene), ("read", status, (bank,
    program), scenes or None) or ("write", status, (bank, program)); None if it is none of these
    (or holds what is not scenes)."""
    if COMMAND is None or not body or len(body) < 2 or body[0] != COMMAND:
        return None
    if body[1] == QUERY and len(body) == 4:
        return "query", body[2], body[3]
    if body[1] not in (READ, WRITE) or len(body) < 7:
        return None
    status, place = body[2], (zs.decode_value(body[3], body[4]), zs.decode_value(body[5], body[6]))
    if body[1] == WRITE:
        return ("write", status, place) if len(body) == 7 else None
    if status:
        return "read", status, place, None
    try:
        return "read", status, place, decode(body[7:])
    except ValueError:
        return None


def key_knob(effect_id: int, param: int) -> bool:
    """Whether that knob is a Key knob of a synth effect: played live, never part of a scene."""
    synth = zs.key_effect(effect_id)
    if synth and synth.key_param <= param < synth.key_param + synth.voices:
        return True
    knob = next((knob for knob in effect_names.knobs(effect_id) if knob["param"] == param), None)
    return bool(knob and zs.KEY_NAME.fullmatch(knob["name"]))


def problems(scenes: dict, most: Optional[int] = None) -> list:
    """What is wrong with scene A and B, as sentences; empty if nothing. A knob of an effect the
    table of names knows must be one of its knobs, with a value up to its highest."""
    found = []
    for name in NAMES:
        scene = scenes.get(name)
        if not isinstance(scene, dict):
            found.append(f"Scene {name} is missing.")
            continue
        if any(not 0 <= slot < SLOTS for slot in scene["effects"]):
            found.append(f"Scene {name} switches an effect beyond 6.")
        if most is not None and len(scene["values"]) > most:
            found.append(f"Scene {name} holds {len(scene['values'])} values, the pedal keeps {most}.")
        for (slot, param), (effect_id, value) in scene["values"].items():
            where = f"Scene {name}, effect {slot + 1}"
            knob = next((knob for knob in effect_names.knobs(effect_id) if knob["param"] == param), None)
            if not 0 <= slot < SLOTS or param < zs.MIN_PARAM or not 0 < effect_id <= 0xFFFFFFFF:
                found.append(f"{where}: not a knob of an effect.")
            elif key_knob(effect_id, param):
                found.append(f"{where}: a Key knob of a synth effect is played, not kept in a scene.")
            elif not 0 <= value <= (knob["max"] if knob else MAX_VALUE) or (effect_names.knobs(effect_id) and not knob):
                found.append(f"{where}, parameter {param - 1}: {value} is not a value of that knob.")
    return found
