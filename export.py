#!/usr/bin/env python3
"""Writes the bridge and its settings onto a Raspberry Pi's SD card.

  python export.py                write to the Pi's boot partition (/Volumes/bootfs) and eject it
  python export.py --to FOLDER    write into another folder instead (a USB stick: the settings
                                  only); nothing is ejected
  python export.py --check        only say whether the settings are complete and valid

The Pi has no screen, so everything is set up on this computer: ports, assignments, learned
ranges, approvals, the keyboard's controllers, the arpeggiator and the step sequencer's pattern.
This script gathers them from config.yaml, measurements.json, approvals.json and controls.json
into one file, checks it with the same rules the Pi applies, and writes it as
expression-bridge.yaml. A file that is already there is kept as expression-bridge.prev.yaml; the
Pi falls back to it if the new one turns out to be unusable. The file holds values only, no code
and no SysEx bytes.

On a boot partition the program goes along, into expression-bridge/; the Pi runs it from there,
so writing the card again brings program and settings up to date together. On a card freshly
written with Raspberry Pi OS (Raspberry Pi Imager), the Pi is also told to install it at its first
start: pi/install.sh is added to the commands cloud-init runs then (user-data).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

import yaml

import bridge
import config_schema as cs
import effect_names
import zoom_sysex as zs

PROJECT = Path(__file__).resolve().parent      # the program, next to this file
DEFAULT_TARGET = Path("/Volumes/bootfs")       # how macOS shows the boot partition of Raspberry Pi OS
BOOT_MARKER = "config.txt"                     # every Raspberry Pi boot partition has it
PROGRAM_FOLDER = "expression-bridge"           # on the boot partition: what the Pi runs
PROGRAM = ("bridge.py", "zoom_sysex.py", "config_schema.py", "arpeggiator.py", "sequencer.py", "scenes.py",
           "effect_switch.py", "synth_controls.py", "status_led.py", "effect_names.py", "zoom_effects.json",
           "ui.py", "ui.html", "LICENSE",
           "pi/install.sh", "pi/expression-bridge.service", "pi/expression-bridge-setup.service",
           "pi/90-expression-bridge-stick.rules", "pi/bluetooth-midi.sh", "pi/expression-bridge-bluetooth.service")
CLOUD_CONFIG = "user-data"                     # what cloud-init does at the Pi's first start
INSTALL = ["bash", f"/boot/firmware/{PROGRAM_FOLDER}/pi/install.sh"]


def gather() -> dict:
    """The settings file as a dictionary, from the files of this installation."""
    if not bridge.CONFIG.exists():
        sys.exit("config.yaml is missing – set the bridge up first (README.md, “First-time setup”).")
    return bridge.gather_settings()


def write(settings: dict, folder: Path) -> Path:
    """Writes the file into the folder, keeping the one that was there, and reads it back."""
    target = cs.store(cs.dump(settings), folder)
    written, problems = cs.load(target)
    if problems or written != settings:
        sys.exit(f"Reading {target} back did not give what was written – do not use this card as it is."
                 + "".join(f"\n  - {problem}" for problem in problems))
    return target


def write_effects(folder: Path) -> Optional[Path]:
    """The effects this bridge read from the pedal's own files go along, beside the settings
    file, so that the Pi describes them (and plays a synth effect's build) from its first start.
    What the Pi read there itself stays; for the same file this computer's wins."""
    try:
        mine = json.loads(bridge.EFFECT_CACHE.read_text(encoding="utf-8")) if bridge.EFFECT_CACHE.exists() else {}
    except (OSError, ValueError):
        mine = {}
    if not isinstance(mine, dict) or not mine:
        return None
    target = folder / cs.EFFECTS_NAME
    try:
        there = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (OSError, ValueError):
        there = {}
    there = dict(there, **mine) if isinstance(there, dict) else mine
    target.write_text(json.dumps(there, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def version() -> str:
    """Which state of the program this is: the commit, and whether files were changed since."""
    def git(*arguments: str) -> str:
        return subprocess.run(["git", "-C", str(PROJECT), *arguments], capture_output=True, text=True,
                              check=True).stdout.strip()
    try:
        state = git("log", "-1", "--format=%h of %cs")
        changed = git("status", "--porcelain", "--untracked-files=no", "--", *PROGRAM)
    except (OSError, subprocess.CalledProcessError):
        return "version unknown"
    return f"commit {state}" + (", with changes not committed" if changed else "")


def write_program(boot: Path) -> Path:
    """Puts the program onto the boot partition, where the Pi runs it from. A file of an older
    version that is no longer part of it is removed."""
    folder = boot / PROGRAM_FOLDER
    for name in PROGRAM:
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((PROJECT / name).read_bytes())
    for old in folder.glob("*.py"):
        if old.name not in PROGRAM:
            old.unlink()
    (folder / "VERSION").write_text(version() + "\n", encoding="utf-8")
    return folder


def plan_installation(boot: Path) -> str:
    """Has cloud-init run pi/install.sh at the Pi's first start. Returns "added", "already" (it
    was added before), or "none" (the card has no user-data: Raspberry Pi OS before cloud-init)."""
    path = boot / CLOUD_CONFIG
    if not path.exists():
        return "none"
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, UnicodeDecodeError):
        sys.exit(f"{path} cannot be read – write the card again with Raspberry Pi Imager.")
    if not isinstance(config, dict) or not isinstance(config.get("runcmd", []), list):
        sys.exit(f"{path} is not as Raspberry Pi Imager writes it – nothing changed in it.")
    commands = config.setdefault("runcmd", [])
    if INSTALL in commands or " ".join(INSTALL) in commands:
        return "already"
    commands.append(INSTALL)
    path.write_text("#cloud-config\n" + yaml.safe_dump(config, sort_keys=False, allow_unicode=True, width=1000),
                    encoding="utf-8")
    return "added"


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--to", metavar="FOLDER", type=Path, help="write into this folder instead of the Pi's SD card")
    parser.add_argument("--check", action="store_true", help="only check the settings, write nothing")
    parser.add_argument("--profile", metavar="NAME", help="export the settings in profiles/NAME/")
    args = parser.parse_args(argv)

    zs.tolerant_console()
    if args.profile is not None:
        bridge.use_profile(args.profile)
    effect_names.use_cache(bridge.EFFECT_CACHE, keep=False)    # the synth builds read from the pedal,
    zs.use_installed_synths()                                  # as the bridge plays them
    folder = args.to or DEFAULT_TARGET
    card = not args.check and (folder / BOOT_MARKER).exists()
    wlan = bridge.ensure_pi_wlan() if card and bridge.CONFIG.exists() else None   # the Pi's own WLAN goes along
    settings = gather()
    problems = cs.validate(settings)
    if problems:
        sys.exit("Not exported, the settings are not complete:" + "".join(f"\n  - {problem}" for problem in problems))
    summary = (f"{len(settings['mappings'])} preset(s) with assignments, "
               f"{sum(len(entries) for entries in settings['learned'].values())} learned range(s), "
               f"{len(settings['controls'])} keyboard controller(s)")
    if args.check:
        print(f"The settings are complete and valid: {summary}.")
        return

    if not folder.is_dir():
        sys.exit(f"{folder} is not there. Put the Pi's SD card into this computer"
                 + ("" if args.to else ", or name another folder with --to") + ".")
    if args.to is None and not (folder / BOOT_MARKER).exists():
        sys.exit(f"{folder} does not look like the boot partition of a Raspberry Pi (no {BOOT_MARKER}) – nothing written.")
    target = write(settings, folder)
    print(f"Written: {target} ({summary}).")
    effects = write_effects(target.parent)
    if effects is not None:
        print(f"Written: {effects} (the effects read from the pedal's own files).")
    if (folder / BOOT_MARKER).exists():               # the Pi's boot partition: the program goes along
        program = write_program(folder)
        print(f"Written: {program} ({(program / 'VERSION').read_text(encoding='utf-8').strip()}).")
        planned = plan_installation(folder)
        print({"added": "At its first start the Pi installs the bridge by itself: it needs the internet once "
                        "(WLAN as set in Raspberry Pi Imager, or a cable) and takes a few minutes. "
                        "expression-bridge-install.txt on the card says how it went.",
               "already": "Program and settings are up to date; the Pi takes them at its next start (a card "
                          "that has not been in the Pi yet installs the bridge then).",
               "none": "This card has no user-data, so the Pi cannot install the bridge by itself (Raspberry "
                       "Pi OS from before November 2025?). After its first start, log in and run once:\n"
                       f"  sudo {' '.join(INSTALL)}"}[planned])
        if wlan:
            print(f"Where no WLAN it knows is around, the Pi opens its own: “{wlan['wlan_name']}”, password "
                  f"{wlan['wlan_password']}. Join it with the phone and open http://<name of the Pi>.local "
                  "for the interface.")
    else:                                             # a USB stick: the settings only
        print("A USB stick carries the settings only: if the program on this computer is newer than the Pi's, "
              "write the card instead (Write Pi Card.command).")
    if args.to is None and sys.platform == "darwin":
        ejected = subprocess.run(["diskutil", "eject", str(folder)], capture_output=True, text=True)
        print("The card is ejected; you can take it out." if ejected.returncode == 0
              else f"The card could not be ejected ({ejected.stderr.strip() or ejected.stdout.strip()}) – eject it in Finder.")


if __name__ == "__main__":
    main()
