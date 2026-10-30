"""The names of the pedal's effects and of their knobs, for the interface: "AnalogDly · Mix (0–100)"
instead of "Effect 2 · Parameter 4".

They come from the effect files (.ZD2) of the pedal, as a backup of it holds them (nam2zoom, the
installer of the synth effects): each file carries the effect's id, its short name as the pedal
shows it, a table of its knobs with their highest value, and the DSP the effect takes ("dsp", as
the pedal counts it for a preset). zoom_effects.json keeps what was read from them; nothing here
talks to the pedal. To read them again, from one or more folders with .ZD2 files in them or below:

    python effect_names.py FOLDER [FOLDER …]

or to set only the DSP the table's effects take, from their files there, nothing else of the table
changed (set_dsp):

    python effect_names.py --dsp FOLDER [FOLDER …]

An effect that is not in the table (one installed later) keeps its number in the interface –
unless the bridge has read its file from the pedal itself (Read Pedal, `read_effect_file`): what
it reads that way is kept apart (use_cache, remember) and taken before the table for that file.

Many knobs show their value on the pedal as text ("Rock", "Sync", "1/8."): the table keeps those
texts per knob (an entry's "labels", see write_table). Those of the custom effects are read here
from their files (_label_tables); Zoom's own effects make theirs with code that only an
interpreter runs (tools/value_labels, on a Mac), and what it put into the table is kept when the
table is read again from the same files.
"""
from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path
from typing import Optional

TABLE = Path(__file__).resolve().parent / "zoom_effects.json"
ID_AT, NAME_AT = 96, slice(100, 111)          # in a .ZD2 file (as KeySynth's adapt_ms60b.py read them)
KNOB_SIZE = 56                                 # one knob in its table: name (12 bytes), highest value, default, …
SHOWN_BY, FLAGS = 36, 48                       # … the function that makes its value's text (0: the number), flags
LAST = 0x4                                     # in its flags: the last knob of the table
HIDDEN = {"Dummy"}                             # knobs some effects carry that do nothing (as those with no range)
FIRST_KNOB = 2                                 # parameter 0 is on/off, 1 the effect type, then the knobs
# The effect's program is an ELF for the pedal's DSP (TI C6000) in the file's chunk DATA: its code
# (.text, linked at 0) right after the ELF's header, its constants (.const, linked at CONST_ADDRESS)
# beginning with the table of knobs ("OnOff"). The custom effects' builds (stomphacks' generator)
# all make a knob's text with one function of 13 instructions that copies it from a table of 8-byte
# texts in .const (one per value, NUL-padded): these are its words with the 16-bit constants of its
# MVK, MVKL and MVKH (the highest value, the table's address in two halves) taken out.
TEXT_AT, CONST_ADDRESS = 0x40, 0x80000000
TEMPLATE = (0x00000028, 0x008089F8, 0x0280002A, 0x0280006A, 0x82000FD8, 0x02106CA0, 0x0290B07A,
            0x029402E4, 0x039422E4, 0x00008000, 0x029002F4, 0x039022F4, 0x008CA362)
CONSTANT = 0xFFFF << 7                         # where an MVK, MVKL or MVKH keeps its constant
TEXT_SIZE = 8                                  # one value's text in such a table
# The DSP an effect takes, as its file declares it: a float (little-endian, 32 bits) after the first
# 16 bytes of the file's chunk INFO, which comes before DATA (KeySynth 52, the bass amps 103–142).
# The pedal adds those of a preset's effects up, on or off (bridge.DSP_BUDGET).
DSP_AT = 16
NOTES = range(0x16, 0x1B)                      # the pedal's characters for note values (1/32 … 1/2) in a text
# Files read from the pedal at every Read Pedal, even when the table or an earlier read describes
# them: nam2zoom's bank of amp models (N2Z Bank) is one file whatever models it holds, and a bank
# with other models (whose names its Model knob shows) can be of the same size.
ALWAYS_READ = {"N2ZBANK.ZD2"}
# Effect numbers (the lower 24 bits of an id) from here on are custom and community effects, and
# Zoom's LINE SEL (F00) and BPM (FF0). One number is one effect, whatever category it was installed
# into (its top byte) and whatever it was called in an earlier build (TrackSyn, now FretSyn). Zoom's
# own effects share numbers between categories (BlackOpt 01000020, AnalogDly 0A000020; the highest
# is E0): those go by the whole id.
CUSTOM_FROM = 0xF00
ZOOM_OWN = {0xF00, 0xFF0}                      # LINE SEL and BPM among them
BUILT_IN = {0xFF0: {"name": "BPM", "knobs": []}}  # in the pedal itself: no effect file to read
# An effect read from the pedal before this day counts as not described, so that the next Read
# Pedal reads its file again: on 2026-10-30 the custom effects were installed into their own
# categories instead of SFX, at the same size (only the id's top byte changed), and the pedal's
# list of files gives names and sizes only (described).
READ_SINCE = "2026-10-30"

