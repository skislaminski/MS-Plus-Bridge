"""A simulated MS-60B+ and MIDI controller, and a test case that runs the project against them.

The simulated pedal behaves the way the real one was measured (see README.md): it answers the
identity request, acknowledges edit enable/disable, reports its preset, and acknowledges a
"set parameter" message only when the value changes. It answers the patch query with a dump of
its effect chain, built the way real dumps of the MS-60B+ look (tests/test_synth.py has a real one).
Switching an effect on and off (parameter 0) it treats like any parameter: it confirms a change
and reports its own footswitch as a change, as measured on the real pedal. That it stays silent
when the effect is in that state already is assumed from how it treats its other parameters.
"""
from __future__ import annotations

import binascii
import contextlib
import io
import json
import shutil
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import mido
import yaml

import bridge
import effect_names
import probe
import ui
import zoom_sysex as zs

PROJECT = Path(__file__).resolve().parent.parent
ID = 0x6E
ZOOM_PORT, CONTROLLER_PORT = "ZOOM MS Plus Series", "SINCO"
KEYBOARD_PORT = "KOMPLETE KONTROL A61"
EXTERNAL_PORT = "MPC One MIDI"                   # an external sequencer: notes and MIDI clock
IDENTITY_REPLY = bytes.fromhex("F07E000602526E002700312E3230F7")
ACK = bytes([0xF0, 0x52, 0x00, ID, 0x00, 0x00, 0xF7])


def cc(value: int, control: int = 25, channel: int = 0) -> mido.Message:
    """An expression-pedal message from the controller."""
    return mido.Message("control_change", channel=channel, control=control, value=value)


def sysex(raw: bytes) -> mido.Message:
    return mido.Message("sysex", data=raw[1:-1])


def keyboard(message: mido.Message):
    """A script action: the MIDI keyboard sends the message."""
    return lambda world: world.keyboard_queue.append(message)


def external(message: mido.Message):
    """A script action: the external sequencer sends the message."""
    return lambda world: world.external_queue.append(message)


def note_on(note: int, velocity: int = 100, channel: int = 0):
    return keyboard(mido.Message("note_on", channel=channel, note=note, velocity=velocity))


def note_off(note: int, channel: int = 0):
    return keyboard(mido.Message("note_off", channel=channel, note=note))


pack_7bit = zs.pack_7bit


PATCH_SIZE = 848                                # the MS-60B+'s patch buffer


def edtb_knobs(knobs) -> int:
    """The knobs of an effect as its EDTB block holds them from bit 30 on: the value of the first
    knob, or the values of its knobs from the first on (as many bits each as zs.EDTB_WIDTHS says)."""
    block, bit = 0, 30
    for value, width in zip((knobs,) if isinstance(knobs, int) else knobs, zs.EDTB_WIDTHS):
        block |= value << bit
        bit += width
    return block


def patch_dump(effects, name: str = "USER-001", off=()) -> bytes:
    """The pedal's reply to the patch query, for a chain of (effect id, value of the first knob or
    the values of its knobs). off: the slots whose effect is switched off. The name is there twice,
    as on the MS-60B+: its first 10 characters in the header, all 28 in the chunk "NAME". As there,
    the patch keeps its own length in the header and the buffer is PATCH_SIZE bytes whatever the
    effects."""
    patch = (b"PTCF" + bytes(4) + (2).to_bytes(4, "little")
             + len(effects).to_bytes(4, "little") + (0x80000).to_bytes(4, "little") + bytes(6)
             + name[:10].ljust(10).encode("ascii")
             + b"".join(effect_id.to_bytes(4, "little") for effect_id, _ in effects)
             + b"TXJ1" + bytes(4) + b"TXE1" + bytes(4)
             + b"EDTB" + (24 * len(effects)).to_bytes(4, "little")
             + b"".join(((slot not in off) | effect_id << 1 | edtb_knobs(knobs)).to_bytes(24, "little")
                        for slot, (effect_id, knobs) in enumerate(effects))
             + b"PRM2" + (32).to_bytes(4, "little") + bytes(32)
             + b"NAME" + (32).to_bytes(4, "little") + name.ljust(28).encode("ascii") + bytes(4))
    patch = (patch[:4] + len(patch).to_bytes(4, "little") + patch[8:]).ljust(PATCH_SIZE, b"\0")
    crc = binascii.crc32(patch) ^ 0xFFFFFFFF
    return (bytes([0xF0, 0x52, 0x00, ID, 0x64, 0x12, 0x01, len(patch) & 0x7F, len(patch) >> 7])
            + pack_7bit(patch) + bytes((crc >> 7 * index) & 0x7F for index in range(5)) + b"\xF7")


