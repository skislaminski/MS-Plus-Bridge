"""The settings file for a bridge that runs without screen and keyboard (Raspberry Pi), and its rules.

A settings file is config.yaml plus what otherwise lives in files of its own, so that one file
carries everything the bridge needs:

    schema_version   2 (files of version 1 are read as well)
    exported_at      when export.py wrote it
    learned          learned ranges per preset: {"9/4": [{slot, param, min, max}, …]}
    pedal_effects    the effects the pedal reported in its presets, by id: ["01000020", …]
    approvals        {"backup_confirmed": timestamp, "messages": {message kind: timestamp}}
    controls         which controller of the keyboard sets which knob of each synth effect (synth_controls.py)
    arp              the arpeggiator's settings that are not the defaults (arpeggiator.py)
    seq              the step sequencer's pattern (sequencer.py)
    effects          which controller switches which effect of the chain on and off (effect_switch.py)
    scenes           sets of parameter values per preset that a press of a button recalls (scenes.py)

It holds values only: never code, and never raw SysEx bytes. Messages are still built in
zoom_sysex.py alone. export.py (on the computer where everything was set up) and bridge.py
(where the file is read) both check a file with validate(), so they cannot disagree.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Optional

import yaml

import arpeggiator
import effect_switch
import scenes as scene_file
import sequencer
import synth_controls
import zoom_sysex as zs

SCHEMA_VERSION = 2       # 2: pedal_effects, the effect of an assignment, the chain of a scene (2026-10-20)
FILE_NAME = "expression-bridge.yaml"
PREVIOUS_NAME = "expression-bridge.prev.yaml"
EFFECTS_NAME = "pedal-effects.json"           # beside it: the effects read from the pedal's own files

REQUIRED_KINDS = ("identity_request", "edit_enable", "edit_disable", "set_param", "query_program")
SYNTH_KINDS = ("query_patch",)                 # needed in addition when a keyboard plays a synth effect
SWITCH_KINDS = ("query_patch", "switch_effect")    # … and when controllers switch effects on and off
CURVES = ("linear", "log", "exp")
MAX_TARGETS = 4                                # parameters one controller (the expression pedal, a knob) sets per preset
MAX_ASSIGNMENTS = 12                           # … and all of them together
RESERVED_CONTROLS = synth_controls.RESERVED_CONTROLS
MAX_EFFECTS = 6                                # without a learned range: effect 1–6 …
MAX_PARAMS = 12                                # … and parameter 1–12 only
UNLEARNED_MAX = 0x3FFF
MAX_PEDAL_EFFECTS = 512                        # what the pedal reported: more than it can hold
MAX_BEND_RANGE = 12

SECTIONS = {
    "ports": {"zoom", "chocolate"},
    "zoom": {"device_id", "firmware"},
    "expression": {"cc", "channel", "min", "max", "invert"},
    "bridge": {"min_interval_ms", "deadband", "passthrough"},
    "ui": {"port", "listen"},
    "synth": {"keyboard", "channel", "external", "external_channel", "effect_id", "poly_effect_id", "low_note",
              "high_note", "sustain", "bend_range"},
    "pi": {"wlan_name", "wlan_password"},          # the Pi's own WLAN, where no other one is around
}
OPTIONAL = ("ui", "synth", "pi")
WLAN_NAME = re.compile(r"[ -~]{1,32}")                 # what a phone shows, printable ASCII
WLAN_PASSWORD = re.compile(r"[ -~]{8,63}")             # WPA2
EFFECT_ID = re.compile(r"[0-9A-Fa-f]{8}")               # an effect as the pedal's patch names it, in hex
EXTRA = {"schema_version", "exported_at", "learned", "pedal_effects", "approvals", "controls", "mappings", "arp",
         "seq", "effects", "scenes"}
# A message, not a name or a value: four or more two-digit hex numbers in a row ("F0 52 00 6E"),
# or one run of hex from F0 to F7. Dates and times do not match (their numbers are joined by - and :).
LOOKS_LIKE_BYTES = re.compile(r"(?:\b[0-9A-Fa-f]{2}\b[\s,]+){3,}\b[0-9A-Fa-f]{2}\b|\b[Ff]0(?:[0-9A-Fa-f]{2})+[Ff]7\b")


def _is_int(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _preset(key) -> Optional[tuple]:
    """"bank/program" → (bank, program), or None if it is not a preset key."""
    bank, _, program = str(key).rpartition("/")
    try:
        patch = (int(bank or 0), int(program))
    except ValueError:
        return None
    return patch if 0 <= patch[0] <= 0x3FFF and 0 <= patch[1] <= 0x7F else None


def _strings(value, where: str):
    """Every string or bytes value in the data, with the place it sits in."""
    if isinstance(value, (str, bytes, bytearray)):
        yield where, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key, f"{where}.{key}" if where else str(key))
            yield from _strings(item, f"{where}.{key}" if where else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _strings(item, f"{where}[{index}]")


def validate(data) -> list:
    """Everything that is wrong with a settings file, as sentences; an empty list means it is valid."""
    if not isinstance(data, dict):
        return ["The file does not contain settings."]
    problems = []
    add = problems.append
    version = data.get("schema_version")
    if isinstance(version, int) and not isinstance(version, bool) and version > SCHEMA_VERSION:
        return [f"Made by a newer program (schema_version {version}): write the card with this computer's "
                "program again (Write Pi Card.command), a USB stick carries the settings only."]

    for key in data:                                       # a typo must not be skipped silently
        if key not in SECTIONS and key not in EXTRA:
            add(f"Unknown entry “{key}”.")
    for name, allowed in SECTIONS.items():
        section = data.get(name)
        if section is None:
            if name not in OPTIONAL:
                add(f"The section “{name}” is missing.")
        elif not isinstance(section, dict):
            add(f"“{name}” must be a section with settings.")
        else:
            for key in section:
                if key not in allowed:
                    add(f"Unknown setting “{key}” in “{name}”.")
    for where, text in _strings(data, ""):                 # values only, never messages
        if isinstance(text, (bytes, bytearray)) or LOOKS_LIKE_BYTES.search(text):
            add(f"“{where}” looks like raw bytes; a settings file holds values only.")
    if problems:
        return problems                                    # the shape is wrong: the rest would only confuse

    if not (isinstance(version, int) and not isinstance(version, bool) and 1 <= version <= SCHEMA_VERSION):
        add(f"schema_version must be 1 to {SCHEMA_VERSION}.")
    if "exported_at" in data and not isinstance(data["exported_at"], str):
        add("exported_at must be a text.")

    ports, zoom, expression, bridge = data["ports"], data["zoom"], data["expression"], data["bridge"]
    synth = data.get("synth") or {}
    keyboard = bool(synth.get("keyboard") or synth.get("external"))    # something plays the synth effect
    if not isinstance(ports.get("zoom"), str) or not ports.get("zoom"):
        add("ports: the name of the pedal's port is missing.")
    if not isinstance(ports.get("chocolate"), str):
        add("ports: chocolate must be a text (empty if there is no expression controller).")
    if not _is_int(zoom.get("device_id"), 0, 0x7F):
        add("zoom: device_id must be a number from 0 to 127.")
    if not isinstance(zoom.get("firmware"), str):
        add("zoom: firmware must be a text.")
    ui = data.get("ui") if isinstance(data.get("ui"), dict) else {}
    if "listen" in ui and not isinstance(ui["listen"], bool):
        add("ui: listen must be true or false.")

    if ports.get("chocolate"):                             # only then is the pedal's CC needed
        if not _is_int(expression.get("cc"), 0, 127):
            add("expression: cc must be a number from 0 to 127.")
        if not _is_int(expression.get("channel"), 1, 16):
            add("expression: channel must be a number from 1 to 16.")
    low, high = expression.get("min"), expression.get("max")
    if not (_is_int(low, 0, 127) and _is_int(high, 0, 127) and low < high):
        add("expression: min and max must be numbers from 0 to 127, with min below max.")
    if not isinstance(expression.get("invert"), bool):
        add("expression: invert must be true or false.")

    wlan = data.get("pi") or {}
    if "wlan_name" in wlan and not (isinstance(wlan["wlan_name"], str) and WLAN_NAME.fullmatch(wlan["wlan_name"])):
        add("pi: wlan_name must be a text of 1 to 32 letters, digits, spaces or signs.")
    if "wlan_password" in wlan and not (isinstance(wlan["wlan_password"], str)
                                        and WLAN_PASSWORD.fullmatch(wlan["wlan_password"])):
        add("pi: wlan_password must be 8 to 63 letters, digits or signs.")

    interval = bridge.get("min_interval_ms")
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 1 <= interval <= 1000:
        add("bridge: min_interval_ms must be a number from 1 to 1000.")
    if not _is_int(bridge.get("deadband", 0), 0, 127):
        add("bridge: deadband must be a number from 0 to 127.")
    if not isinstance(bridge.get("passthrough", False), bool):
        add("bridge: passthrough must be true or false.")

    if keyboard:
        for name in ("keyboard", "external"):
            if not isinstance(synth.get(name, ""), str):
                add(f"synth: {name} must be a text.")
        if synth.get("keyboard") and synth.get("keyboard") == synth.get("external"):
            add("synth: keyboard and external must be two different ports.")
        for name in ("channel", "external_channel"):
            if not _is_int(synth.get(name, 0), 0, 16):
                add(f"synth: {name} must be a number from 0 to 16 (0 = any).")
        ids = {name: synth.get(name) for name in ("effect_id", "poly_effect_id") if synth.get(name) is not None}
        for name, number in ids.items():        # only a build with an id of its own needs one of them
            effect = zs.KEYPOLY if name.startswith("poly") else zs.KEYSYNTH
            if not _is_int(number, 1, 0xFFFFFFFF) or zs.key_effect(number) not in (None, effect):
                add(f"synth: {name} must be the id of the {effect.name} effect.")
        if len(ids) == 2 and ids["effect_id"] == ids["poly_effect_id"]:
            add("synth: effect_id and poly_effect_id must be two different effects.")
        note_low, note_high = synth.get("low_note", 0), synth.get("high_note", 127)
        if not (_is_int(note_low, 0, 127) and _is_int(note_high, 0, 127) and note_low <= note_high):
            add("synth: low_note and high_note must be notes from 0 to 127, with low_note ≤ high_note.")
        if not isinstance(synth.get("sustain", True), bool):
            add("synth: sustain must be true or false.")
        bend = synth.get("bend_range", 2)
        if isinstance(bend, bool) or not isinstance(bend, (int, float)) or not 0 <= bend <= MAX_BEND_RANGE:
            add(f"synth: bend_range must be a number from 0 to {MAX_BEND_RANGE}.")

    learned = data.get("learned", {})
    ranges = {}                                            # (preset, slot, param) → (min, max)
    if not isinstance(learned, dict):
        add("learned must list ranges per preset.")
        learned = {}
    for key, entries in learned.items():
        patch = _preset(key)
        if patch is None or not isinstance(entries, list):
            add(f"learned: “{key}” is not a preset with a list of ranges.")
            continue
        for entry in entries:
            if not (isinstance(entry, dict) and set(entry) == {"slot", "param", "min", "max"}
                    and _is_int(entry["slot"], 0, 0x7F) and _is_int(entry["param"], zs.MIN_PARAM, 0x7F)
                    and zs.is_effect_param(entry["slot"], entry["param"])
                    and _is_int(entry["min"], 0, UNLEARNED_MAX) and _is_int(entry["max"], entry["min"], UNLEARNED_MAX)):
                add(f"learned: preset {key} has an entry that is not a valid range.")
                continue
            ranges[(patch, entry["slot"], entry["param"])] = (entry["min"], entry["max"])
    pedal_effects = data.get("pedal_effects", [])
    if not (isinstance(pedal_effects, list) and len(pedal_effects) <= MAX_PEDAL_EFFECTS
            and all(isinstance(text, str) and EFFECT_ID.fullmatch(text) and int(text, 16) for text in pedal_effects)):
        add(f"pedal_effects must list at most {MAX_PEDAL_EFFECTS} effects, each as its id in eight hex digits.")

    mappings = data.get("mappings")
    if not isinstance(mappings, dict):
        add("mappings must list the assignments per preset (an empty one is written as {}).")
        mappings = {}
    for key, entries in mappings.items():
        patch = _preset(key)
        if patch is None or not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ASSIGNMENTS:
            add(f"mappings: “{key}” must be a preset with 1 to {MAX_ASSIGNMENTS} parameters.")
            continue
        seen = set()
        per_controller: dict = {}
        for entry in entries:
            if not (isinstance(entry, dict)
                    and {"slot", "param", "min", "max"} <= set(entry)
                    <= {"slot", "param", "min", "max", "invert", "curve", "name", "cc", "effect"}
                    and all(_is_int(entry[field], 0, UNLEARNED_MAX) for field in ("slot", "param", "min", "max"))
                    and (entry.get("effect") is None or (isinstance(entry["effect"], str)
                                                          and EFFECT_ID.fullmatch(entry["effect"])))):
                add(f"mappings: preset {key} has an entry that is not a valid assignment.")
                continue
            cc = entry.get("cc")
            if cc is not None and not (_is_int(cc, 0, 127) and cc not in RESERVED_CONTROLS):
                add(f"mappings: preset {key} has a controller that cannot set a parameter ({cc}).")
            else:
                per_controller[cc] = per_controller.get(cc, 0) + 1
            place = (entry["slot"], entry["param"])
            where = f"preset {key}, effect {entry['slot'] + 1}, parameter {entry['param'] - 1}"
            if place in seen:
                add(f"mappings: {where} is assigned twice.")
            seen.add(place)
            limits = ranges.get((patch,) + place)
            if limits is None:                             # not learned: the fixed grid only
                if not (entry["slot"] < MAX_EFFECTS and zs.MIN_PARAM <= entry["param"] < zs.MIN_PARAM + MAX_PARAMS):
                    add(f"mappings: {where} is outside effects 1–{MAX_EFFECTS} / parameters 1–{MAX_PARAMS} "
                        "and has no learned range.")
                elif not entry["min"] <= entry["max"]:
                    add(f"mappings: {where} has min above max.")
            elif not limits[0] <= entry["min"] <= entry["max"] <= limits[1]:
                add(f"mappings: {where} leaves its learned range {limits[0]}–{limits[1]}.")
            if not isinstance(entry.get("invert", False), bool):
                add(f"mappings: {where}: invert must be true or false.")
            if entry.get("curve", "linear") not in CURVES:
                add(f"mappings: {where}: unknown curve.")
            if not isinstance(entry.get("name", ""), str) or len(entry.get("name", "")) > 40:
                add(f"mappings: {where}: the name must be a text of at most 40 characters.")
        if any(count > MAX_TARGETS for count in per_controller.values()):
            add(f"mappings: in preset {key} one controller sets more than {MAX_TARGETS} parameters.")

    approvals = data.get("approvals")
    if not (isinstance(approvals, dict) and set(approvals) <= {"backup_confirmed", "messages"}
            and isinstance(approvals.get("messages"), dict)):
        add("approvals must hold backup_confirmed and the approved message kinds.")
    else:
        if not approvals.get("backup_confirmed"):
            add("approvals: the backup of the patches has not been confirmed (python probe.py approve).")
        for kind in approvals["messages"]:
            if kind not in zs.KINDS:
                add(f"approvals: “{kind}” is not a message kind of the bridge.")
        switching = data.get("effects") or (isinstance(data.get("scenes"), dict) and any(
            isinstance(scene, dict) and scene.get("effects")
            for entries in data["scenes"].values() if isinstance(entries, list) for scene in entries))
        needed = REQUIRED_KINDS + (SYNTH_KINDS if keyboard else ()) + (SWITCH_KINDS if switching else ())
        missing = [kind for kind in dict.fromkeys(needed) if not approvals["messages"].get(kind)]
        if missing:
            add(f"approvals: not approved: {', '.join(missing)} (python probe.py approve).")

    controls = data.get("controls", {})
    for problem in synth_controls.problems(controls):
        add(problem)
    for problem in arpeggiator.problems(data.get("arp", {})) + sequencer.problems(data.get("seq", {})):
        add(problem)
    for problem in scene_file.problems(data.get("scenes", {})):
        add(problem)
    switches = data.get("effects", {})
    for problem in effect_switch.problems(switches):
        add(problem)
    if isinstance(controls, dict) and not synth_controls.problems(controls) and not effect_switch.problems(switches):
        if set(controls.values()) & {switch["cc"] for switch in switches.values()}:
            add("effects: one controller both switches an effect and is assigned on the keyboard.")
    return problems


class Approvals:
    """The approvals of a settings file: what zs.Approvals is on the computer, but read-only."""

    def __init__(self, data: dict):
        self._data = data

    @property
    def backup_confirmed(self) -> bool:
        return bool(self._data.get("backup_confirmed"))

    def is_approved(self, kind: str) -> bool:
        return bool(self._data.get("messages", {}).get(kind))


class Measurements:
    """The learned ranges and the effects the pedal reported of a settings file: what
    zs.Measurements is on the computer, kept in memory; a bridge whose settings can be changed
    over the network writes them into the file."""

    def __init__(self, learned: dict, effects=()):
        self.patches = {preset: {f"{entry['slot']}/{entry['param']}": dict(entry) for entry in entries}
                        for preset, entries in learned.items()}
        self.effects: set = set(effects)

    def found(self, effect_ids) -> bool:
        """Records the effects of a patch the pedal reported, as zs.Measurements does."""
        new = {effect_id for effect_id in effect_ids if effect_id} - self.effects
        self.effects |= new
        return bool(new)

    def get(self, patch: tuple, slot: int, param: int) -> Optional[dict]:
        return self.patches.get(f"{patch[0]}/{patch[1]}", {}).get(f"{slot}/{param}")

    def for_patch(self, patch: tuple) -> list:
        return list(self.patches.get(f"{patch[0]}/{patch[1]}", {}).values())

    def add(self, patch: tuple, slot: int, param: int, values) -> dict:
        """Widens the range by values the pedal reported, as zs.Measurements does."""
        known = self.get(patch, slot, param) or {}
        low = min([*values, known["min"]] if known else values)
        high = max([*values, known["max"]] if known else values)
        entry = {"slot": slot, "param": param, "min": low, "max": high}
        self.patches.setdefault(f"{patch[0]}/{patch[1]}", {})[f"{slot}/{param}"] = entry
        return entry

    def move(self, patch: tuple, moves: dict) -> bool:
        """The learned ranges of effects that moved in the chain go with them (moves: place → new
        place); a range left in a new place by an effect that is gone makes way for one that moves
        in on the same parameter, and stays otherwise (an assignment kept there may need it).
        Returns whether anything changed."""
        key = f"{patch[0]}/{patch[1]}"
        entries = self.patches.get(key, {})
        if not any(entry["slot"] in moves for entry in entries.values()):
            return False
        kept = {name: entry for name, entry in entries.items() if entry["slot"] not in moves}
        for entry in entries.values():
            if entry["slot"] in moves:
                entry = dict(entry, slot=moves[entry["slot"]])
                kept[f"{entry['slot']}/{entry['param']}"] = entry
        self.patches[key] = kept
        return True

    def preset(self, patch: tuple):
        """A copy of the learned ranges of a preset, None if it has none."""
        entries = self.patches.get(f"{patch[0]}/{patch[1]}")
        return {name: dict(entry) for name, entry in entries.items()} if entries else None

    def set_preset(self, patch: tuple, entries) -> None:
        """Puts back what preset() gave."""
        key = f"{patch[0]}/{patch[1]}"
        if entries:
            self.patches[key] = {name: dict(entry) for name, entry in entries.items()}
        else:
            self.patches.pop(key, None)

    def copy(self, source: tuple, target: tuple) -> None:
        """The learned ranges of a preset for another one too (a preset stored into another place
        on the pedal); that place's own go."""
        entries, key = self.patches.get(f"{source[0]}/{source[1]}"), f"{target[0]}/{target[1]}"
        if entries:
            self.patches[key] = {name: dict(entry) for name, entry in entries.items()}
        else:
            self.patches.pop(key, None)

    def replace_effects(self, effect_ids) -> None:
        self.effects.clear()
        self.effects.update(effect_id for effect_id in effect_ids if effect_id)

    def forget(self, patch: tuple, places) -> bool:
        """Takes the learned ranges of (slot, param) places away: another effect's, where an
        assignment moved in without one of its own. Returns whether there was any."""
        entries = self.patches.get(f"{patch[0]}/{patch[1]}", {})
        gone = [name for name in (f"{slot}/{param}" for slot, param in places) if name in entries]
        for name in gone:
            del entries[name]
        return bool(gone)