_table: Optional[dict] = None
_by_number: Optional[tuple] = None             # (table, lower 24 bits of an id → the entries that have them)
# Effect files read from the pedal itself (Read Pedal, read_effect_file): file name → {id (hex),
# name, knobs, size, dsp (None if its file declares none), labels if any}, {id: None, size} for a
# file of that size that holds no effect, or {id: None, size, unread: True} for one that could not
# be read (tried again, after the others); kept in CACHE (if set and kept).
CACHE: Optional[Path] = None
_keep = True                                   # False: CACHE is only read (a bridge without screen and network)
_read: dict = {}
_read_version = 0
_merged: Optional[tuple] = None                # (table, version of _read, the two together)
_seen: Optional[tuple] = None                  # (table, version of _read) when version() last counted
_version = 0
CONFLICTS: list = []                           # effects read that are left out: their id is another's


def read_zd2(data: bytes) -> Optional[dict]:
    """{id, name, knobs: [[name, max], …]} of an effect file, with dsp (the DSP it takes, _dsp) if
    the file declares it and labels ({param as a string: [text per value]}, see write_table) if it
    holds texts of its knobs' values (_label_tables); None if it is none."""
    if data[:4] != b"ZDLF" or len(data) < 120:
        return None
    effect_id = struct.unpack_from("<I", data, ID_AT)[0]
    name = data[NAME_AT].split(b"\0")[0].decode("ascii", "replace").strip()
    if not name:
        return None
    start = data.find(b"OnOff\0")
    entries = _knob_entries(data, start)[0] if start >= 0 else []
    effect = {"id": effect_id, "name": name, "knobs": [[knob, highest] for _, knob, highest in entries]}
    load = _dsp(data)[1]
    if load is not None:
        effect["dsp"] = load
    texts = {}
    for param, (position, count) in _label_tables(data, start, entries).items():
        if position + TEXT_SIZE * count > len(data):
            continue                              # not within what was read
        table = data[position:position + TEXT_SIZE * count]
        found = [table[at:at + TEXT_SIZE].split(b"\0")[0] for at in range(0, len(table), TEXT_SIZE)]
        if not all(32 <= byte < 127 or byte in NOTES for text in found for byte in text):
            continue                              # no texts
        found = [text.decode("ascii").strip(" ") for text in found]
        if found != [str(value) for value in range(count)]:      # not just the numbers
            texts[str(param)] = found
    if texts:
        effect["labels"] = texts
    return effect


def _knob_entries(data: bytes, start: int) -> tuple:
    """The knobs in the table of an effect file that begins at start ("OnOff"), past on/off and the
    effect type: ([(position, name, highest value)], whether the table ended within data – at the
    knob marked as its last (LAST), or at one without a name, as with files that mark none)."""
    entries, position = [], start + 2 * KNOB_SIZE
    while position + KNOB_SIZE <= len(data):
        knob = data[position:position + 12].split(b"\0")[0]
        if not knob or not all(32 <= byte < 127 for byte in knob):
            return entries, True
        entries.append((position, knob.decode("ascii"), struct.unpack_from("<I", data, position + 12)[0]))
        if struct.unpack_from("<I", data, position + FLAGS)[0] & LAST:
            return entries, True
        position += KNOB_SIZE
    return entries, False


