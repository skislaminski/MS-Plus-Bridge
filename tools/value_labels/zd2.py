"""An effect file (.ZD2) as far as this tool reads it: its chunks, the TI C6000 ELF in its DATA
chunk (the effect's code and constants), and the parameter table at the start of that ELF's
.const section.

One entry of the parameter table is 56 bytes, 14 little-endian words: entry 0 is "OnOff", entry 1
the effect, entries 2.. the knobs (the entry's index is the parameter number sent to the pedal).
    words 0-2  name (12 bytes, NUL padded)     word 3  highest value      word 4  default
    word 7     edit handler                    word 9  value-display function (0: the number)
    word 12    flags (0x4: the last entry of the table)
"""
from __future__ import annotations

import struct
from typing import List, NamedTuple, Optional

ENTRY_SIZE = 56
HIGHEST, DISPLAY, FLAGS = 3, 9, 12          # words of an entry
LAST = 0x4                                  # in FLAGS: no entry follows
ALLOC, WRITE = 0x2, 0x1                     # section flags
PROGBITS, NOBITS = 1, 8                     # section types


class Section(NamedTuple):
    name: str
    type: int
    flags: int
    addr: int
    offset: int
    size: int


class Entry(NamedTuple):
    name: str
    words: tuple


class Segment(NamedTuple):
    """Memory the effect's code sees: a section of the ELF loaded at its address."""
    name: str
    addr: int
    data: bytes                             # zeros for a section the file has no bytes for (NOBITS)
    loaded: bool                            # False: set by the effect at run time (.far)
    writable: bool


def chunks(data: bytes) -> dict:
    """{tag: bytes} of the chunks of an effect file ("ZDLF", the header's length, the header, then
    ICON, TXJ1, …, DATA, PRMJ, … each with a 4-letter tag and its length)."""
    if data[:4] != b"ZDLF" or len(data) < 8:
        return {}
    position = 8 + struct.unpack_from("<I", data, 4)[0]
    found = {}
    while position + 8 <= len(data):
        tag, length = data[position:position + 4], struct.unpack_from("<I", data, position + 4)[0]
        if not all(65 <= byte <= 90 or 48 <= byte <= 57 for byte in tag):
            break
        found[tag.decode("ascii")] = data[position + 8:position + 8 + length]
        position += 8 + length
    return found


class Elf:
    """The sections of a little-endian ELF32 (TI C6000: .text at 0, .const at 0x80000000)."""

    def __init__(self, data: bytes):
        if data[:4] != b"\x7fELF" or len(data) < 52:
            raise ValueError("not an ELF file")
        self.data = data
        shoff = struct.unpack_from("<I", data, 0x20)[0]
        shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x2E)
        headers = [struct.unpack_from("<10I", data, shoff + index * shentsize) for index in range(shnum)]
        names = headers[shstrndx][4] if shstrndx < shnum else None
        self.sections: List[Section] = []
        for header in headers:
            name = ""
            if names is not None:
                end = data.index(b"\0", names + header[0])
                name = data[names + header[0]:end].decode("latin1")
            self.sections.append(Section(name, header[1], header[2], header[3], header[4], header[5]))

    def section(self, name: str) -> Optional[Section]:
        return next((section for section in self.sections if section.name == name), None)

    def bytes_of(self, section: Section) -> bytes:
        return self.data[section.offset:section.offset + section.size]

    def segments(self) -> List[Segment]:
        """The sections the effect's code is loaded with (ALLOC), as memory."""
        out = []
        for section in self.sections:
            if not section.size or not section.flags & ALLOC or section.type not in (PROGBITS, NOBITS):
                continue
            loaded = section.type == PROGBITS
            out.append(Segment(section.name, section.addr,
                               self.bytes_of(section) if loaded else bytes(section.size),
                               loaded, bool(section.flags & WRITE)))
        return out


def elf_of(data: bytes) -> Optional[Elf]:
    """The ELF in an effect file's DATA chunk, None if it has none."""
    chunk = chunks(data).get("DATA")
    try:
        return Elf(chunk) if chunk else None
    except (ValueError, struct.error):
        return None


def parameter_table(elf: Elf) -> List[Entry]:
    """The entries of the parameter table: from "OnOff" to the entry flagged LAST (or to a name
    that is no text, as effect_names.read_zd2 stops)."""
    const = elf.section(".const")
    if const is None:
        return []
    data = elf.bytes_of(const)
    start = data.find(b"OnOff\0")
    if start < 0:
        return []
    entries: List[Entry] = []
    position = start
    while position + ENTRY_SIZE <= len(data):
        name = data[position:position + 12].split(b"\0")[0]
        if len(entries) >= 2 and (not name or not all(32 <= byte < 127 for byte in name)):
            break
        words = struct.unpack_from("<14I", data, position)
        entries.append(Entry(name.decode("latin1"), words))
        position += ENTRY_SIZE
        if len(entries) > 2 and words[FLAGS] & LAST:
            break
    return entries