def wlan_fingerprint(settings: dict) -> str:
    """Which WLAN of its own the settings give the Pi, as pi/install.sh notes it once it has set it
    up, and as the bridge compares it (empty: none). Both use this one function."""
    wlan = (settings or {}).get("pi") or {}
    if not (wlan.get("wlan_name") and wlan.get("wlan_password")):
        return ""
    return hashlib.sha256(f"{wlan['wlan_name']}\n{wlan['wlan_password']}".encode("utf-8")).hexdigest()[:16]


def build(config: dict, learned_patches: dict, approvals: dict, controls: dict, exported_at: str,
          arp: Optional[dict] = None, seq: Optional[list] = None, switches: Optional[dict] = None,
          scenes: Optional[dict] = None, pedal_effects=()) -> dict:
    """The settings file made from what the computer keeps in config.yaml, measurements.json,
    approvals.json and controls.json. Of the learned ranges only the limits go in, of the
    arpeggiator's settings only those that are not the defaults, and the sequencer's pattern and
    the switches of the effects (as effect_switch.read gives them) and the effects the pedal
    reported if there are any."""
    settings = {"schema_version": SCHEMA_VERSION, "exported_at": exported_at}
    settings.update({key: value for key, value in config.items() if key != "mappings"})
    settings["mappings"] = config.get("mappings") or {}
    settings["learned"] = {
        preset: [{field: entry[field] for field in ("slot", "param", "min", "max")}
                 for entry in sorted(entries.values(), key=lambda entry: (entry["slot"], entry["param"]))]
        for preset, entries in sorted(learned_patches.items())}
    if pedal_effects:
        settings["pedal_effects"] = [f"{effect_id:08X}" for effect_id in sorted(pedal_effects)]
    settings["approvals"] = {"backup_confirmed": approvals.get("backup_confirmed"),
                             "messages": dict(approvals.get("messages") or {})}
    settings["controls"] = dict(controls)
    if arp and arpeggiator.changed(arp):
        settings["arp"] = arpeggiator.changed(arp)
    if seq:
        settings["seq"] = {"steps": list(seq)}
    if switches:
        settings["effects"] = effect_switch.stored(switches)
    if scenes and scene_file.stored(scenes):
        settings["scenes"] = scene_file.stored(scenes)
    return settings