def _label_tables(data: bytes, start: int, entries: list) -> dict:
    """{param: (where its table of texts begins in the file, how many values)} of the knobs whose
    text the function of the custom builds makes (TEMPLATE), as far as its code is within data;
    its constant for the highest value must be the knob's. The table itself may lie beyond data."""
    program = _program(data)
    if program is None:
        return {}
    tables = {}
    for index, (position, _, highest) in enumerate(entries):
        function = struct.unpack_from("<I", data, position + SHOWN_BY)[0]
        code = program + TEXT_AT + function
        if not function or code + 4 * len(TEMPLATE) > len(data):
            continue
        words = struct.unpack_from(f"<{len(TEMPLATE)}I", data, code)
        if tuple(word & ~CONSTANT if (word & 0x7C) in (0x28, 0x68) else word for word in words) != TEMPLATE:
            continue                              # another function (Zoom's own effects)
        constants = [(word & CONSTANT) >> 7 for word in words]    # of the MVK, MVKL and MVKH among them
        address = constants[3] << 16 | constants[2]
        if constants[0] == highest and address >= CONST_ADDRESS:
            tables[FIRST_KNOB + index] = (start + address - CONST_ADDRESS, highest + 1)
    return tables


def _chunks(data: bytes):
    """The chunks of an effect file after its header, as far as their heads are within data: (tag,
    where its content begins, its length); one whose tag is none ends them, as (None, where, 0)."""
    if len(data) < 8:
        return
    position = 8 + struct.unpack_from("<I", data, 4)[0]          # the chunks after the file's header
    while position + 8 <= len(data):
        tag = data[position:position + 4]
        if not tag.isalnum():
            yield None, position, 0
            return
        length = struct.unpack_from("<I", data, position + 4)[0]
        yield tag, position + 8, length
        position += 8 + length


def _program(data: bytes) -> Optional[int]:
    """Where the ELF of an effect file (its chunk DATA) begins, None if not within data."""
    for tag, start, _ in _chunks(data):
        if tag == b"DATA":
            return start if data[start:start + 4] == b"\x7fELF" else None
    return None


def _dsp(data: bytes) -> tuple:
    """(whether it is known yet, the DSP the effect takes as its file declares it – the float after
    the first DSP_AT bytes of its chunk INFO, rounded to two decimals – or None if it declares
    none): known once that float is within data, or DATA (which comes after INFO) or the end of
    the chunks is. A value that is no load (_is_load) is none."""
    for tag, start, length in _chunks(data):
        if tag == b"INFO" and length >= DSP_AT + 4:
            if start + DSP_AT + 4 > len(data):
                return False, None
            load = struct.unpack_from("<f", data, start + DSP_AT)[0]
            return True, round(load, 2) if _is_load(load) else None
        if tag in (b"DATA", None):
            return True, None
    return False, None


def build(folders) -> dict:
    """The table from the .ZD2 files in those folders, with the DSP the effects take and the texts
    of the values the files hold (read_zd2); of two files of the same effect the newer wins: the
    same id, or for a custom effect the same number (CUSTOM_FROM)."""
    found: dict = {}
    for folder in folders:
        for path in Path(folder).expanduser().rglob("*"):
            if path.suffix.upper() != ".ZD2" or not path.is_file():
                continue
            effect = read_zd2(path.read_bytes())
            if effect is None:
                continue
            effect["file"] = path.name.upper()         # as the pedal lists it (Session reads its listings)
            effect["size"] = path.stat().st_size       # … with its size: a file of another size is another build
            number = effect["id"] & 0xFFFFFF
            key = number if number >= CUSTOM_FROM else effect["id"]
            when = path.stat().st_mtime
            if key not in found or when > found[key][0]:
                found[key] = (when, effect)
    # One file on the pedal is one effect: of two builds of it under other numbers (N2ZBANK.ZD2 was
    # 09000F87, then nam2zoom 1.1.1 made it 04001787) the newer is the one there.
    newest: dict = {}
    for when, effect in found.values():
        if effect["file"] not in newest or when > newest[effect["file"]][0]:
            newest[effect["file"]] = (when, effect)
    return {f"{effect['id']:08X}": {"name": effect["name"], "knobs": effect["knobs"], "file": effect["file"],
                                    "size": effect["size"], **({"dsp": effect["dsp"]} if "dsp" in effect else {}),
                                    **({"labels": effect["labels"]} if "labels" in effect else {})}
            for _, effect in sorted(newest.values(), key=lambda item: item[1]["id"])}