def mapping(slot: int = 0, param: int = 2, low: int = 0, high: int = 100, **extra) -> dict:
    """An assignment as it is stored in config.yaml."""
    return dict({"slot": slot, "param": param, "min": low, "max": high,
                 "invert": False, "curve": "linear", "name": ""}, **extra)


def learned(*ranges: tuple) -> dict:
    """Learned ranges of one preset, from (slot, param, min, max) tuples."""
    return {f"{slot}/{param}": {"slot": slot, "param": param, "min": low, "max": high,
                                "values": [low, high]} for slot, param, low, high in ranges}


class World:
    """The simulated devices, driven by a timeline of (seconds, action) pairs. An action is a
    message from the controller or a callable that takes the world."""

    def file_size(self, name: str) -> int:
        """The size of a file on the pedal: as its list says (listed_sizes), else of its bytes if it
        can be read, else as the table has it."""
        if name in self.listed_sizes:
            return self.listed_sizes[name]
        if name in self.file_data:
            return len(self.file_data[name])
        return next((entry.get("size", 0) for entry in effect_names.table().values() if entry.get("file") == name), 0)

    def __init__(self, script=(), length=None, acks=True, program=94, ports=None, chain=(), off=()):
        self.script = sorted(script, key=lambda item: item[0])
        self.length = length if length is not None else (self.script[-1][0] if self.script else 0) + 0.6
        self.acks = acks                      # False: the pedal never acknowledges a parameter
        self.program = program                # preset 095 is bank 0, program 94 in this simulation
        self.accept = lambda slot, param, value: True
        self.ports = ports or (lambda seconds: [ZOOM_PORT, CONTROLLER_PORT])
        self.chain = list(chain)              # effect ids in slot order, for the patch query
        self.edit_mode = False                # parameters are confirmed only in edit mode
        self.name = "USER-001"                # the preset's name
        self.takes_names = True               # False: the pedal ignores a name sent to it
        self.takes_effects = True             # False: the pedal ignores another effect sent to a place
        self.takes_patches = True             # False: the pedal ignores a patch sent to it
        self.uploads = []                     # the patches sent to it, unpacked
        self.patches = True                   # False: the pedal does not answer the patch query
        self.bank = 0                         # the bank of the preset (the MS-60B+ has ten of ten)
        self.stored = {}                      # (bank, program) → the preset stored there, unpacked
        self.answers_presets = True           # False: the pedal does not answer for its stored presets
        self.chosen_bank = 0                  # the bank select sent to it, for the next program change
        self.files = []                       # the files on the pedal, for the installer's listing
        self.pc_mode = False                  # PC mode (`52`): the file listing works only in it
        self.listed = 0                       # how many entries of the listing were handed out
        self.file_log = []                    # the file messages it got (hex of the body)
        self.file_data = {}                   # name → the bytes of a file that can be read (others: as the table's)
        self.listed_sizes = {}                # name → the size its list gives, if not that of its bytes
        self.reading = None                   # [name, position, the next 512 bytes or None] of the file opened
        self.silent_files = set()             # files it opens but then stops answering for (until closed)
        self.list_ends_after = None           # how many entries of the list it hands out before it stops answering
        # three forms of its answers seen in the log of 2026-10-21: the second open refused (-1) and the
        # status after it -1 too; the end of the session answered 0.6 s later; the end of a file as an
        # empty part (`60 04 22 00 … 00 00`, then `60 04 22 01 00 00 00 7F 7F 7F 7F 0F`)
        self.second_open_refused = False
        self.session_end_delay = 0.0
        self.empty_end = False
        self.refused = False                  # the last open was refused: the next status says -1
        self.zoom_queue, self.controller_queue, self.keyboard_queue, self.external_queue = [], [], [], []
        self.sent = []                        # (seconds, hex) of every SysEx sent to the pedal
        self.channel_messages = []            # everything else sent to the pedal
        self.values = {}                      # (slot, param) → value the pedal holds
        self.values.update({(slot, 0): 0 for slot in off})    # parameter 0: the effect is on (1) or off
        self.whole_patch = False              # True: the patch has every knob it holds, not only the first
        self.started = time.monotonic()
        self.stopped = False

    def now(self) -> float:
        return time.monotonic() - self.started

    def advance(self) -> None:
        """Plays every action that is due; ends the run once, like a single Ctrl+C."""
        while self.script and self.script[0][0] <= self.now():
            _, action = self.script.pop(0)
            if isinstance(action, mido.Message):
                self.controller_queue.append(action)
            else:
                action(self)
        if self.now() > self.length and not self.stopped:
            self.stopped = True
            raise KeyboardInterrupt

    def report_preset(self) -> None:
        self.zoom_queue += [mido.Message("control_change", control=0, value=0),
                            mido.Message("control_change", control=32, value=self.bank),
                            mido.Message("program_change", program=self.program)]

    def preset_change(self, program: int) -> None:
        """The player selects another preset on the pedal."""
        self.program = program
        self.zoom_queue.append(sysex(bytes([0xF0, 0x52, 0, ID, 0x64, 0x26, 0, 0, self.bank, 0, program, 0, 0xF7])))
        self.zoom_queue.append(sysex(bytes.fromhex("F052006E64200064027800000000F7")))   # tempo
        self.report_preset()

    def replug(self) -> None:
        """The pedal's USB cable is pulled and plugged back in so quickly that the port list
        never shows it missing. As measured on the real pedal: afterwards it answers queries
        but has left edit mode and confirms no parameter."""
        self.edit_mode = False

    def knob(self, slot: int, param: int, value: int) -> None:
        """The player turns a knob on the pedal."""
        self.zoom_queue.append(sysex(zs.build_set_param(ID, slot, param, value)))

    def edit_name(self, name: str) -> None:
        """The player edits the preset's name on the pedal, which reports every character (and,
        as measured, a burst of program changes 0 without bank select in between)."""
        self.name = name
        for place, character in enumerate(name.ljust(zs.NAME_LENGTH)):
            self.zoom_queue.append(sysex(zs.build_name_character(ID, place, character)))
            if place == 3:
                self.zoom_queue += [mido.Message("program_change", program=0)] * 15

    def name_itself(self, name: str, reported) -> None:
        """The pedal gives the preset a name by itself (an empty one that gets an effect) and
        reports only some of its characters: (place, character) as measured."""
        self.name = name
        for place, character in reported:
            self.zoom_queue.append(sysex(zs.build_name_character(ID, place, character)))

    def stomp(self, slot: int, on: bool) -> None:
        """The player switches an effect on the pedal, which reports it like a knob."""
        self.values[(slot, 0)] = int(on)
        self.knob(slot, 0, int(on))

    def rearrange(self, order) -> None:
        """The player moves effects on the pedal: order lists the old places in their new order.
        The pedal sends its patch after it, as measured (`64 12 00`)."""
        self.chain = [self.chain[slot] for slot in order]
        self.values = {(order.index(slot), param): value for (slot, param), value in self.values.items()
                       if slot in order}
        self.report_patch()

    def swap_effect(self, slot: int, effect_id: int) -> None:
        """The player puts another effect into a place on the pedal (its knobs start anew)."""
        self.chain[slot] = effect_id
        self.values = {place: value for place, value in self.values.items() if place[0] != slot}
        self.report_patch()

    def knobs(self, slot: int):
        """What the patch has of the knobs of the effect in that place: the value of the first, or
        with whole_patch those of all of them."""
        if not self.whole_patch:
            return self.values.get((slot, 2), 0)
        return tuple(self.values.get((slot, zs.MIN_PARAM + index), 0) for index in range(len(zs.EDTB_WIDTHS)))

    def report_patch(self) -> None:
        dump = bytearray(patch_dump([(effect_id, self.knobs(slot))
                                     for slot, effect_id in enumerate(self.chain)], name=self.name))
        dump[6] = 0x00                         # sent by the pedal itself, not asked for
        self.zoom_queue.append(sysex(bytes(dump)))

    def new_chain(self, chain, off=()) -> None:
        """The preset on the pedal has other effects from now on (set before preset_change)."""
        self.chain = list(chain)
        self.values = {(slot, 0): 0 for slot in off}

    def receive(self, message: mido.Message) -> None:
        if message.type != "sysex":
            self.channel_messages.append(message)
            if message.type == "control_change" and message.control == 32:
                self.chosen_bank = message.value
            elif message.type == "program_change":     # it loads the preset as stored
                self.bank, self.program = self.chosen_bank, message.program
                data = self.stored.get((self.bank, self.program))
                if data is not None:
                    self.chain = [int.from_bytes(data[36 + 4 * slot:40 + 4 * slot], "little")
                                  for slot in range(int.from_bytes(data[12:16], "little"))]
                    self.values = {}
            return
        raw = bytes(message.bytes())
        self.sent.append((self.now(), zs.to_hex(raw)))
        if raw == zs.IDENTITY_REQUEST:
            self.zoom_queue.append(sysex(IDENTITY_REPLY))
        elif raw[4] in (0x50, 0x51):
            self.edit_mode = raw[4] == 0x50
            self.zoom_queue.append(sysex(ACK))
        elif raw[4] in (0x52, 0x53):
            self.pc_mode = raw[4] == 0x52
            self.zoom_queue.append(sysex(ACK))
        elif raw[4] == 0x60:                      # file access: the listing, reading a file, the end of the session
            self.file_log.append(raw[4:-1].hex())
            answer = lambda *body: self.zoom_queue.append(sysex(bytes([0xF0, 0x52, 0x00, ID, 0x60, *body, 0xF7])))
            done, past_end, failed = (0x03, 0, 0, 0, 0, 0), (0x03, 0x78, 0x7F, 0x7F, 0x7F, 0x0F), (0x03, 0x7F, 0x7F, 0x7F, 0x7F, 0x0F)
            silent = self.reading is not None and self.reading[0] in self.silent_files
            if raw[5] in (0x25, 0x26) and self.pc_mode:
                if raw[5] == 0x25:
                    self.listed = 0
                if self.list_ends_after is not None and self.listed >= self.list_ends_after:
                    return
                if self.listed < len(self.files):
                    name = self.files[self.listed]
                    self.listed += 1
                    size = self.file_size(name)
                    self.zoom_queue.append(sysex(bytes([0xF0, 0x52, 0x00, ID, 0x60, 0x04, 0x25, 0, 0, 0x17, 0, 0, 0, 0, 0])
                                                 + name.encode("ascii").ljust(12, b"\0") + b"\x01"   # as the pedal
                                                 + bytes(2) + bytes((size >> 7 * index) & 0x7F for index in range(5))
                                                 + bytes(5) + b"\xF7"))
                else:
                    answer(0x03, 0x7A, 0x7F, 0x7F, 0x7F, 0x0F)              # the end of the list
            elif raw[4:16] == zs.FILE_OPEN_READ and self.pc_mode:
                name = raw[16:-2].decode("ascii")
                if name in self.file_data and self.reading is not None and self.reading[0] == name \
                        and self.second_open_refused:
                    self.refused = True
                    answer(*failed)                                         # opened already
                elif name in self.file_data:
                    if self.reading is None or self.reading[0] != name:
                        self.reading = [name, 0, None]
                    answer(0x04, 0x20, 0, 0, 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
                else:
                    answer(*failed)                                         # no such file
            elif raw[4:-1] in (zs.FILE_READ_NEXT, zs.FILE_STATUS) and silent:
                pass
            elif raw[4:-1] == zs.FILE_READ_NEXT:
                name, position, _ = self.reading or (None, 0, None)
                if name is not None and position >= len(self.file_data[name]) and self.empty_end:
                    self.reading = [name, position, b""]
                    answer(0x04, 0x22, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
                elif name is None or position >= len(self.file_data[name]):
                    answer(*past_end)
                else:
                    block = self.file_data[name][position:position + 512]
                    self.reading = [name, position + len(block), block]
                    answer(0x04, 0x22, 0, 0, len(block) & 0x7F, len(block) >> 7, 0, 4, 0, 0, 0, 0, 0, 0, 0, 0)
            elif raw[4:-1] == zs.FILE_STATUS:
                if self.refused:
                    self.refused = False
                    answer(*failed)
                elif self.reading and self.reading[2] == b"":           # the empty part at the end
                    self.reading[2] = None
                    answer(0x04, 0x22, 0x01, 0x00, 0, 0, 0x7F, 0x7F, 0x7F, 0x7F, 0x0F)
                elif self.reading and self.reading[2] is not None:
                    block, self.reading[2] = self.reading[2], None      # the bytes asked for, with their CRC
                    crc = binascii.crc32(block) ^ 0xFFFFFFFF
                    self.zoom_queue.append(sysex(bytes([0xF0, 0x52, 0x00, ID, 0x60, 0x04, 0x22, 0x01, 0x00,
                                                        len(block) & 0x7F, len(block) >> 7]) + zs.pack_7bit(block)
                                                 + bytes((crc >> 7 * index) & 0x7F for index in range(5)) + b"\xF7"))
                else:
                    answer(*done)
            elif raw[4:7] == b"\x60\x21\x40":           # the file closed
                self.reading = None
                answer(*done)
            elif raw[4:6] == b"\x60\x09":                # the end of the file session
                if self.session_end_delay:
                    self.script.append((self.now() + self.session_end_delay, lambda world: world.zoom_queue.append(
                        sysex(bytes([0xF0, 0x52, 0x00, ID, 0x60, 0x05, 0x00, 0xF7])))))
                    self.script.sort(key=lambda item: item[0])
                else:
                    answer(0x05, 0x00)
            else:
                answer(0x00, raw[5], 0, 0, 0)
        elif raw[4] == zs.QUERY_STORED:           # a stored preset, in the form it reports a saved one
            if self.answers_presets:
                place = (raw[7] | raw[8] << 7, raw[9] | raw[10] << 7)
                data = self.stored.get(place) or zs.unpack_7bit(patch_dump([(0, 0)], name="Empty")[9:-6])
                self.zoom_queue.append(sysex(zs.build_save_preset(ID, *place, data)))
        elif raw[4] == zs.STORED_PATCH:           # a preset into its place
            stored = zs.parse_stored_preset(raw[4:-1])
            if stored is not None:
                self.stored[stored[:2]] = stored[3]
                self.zoom_queue.append(sysex(ACK))
        elif raw[4] == 0x33:
            self.report_preset()
        elif raw[4] == zs.PATCH_UPLOAD:          # a patch into the edit buffer: its effects, in their order
            data = zs.unpack_7bit(raw[5:-1])
            self.uploads.append(data)
            if self.takes_patches:
                ids = [int.from_bytes(data[36 + 4 * slot:40 + 4 * slot], "little")
                       for slot in range(int.from_bytes(data[12:16], "little"))]
                order = []
                for effect_id in ids:                # each place: where that effect was (None: a new empty one)
                    order.append(next((slot for slot, old in enumerate(self.chain) if old == effect_id
                                       and slot not in order), None))
                self.chain = ids
                self.values = {(order.index(slot), param): value for (slot, param), value in self.values.items()
                               if slot in order}
        elif raw[4:6] == b"\x64\x13" and self.patches:
            self.zoom_queue.append(sysex(patch_dump(
                [(effect_id, self.knobs(slot)) for slot, effect_id in enumerate(self.chain)],
                name=self.name, off=[slot for slot in range(len(self.chain)) if self.values.get((slot, 0), 1) == 0])))
        elif raw[4:7] == b"\x64\x20\x00":
            key, value = (raw[7], raw[8]), raw[9] | raw[10] << 7
            if not self.accept(*key, value):
                return
            if key[1] == zs.EFFECT_PARAM and key[0] < zs.MAX_SLOTS:     # another effect in the place
                if self.takes_effects and key[0] < len(self.chain):
                    self.chain[key[0]] = zs.decode_effect(raw[9:14])
                    self.values = {place: held for place, held in self.values.items() if place[0] != key[0]}
                    if self.acks and self.edit_mode:
                        self.zoom_queue.append(sysex(raw[:6] + b"\x01" + raw[7:]))
                return
            if key[0] == zs.NAME_SLOT:            # a character of the name
                if not self.takes_names:
                    return
                name = list(self.name.ljust(zs.NAME_LENGTH))
                name[key[1]] = chr(value)
                self.name = "".join(name).rstrip()
            held = self.values.get(key, 1 if key[1] == 0 else None)     # an effect is on unless switched off
            if self.acks and self.edit_mode and value != held:
                self.zoom_queue.append(sysex(raw[:6] + b"\x01" + raw[7:]))
            self.values[key] = value

    @property
    def params(self) -> list:
        """(seconds, slot, param, value) of every "set parameter" message sent to the pedal."""
        found = []
        for seconds, text in self.sent:
            raw = bytes.fromhex(text.replace(" ", ""))
            if raw[4:7] == b"\x64\x20\x00":
                found.append((seconds, raw[7], raw[8], raw[9] | raw[10] << 7))
        return found

    def count(self, text: str) -> int:
        return sum(1 for _, sent in self.sent if sent == text)


class Port:
    """Stands in for a mido port."""

    def __init__(self, world: World, queue):
        self.world, self.queue = world, queue

    def iter_pending(self):
        self.world.advance()
        while self.queue:
            yield self.queue.pop(0)

    def send(self, message: mido.Message) -> None:
        self.world.receive(message)

    def close(self) -> None:
        pass


class Client:
    """Calls the bridge's HTTP interface at given moments of the simulation."""

    def __init__(self, world: World, port: int):
        self.world, self.port = world, port

    def at(self, seconds: float) -> None:
        time.sleep(max(0.0, self.world.started + seconds - time.monotonic()))

    def call(self, path: str, body=None, headers=None) -> tuple:
        """Returns (status, body, headers); JSON bodies of /api paths are decoded."""
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {},
                                         data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                raw, is_json = response.read(), response.headers.get("Content-Type", "").startswith("application/json")
                return response.status, json.loads(raw) if is_json else raw, dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read()), {}


class SimulatedCase(unittest.TestCase):
    """A temporary installation: its own config, approvals, measurements and logs."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        paths = {"ROOT": self.folder, "CONFIG": self.folder / "config.yaml",
                 "APPROVALS": self.folder / "approvals.json",
                 "MEASUREMENTS": self.folder / "measurements.json"}
        for module, values in ((bridge, dict(paths, RETRY_SECONDS=0.2, PORT_CHECK_SECONDS=0.05,
                                             CONTROLS=self.folder / "controls.json",
                                             EFFECT_CACHE=self.folder / "pedal_effects.json")),
                               (probe, paths)):
            for name, value in values.items():
                patcher = mock.patch.object(module, name, value)
                patcher.start()
                self.addCleanup(patcher.stop)
        self.approve(*bridge.REQUIRED_KINDS)
        zs.setup_logging(self.folder / "logs", console=False)
        self.addCleanup(self._close_log)
        bridge.EVENTS.clear()
        self.addCleanup(self._forget_read_effects)
        self.instance = None
        self.output = ""

    @staticmethod
    def _forget_read_effects() -> None:
        """What a test read from the simulated pedal's files stays with that test."""
        effect_names.use_cache(None)
        bridge.chain_entry.cache_clear()
        bridge.effect_choices.cache_clear()

    @staticmethod
    def _close_log() -> None:
        for handler in list(zs.log.handlers):
            handler.close()
            zs.log.removeHandler(handler)

    def approve(self, *kinds: str, backup: bool = True) -> None:
        """Replaces the approvals of the installation."""
        bridge.APPROVALS.unlink(missing_ok=True)
        approvals = zs.Approvals(bridge.APPROVALS)
        if backup:
            approvals.confirm_backup()
        for kind in kinds:
            approvals.approve(kind)

    def install(self, mappings=None, measured=None, synth=None, **bridge_settings) -> None:
        """Writes config.yaml (from the example) and measurements.json."""
        cfg = yaml.safe_load((PROJECT / "config.example.yaml").read_text(encoding="utf-8"))
        cfg["ports"]["chocolate"] = CONTROLLER_PORT
        if synth is not None:
            cfg["synth"] = synth
        cfg["zoom"].update(device_id=ID, firmware="1.20")
        cfg["expression"].update(cc=25, channel=1)
        cfg["bridge"].update(bridge_settings)
        cfg["mappings"] = mappings or {}
        bridge.CONFIG.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        bridge.MEASUREMENTS.write_text(json.dumps({"patches": measured or {}}), encoding="utf-8")

    @contextlib.contextmanager
    def devices(self, world: World):
        """Replaces the MIDI ports with the simulated world and captures what is printed."""
        def names():
            # The world ends the run from a port that is read (World.advance); one that waits
            # with no port open (a device missing) ends a second after it all the same.
            if world.now() > world.length + 1.0 and not world.stopped:
                world.stopped = True
                raise KeyboardInterrupt
            return world.ports(world.now())
        queues = {ZOOM_PORT: world.zoom_queue, KEYBOARD_PORT: world.keyboard_queue,
                  EXTERNAL_PORT: world.external_queue}
        opened = lambda name: Port(world, queues.get(name, world.controller_queue))
        with mock.patch.object(mido, "get_input_names", names), \
                mock.patch.object(mido, "get_output_names", names), \
                mock.patch.object(mido, "open_input", opened), \
                mock.patch.object(mido, "open_output", lambda name: Port(world, None)), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            try:
                yield
            finally:
                self.output = printed.getvalue()

    def run_bridge(self, world: World, client=None) -> "bridge.Bridge":
        """Runs the bridge until the world ends. client(Client) runs alongside in a thread."""
        failures = []

        def guarded(connection: Client) -> None:
            try:
                client(connection)
            except Exception as error:   # reported in the main thread below
                failures.append(error)

        with self.devices(world):
            self.instance = bridge.Bridge()
            thread = None
            if client is not None:
                with socket.socket() as probe_socket:
                    probe_socket.bind(("127.0.0.1", 0))
                    port = probe_socket.getsockname()[1]
                ui.serve(self.instance, {"port": port})
                thread = threading.Thread(target=guarded, args=(Client(world, port),), daemon=True)
                thread.start()
            self.instance.run()
            if thread is not None:
                thread.join(5)
        if failures:
            raise failures[0]
        return self.instance

    def start_fails(self) -> str:
        """The message with which the bridge refuses to start."""
        with self.devices(World()), self.assertRaises(SystemExit) as refused:
            bridge.Bridge()
        return str(refused.exception)
