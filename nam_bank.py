"""An N2Z Bank (N2ZBANK.ZD2 + .ZIC) from 1–10 Compact NAM models, for the installer in the bridge
(workshop.py, the NAM tab).

It uses nam2zoom's own packaging as it is: its precompiled bank templates, with only the
weights and the Model knob's labels filled in. Nothing is compiled, and the DSP code stays
untouched. nam/ is a byte-exact copy of that part of nam2zoom 1.2.3 (Aleksandar Vukasinovic, MIT,
nam/LICENSE); the templates check the DSP sources by SHA-256, so .gitattributes keeps their line
endings. Training (other NAMs into Compact ones) stays with nam2zoom.app, and so does its own
upload. Here the Zoom MS Installer installs the bank."""
from __future__ import annotations

import json
import re
import struct
import sys
import tempfile
from pathlib import Path
from typing import Optional

NAM = Path(__file__).resolve().parent / "nam"
sys.path.insert(0, str(NAM / "tools"))
from nam2zoom.bank import MAX_MODELS, build_bank, load_models, normalize_labels, prepare_bank  # noqa: E402
from nam2zoom.hybrid import classify  # noqa: E402

MAX_NAM = 16 * 1024 * 1024      # per model in a bank: nam2zoom's own limit (bank.load_models)
MAX_CHECK = 64 * 1024 * 1024    # what nam2zoom looks at to say whether it can train it (hybrid.classify)
ID_AT = 96                      # where a ZD2 holds its effect id


def _file(folder: str, name: str, index: int = 0) -> Path:
    """Where an uploaded model is kept for a moment: its own name, without anything a path could do."""
    stem = re.sub(r"[^A-Za-z0-9 _().-]", "", Path(name).stem)[:60].strip(" .") or "model"
    return Path(folder) / f"{index:02d}" / f"{stem}.nam"


def check(name: str, data: bytes) -> dict:
    """What nam2zoom makes of one .nam: "direct" (a Compact model, into a bank as it is),
    "adaptable" (convert it in nam2zoom.app first) or "unsupported", why, and the label it gets."""
    with tempfile.TemporaryDirectory() as folder:
        path = _file(folder, name)
        path.parent.mkdir()
        path.write_bytes(data)
        try:
            result = classify(path) if data else {"status": "unsupported", "reason": "the file is empty"}
        except (ValueError, TypeError, KeyError, AttributeError) as error:   # also JSON of another shape
            result = {"status": "unsupported", "reason": str(error)}
        result["label"] = normalize_labels([path], None)[0]
    return result


def build(models: list, labels: list) -> tuple:
    """The N2ZBANK.ZD2 and .ZIC of the models [(file name, bytes)], with these labels on the
    pedal, as nam2zoom.app builds them. Raises ValueError with nam2zoom's reason."""
    with tempfile.TemporaryDirectory() as folder:
        paths = []
        for index, (name, data) in enumerate(models):
            path = _file(folder, name, index)
            path.parent.mkdir()
            path.write_bytes(data)
            paths.append(path)
        try:
            effect = build_bank(prepare_bank(load_models(paths, labels), Path(folder) / "bank"))
        except (OSError, ValueError) as error:          # its reasons name the file, not where it was kept
            raise ValueError(re.sub(re.escape(folder) + r"/\d+/", "", str(error))) from error
        return effect.read_bytes(), effect.with_suffix(".ZIC").read_bytes()


def patches_using_bank(data_root: str) -> Optional[dict]:
    """The saved patches that hold the N2Z Bank on the pedal, as the newest complete backup of the
    installer has them: {"backup": which, "patches": ["012 NAME", …]}; None without a backup.
    nam2zoom.app refuses to replace its bank while any saved patch has an effect that is not
    Zoom's own; here they are named, and the choice is yours."""
    newest = None
    for manifest in Path(data_root, "Backups").glob("*/manifest.json"):
        try:
            info = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if info.get("complete") is True and (newest is None or info.get("created_ts", 0) > newest[0].get("created_ts", 0)):
            newest = (info, manifest.parent)
    if newest is None:
        return None
    info, folder = newest
    found = []
    bank = folder / "files" / "N2ZBANK.ZD2"
    if bank.is_file():
        effect_id = struct.unpack_from("<I", bank.read_bytes(), ID_AT)[0]
        for patch in sorted((folder / "patches").glob("*.zptc")):
            data = patch.read_bytes()
            count = int.from_bytes(data[12:16], "little") if data[:4] == b"PTCF" else 0
            ids = [int.from_bytes(data[36 + 4 * slot:40 + 4 * slot], "little")
                   for slot in range(min(count, 6)) if 40 + 4 * slot <= len(data)]
            if effect_id in ids:                         # name: 10 characters at 26 (zoom_sysex.parse_patch_dump)
                found.append(f"{patch.stem} {data[26:36].decode('ascii', 'replace').strip(chr(0) + ' ')}".strip())
    return {"backup": f"{info.get('model', '?')}, {str(info.get('created', '')).replace('T', ' ')}", "patches": found}