def set_dsp(made: dict, folders, say=print) -> dict:
    """The DSP the table's effects take, from their files in those folders or below (in place),
    nothing else of the table changed: of the files of an entry's name the newest of its size, else
    the newest of that name (said). An entry whose file is not there, or declares none, keeps what
    it has (said). Returns {key: the file read} of the entries set."""
    files: dict = {}
    for folder in folders:
        for path in Path(folder).expanduser().rglob("*"):
            if path.suffix.upper() == ".ZD2" and path.is_file():
                files.setdefault(path.name.upper(), []).append((path.stat().st_mtime, path))
    done = {}
    for key, entry in made.items():
        found = sorted(files.get(str(entry.get("file", "")).upper(), []), key=lambda item: -item[0])
        paths = [path for _, path in found]                       # newest first
        if not paths:
            say(f"{key} {entry.get('name')}: no {entry.get('file')} there – its DSP stays as it is")
            continue
        path = next((path for path in paths if path.stat().st_size == entry.get("size")), None)
        if path is None:
            path = paths[0]
            say(f"{key} {entry.get('name')}: no {entry.get('file')} of {entry.get('size')} bytes there, "
                f"the newest of that name is read: {path} ({path.stat().st_size} bytes)")
        load = _dsp(path.read_bytes())[1]
        if load is None:
            say(f"{key} {entry.get('name')}: {path} declares no DSP – its DSP stays as it is")
            continue
        entry["dsp"] = load
        done[key] = path
    return done


def kept_labels(made: dict, before: dict) -> dict:
    """The table made again (build), with the texts of the table before for the knobs whose texts
    the files do not hold (those of Zoom's own effects, from tools/value_labels) – where the entry
    is made from the same file with the same knobs."""
    by_file = {entry.get("file"): entry for entry in before.values() if isinstance(entry, dict)}
    for entry in made.values():
        old = by_file.get(entry["file"])
        if old is None or old.get("knobs") != entry["knobs"] or not old.get("labels"):
            continue
        texts = dict(old["labels"], **entry.get("labels", {}))
        entry["labels"] = dict(sorted(texts.items(), key=lambda item: int(item[0])))
    return made


