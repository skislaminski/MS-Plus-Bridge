# Quick start

The short way to a running bridge. Everything in detail: [README](README.md).

> **Unofficial and at your own risk.** The bridge talks to the Zoom MS-60B+ with reverse-engineered
> SysEx. Back up your patches first (Zoom's own software) and read [Safety](README.md#safety).

## What you need

- A **Zoom MS-60B+** (firmware 1.20), connected by USB. The other MS Plus pedals (MS-50G+,
  MS-70CDR+, MS-200D+) are **experimental**: untested, use them only with your patches backed up.
- A **computer** with Python 3.9 or newer (macOS, Windows or Linux) – or a **Raspberry Pi**.
- Optional: a **foot controller with an expression pedal** that sends a MIDI CC (e.g. M-Vave
  Chocolate Plus, Line 6 Helix), a **MIDI keyboard**, a **phone or tablet** for the interface.

## 1. Download and start

Download this repository (green **Code** button → **Download ZIP**) and unpack it. Then start it:

| System | Start |
|---|---|
| macOS | Double-click `Start Bridge.command` (the first time: right-click → Open) |
| Windows | Double-click `Start Bridge.bat` |
| Linux | Run `./Start Bridge.sh` in a terminal |

The first start sets up the Python environment by itself (a minute). The interface opens in the
browser at `http://127.0.0.1:8765`.

## 2. Set it up once

In a terminal in the folder (Windows: `.venv\Scripts\python` instead of `.venv/bin/python`):

```bash
.venv/bin/python probe.py ports        # which MIDI ports there are; enter yours in config.yaml
.venv/bin/python probe.py identity     # the pedal's device id (asks you to confirm your backup)
.venv/bin/python probe.py cc           # move the expression pedal: its CC and range
.venv/bin/python probe.py approve edit_enable edit_disable set_param query_program
```

Without a foot controller, leave `chocolate:` in `config.yaml` empty and skip `probe.py cc`.

## 3. Play

- **Link a knob to the expression pedal:** in the interface, open an effect under *Preset* →
  *Effects*, tap the symbol in front of a knob and move the pedal (or any controller knob). Heel and
  toe can be set on the strip that appears.
- **Effects:** choose, move, add and delete effects of the preset; the DSP meter shows what fits.
- **Scenes:** *Add scene* → *Capture* → turn knobs → *Apply*; recall it with a footswitch.
- **Presets:** choose, save and rename them from the interface.

## 4. On a phone or tablet (iPhone, iPad, Android)

Phones and tablets cannot run the bridge, but they open its interface over the WLAN:

- **Computer:** set `listen: true` under `ui:` in `config.yaml` (or start with `--listen`). The
  bridge shows the address to open, e.g. `http://192.168.1.20:8765`. Everyone in that WLAN can use it.
- **Raspberry Pi:** put its SD card into the computer and double-click `Write Pi Card.command`
  (Windows, Linux: `.venv/bin/python export.py`).
  The Pi then opens its own WLAN ("MS Plus Bridge", password shown on the computer under
  *Export…*) where no known WLAN is around: join it and open `http://10.42.0.1`.

## 5. Keyboard synths (optional)

A MIDI keyboard can play synth effects on the pedal: **KeySynth, KeySweep, KeyFM, KeyPoly, KeyWave,
KeyBand, Pluck**. **The effects are not part of this repository** – they are published separately.
Install them on the pedal with Zoom's installer (keep a backup: a custom effect can make a pedal
unusable), then fill in the `synth:` section of `config.yaml` (your keyboard's port) and start the
bridge again. After installing new versions, click **Read Pedal** in the interface: the bridge
reads their knobs from the pedal and plays them at once.

## Help

- Something does not respond: the *Activity* box in the interface says what the bridge did.
- Every MIDI message is logged in `logs/sysex.log`.
- More: [README](README.md) – features, the Raspberry Pi, safety, what was measured.