def dump(settings: dict, from_interface: bool = False) -> str:
    how = ("# Written by the bridge itself, changed in its interface - change them there, or on the\n"
           "# computer you set the bridge up on and export again, rather than editing this file.\n"
           if from_interface else
           "# Written by export.py - change the settings on the computer you set the bridge up on\n"
           "# and export again, rather than editing this file.\n")
    return ("# Settings of the MS Plus Bridge for a computer without screen (Raspberry Pi).\n"
            + how + yaml.safe_dump(settings, sort_keys=False, allow_unicode=True))


def read(text: str, name: str = FILE_NAME) -> tuple:
    """(settings, problems) of the text of a settings file."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None, [f"{name} is not a readable settings file."]
    problems = validate(data)
    return (None if problems else data), problems


def load(path: Path) -> tuple:
    """(settings, problems) of one file. A file that is missing or cannot be read is a problem too."""
    try:
        return read(Path(path).read_text(encoding="utf-8"), Path(path).name)
    except OSError as error:
        return None, [f"{Path(path).name} cannot be read: {error.strerror or error}."]
    except UnicodeDecodeError:
        return None, [f"{Path(path).name} is not a readable settings file."]


def store(text: str, folder: Path) -> Path:
    """Puts a settings file into the folder; the one that was there is kept as the previous one.
    The new file is written in full before it takes the name, so a power cut in between leaves
    the previous one to fall back to, never half a file."""
    target, previous = Path(folder) / FILE_NAME, Path(folder) / PREVIOUS_NAME
    written = Path(folder) / (FILE_NAME + ".new")
    with open(written, "w", encoding="utf-8") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    if target.exists():
        target.replace(previous)
    written.replace(target)
    try:                                   # the renames too (the Pi's boot partition is written lazily)
        handle = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)
    except OSError:                        # Windows opens no folder like this
        pass
    return target


def stick_text(place: Optional[Path]) -> Optional[str]:
    """The text of the settings file on a USB stick, None if there is none: place is the folder
    the stick is mounted as, or the folder sticks are mounted in (the file is then one level down)."""
    if place is None:
        return None
    try:
        found = [Path(place) / FILE_NAME] + sorted(Path(place).glob(f"*/{FILE_NAME}"))
        return next(path for path in found if path.is_file()).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, StopIteration):
        return None                                        # no stick, or it was just pulled out


def load_with_fallback(folder: Path) -> tuple:
    """The settings to run with: the current file, or the previous one if the current one is
    missing or invalid. Returns (settings or None, "current" / "previous" / None, what was wrong)."""
    current, problems = load(Path(folder) / FILE_NAME)
    if current is not None:
        return current, "current", []
    previous, older = load(Path(folder) / PREVIOUS_NAME)
    if previous is not None:
        return previous, "previous", problems
    return None, None, problems + older