def table() -> dict:
    """The table, with what was read from the pedal itself in place of the table's entry for the
    same file where the table describes another build of it, or none (see described). A table
    read again from the files of the build that was read wins over it (effect_names.py after
    installing effects) – not for a file read at every Read Pedal (ALWAYS_READ): what was read of
    it is what is on the pedal. A build read keeps the texts of the values the table has for the
    same file with the same knobs where its own file gives none (those of code that only
    tools/value_labels runs, as kept_labels) – again not for a file read at every Read Pedal. One
    id is one effect on the pedal: an effect read takes the place of the table's entry with its
    id, and of two read with the same id the first by file name counts (the other is in
    CONFLICTS, and its file is described by neither)."""
    global _table, _merged
    if _table is None:
        try:
            _table = json.loads(TABLE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _table = {}
    if not _read:
        return _table
    if _merged is None or _merged[0] is not _table or _merged[1] != _read_version:
        by_file = {entry.get("file"): key for key, entry in _table.items()}
        taken, others, conflicts = {}, set(), []
        for file, entry in sorted(_read.items()):
            key = by_file.get(file)
            if key is not None and file not in ALWAYS_READ and _same_build(key, _table[key], entry.get("size")):
                continue                          # the table describes this build
            if entry.get("id") is None:           # no effect in it (an install broken off: 0 bytes) – nor
                if key is not None and not entry.get("unread"):    # the table's build of it, then
                    others.add(file)
                continue
            others.add(file)                      # the table's entry for it is another build
            if entry["id"] in taken:
                kept = taken[entry["id"]]
                conflicts.append(f"{entry['name']} ({file}) is left out: {kept['name']} ({kept['file']}) has its id")
                continue
            texts, before = entry.get("labels") or {}, _table[key] if key is not None else {}
            if file not in ALWAYS_READ and before.get("knobs") == entry["knobs"] and before.get("labels"):
                texts = dict(before["labels"], **texts)   # as kept_labels: those the file's code makes
            taken[entry["id"]] = {"name": entry["name"], "knobs": entry["knobs"], "file": file, "size": entry["size"],
                                  **({"dsp": entry["dsp"]} if entry.get("dsp") is not None else {}),
                                  **({"labels": texts} if texts else {})}
        merged = {key: entry for key, entry in _table.items() if key not in taken and entry.get("file") not in others}
        merged.update(taken)
        CONFLICTS[:] = conflicts
        _merged = (_table, _read_version, merged)
    return _merged[2]


def _same_build(key: str, entry: dict, size: Optional[int]) -> bool:
    """Whether a table entry describes the file of that size: one of Zoom's own effects by its
    name alone (the size of their files may change with the firmware, their knobs do not), a
    custom effect also by its size where both know it (another size: another build, whose knobs
    may be others, as SlamDrive's)."""
    number = int(key, 16) & 0xFFFFFF
    custom = number >= CUSTOM_FROM and number not in ZOOM_OWN
    return not custom or size is None or entry.get("size") in (None, size)


def read_zd2_part(data: bytes) -> tuple:
    """read_zd2 of the beginning of an effect file, as far as it has been read from the pedal:
    (True, the effect or None if the file holds none) once that is known – its table of knobs
    ended within it (the table sits after the effect's code, at about half of the file), and so
    did the tables of texts of its knobs' values (_label_tables, a custom effect's: after it), and
    the DSP it takes is known (_dsp: its chunk INFO comes before the code), or it is no effect
    file; (False, None) while it may go on beyond it."""
    if len(data) >= 4 and data[:4] != b"ZDLF":
        return True, None
    if len(data) < 120:
        return False, None
    start = data.find(b"OnOff\0")
    if start < 0:
        return False, None
    entries, ended = _knob_entries(data, start)
    if not ended or not _dsp(data)[0] or any(position + TEXT_SIZE * count > len(data)
                                             for position, count in _label_tables(data, start, entries).values()):
        return False, None
    return True, read_zd2(data)                   # its end is within what was read


def use_cache(path: Optional[Path], keep: bool = True) -> None:
    """Where what is read from the pedal itself is kept (None: in memory only; keep False: only
    read from there); what is there is taken at once. An entry not as remember writes it is left
    out, and so are texts of values that do not fit its knobs (_fitting_labels). One of an effect
    without "dsp" (read before the bridge read the DSP of effects, 2026-10-22) is taken without it:
    it counts as not described, so that the next Read Pedal reads its file again (described), as
    one read before READ_SINCE."""
    global CACHE, _keep, _read, _read_version
    CACHE, _keep = path, keep
    try:
        kept = json.loads(path.read_text(encoding="utf-8")) if path is not None and path.exists() else {}
    except (OSError, ValueError):
        kept = {}
    _read = {}
    for file, entry in (kept.items() if isinstance(kept, dict) else ()):
        try:
            if not isinstance(entry["size"], int):
                continue
            if entry["id"] is None:
                _read[str(file).upper()] = {"id": None, "size": entry["size"], **({"unread": True} if entry.get("unread") else {})}
            else:
                knobs = [[str(name), int(highest)] for name, highest in entry["knobs"]]
                texts = _fitting_labels(entry.get("labels"), knobs)
                load = entry.get("dsp")
                if load is not None and not _is_load(load):
                    continue
                _read[str(file).upper()] = {"id": f"{int(entry['id'], 16):08X}", "name": str(entry["name"]),
                                            "knobs": knobs, "size": entry["size"],
                                            **({"dsp": None if load is None else float(load)} if "dsp" in entry else {}),
                                            **({"labels": texts} if texts else {}),
                                            **({"since": entry["since"]} if isinstance(entry.get("since"), str) else {})}
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    _read_version += 1


def _is_load(value) -> bool:
    """Whether a value is a DSP load as _dsp reads one: a number, finite, not below 0."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _fitting_labels(labels, knobs: list) -> dict:
    """Of the texts of an entry ({param as a string: [text per value]}) those that fit its knobs:
    for a knob it has, one text for each of its values."""
    fitting = {}
    for param, texts in (labels.items() if isinstance(labels, dict) else ()):
        index = int(param) - FIRST_KNOB if isinstance(param, str) and param.isascii() and param.isdigit() else -1
        if (0 <= index < len(knobs) and isinstance(texts, list) and len(texts) == knobs[index][1] + 1
                and all(isinstance(text, str) for text in texts)):
            fitting[str(int(param))] = texts
    return fitting


def _store() -> None:
    global _read_version
    _read_version += 1
    if CACHE is not None and _keep:
        try:
            CACHE.write_text(json.dumps(_read, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError:
            pass                                  # kept for this run only


def remember(effects) -> None:
    """Effect files read from the pedal ({id, name, knobs, file, size, dsp and labels if any}, {file,
    size, id: None} for a file that holds no effect, {file, size, id: None, unread: True} for one
    that could not be read): from now on taken with the table (see table), and kept for the next
    start, by file name – an effect with "dsp" null if its file declares none."""
    for effect in effects:
        file = effect["file"].upper()
        if effect["id"] is not None:
            texts = _fitting_labels(effect.get("labels"), effect["knobs"])
            _read[file] = {"id": f"{effect['id']:08X}", "name": effect["name"], "knobs": effect["knobs"],
                           "size": effect["size"], "dsp": effect.get("dsp"), **({"labels": texts} if texts else {}),
                           "since": READ_SINCE}
        elif not effect.get("unread"):
            _read[file] = {"id": None, "size": effect["size"]}
        elif _read.get(file, {}).get("size") != effect["size"] or _read[file].get("unread"):
            _read[file] = {"id": None, "size": effect["size"], "unread": True}   # what was read of it stays
    _store()


def forget_unlisted(entries) -> None:
    """After a complete list of the pedal's files [(name, size)]: what was read of a file that is
    not on the pedal any more, or not at that size, is forgotten (the table, or reading it again,
    tells what it is now)."""
    listed = {name.upper(): size for name, size in entries}
    stale = [file for file, entry in _read.items() if file not in listed or listed[file] != entry.get("size")]
    for file in stale:
        del _read[file]
    if stale:
        _store()


def tried(name: str, size: Optional[int]) -> bool:
    """Whether that file could not be read when it was tried (it comes after the others)."""
    entry = _read.get(name.upper())
    return entry is not None and bool(entry.get("unread")) and entry.get("size") == size


def described(name: str, size: Optional[int]) -> bool:
    """Whether the file of that name and size on the pedal is described: read from the pedal at
    that size (also as one that holds no effect) – an effect with the DSP it takes, as read since
    2026-10-22 (use_cache), and read since READ_SINCE – or in the table as that build (_same_build).
    Never one that is read at every Read Pedal (ALWAYS_READ)."""
    file = name.upper()
    if file in ALWAYS_READ:
        return False
    read = _read.get(file)
    if (read is not None and not read.get("unread") and (size is None or read.get("size") == size)
            and (read.get("id") is None or ("dsp" in read and read.get("since", "") >= READ_SINCE))):
        return True
    if _table is None:
        table()
    key = next((key for key, entry in _table.items() if entry.get("file") == file), None)
    return key is not None and _same_build(key, _table[key], size)


def _numbers(known: dict) -> dict:
    global _by_number
    if _by_number is None or _by_number[0] is not known:      # made again for another table (tests)
        index: dict = {}
        for key, entry in known.items():
            index.setdefault(int(key, 16) & 0xFFFFFF, []).append(entry)
        _by_number = (known, index)
    return _by_number[1]


def effect(effect_id: Optional[int]) -> Optional[dict]:
    """{name, knobs} of an effect by its id from a patch dump; the id's category byte may differ
    from the file's (as with the synth effects), then its lower 24 bits decide, if only one has them."""
    if not effect_id:
        return None
    known = table()
    exact = known.get(f"{effect_id:08X}")
    if exact:
        return exact
    same = _numbers(known).get(effect_id & 0xFFFFFF, [])
    return same[0] if len(same) == 1 else BUILT_IN.get(effect_id & 0xFFFFFF) if not same else None


def by_file(name: str) -> Optional[int]:
    """The id of the effect whose file is called so on the pedal ("HOGI.ZD2"), None if not known."""
    known = table()
    return next((int(key, 16) for key, entry in known.items() if entry.get("file") == name.upper()), None)


def knobs(effect_id: Optional[int]) -> list:
    """[{param, name, max}] of the knobs of an effect, param as sent to the pedal; empty if unknown."""
    entry = effect(effect_id)
    return [{"param": FIRST_KNOB + index, "name": name, "max": highest}
            for index, (name, highest) in enumerate(entry["knobs"] if entry else ())
            if name not in HIDDEN and highest > 0]


def dsp(effect_id: Optional[int]) -> Optional[float]:
    """The DSP an effect takes, as its file declares it (an entry's "dsp"; the pedal adds those of a
    preset's effects up, on or off); None if not known."""
    entry = effect(effect_id)
    load = entry.get("dsp") if entry else None
    return float(load) if _is_load(load) else None


def version() -> int:
    """A number that changes whenever the table or what was read from the pedal itself does: the
    interface then asks for the texts of the knobs' values again (labels)."""
    global _seen, _version
    table()
    if _seen is None or _seen[0] is not _table or _seen[1] != _read_version:
        _seen, _version = (_table, _read_version), _version + 1
    return _version


def labels(effect_id: Optional[int]) -> dict:
    """{param: [the text the pedal shows for each value 0..highest]} of the knobs of an effect
    whose text is not just the number (entry "labels", see write_table); empty if none or unknown."""
    entry = effect(effect_id)
    return {int(param): texts for param, texts in (entry.get("labels") or {}).items()} if entry else {}


def write_table(made: dict, path: Path = TABLE) -> None:
    """The table as zoom_effects.json holds it: one effect per entry, each list of value texts
    ("labels": {param as a string: [text per value 0..highest]}) on a line of its own. A text is
    as the pedal shows it without padding; note values are the pedal's own characters 0x16 (1/32)
    to 0x1A (1/2), the interface writes them out."""
    lines = ["{"]
    for number, (key, entry) in enumerate(made.items()):
        rest = {name: value for name, value in entry.items() if name != "labels"}
        body = json.dumps(rest, ensure_ascii=False)[:-1]
        texts = entry.get("labels") or {}
        if texts:
            body += ', "labels": {\n' + ",\n".join(
                f"   {json.dumps(param)}: {json.dumps(values, ensure_ascii=False)}" for param, values in texts.items()) + "}"
        lines.append(f" {json.dumps(key)}: {body}}}" + ("," if number < len(made) - 1 else ""))
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv) -> None:
    if not argv or argv == ["--dsp"]:
        sys.exit(__doc__)
    if argv[0] == "--dsp":                        # only the DSP of the table's effects: the same effects
        made = json.loads(TABLE.read_text(encoding="utf-8"))
        done = set_dsp(made, argv[1:])
        write_table(made, TABLE)
        print(f"The DSP of {len(done)} of {len(made)} effects written to {TABLE.name}")
        return
    made = build(argv)
    if not made:
        sys.exit("No .ZD2 effect files found there.")
    try:
        before = json.loads(TABLE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        before = {}
    write_table(kept_labels(made, before if isinstance(before, dict) else {}), TABLE)
    print(f"{len(made)} effects written to {TABLE.name}")


if __name__ == "__main__":
    main(sys.argv[1:])
