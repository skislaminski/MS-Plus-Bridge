"""Sets the value texts ("labels") of the effect table (zoom_effects.json) from the effect files:
for every effect of the table whose file is in the folders, every knob's display function is run
for every value 0..highest (c6x.py), and the knobs whose text is not just the number get it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import effect_names

from . import c6x, listing, zd2

SKIP = ("_REBUILT", "_TEMPLATE")     # intermediates of a custom build, next to the file itself


def effect_files(folders) -> Dict[str, List[Path]]:
    """File name as the pedal lists it ("DRUMBOX.ZD2") → the files of that name in the folders or
    below, newest first."""
    found: Dict[str, List[Tuple[float, Path]]] = {}
    for folder in folders:
        for path in Path(folder).expanduser().rglob("*"):
            name = path.name.upper()
            if path.suffix.upper() != ".ZD2" or any(part in name for part in SKIP) or not path.is_file():
                continue
            found.setdefault(name, []).append((path.stat().st_mtime, path))
    return {name: [path for _, path in sorted(paths, key=lambda item: -item[0])] for name, paths in found.items()}


def choose(paths: List[Path], size: Optional[int]) -> Path:
    """Of the files of one name the newest of the build the table describes (its size), else the newest."""
    return next((path for path in paths if size is not None and path.stat().st_size == size), paths[0])


def value_texts(data: bytes, dis6x: str) -> Tuple[List[list], Dict[int, List[str]], Dict[int, str]]:
    """Of an effect file: its knobs as its parameter table has them ([name, highest]), the text for
    each value of each knob that has a display function ({param: texts}, as the code writes them),
    and why it could not be run for those that failed ({param: reason})."""
    elf = zd2.elf_of(data)
    if elf is None:
        raise ValueError("no ELF in its DATA chunk")
    entries = zd2.parameter_table(elf)
    if len(entries) < 2:
        raise ValueError("no parameter table in its .const section")
    code = listing.Listing(listing.disassemble(elf.data, dis6x))
    segments = elf.segments()
    knobs, texts, failed = [], {}, {}
    for param, entry in enumerate(entries[effect_names.FIRST_KNOB:], start=effect_names.FIRST_KNOB):
        knobs.append([entry.name, entry.words[zd2.HIGHEST]])
        function = entry.words[zd2.DISPLAY]
        if not function:
            continue                          # the pedal shows the number
        try:
            texts[param] = c6x.texts(code, segments, function, entry.words[zd2.HIGHEST])
        except c6x.NotModelled as error:
            failed[param] = str(error)
    return knobs, texts, failed


def labels_of(texts: Dict[int, List[str]]) -> Dict[str, List[str]]:
    """The table's "labels" of an effect: for each knob whose text is not just the number, the text
    of each value without the padding spaces (the note characters 0x16..0x1A stay as they are)."""
    out = {}
    for param, values in sorted(texts.items()):
        shown = [text.strip(" ") for text in values]
        if any(text != str(value) for value, text in enumerate(shown)):
            out[str(param)] = shown
    return out


def update(table: dict, folders, dis6x: str, say=print) -> dict:
    """Sets the labels of the table's entries whose file is in the folders (in place); an entry
    whose file is not there keeps what it has, and so does one whose file has other knobs than
    the table (with a warning). Returns what was done: effects (read from their files), labelled (effects with text), knobs
    (with text), failed ([(effect, knob, reason)]), skipped ([(effect, reason)]), missing (files
    not found), other_builds (files read that are not of the size the table has)."""
    files = effect_files(folders)
    done = {"effects": 0, "labelled": 0, "knobs": 0, "failed": [], "skipped": [], "missing": 0, "other_builds": 0}
    for key, entry in table.items():
        paths = files.get(str(entry.get("file", "")).upper())
        if not paths:
            done["missing"] += 1
            continue
        path = choose(paths, entry.get("size"))
        effect = f"{key} {entry.get('name')} ({path.name})"
        data = path.read_bytes()
        read = effect_names.read_zd2(data)
        if read is None or read["knobs"] != entry.get("knobs"):
            done["skipped"].append((effect, f"{path} has other knobs than the table"))
            say(f"warning: {effect}: {path} has other knobs than the table, skipped")
            continue
        try:
            knobs, texts, failed = value_texts(data, dis6x)
        except (ValueError, RuntimeError, OSError) as error:
            done["skipped"].append((effect, str(error)))
            say(f"warning: {effect}: {error}, skipped")
            continue
        if knobs != read["knobs"]:
            done["skipped"].append((effect, "the ELF's parameter table has other knobs than read_zd2 reads"))
            say(f"warning: {effect}: the ELF's parameter table has other knobs than read_zd2 reads, skipped")
            continue
        done["effects"] += 1
        done["other_builds"] += entry.get("size") not in (None, len(data))
        for param, reason in sorted(failed.items()):
            name = knobs[param - effect_names.FIRST_KNOB][0]
            done["failed"].append((effect, name, reason))
            say(f"failed: {effect} {name}: {reason}")
        labels = labels_of(texts)
        for param, found in sorted((read.get("labels") or {}).items(), key=lambda item: int(item[0])):
            if labels.get(param) != found:        # read_zd2 reads the custom builds' tables itself
                say(f"warning: {effect} {knobs[int(param) - effect_names.FIRST_KNOB][0]}: read_zd2 reads "
                    f"other texts ({found[:4]} …) than its code writes ({(labels.get(param) or [])[:4]} …)")
        if labels:
            entry["labels"] = labels
            done["labelled"] += 1
            done["knobs"] += len(labels)
        else:
            entry.pop("labels", None)
    return done


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.value_labels",
                                     description="Sets the value texts of zoom_effects.json from the effect files (.ZD2).")
    parser.add_argument("folders", nargs="+", metavar="FOLDER", help="folders with .ZD2 files in them or below")
    parser.add_argument("--dis6x", metavar="PATH", help="TI's disassembler (else TI_CGT_DIS6X, the PATH, "
                                                         f"{listing.DEFAULT_DIS6X})")
    parser.add_argument("--table", metavar="PATH", default=str(effect_names.TABLE),
                        help="the table to update (default: zoom_effects.json of the bridge)")
    parser.add_argument("--dry-run", action="store_true", help="only say what would be set")
    args = parser.parse_args(argv)
    dis6x = listing.dis6x_path(args.dis6x)
    if dis6x is None:
        print(f"dis6x not found ({args.dis6x or 'give it with --dis6x or TI_CGT_DIS6X'}); "
              "it comes with TI's C6000 code generation tools.", file=sys.stderr)
        return 1
    path = Path(args.table)
    table = json.loads(path.read_text(encoding="utf-8"))
    done = update(table, args.folders, dis6x)
    print(f"{done['effects']} of {len(table)} effects read from their files ({done['other_builds']} of another "
          f"build than the table's), {done['missing']} not found (they keep their texts), {len(done['skipped'])} skipped; "
          f"{done['knobs']} knobs of {done['labelled']} effects show text, {len(done['failed'])} knobs failed.")
    if args.dry_run:
        print("Nothing written (--dry-run).")
    else:
        effect_names.write_table(table, path)
        print(f"Written: {path}")
    return 0
