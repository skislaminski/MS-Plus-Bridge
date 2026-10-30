# MS Plus Bridge – reference

Everything in detail: how each part works, what was measured on the pedal, the history of the synth effects' builds. For the short version see the [README](../README.md).

*Experimental for the other MS Plus pedals (MS-50G+, MS-70CDR+, MS-200D+).*

![MS Plus Bridge: expression pedal, MIDI keyboard and phone in control of the Zoom MS Plus pedals](ms-plus-bridge.png)

Control a **Zoom MS-60B+** from MIDI controllers, in real time:

- an **expression pedal** moves effect parameters, and
- a **MIDI keyboard** plays **KeySynth**, a synth voice that runs on the pedal as a custom
  effect (optional), or one of its siblings **KeySweep**, **KeyFM**, **KeyWave**, **Pluck** and the
  four-voice **KeyPoly** and **KeyBand** (see [The synth effects](#the-synth-effects); the effects
  themselves are not part of this repository).

It runs on **macOS, Windows and Linux** (and on a **Raspberry Pi** without screen, see
[On a Raspberry Pi](#on-a-raspberry-pi-without-screen)). **iPhone, iPad and Android** cannot run it
– they have no USB host for the pedal that this program could use – but they open its interface
in the browser, over the WLAN: see [On a phone or tablet](#on-a-phone-or-tablet-ios-android).

The MS-60B+ officially accepts only patch changes over USB MIDI. Parameter changes need
unofficial SysEx messages, which a plain MIDI controller cannot send. This program sits in
between: it receives the controllers' messages and translates them into the pedal's SysEx.

```
expression pedal → MIDI controller (sends CC) ─┐
                                               ├→ this bridge (computer, USB host) → MS-60B+
MIDI keyboard (notes, wheels, knobs) ──────────┘
```

**New here?** The [Quick start](../QUICKSTART.md) gets the bridge running in a few steps.

> **Unofficial and at your own risk.** The SysEx protocol was reverse engineered and is not
> documented or supported by Zoom. This project is not affiliated with Zoom or M-Vave.
> Read [Before you start](#before-you-start) and [Safety](#safety).

## Status

Developed and tested with exactly this setup:

- Zoom MS-60B+, firmware 1.20
- M-Vave Chocolate Plus (USB mode) with an expression pedal on its TRS input
- Native Instruments Komplete Kontrol A61 as the keyboard, with KeySynth 0.21 on the pedal
- macOS on Apple Silicon, Python 3.9

Since then also on a Raspberry Pi 4 (Raspberry Pi OS, its interface on an iPhone). Not tested:
other MS Plus pedals (MS-50G+, MS-70CDR+, MS-200D+), other firmware versions, Windows, Linux on a
desktop. Other MIDI controllers should work if they send the expression pedal as a CC.
See [Installation](#installation) for all three systems.

> **Other MS Plus pedals (MS-50G+, MS-70CDR+, MS-200D+) are experimental.** They speak the same
> SysEx family, so the bridge may work with them, but it was built and measured on the MS-60B+
> only: their effects, knobs, DSP budget and patch layout are not checked. Try it with your patches
> backed up, and please report what you find.

## Features

- Browser interface on `http://127.0.0.1:8765`: live status, assignments, export and import. The
  effects and knobs of the preset on the pedal are shown by name ("AnalogDly · Mix (0–100)"), see
  [Using the interface](#using-the-interface)
- Up to twelve parameters per preset, up to four of them on one controller, each with its own
  range, direction (inverted or not) and curve
- Pick a parameter of the preset's effects by name, or let the pedal report a knob's exact
  range ("Learn from pedal")
- Follows preset changes on the pedal; presets without an assignment are left alone
- Waits for the pedal and reconnects after it was replugged (tested). The keyboard, the external
  sequencer and the foot controller are optional and join or leave while it runs, without
  connecting anew; with none of them the preset is still edited in the interface
- The pedal, the keyboard and the foot controller are chosen in the interface from the MIDI
  ports that are connected, or "Any keyboard" for whichever is plugged in; a second pedal gets a
  second bridge, see [Two pedals](#two-pedals)
- Optional: a MIDI keyboard plays the custom effect KeySynth, KeySweep, KeyFM, KeyWave, Pluck (one voice)
  or KeyPoly, KeyBand (four voices) on the pedal, with pitch wheel, and with any wheel, knob or slider of the keyboard assigned to
  the synth's knobs by "Learn", see [Keyboard synth](#keyboard-synth)
- Optional: an arpeggiator for the keyboard synth, at its own tempo or to MIDI clock, see
  [Arpeggiator](#arpeggiator)
- Optional: a step sequencer that plays a recorded pattern from the key you hold, see
  [Step sequencer](#step-sequencer)
- Optional: an external sequencer (an MPC, for example) plays the synth effect too and gives the
  arpeggiator and the step sequencer its MIDI clock, see [External sequencer](#external-sequencer)
- Optional: footswitches, buttons or pads switch the effects of the chain on and off, see
  [Switching effects on and off](#switching-effects-on-and-off)
- Optional: scenes set parameters to stored values and switch effects with one press, see
  [Scenes](#scenes)
- Optional: rename the preset on the pedal from the interface, see
  [Renaming a preset](#renaming-a-preset); choose a preset, or save it into its place or another
  one, see [Choosing and saving presets](#choosing-and-saving-presets)
- Optional: put another effect into a place of the chain from the interface, see
  [Changing an effect](#changing-an-effect), drag an effect to another place, see
  [Moving an effect](#moving-an-effect), add an empty place for one, see
  [Adding an effect](#adding-an-effect), or delete one, see [Deleting an effect](#deleting-an-effect)
- Every knob of the preset's effects as a slider, see [The editor](#the-editor)
- Sends only message kinds you have approved, and logs every SysEx message sent and received

## Before you start

- **Back up your patches.** The bridge asks you to confirm this before it sends anything to
  the pedal.
- **Turn Auto Save off** in the settings of the MS-60B+. With Auto Save on, every pedal
  movement is written into the stored patch.
- **Mind the firmware.** Everything here was tested on an MS-60B+ with firmware 1.20 only.
  Other MS Plus pedals and other firmware versions may behave differently, and a firmware
  update can break the bridge.
- **The computer stays in the chain.** Controller and pedal are both USB devices, so both plug
  into the computer, and the bridge has to run while you play.
- **Close other software that talks to the pedal** (Zoom Explorer, editors, a DAW that has the
  MIDI ports open). On Windows a MIDI port can usually be opened by one program at a time.
- **Your controller must send the expression pedal as a MIDI CC over USB.** On the M-Vave
  Chocolate Plus: mode switch on U, and the expression CC assigned in the CubeSuite app. If no
  CC arrives over USB, users report that switching the Chocolate's Bluetooth off helps
  (hold B + C).
- **Effect and parameter numbers are not checked against the preset.** If you assign a number
  that does not exist there, the bridge stops sending after five unconfirmed messages, but how
  the pedal itself reacts has not been tested. Use "Learn from pedal" when in doubt. If the
  pedal ever stops responding, disconnect its power and USB and start it again.
- **Effects moved, replaced or deleted in a preset:** with the patch query approved
  (`query_patch`), its assignments and the ranges learned for them go with their effect, and wait
  while it is not in the chain, see [When effects move](#when-effects-move). Without it they stay
  by place: check the assignments, which now point to other parameters, and learn the ranges again.
- The interface is reachable from the same computer only (`127.0.0.1`). Closing the browser
  tab does not stop the bridge.

## Installation

You need **Python 3.9 or newer** and this repository: click the green **Code** button →
**Download ZIP** and unpack it, or clone it.

### macOS

1. Open **Terminal** and go to the unpacked folder: type `cd ` (with the space), drag the
   folder into the Terminal window and press Enter.
2. Check that Python is there:

   ```bash
   python3 --version
   ```

   If macOS offers to install the command line developer tools, accept and repeat the command
   when it has finished. Alternatively install Python from [python.org](https://www.python.org/downloads/).
3. Create the environment and install the two MIDI libraries and the YAML reader into it:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```

4. Optional, for starting by double-click later. A ZIP download loses the "executable" flag
   and macOS marks downloaded files as untrusted, so run this once:

   ```bash
   chmod +x "Start Bridge.command"
   xattr -d com.apple.quarantine "Start Bridge.command"
   ```

   The second command may answer that there is nothing to remove; that is fine.
5. Continue with [First-time setup](#first-time-setup).

### Windows

Nobody has run this on Windows yet, so expect rough edges and please report what you find.

1. Install Python from [python.org](https://www.python.org/downloads/). Version 3.11 or newer is
   recommended: older versions sleep in coarse steps on Windows (typically about 16 ms), which
   makes the bridge less responsive.
2. Open the unpacked folder in Explorer, type `cmd` into the address bar and press Enter. A
   terminal opens in that folder.
3. Create the environment:

   ```bat
   py -3 -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

4. Continue with [First-time setup](#first-time-setup), writing `.venv\Scripts\python` wherever
   the steps say `.venv/bin/python`.

### Linux

Not tested either. The commands are the same as on macOS, without step 4; or run
`./Start Bridge.sh` in the folder (it sets up the environment on its first start, like the
launchers for macOS and Windows).

- On Debian and Ubuntu, install the venv module first: `sudo apt install python3-venv`.
- If `pip` has to build python-rtmidi from source, it needs a C++ compiler and the ALSA
  development package (`libasound2-dev`).

## First-time setup

This is needed once per computer. Connect the controller and the MS-60B+ by USB first. The
values go into `config.yaml`, a plain text file you can edit with any text editor.

1. List the MIDI ports. On the first run this creates `config.yaml` from the example.

   ```bash
   .venv/bin/python probe.py ports
   ```

   Enter a unique part of each port name under `ports:` in `config.yaml`.

2. Identify the pedal. This is the first message sent to it: the script asks you to confirm
   that your patches are backed up and shows the message as hex before sending.

   ```bash
   .venv/bin/python probe.py identity
   ```

   Enter the reported `device_id` and `firmware` in `config.yaml`.

3. Measure the expression pedal. This sends nothing to the MS-60B+. (No foot controller, only a
   keyboard or nothing but the pedal? Leave `chocolate:` under `ports:` empty and skip this step.)

   ```bash
   .venv/bin/python probe.py cc
   ```

   Enter the reported `cc`, `channel`, `min`, `max` and `invert` under `expression:`.

4. Approve the remaining message kinds the bridge needs. `probe.py approve` without arguments
   lists their exact bytes.

   ```bash
   .venv/bin/python probe.py approve edit_enable edit_disable set_param query_program
   ```

## Starting the bridge

| System | Double-click | Or in the terminal |
|---|---|---|
| macOS | `Start Bridge.command`, afterwards the app it makes | `.venv/bin/python bridge.py --open` |
| Windows | `Start Bridge.bat` | `.venv\Scripts\python bridge.py --open` |
| Linux | `Start Bridge.sh` (in a terminal) | `.venv/bin/python bridge.py --open` |

The interface opens in the browser at `http://127.0.0.1:8765`. If the pedal is missing, the
bridge waits for it and connects as soon as it appears. The pedal is all it needs: the keyboard,
the external sequencer and the foot controller are optional. With none of them connected the
bridge connects to the pedal alone ("Ready: no controller → …"), its preset can be edited in the
interface (effects, knobs, name, scenes, presets, Read Pedal), and Activity says once that no
controller is connected (or that none is set up, when "Devices" has "None" for all of them).
Each one joins when it is plugged in ("… connected") and leaves when it is pulled ("… is gone"),
while the bridge stays connected to the pedal; a note that was held is released.

**A clickable icon.** On macOS the first start with `Start Bridge.command` makes
`MS Plus Bridge.app` in the same folder (an `Expression Bridge.app` from before the bridge's new
name is replaced): an app with its own icon that opens that script in
a Terminal window and nothing more. Double-click it from then on, or drag it into the Dock. It
is made on your computer because macOS blocks unsigned apps that come out of a download; if you
move the folder, start once with `Start Bridge.command` again and the app is made afresh. On
Windows, make a shortcut to `Start Bridge.bat` and give it `icon.ico` (Properties → Change
Icon).

Started again while it runs (the app double-clicked once more), it opens the running bridge's
interface in the browser instead of starting a second one. Stop the bridge with **Ctrl+C** in its window: it then switches the pedal's edit mode off and
closes the MIDI ports. Closing the window on Windows ends it without that step, which had no
lasting effect in testing on macOS.

## Using the interface

The interface is a column of parts that fold: a click on a title opens or closes it, and its line
shows the state while it is closed. Which parts are open is remembered in the browser. From the
top: Devices, Preset, Arpeggiator and Step sequencer (those two only when a keyboard synth is set
up, see [Keyboard synth](#keyboard-synth)), and Activity. Where a controller can be learned, it
says "Learn" or, once it has one, its number ("CC 22", "Exp") with an × to take it away.

**Preset** is everything about the preset that is selected on the pedal: its number and name
(a click on the name gives it another, see [Renaming a preset](#renaming-a-preset)), and three parts of its
own: Effects and Scenes. The effects and their knobs are called as
the pedal calls them ("AnalogDly", "Mix"), read from the pedal's effect files (see
[Names of effects and knobs](#names-of-effects-and-knobs)).

- **Effects**: the effects of the preset, each with its on/off lamp, the controller that switches
  it (see [Switching effects on and off](#switching-effects-on-and-off)), and an arrow that opens
  all its knobs under it ([The editor](#the-editor)). The title line shows how many knobs of the
  preset are linked to a controller, the DSP its effects take, and the expression pedal's position
  (0–127). **DSP** is a bar with one part per effect of the chain, in their order, each as wide as
  its share of the pedal's budget of 270 (an effect that is off is dimmer: it counts all the same;
  hovering a part names it, "AMPG SVT: 121"), and the sum, "204 / 270": orange above 90 %, red
  above the budget (see [Changing an effect](#changing-an-effect)). It stays there while Effects
  is folded. An effect whose DSP is not known is left out, and hovering the bar names it. On a
  phone, with the DSP shown, the title line wraps and the expression pedal's position goes to a
  line of its own, at the right. A controller is
  the expression pedal, or any knob, slider, wheel or pedal of the keyboard, the foot controller or
  the external sequencer (it sends a CC number; that number is what is kept). One controller sets
  up to four knobs of a preset, all together up to twelve.
  - **Linking a knob**: tap the symbol at the start of its line (the expression pedal, grey), then
    move the expression pedal or the control that should set it; the first one moved gets it. While
    it waits the symbol blinks blue (tapped again, it stops waiting). Once linked it is orange: the
    expression pedal, or a knob for a controller (hovering it names it: "CC 16 sets it"). Tapped
    then, the next controller moved takes the knob over.
  - **A linked knob** shows, instead of its own slider, the range the controller moves it in:
    **H** and **T** (heel and toe; for a control: its value at 0 and at 127) as handles on the
    slider, which can be dragged, and as values beside it (on a phone under it, bigger), which can
    be tapped and typed into:
    the value as shown (case does not matter, 1/4x2 works too), a number equal to a shown number
    (-3 for -3.0, DrumBox Tempo 120), or #n for the raw step n. A knob with up to 32 values gives a
    list to choose from instead (those beyond the learned range greyed).
    A hollow point shows what the knob is now; drag it to set the knob by hand (the pedal or the
    controller sets it again when it moves). A heel above the toe inverts the
    direction; ⇅ swaps the two. The curve symbol switches between linear, changing early and
    changing late. × takes the link away. Every change is kept at once.
  - **The range learned on the pedal** (dashed on the slider) is the limit: a handle dragged or a
    number typed beyond it springs back to where it was. Without a learned range the knob's own
    range from the effect file is the limit.
  - A folded effect shows its links as small chips ("Gain 90→20").
  - **Learn from pedal** (optional, below the effects): click it, turn the knob on the MS-60B+
    once from minimum to maximum, click "Apply". The pedal reports the effect, the knob and its
    exact range, and it is linked to the expression pedal. A knob that is only available once
    learned (beyond parameter 12, as Pong Delay's last two) says so when its symbol is tapped.
  - A link whose effect is not in the chain now, or whose effect's knobs are not known, is listed
    below the effects, with the fields it always had.
  - To change another preset's assignments, select that preset on the pedal.
  - **A synth effect's knobs** are linked like any other. Its Key knobs are shown too, greyed and
    without a symbol: they cannot be linked, they are notes, played by the keys. That holds for
    every synth effect in the chain, the one the keyboard plays or not. See [Keyboard synth](#keyboard-synth).
  - **Assignments from before 2026-10-19** of a keyboard control to a synth knob held in every
    preset with that synth. They still do, and are listed below the parameters of a preset with
    that synth, with the knob and the control ("KeySynth · Vib (LFO)", "Mod wheel"); "Remove" takes
    one away. New ones are made per preset, as above.
  - **One control, one job:** a control that switches an effect, recalls a scene or switches the
    arpeggiator or the sequencer does nothing else. Learned for one of those, it lets go of the
    parameters it set (a scene: in its own preset only); learned for a parameter, it stops
    switching.
  - **Moving effects on the pedal:** see [When effects move](#when-effects-move).
- **Scenes**: stored values for the preset on the pedal, recalled with one press, see
  [Scenes](#scenes).

- **Devices**: which MIDI port the pedal, the keyboard, the external sequencer and the foot
  controller (the Chocolate: expression pedal and footswitches) are. Choose another one and the
  bridge connects anew at once; the choice is kept in `config.yaml`. All but the pedal can be
  "None", all of them too: then the bridge connects to the pedal alone, whose preset is edited in
  the interface. "Any keyboard" takes whichever MIDI device is connected and is none
  of the others (not the pedal, not what the foot controller or the external sequencer is called),
  for a keyboard that is not always the same. As the pedal only ports named "ZOOM MS …" are
  offered, so that nothing is sent to another device by a slip of the mouse; any other name can
  still be entered in `config.yaml`. A new foot controller keeps the CC measured for the old one:
  measure it again with `probe.py cc` if it sends another.
  - "Export…" → "File for the Pi" saves everything a bridge without screen needs as
    `expression-bridge.yaml`, for its card or a USB stick put into the running Pi (the program on
    the Pi stays; a stick carries the settings only), see
    [On a Raspberry Pi](#on-a-raspberry-pi-without-screen).
  - "Export…" → "Backup" saves the assignments of every preset with their learned ranges, the keyboard's
    controllers, the arpeggiator's settings, the sequencer's pattern, the effect switches and the
    scenes as a JSON file; "Import…" brings them back, on this or another computer, and replaces
    what was there. A controller the file's keyboard part assigns no longer switches an effect
    (one controller, one job); on the Pi the whole file is kept or nothing of it.
  - Once a card for the Pi has been written, the Pi's own WLAN is shown here with its password,
    in the computer's interface only (not in the Pi's).
- **Activity**: the most recent lines of the bridge's log; above them, where a synth is set up,
  which synth effect the keyboard plays in this preset, its voices and how long a note takes until
  the pedal confirms it.
- Hovering over a button or list shows a short hint where there is one; everything else is
  described here.


### Names of effects and knobs

The pedal does not say over MIDI what its effects and knobs are called: a preset reports only
their numbers. The names come from the pedal's effect files (`.ZD2`), which a backup of the pedal
holds (nam2zoom, or the installer of the synth effects): each carries the effect's number, its
short name as the pedal shows it, and its knobs with their highest value.

**`zoom_effects.json` describes the effects installed on this MS-60B+**, not the newest builds:
it is read from the backups of the pedal and from the copies the installer uploaded to it (its
`Adapted/ms-60bp` folder), plus a collection of community effects (138 effects: 97 of Zoom's,
LINE SEL among them, and 41 others; BPM, which is in the pedal itself without a file, by name
only). Read it again **after installing effects**, from the same folders:

```bash
.venv/bin/python effect_names.py FOLDER_WITH_BACKUPS ADAPTED_FOLDER [MORE_FOLDERS …]
```

Of two files of the same effect the newer wins. A custom or community effect is one effect by its
number, whatever category it was installed into and whatever an earlier build called it (TrackSyn,
now FretSyn); Zoom's own effects share numbers between categories and go by the whole id. Do not
read it from build folders: an effect built but not installed would be described wrongly (on
2026-10-20 a table read from the newest builds was wrong for 9 effects on the pedal).

**Values as the pedal shows them.** Many knobs show their value on the pedal as text: DrumBox's
Pattern Rock … Click, its Tempo 40 … 250 and Sync, a delay's Time 1 … 4000 and then note values,
1.0kHz, -3.0. The interface shows them the same way, note values as fractions: 1/16, 1/8. dotted,
1/8T triplet (the pedal draws it as a quarter note with a 3: three eighths in a quarter), 1/4×2.
What is stored (assignments, scenes, learned ranges, the settings file) stays the raw numbers.
The texts of custom effects are in their files and read with the knobs (by `effect_names.py`,
and by Read Pedal). Zoom's own effects make theirs in code: a developer tool on a Mac runs that
code once for every value and writes the texts into `zoom_effects.json` (257 knobs of 108 effects
on 2026-10-21; a knob whose texts are just its numbers gets none). It needs TI's disassembler
`dis6x`, from the free TI C6000 code generation tools; the Pi does not need it:

```bash
.venv/bin/python -m tools.value_labels [--dis6x PATH] [--table PATH] [--dry-run] FOLDER [FOLDER …]
```

It sets the texts of every effect in the table whose file is in the folders; the others keep
theirs. Reading the table again with `effect_names.py` keeps the texts of every effect whose file
and knobs are unchanged, so run the tool after such a reading, with the same folders.

The table keeps each file's size as well. **An effect the table does not describe is read from
the pedal itself** (see [Read Pedal](#which-effects-the-pedal-has-read-pedal)): a file it has no
entry for, or a custom effect whose file has another size than its entry (another build, as
SlamDrive's on 2026-10-21). So the table that comes with the bridge is enough for any pedal, with
whatever effects are installed on it; nobody needs the folders above. What is read that way is
kept in `pedal_effects.json` (on the Pi: `pedal-effects.json` beside its settings file) and taken
in place of the table's entry for the same file – unless the table describes that build itself
(read again after installing effects, the same size): then the table counts. Delete
`pedal_effects.json` to have every file read again. Zoom's own effects go by their file's name, as
the size of their files may change with the firmware and their knobs do not. One id is one effect
on the pedal: an effect read takes the place of the table's entry with its id.

**The DSP each effect takes.** Every effect file declares it itself (a float after the first 16
bytes of its `INFO` chunk: KeySynth 1.10's 52, Zoom's effects 6.64 to 142.02, N2Z Bank
150), and the pedal adds those of a preset's effects up against its budget of 270 (see [Changing
an effect](#changing-an-effect)). The table keeps it per effect (`"dsp"`, two decimals);
`effect_names.py` reads it with the rest. To set only it, for the effects that are in the table,
changing nothing else:

```bash
.venv/bin/python effect_names.py --dsp FOLDER [FOLDER …]
```

Of the files of an entry's name it takes the newest of the entry's size, else the newest of that
name, and says which. It was filled so on 2026-10-22 from the folders the table was read from:
all 138 effects, from the pedal's backups of that day (KeyPoly, SlamDrive, FMSynth, OrganSynth and
VoxSynth from the earlier one, of the builds the table describes); DrumBox from a file of another
size (30163 bytes, its entry has 30065); FretSyn 70 as on the pedal now (older backups of the same
size say 55). BPM has no file and so no DSP. Read Pedal reads it with the knobs (it comes before
them in the file); an effect it read before 2026-10-22 has none and is read once more at the next
Read Pedal.

When a preset shows a knob holding more than its entry allows (or a knob it does not have), the
pedal has another build than the table describes: Activity warns once, and until the table is read
again that effect's knobs go by number, the editor and the keyboard's controllers leave it alone.

An effect that is not in it is shown by its number ("Effect 3 · Parameter 2"), and its knobs as
1–12. A preset's effects are read from the pedal with the patch query (approval `query_patch`);
without it the names are not known either. An effect changed or moved on the pedal is read as soon
as the pedal reports it, and what is assigned follows its effect, see
[When effects move](#when-effects-move).

## Keyboard synth

Optional, and only useful with the custom effect **KeySynth** or one of its siblings
([below](#the-synth-effects)) installed on the pedal. Notes work with KeySynth 0.21 and later; the
knobs a controller of the keyboard sets follow the build on the pedal, as the table of effect
names describes it ([below](#the-synth-effects)). KeySynth is
a two-oscillator synth voice whose first knob, "Key", is gate and pitch in one number (0 = off,
n = 10-cent steps above C0). The effects are not part of this repository (until 2026-10-30
`keysynth/` held KeySynth 1.10 with its source); they are installed on the pedal with Zoom's
installer, and **a custom effect can make a pedal unusable** – keep a backup. With the `synth:`
section filled in in `config.yaml`, a MIDI keyboard plays that effect:

- KeySynth is monophonic, last note wins; releasing it returns to a note that is still held.
  Note on with velocity 0 counts as note off, the sustain pedal (CC 64) holds notes, "all notes
  off" and stopping the bridge close the gate. The effect plays MIDI notes 12–111 (C0 to D#8).
- **Pitch wheel:** bends the sounding note, ±2 semitones unless `bend_range` says otherwise.
  Note and bend travel as one value, so a note always arrives with its pitch. While a note
  sounds the wheel is followed in steps of at most 90 cents per message; the effect smooths
  them.
- **Any other control of the keyboard** (mod wheel, knobs, sliders) can set one of the effect's
  other knobs, like any parameter: under "Preset" → "Effects", open the effect, tap the symbol
  beside the knob and move the control. It belongs to
  that preset; a control can set several parameters at once, of the synth and of other effects.
  It works with any keyboard and any controller number, as long as the control sends absolute
  values 0–127 (endless encoders in relative mode do not). Nothing is assigned until you do it,
  not even the mod wheel.
- **LFO depth from a wheel:** the effect's LFO knob is kind and depth in one (Vib50 … Vib1, Off,
  Trm1 … Trm50, the values 0–100 with Off at 50). For the mod wheel of a hardware synth, give it
  the range 50 to 0 (vibrato) or 50 to 100 (tremolo; on KeySweep and KeyPoly the filter sweep):
  at rest the LFO is off, at full travel deepest. KeyWave's LFO knob is a depth from 0 (off), and
  its LDest says what it moves.
- **Assignments from before 2026-10-19** (`controls.json`): a keyboard control set a knob of a synth
  effect in every preset with that synth, kept per synth (one control could set Cutoff on KeySweep
  and Index on KeyFM), with "Vib" and "Trm" as the two halves of the LFO knob. They still work and
  are listed below the effects where that synth is in the preset; new ones are not made that way.
  Assignments of one name for all synths ("Vib": 1) are taken over for every synth that has it.
- A control that switches the arpeggiator or the step sequencer does nothing else.
- The bridge finds the effect itself: it asks the pedal for the current patch when it starts,
  after every preset change, and while a key is held and the effect is not there yet (once
  per second). No effect in the preset: the keyboard is ignored. It knows the effect by its id,
  in whichever category of the pedal it was installed; nothing has to be entered.
- Notes are sent at once, never spaced or merged; expression values wait their turn.
- The keyboard and the foot controller are both optional: the bridge starts with either one or
  with neither, and each may be plugged in or pulled while it runs (see
  [Starting the bridge](#starting-the-bridge)).
- It needs one more approval, for the patch query:
  `.venv/bin/python probe.py approve query_patch`

Each confirmed note is printed with the time from key press to the pedal's acknowledgement
(bend steps are not, they would flood the terminal).

### The synth effects

The keyboard plays seven effects. All of them take gate and pitch through Key knobs in KeySynth's
format, so notes, pitch wheel, arpeggiator and step sequencer work the same on each:

| Effect | What it is | Voices | Id | Its knobs besides Key | Played through the bridge on the MS-60B+ |
|---|---|---|---|---|---|
| KeySynth | two oscillators | 1 | `…0f61` | Wave1, Wave2, Pitch, Dtune, Blend, Glide, Atk, Rel, LFO, Rate, Level | yes |
| KeySweep | KeySynth's voice through a resonant 24 dB low-pass with its own envelope (called KeySynF until 2026-10-17) | 1 | `…0f71` | Wave, Cutoff, Reso, FEnv, FDcy, Atk, Rel, Glide, LFO, Rate, Level | yes, 2026-10-11/12 |
| KeyFM | one sine modulates another | 1 | `…0f72` | Ratio, Index, MEnv, MDcy, Fdbk, Atk, Rel, Glide, LFO, Rate, Level | yes, 2026-10-11/12 |
| KeyPoly | four voices in one effect | 4 | `…0f73` | Wave, Cutoff, Reso, Atk, Rel, LFO, Rate, Level | yes, all four voices |
| KeyWave | moves through the frames of a wavetable (the WaveTbl prototype before) | 1 | `…0f7c` | Wave, WEnv, WDcy, Sub, Atk, Rel, Glide, LFO, Rate, LDest, Level | yes, 2026-10-18 |
| KeyBand | a piano, an electric piano, strings or brass on four voices (prototype; 0.20 of 2026-10-22, before it one voice with Chord and Oct; since 0.21 of 2026-10-23 without the bass while on; 0.22 of 2026-10-24 plays up to D#8, E7 before) | 4 | `…0f94` | Voice, Level, Atk, Dec, Rel, Tone, Move, Vel | 0.1x yes, 2026-10-22; 0.20–0.22 not yet |
| Pluck | plucked strings (Karplus-Strong), added to the bass (prototype of 2026-10-21) | 1 | `…0f8f` | Level, Trig, Decay, Tone (Bright before 0.12), Pick, Oct, Dyn, Thr, Dry (since 0.13) | yes, 2026-10-22 |

- **The builds of 2026-10-22** (KeySynth 1.13, KeySweep 0.12, KeyFM 0.12, KeyPoly 0.14, KeyWave
  0.12; on the pedal since 2026-10-22). Their last knob is **Level** again: the synth's level
  alone (0–100; 80 is as loud as Mix 50 was, 100 is 1.5 times that, 0 silent), and the bass
  passed unchanged. The builds of 2026-10-23 (KeySynth 1.14, KeySweep 0.13, KeyFM 0.13, KeyPoly
  0.15, KeyWave 0.13, and KeyBand 0.21; on the pedal since 2026-10-23) keep these knobs but mute
  the bass while they are on: only the synth is heard, nothing while no key is held; switched
  off, the bass passes (the two crossfade). KeySynth 1.14 declares a DSP of 48 (1.13: 47). The
  builds of 2026-10-24 (KeySweep 0.14, KeyFM 0.14, KeyWave 0.14, KeyBand 0.22 and Pluck 0.12; on
  the pedal since 2026-10-24, KeySynth 1.14 and KeyPoly 0.15 unchanged) keep the knobs (Pluck's
  Bright is called Tone) and fix what a review of all effects found; they declare more DSP where
  the count was too low: KeySweep 52, KeyBand 86, Pluck 106 (and Orchestra 116, Melody 64), so a
  full chain with one of them reaches the budget of 270 sooner. The builds of 2026-10-26 (KeySynth
  1.15, KeySweep 0.15, KeyFM 0.15, KeyPoly 0.16, KeyWave 0.15) only show values as the pedal
  displays them (Rate and Cutoff in Hz; the interface takes these texts from the files, the
  values sent stay 0–100); Pluck 0.13 has **Dry** as its last knob, the bass beside the strings
  (80 by default, the bass as it is; a preset saved with an earlier Pluck has 0 there, no bass),
  and declares 117. The
  bridge does not rely on hearing the bass either way. The builds of 2026-10-20
  (KeySynth 1.12 …) had **Mix** there, the bass against the synth; a preset made with them keeps
  its Mix value as Level (Mix 50 becomes Level 50, about 4 dB quieter: set Level to 80). A
  controller assigned to Mix is left out, as one to a knob the effects no longer have; where the
  pedal still has a build with Mix, that is kept and Level left out. KeySynth's former Mix of its
  two oscillators is **Blend** (since 2026-10-20); its Wave1 and KeyPoly's Wave have ten
  waveforms. The lists in `zoom_sysex.py` are these builds.
- **KeyBand and Pluck** (prototypes of 2026-10-21, KeyBand 0.22 of 2026-10-24 and Pluck 0.13 of
  2026-10-26; the bridge knows them by number only) have Level too, the level of what they play (Pluck adds it to the bass,
  KeyBand 0.21 is heard alone while on), and no LFO. A new note (Key from 0, or a jump of more
  than 9 clicks) is struck at once, smaller moves are bends; Pluck ignores bends, and its string
  rings out by Decay (Key 0 does not stop it). Pluck holds notes below C1 and above E6 there
  itself. Pluck 0.12 calls its knob 5 **Tone**, the builds before **Bright**: the same knob, in
  the same place; a controller assigned to either name sets it under the name the build on the
  pedal has.
- **KeyBand 0.20 has four voices**, Key1–Key4 (its knobs 1–4) in KeySynth's format, and the
  bridge gives them to the notes as on KeyPoly (see [Four voices](#four-voices-keypoly-and-keyband)):
  a note keeps its voice, a fifth takes the voice of the oldest note. Chord and Oct are gone; a
  chord is played with the keys. Its knobs: Key1–Key4, Voice (Piano, EPian, Strng, Brass), Level,
  Atk, Dec, Rel, Tone, Move, Vel.
- **KeyBand 0.1x** (one Key, Chord and Oct) is still played where the table has that build (read
  from the pedal by Read Pedal): with one voice and its own knobs. The bridge takes the build at
  its start, and one Read Pedal reads later at once (since 2026-10-27; before, from the next start):
  its knobs in the effects and the keyboard's list, its voices, and the controllers assigned to it
  (under the names that build has). **Presets made with 0.1x need making anew** on 0.20: the pedal takes
  their knob values by place. Assignments of the keyboard to KeyBand's Chord or Oct are left out.
- **KeyBand's Vel** is how hard a note is struck: the effect reads it for a voice when that voice
  strikes, and the voice keeps it. Before every new note the bridge sends the key's velocity
  there (127 = 100, never below 1), right before the Key of the voice that strikes and through the
  same path, so it arrives first and the note waits for nothing else; not if Vel holds that value
  already, and never for a step of the pitch wheel. A chord of four keys struck as hard as each
  other gets Vel once; struck differently, each Key gets its own Vel right before it (notes struck
  within one block of the effect all take the Vel that stands then). For the arpeggiator and the
  step sequencer it is the velocity of the key the note comes from (unknown: Vel is left alone);
  a note the sustain pedal holds keeps its voice and what it was struck with. A controller
  assigned to Vel in the preset wins: then the bridge sends nothing there by itself. When the
  pedal does not confirm a note (it may miss a message while its menus are open), the note goes
  again, with Vel at its velocity right before it where Vel is not confirmed either or holds that
  of another note by then; a step of the pitch wheel goes again alone.
- **Pluck's Trig** says what plucks a string: the Key knob (0, Key) or the bass (1, Bass, its
  default; then Key does nothing). When the keyboard, the arpeggiator or the sequencer strikes a
  note on Pluck while Trig is on Bass (as the preset has it, or as it was turned on the pedal
  since), the bridge sets it to Key in the same way, right before the Key, and says so in Activity:
  "Pluck: Trig set to Key, so the keyboard plays it – save the preset on the pedal to keep it". A
  controller assigned to Trig wins. Unconfirmed, Trig goes again with the note, as Vel does.
- **Which build is on the pedal decides where the knobs are**, and how many voices it has: one
  for a "Key", as many as "Key1", "Key2" … next to each other (at most four). The bridge takes
  each synth effect's knob list from `zoom_effects.json` (read from the pedal's own files, see
  [Names of effects and knobs](#names-of-effects-and-knobs)) when it has the effect and its Key
  knobs are where a patch keeps them, and the lists in `zoom_sysex.py` otherwise. A controller
  assigned to "Rate" sets Rate wherever the installed build has it; one assigned to a knob that
  build lacks (Blend on KeySynth 1.11) does nothing there. Read the table again after installing
  new builds, then start the bridge again.
- **The Key knobs stay first.** A preset keeps knobs 1–5 and 9 in 12 bits, the others in 8
  (0–255; measured from the MS-60B+'s own patches, see `EDTB_WIDTHS` in `zoom_sysex.py`), and Key
  needs 0–1000.
- **Only KeySynth is in this repository**; the other six are built separately. Their knobs and
  ranges above are those of their `manifest.json`, in `zoom_sysex.py`. On 2026-10-11 and 12 the bridge played KeySweep (483
  confirmed notes), KeyFM (685) and KeyPoly (525, on all four Key knobs) on the MS-60B+, on
  2026-10-18 all five one after another (KeyWave 54 of 54), with no note left unconfirmed.
- **The bridge knows an effect by the last three bytes of its id.** The first byte is the
  category, and an installer may put the effect into another one: KeySweep is `07000f71` as it is
  built, was `09000f71` in "SFX" of the MS-60B+ here and is `08000f71` in "Synth" since
  2026-10-30. A new name does not change the id, nor does another category: what is assigned to
  a custom effect stays with it.
  Whichever of the seven comes first in the chain of the preset is played.
- Any other effect whose id ends in one of these seven numbers would be taken for that synth
  effect and get its Key knob written. On the MS-60B+ here (125 effects) there was none.
- A build with an id of its own can be named in `config.yaml`: `effect_id:` under `synth:` for
  KeySynth, `poly_effect_id:` for KeyPoly.
- A control spreads its travel over the whole range of the knob it sets: KeySweep's and
  KeyPoly's Wave and KeySynth's Wave1 have ten settings, KeyWave's Wave a hundred, KeyFM's Ratio
  sixteen.
- On KeySweep and KeyPoly the upper half of the LFO knob moves the filter and not the level
  (Vib50 … Vib1, Off, Flt1 … Flt50): the list has "Flt" there in place of "Trm".
- KeyWave's LFO knob is a plain depth, 0 = off, so a wheel on "LFO" starts with the LFO off; its
  LDest (Wave, Pitch, Level) says what the LFO moves. It has no "Vib" and "Trm".
- Assignments stored under KeySynF are taken over for KeySweep.

### Four voices: KeyPoly and KeyBand

KeyPoly has four Key knobs, Key1–Key4 (its knobs 1–4), each in KeySynth's format, and eight knobs
the voices share. Without the bridge it can only be played by turning the four Key knobs by hand; the bridge
gives every note one of them. It has played on the MS-60B+ on all four voices (525 confirmed notes,
see [The synth effects](#the-synth-effects)); **with the arpeggiator and the step sequencer it has
not run on a pedal.** KeyBand 0.20 has its Key1–Key4 in the same places and gets its notes by the
same rules, with Vel before each new one; **it has not been played through the bridge yet.**

- **A note keeps its voice.** Releasing a key closes its voice only; the others are not written
  at all. A chord goes out at once, one message per note.
- **A new note** takes the free voice that last played the same note (so one note never sounds
  on two voices), otherwise the voice that has been free longest, whose release has faded most.
- **A fifth note** takes the voice of the oldest note. Keys that are down beat notes the sustain
  pedal holds. If a newer key is released while the older one is still down, the older one gets a
  voice back, as with KeySynth.
- **Pitch wheel:** every sounding voice follows, in the same small steps as on KeySynth; the
  voices take turns, so a full bend of a four-note chord takes about four times as long.
- Key2–Key4 are left alone until a note needs them. The patch dump holds every knob (see
  `EDTB_WIDTHS` in `zoom_sysex.py`), and the bridge reads all four from it; one that holds a note
  nothing plays (a KeyBand 0.1x preset's Level, Voice and Chord read as 0.20's Key2–Key4) it
  closes at the first patch. When the bridge stops it closes every voice it has played.
- There is no glide: a tied note lands at once.

### Arpeggiator

The bridge can play the keys that are down one after another, on KeySynth and KeyPoly alike
("Arpeggiator" in the interface). It has run on the pedal with KeySynth at
its own tempo (see [What was measured](#what-was-measured-on-the-ms-60b-firmware-120)); **with
KeyPoly and with MIDI clock it has not.**

- **Mode:** up, down, up and down, as played (in the order the keys were pressed) or random.
  **Octaves:** 1–4. **Rate:** 1/4 to 1/32, triplets included. **Gate:** how much of a step a note
  sounds; at 100 % the notes are tied (with KeySynth's Glide they slide).
- **Latch** keeps a chord playing after you let go, until you play a new one. Notes the sustain
  pedal holds stay in the chord, too.
- **Clock:** its own tempo (40–300 BPM; the first key starts it at once), or **MIDI clock**: a
  step every so many clocks counted from Start, so it stays on the beat. Stop silences it and
  starts the pattern over, Continue picks up again; a clock that just stops silences it after half
  a second. The clock is that of the [external sequencer](#external-sequencer) if one is set up,
  otherwise the one arriving at the keyboard's port. For a DAW's clock, send it to a virtual port
  (IAC on macOS) and choose that port as the external sequencer.
- **On and off** in the interface, or from the keyboard: click "Learn" in the row "Controller" and
  press a button or pedal (on at values 64–127, like the sustain pedal). It is off whenever the
  bridge starts; its other settings are kept in `controls.json` and travel to the Pi with
  `export.py`.
- A setting changed while it plays applies from the next step; the chord, latched or held, plays
  on. Only changing the clock starts it over.
- On KeyPoly every note takes the next voice, so the notes ring into each other. The pitch wheel
  bends the arpeggio. Its notes are not printed in the terminal.
- **Load:** a note and its gate-off per step, kept at least the minimum interval (10 ms) apart. At
  1/16 and 120 BPM that is 16 messages per second, as in the 71-minute test; 1/32 at 300 BPM would
  be 80 per second, more than has been tried on the pedal.

### Step sequencer

The bridge can also play a pattern you have recorded: up to 64 steps, each a note, a rest or a tie
("Step sequencer" in the interface). It has run on the pedal with KeySynth:
written and recorded patterns with rests and ties, moved with other keys while they played, and
with Latch. **With MIDI clock and with KeyPoly it has not run on a pedal.**

- **The key you hold plays the pattern** and sets its pitch: the pattern's first note sounds at
  that key, the others keep their distance from it. The key the pattern starts with plays it as
  recorded, any other key moves it up or down, also while it plays, without starting it over. Let
  go and it stops; the next key starts it from its first step. A note that would leave what the
  effect can play (or `low_note`–`high_note`) is left out as a rest.
- **Record:** click "Record" and play the notes one after another; they sound as usual meanwhile.
  "Rest" adds a silent step, "Tie" lets the note before it sound on for another step, "Back" takes
  the last step away. "Stop" keeps the pattern, "Cancel" keeps the old one. Switching the
  sequencer on ends the recording and keeps it, too.
- **Or write it** into the "Pattern" field and press Enter: note names (`C3`, `F#2`, `Bb4`; C4 is
  middle C), `-` for a rest and `_` for a tie, for example `C3 E3 - G3 _ C4`. Esc brings back the
  stored pattern.
- **Rate, gate, clock, tempo and latch are the arpeggiator's.** The sequencer runs on the same
  clock: its own tempo, or MIDI clock (Stop starts the pattern over). With Latch the pattern plays
  on after you let go, from the key pressed last, until you switch it off. A tied note is held
  whatever the gate.
- **On and off** in the interface or with a control learned in its row "Controller", like the
  arpeggiator. Only one of the two plays: switching one on switches the other off. It is off
  whenever the bridge starts. The pattern is kept in `controls.json` and travels to the Pi with
  `export.py`; there it can be switched on and played, but not recorded.
- It plays one note at a time. On KeyPoly every note takes the next voice, as with the
  arpeggiator. The pitch wheel bends it, and its notes are not printed in the terminal.

### External sequencer

An MPC or another sequencer can be a second source of notes next to the keyboard, and the clock
for the arpeggiator and the step sequencer. **Written and tested against the simulated devices
only; it has not run with an MPC.**

- Choose its MIDI port under "Devices" → "External sequencer". In `config.yaml` that is
  `external:` under `synth:`; the bridge adds the line if yours is older. A keyboard is not needed
  then: the sequencer alone is enough.
- **Its notes play the synth effect** like keys: a MIDI track on the MPC plays KeySynth or
  KeyPoly. With `external_channel:` (1–16) only that channel is listened to, so the MPC's other
  tracks can go to other gear over the same cable; 0 listens to all channels.
- Keyboard and sequencer play the same keys: on KeySynth the last note wins whoever played it. Its
  pitch wheel, sustain pedal and controllers count like the keyboard's, and with the arpeggiator
  or the step sequencer on, its notes feed them.
- **Its MIDI clock is the one that counts** (Clock: "MIDI clock" in the arpeggiator's settings):
  Start, Stop and Continue of the MPC start and stop the arpeggiator and the step sequencer in
  time. A clock at the keyboard's port is ignored while an external sequencer is set up.
- It may be plugged in or pulled while the bridge runs. When it or the keyboard goes, every note
  is released.
- **How to connect an MPC One+:** its MIDI OUT into a USB MIDI interface at the computer is the
  safe way. Whether its USB-B port shows up as a MIDI device while it runs standalone is not
  confirmed.

## Switching effects on and off

A footswitch of the foot controller, a button of the keyboard or a pad of the external
sequencer can switch each effect of the chain on and off ("Preset" → "Effects" in the interface).
The message is the one the zoom-explorer source uses (parameter 0 of the effect, value 0 or 1).
It has run on the MS-60B+ from the interface and from a knob of the keyboard (see
[What was measured](#what-was-measured-on-the-ms-60b-firmware-120)). **Not tried on a pedal:** a
footswitch of the foot controller, "Press", a preset with fewer than six
effects, and a switch the pedal does not confirm. Try it first with a preset you have backed up.

1. Approve the message kind and, if you have not yet, the patch query, then start the bridge again:

   ```bash
   .venv/bin/python probe.py approve switch_effect query_patch
   ```

2. In the interface, click "Learn" next to an effect's lamp (they are listed by name and place) and press
   the switch that should switch it. It is learned from any of the controllers connected (with
   none, "Learn" says so). One control
   switches one effect; a control that set a synth knob or parameters of presets loses that.
3. Choose how it switches in the list next to it. **Value:** on at values 64–127, off below, for a
   switch that sends both itself (a toggle). **Press:** each press switches the effect over, for a
   switch that sends a value only while it is held down. Its controller shows as "CC 22"; the ×
   beside it takes the switch away.

- Effects are counted by their place in the chain, the same in every preset (unlike the
  parameters and scenes of a preset, which go with their effect, see
  [When effects move](#when-effects-move)). The bridge reads the chain from the pedal at the start
  and after every preset change; a place the preset does not have is not switched, and nothing is
  switched before the pedal has reported the chain.
- The interface shows which effects are on and switches them with a click, without a controller.
- If the pedal does not confirm a switch, the bridge sends it once more and then reads the preset
  again, so it shows what the pedal really holds.
- With Auto Save on, the pedal writes the switched state into the stored patch.
- The switches are kept in `controls.json` and travel with the backup ("Export…") and with the file for the Pi
  for the Pi. From the keyboard only its `channel:` is listened to, from the foot controller
  every channel; the expression pedal's own controller is never taken for a switch.

## Scenes

A scene sets parameters of the current preset to stored values, and switches effects on or off,
with one press: a level from 100 to 30, a delay on and its mix up. It has run on the
MS-60B+: scenes with up to eight values and five effect switches, recalled from the interface and
from learned controllers. It uses the messages of the expression pedal and the effect switches.

1. Select the preset on the pedal. In the interface, under "Preset" → "Scenes", click "Add
   scene" and give it a name.
2. Click "Capture", turn the knobs on the pedal (or in the effects above) to where the scene
   should put them, switch effects if the scene should, and click "Apply". The scene keeps the
   last value of every knob you touched **after** "Capture" – one set before is not taken. While
   it captures, its row shows in blue what it has taken so far ("nothing taken yet" at first).
   Capture again to add to it; a click on a value takes it out.
3. Recall it with "Recall" next to its name (orange: recalled last), or click "Learn" and press a footswitch,
   button or pad: from then on every press recalls it.
4. A click on its name renames it (up to 24 characters): Enter or a click elsewhere keeps the new
   name, Escape the old one.

- Scenes belong to a preset, up to eight each with up to eight values; every preset has its own,
  and the same controller can recall another scene in another preset.
- The values go out one per minimum interval (10 ms), so a scene with four values is set after
  about 40 ms. They jump; there is no fade.
- A value captured from the pedal counts as learned, like after "Learn from pedal". A scene never
  sets a value outside what was learned, and never a knob of the synth effect.
- If the expression pedal controls the same parameter, its next movement takes over again.
- Switching effects in a scene needs the approval of
  [Switching effects on and off](#switching-effects-on-and-off).
- Scenes are kept in `controls.json` and travel with the backup ("Export…") and the file for the Pi.

## When effects move

The parameters of a preset, the values and switches of its scenes, and the ranges learned for
them remember the effect they were made for (by the effect's id, as the pedal's patch names it).

- **An effect moved in the chain on the pedal:** what is assigned to it goes along. The pedal
  sends its patch after every step, the bridge follows at once, and keeps the new place. Changing
  the preset without saving puts the effects back, and the assignments with them.
- **An effect that is not in the chain** (replaced by another one, or deleted): what is assigned
  to it waits and sends nothing; the interface shows it by that effect's name, "not in this preset
  now". Put the effect back, anywhere in the chain, and it works again. If an effect that moved
  takes the place of one that is gone, the parameters of the gone one that would be assigned
  twice make way (Activity says which).
- **Two of the same effect:** each keeps its own assignments while it stays in its place. If one
  of them moves or goes, the bridge cannot tell which is which: its assignments wait.
- **Assignments from before 2026-10-19** learn their effect the next time their preset is selected
  on the pedal (as it was saved), not at the start of the bridge, when the pedal may hold changes
  that are not saved. New ones learn it at once.
- This needs the patch query (`probe.py approve query_patch`). Without it, everything stays by
  its place, as before. The switches of the effects (the same in every preset) stay by place too.

## Renaming a preset

A click on the preset's name in the interface gives the preset selected on the pedal another
name: up to 28 characters, letters, digits, spaces and `` ! # $ % & ' ( ) + , - . ; = @ [ ] ^ _ ` { } ~ ``
(the characters Zoom's manuals list for names). The bridge sends it the way the pedal itself
reports a name edited on it, one message per character, and reads the patch afterwards: the
interface and Activity say whether the pedal took it.

The name is changed like a knob: **save the preset on the pedal** to keep it (with Auto Save on,
the pedal does that itself). Changing the preset before that leaves the old name. A preset changed
while the characters are still being sent (on the pedal, or by a program change the bridge passes
on) stops the renaming: the rest of the name does not go into the other preset.

Tried on the MS-60B+ on 2026-10-19: a name in capitals and small letters was taken and
confirmed character by character, kept after saving on the pedal, and gone again (the old name
back) when the preset was changed without saving.

1. Approve the message kind and, if you have not yet, the patch query, then start the bridge again:

   ```bash
   .venv/bin/python probe.py approve rename_patch query_patch
   ```

2. Click the preset's name, type the new one, and press Enter (on a phone: Done) or click
   anywhere else. Escape keeps the old name; an unchanged name is not sent.

A name edited on the pedal shows in the interface as it is typed. **An empty preset gets a
name from the pedal itself** as soon as something is changed in it (an effect chosen for it:
"Empty" became "USER-012" on 2026-10-21). The pedal reports only some characters of that name
(U, S, E, R and 1, not the -, 0 and 2), so the bridge reads the patch after any name character
the pedal reports and shows the name it holds – before that fix it showed "USERy 1".

## Choosing and saving presets

**Choosing.** A click on the preset's number lists the pedal's 100 presets, with their names once
the bridge has read them; choosing one makes the pedal load it, as its footswitches do. The bridge
sends what the pedal sends when a preset is chosen on it: bank select and program change. The
pedal loads the preset as it is stored, so what was changed in the one before and not saved is
gone; an MS Plus pedal stores nothing on a program change (zoom-explorer measured it; the original
MS series did).

**The names** are read once the bridge is connected, one place after the other between everything
else (`46 00 00 <bank> <program>`, a few seconds for all 100), and follow a preset saved on the
pedal: it reports every preset saved on it.

**Saving.** "Save" beside the name stores the preset on the pedal as it is now: into its own place,
or, after asking, into another one, whose preset it replaces. The bridge reads the preset afresh,
sends it into the place in the form the pedal itself reports a saved preset in
(`45 00 00 <bank> <program> <length> <patch> <CRC>`; for all 9 such reports in the logs that
form gives the same message byte for byte), and reads the place back: the interface and Activity
say whether it holds the preset as sent. Into another place, what the bridge keeps for the preset
(its parameters, scenes and learned ranges) goes to that place too, replacing what it had there,
and the pedal is switched to it. Is another preset chosen on the pedal before it has sent the
patch, nothing is saved ("Saving stopped: preset changed").

**Undo.** After a save, a small ↶ appears beside "Save": it writes the place back as the bridge
read it before the save (the names are read with their whole patch), and what the bridge keeps
for that place (parameters, scenes, learned ranges) too; the pedal switches to it. It holds for
the last save, until the preset is saved over on the pedal or the bridge is started again; a save
into a place whose stored preset the bridge had not read yet leaves nothing to undo. No
approval of its own: it is a save.

Tried on the MS-60B+ on 2026-10-20: all 100 names read in 2.5 s after connecting; a preset
chosen by its number (the pedal reports the change 2 ms later, as when it is chosen on it); an
effect deleted, another preset chosen and back: the effect was there again, a program change
stores nothing; 011 saved into the empty 012 (read back as sent, the pedal switched to it, the
assignments went along); in 012 an effect deleted and saved into its own place: gone for good,
also after choosing presets on the pedal. Back up the presets before saving into another place.

1. Approve the message kinds and, if you have not yet, the patch query, then start the bridge again:

   ```bash
   .venv/bin/python probe.py approve select_preset query_preset save_preset query_patch
   ```

2. Click the preset's number to choose one; "Save" to store the one on the pedal.

## Which effects the pedal has ("Read Pedal")

The effects to choose from (see below) are those the pedal ever reported in a preset or in a list
of its files that passed by; nothing is taken off. **Read Pedal**, under "Effects", reads the
pedal's own list of its effect files, as the installer does, and from then on exactly those can
be chosen (plus the effects of the preset on the pedal now). It takes a second or two (longer when effect
files are read, see below), in which the pedal is in PC mode and does not play; it is always left again, and the keyboard and the
controllers wait. A list that is incomplete (fewer than ten files, or interrupted) changes
nothing. Files of effects the table does not know are named, not offered.

**At the start** the bridge reads the list once by itself, after the names of the presets, so that
the choice is always the pedal's own – but only when the preset on the pedal is exactly as it is
stored (the bridge asks for the patch right then and compares the two, so a knob turned in the
meantime counts). Not again when the pedal is connected anew while the bridge runs (a cable
pulled while playing: no PC mode a few seconds later). With changes not saved it leaves it and says so in
Activity; "Read Pedal" reads it whenever you like. As the pedal goes into PC mode it loads its
preset again as it is saved: what was changed and not saved is lost (the interface asks first). The answers name each file in 12 places; a name of
12 characters fills them all ("160_COMP.ZD2"), which a first try on 2026-10-21 read as the end
of the list after 7 files – fixed. At the next start the bridge read all 295 files in half a
second (the pedal was in PC mode for 1.1 s): 138 effects the table describes, the choice since. Approve first:

```bash
.venv/bin/python probe.py approve read_pedal
```

**Effect files the table does not describe** (see [Names of effects and
knobs](#names-of-effects-and-knobs)) are read while the pedal is in PC mode anyway, after its list:
each as the installer reads one for its backup, but only as far as the effect's table of knobs –
about half of the file, roughly a second per effect (27 files the installer read on 2026-10-17
to 21, read again from the log: the knobs of each as from the whole file, after 10 to 37 of its
512-byte parts; most of the time is the pedal ending the file session, about 0.6 s). At most 20
at a time and none begun after 20 seconds; Activity names them first, and the button shows
"Reading…" meanwhile (other clicks wait, and one the interface gave up on is not done later).
Once read, a file is known by its name and size and not read again – also one that holds no
effect – until the pedal's list no longer has it at that size. A file the pedal does not open,
that ends before its size, or a part of it not as sent (its CRC), is tried again at the next
Read Pedal, after the others; if the pedal stops answering for one, the reading ends there for
now and the list read before is taken all the same. The file session is always ended as the
installer ends it, also when the list stops halfway, before the pedal leaves PC mode.

A custom effect whose knobs show text is read a little further, to its table of texts (on average
6 parts more; HITS.ZD2, the most, 90 of its 104). N2Z Bank (N2ZBANK.ZD2) is read again at every
Read Pedal, as a bank with other models can have the same size. A custom effect the pedal has in
another build keeps the table's texts as long as its knobs are the same.

On the Pi, `export.py` puts what was read on the computer beside the settings file
(`pedal-effects.json`); a Pi with its interface on the network adds what it reads itself, one
without only reads it.

A synth effect (KeySynth …) whose file holds other knobs than the bridge plays is played with
notes only until the bridge starts again (not at all if the Key knobs the bridge plays are not all
Key knobs in it, as with KeyBand played with four voices on a 0.1x); from then on with the knobs
read, and with as many voices as its Key knobs. This has its own
approval:

```bash
.venv/bin/python probe.py approve read_effect_file
```

## Changing an effect

A click on an effect's name under "Preset" → "Effects" lists the effects the pedal has, by
group (Dynamics, Filter … Reverb) as the pedal lists them: a custom or community effect in the
category it is installed into (the synth effects in Synth, others in Filter, Drive, Modulation,
Pitch shift, Delay or SFX), and once
also where an older preset reported it under the id of an earlier build. Choosing one puts it
into that place of the preset selected on the pedal, with its knobs at their defaults. The bridge
sends it as zoom-explorer does (parameter 1 of the place, the effect's whole id as its value)
and reads the patch afterwards: the interface and Activity say whether the pedal took it.

- **Only effects the pedal has:** those in its own list of its files, and those it reported in a
  preset (kept in `measurements.json`; on 2026-10-20 all 123 effect files on this pedal, and BPM).
  The bridge reads the list whenever the installer asks the pedal for it while the bridge runs
  (`60 04 25 …`, the answers pass by on the same port), and takes an effect file it names as one
  the pedal has, once it is in `zoom_effects.json`. Nothing is taken off: the installer lists the
  files after deleting what it replaces and before writing it again, so a list does not show what
  is gone for good. An effect deleted from the pedal stays in the choice; choosing it, the bridge
  reads back that the pedal did not take it.
- **A place that holds an effect, or an empty one** (see [Adding an effect](#adding-an-effect)).
  Deleting effects stays on the pedal.
- **Only what fits into the DSP:** every effect file declares the DSP the effect takes (see
  [Names of effects and knobs](#names-of-effects-and-knobs)), and the MS-60B+ runs a preset whose
  effects take up to **270** together, those switched off as well (measured on 2026-10-22: 269.93
  ran, 270.04 showed PROCESS OVERFLOW, also with one of the effects off). Over MIDI the pedal does
  not check it: it takes any effect, shows PROCESS OVERFLOW and then ignores switching it. So the
  bridge refuses an effect that does not fit beside the others of the preset, on or off ("AC 370
  takes 127 of the DSP, 121 are free in this preset – the pedal would show PROCESS OVERFLOW"). In
  the list each effect shows the DSP it takes ("AC 370 · 127"); one that does not fit is greyed out
  and cannot be chosen, never the one in the place now (the DSP the chain takes is in the title
  line of "Effects"). The loads are compared as the files declare them. An effect
  whose DSP is not known (not in the table, and not read from the pedal yet) is let through, and
  one in another place counts as nothing. Adding an empty place adds
  nothing, moving an effect changes nothing.
- Like a knob: **save the preset on the pedal** to keep it. Changing the preset before that
  leaves it as it was saved.
- What was assigned to the effect that was replaced waits, as in
  [When effects move](#when-effects-move); the new effect gets assignments of its own.

Tried on the MS-60B+ on 2026-10-19: PH+Dist replaced by Hall REV, then by AMPG SVT, from the
interface. The pedal showed and played each new effect and sent its patch about 0.12 s later
(it does not confirm this message as it does a knob); changing the preset without saving brought
PH+Dist back. Not tried yet: a custom effect, and a place other than the first.

1. Approve the message kind and, if you have not yet, the patch query, then start the bridge again:

   ```bash
   .venv/bin/python probe.py approve change_effect query_patch
   ```

2. Click the name of the effect to replace and choose the new one. Escape or a click elsewhere
   leaves it as it is.

## The editor

The arrow (▸) beside an effect's name under "Preset" → "Effects" opens all its knobs as sliders
under it, named as the pedal names them, each with what it holds now. The lamp beside the name
switches the effect: green on, red off. Moving a slider sets the knob on the
pedal, like turning it there; a knob turned on the pedal moves its slider.

- **What it holds** is read from the preset (each effect's 24 bytes of settings, see
  `EDTB_WIDTHS` in `zoom_sysex.py`: 151 knobs turned on the MS-60B+ read back the same), then
  follows what the pedal reports and confirms.
- **Its range** is the knob's own, from the effect file (`zoom_effects.json`). An effect the table
  of names does not know has no sliders.
- **Its value** is shown as the pedal shows it ("Rock", "1/8.", "1.0kHz"; see
  [Names of effects and knobs](#names-of-effects-and-knobs)), wherever a value of it appears: the
  slider, heel and toe, the chips of a folded effect, scenes and the list of parameters.
- The Key knobs of a synth effect are shown, not set: they are notes, played or not.
- A slider sets only the effect it was made for: if another effect is in that place by the time
  its value arrives (moved or changed on the pedal), nothing is sent. While an effect is being
  changed or moved, the editor waits.
- Values go out with the same pace as those of the assignments, after them; while the slider is
  held, only the latest one waits. Like a knob: **save the preset on the pedal** to keep them.
- Uses the "set parameter" message only, which the bridge needs anyway: no approval of its own.

## Adding an effect

The green + under "Preset" → "Effects" adds an empty place at the end of the chain (at most
six); choose an effect for it by clicking its name ("Empty"), as in
[Changing an effect](#changing-an-effect), and drag it where it should go
([Moving an effect](#moving-an-effect)).

The pedal has no message for it either. As for a move, the bridge reads the preset afresh, inserts
the place (its id 0, its 24 bytes of settings as the pedal writes them for an empty place, and the
preset's own length; the bits of "PRM2" of the places after it move up), sends the whole preset
back and reads it again. For all 19 places added on the MS-60B+ itself (logs of 2026-10-18 to 20),
that gives the preset the pedal reported next, apart from which effect is selected on it and knobs
turned in between.

- Like a knob: **save the preset on the pedal** to keep it.

Tried on the MS-60B+ on 2026-10-20: two places added to a preset of one effect, SlowATTCK and
BassGEQ chosen for them, BassGEQ dragged from place 3 to 1 and PH+Dist from 2 to 3. The pedal
showed and played each step; read back 0.3 s after each added place it held the preset exactly as
sent (apart from which effect is selected on it), and it answered an effect chosen for an empty
place with its patch after about 0.1 s, as for a changed one. Changing the preset without saving
brought the saved one back.

1. Approve the message kind and, if you have not yet, the patch query, then start the bridge again:

   ```bash
   .venv/bin/python probe.py approve add_effect query_patch
   ```

2. Click the green +, then the name of the new empty place, and choose an effect.

## Deleting an effect

The red × beside an effect's name under "Preset" → "Effects" deletes it from the chain (an
empty place too); those after it move up by one. A preset keeps at least one effect: the last
one cannot be deleted, choose another one for it instead.

The pedal has no message for it either. As for a move, the bridge reads the preset afresh, takes
the effect out (its id, its 24 bytes of settings, and 28 bytes off the preset's own length; the
bits of "PRM2" of the places after it move down), sends the whole preset back and reads it
again. Which effect is selected on the pedal stays selected; if it is the one deleted, the one
after it is, or the last. For the 4 effects deleted on the MS-60B+ itself (logs of 2026-10-19)
that gives the preset the pedal reported next, apart from knobs turned in between and places the
pedal left in its list of selected ones that were gone.

- What is assigned to the effects after it goes along, as in
  [When effects move](#when-effects-move); what is assigned to the effect deleted waits, as for
  an effect that is not in the preset. Where the effect moving into its place brings an
  assignment of the same parameter, or entries of the same scene, those of the deleted effect
  make way for them (Activity says so) and do not come back if it does.
- Like a knob: **save the preset on the pedal** to keep it; changing the preset without saving
  brings it back.

Tried on the MS-60B+ on 2026-10-20, in a preset of one effect (PH+Dist): empty places deleted at
the end and before PH+Dist, AMPG SVT deleted after an empty place before it was (its amp bit moved
down with it) and as the last effect, BlackOpt deleted from the middle with AMPG SVT after it, and
empty places deleted with the × in the interface. Read back 0.3 s after each of the seven, the
pedal held the preset exactly as sent, which effect is selected on it included. At the end the
preset was byte for byte the one it started with.

1. Approve the message kind and, if you have not yet, the patch query, then start the bridge again:

   ```bash
   .venv/bin/python probe.py approve delete_effect query_patch
   ```

2. Click the × beside the effect.

## Moving an effect

Drag an effect by its grip (⠿, left of its name under "Preset" → "Effects") onto another effect:
it moves to that place in the chain, and those in between move up by one. With the grip selected,
the arrow keys move it one place. Escape while dragging leaves it where it is.

The pedal has no message for moving an effect. The bridge does what zoom-explorer does: it reads
the preset from the pedal afresh, swaps the effect with its neighbour place by place (its id, its
24 bytes of settings and its bits in the chunk "PRM2"), sends the whole preset back into the
pedal's edit buffer and reads it again: the interface and Activity say whether the pedal took it.
Nothing else in the preset is changed. The swap gives, byte for byte, the preset the pedal itself
reported after each of 23 moves made on it (apart from on/off and which effect is selected on it).

- What is assigned to the effect goes along, as in [When effects move](#when-effects-move).
- Like a knob: **save the preset on the pedal** to keep it.
- The protocol reference warns that a malformed preset sent to a pedal can hang it; see
  [Safety](#safety) for what is checked. Try it first with a preset you have backed up.

Tried on the MS-60B+ on 2026-10-19: three moves in a preset of two effects (BrghtHall and HOGi,
EnvFilter and AnalogDly). The pedal showed and played the new order and, read 0.3 s later, held
the preset exactly as it was sent (apart from which effect is selected on it); it does not answer
the preset itself. Changing the preset without saving brought the saved one back. On 2026-10-20
a move over two places in a preset of three effects was tried too (see
[Adding an effect](#adding-an-effect)).

1. Approve the message kind and, if you have not yet, the patch query, then start the bridge again:

   ```bash
   .venv/bin/python probe.py approve move_effect query_patch
   ```

2. Drag an effect by its grip onto the place it should go to.

## Two pedals

One bridge plays one pedal. A second pedal of the MS Plus series gets a second bridge with a
profile of its own: its settings, approvals, learned ranges, keyboard controllers and log are kept
in `profiles/NAME/`, apart from those of the first. **This has not been tried with two pedals.**

1. Set the profile up like the first installation ([First-time setup](#first-time-setup)), with
   `--profile NAME` before each command. The first one creates `profiles/second/config.yaml`:

   ```bash
   .venv/bin/python probe.py --profile second ports
   ```

2. In that `config.yaml`, give the second bridge a port of its own for the interface under
   `ui:`, for example `8766`. Two bridges cannot share one.
3. Start it next to the first one. Its interface names the profile in its title.

   ```bash
   .venv/bin/python bridge.py --profile second --open
   ```

4. Choose its pedal, keyboard and foot controller under "Devices". Each pedal can have its
   own controllers. Both bridges can also listen to the same keyboard or foot controller,
   each with its own assignments; macOS and Linux let several programs read one MIDI port.

Pedals of the MS Plus series all report the port name "ZOOM MS Plus Series". If the system
numbers the second one ("ZOOM MS Plus Series 2"), each bridge is given the exact name and finds
its pedal. **If both ports carry exactly the same name, the bridges cannot tell them apart yet**;
`python probe.py ports` shows what your system makes of them. `export.py --profile NAME` writes a
profile's settings for a Pi.

## On a phone or tablet (iOS, Android)

iPhone, iPad and Android phones and tablets cannot run the bridge, but they can use its
interface: everything the computer's browser shows – presets, the effects and their knobs,
scenes, assignments, the keyboard's controllers – in Safari, Chrome or another browser. The bridge
runs on a computer or a Raspberry Pi that the pedal is plugged into, and the phone reaches it over
the WLAN.

- **A Raspberry Pi** (below) is made for it: open `http://<name of the Pi>.local` in its WLAN, or
  join the Pi's own WLAN and open `http://10.42.0.1`.
- **A Mac, Windows or Linux computer:** set `listen: true` under `ui:` in `config.yaml` (or start
  it with `--listen`, e.g. `Start Bridge.command --listen`). The bridge then says in its window
  where a phone in the same WLAN finds it, for example `http://my-mac.local:8765` or
  `http://192.168.1.20:8765`. Windows asks once whether Python may accept connections: allow it for
  private networks. **Everyone in that WLAN can then use the interface** (and so change the pedal);
  leave it off in a WLAN you share with strangers.
- Type the address with `http://` in front. Brave on iOS needs "Local Network" allowed in the iOS
  settings; Safari needs nothing.

## On a Raspberry Pi, without screen

A Raspberry Pi takes the computer's place on the pedalboard: pedal, keyboard and foot controller
are plugged into it, and the bridge starts when it is switched on. Everything is set up on this
computer first (devices, assignments, approvals, the keyboard's controllers); the Pi runs what it
is given, and its interface can be opened on a phone or tablet to change things there. The first start and
playing have run on a Raspberry Pi 4 (2026-10-18); the interface on the Pi, its own WLAN and
Bluetooth MIDI were written and tested against simulated devices and a stand-in for the Pi's
system (`tests/test_pi_install.py`) only.

### From an empty SD card

1. **Write Raspberry Pi OS onto the card** with [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
   (version 2.0 or later): device *Raspberry Pi 4*, system *Raspberry Pi OS (other) → Raspberry Pi
   OS Lite (64-bit)*, then the card. In the customisation give it a host name (for example
   `msplusbridge`), a user name and password, and your **WLAN** with its country (a phone's
   hotspot will do). The Pi needs the internet once, at its first start; a network cable does as
   well. SSH is not needed.
2. **Leave the card in this computer** (or put it in again): it appears as `bootfs`. With the
   bridge set up and working here, double-click **`Write Pi Card.command`**, or:

   ```bash
   .venv/bin/python export.py
   ```

   It writes the settings (`expression-bridge.yaml`), the program (folder `expression-bridge/`) and,
   on a card that has not been in the Pi yet, tells the Pi to install it at its first start. It
   prints the name and password of the Pi's own WLAN (see below) and ejects the card.
3. **Card into the Pi**, the pedal (on its own power supply), keyboard and foot controller
   into its USB ports, power on. The first start takes a few minutes: Raspberry Pi OS sets itself
   up, the Pi loads four packages (`python3-mido`, `python3-rtmidi`, `python3-yaml`, `bluez`), sets
   up the services and starts the bridge. Meanwhile the green light beats like a heart.
4. From then on: switch it on and play. **The internet is not needed any more**, nor the hotspot.

What the installation did is in `expression-bridge-install.txt` on the card, readable when the
card is back in this computer. Without internet it stops, says so there and tries again at every
start. On a card written with Raspberry Pi OS from before November 2025 (no `user-data` on it,
`export.py` says so), log in once and run `sudo bash /boot/firmware/expression-bridge/pi/install.sh`.

**A newer program on a Pi that is set up already:** write the card again. If the program needs
more of the system than the Pi has (a higher `LEVEL` in `pi/install.sh`: level 2 brought Bluetooth
MIDI, the Pi's own WLAN and the interface), the bridge runs the installation again by itself at
its next start; that needs no internet unless a package is missing, and then it restarts.

The green light:

| Light | Meaning |
|---|---|
| beating like a heart | installing (first start) |
| slow blinking | the bridge waits for the pedal or a controller |
| steady | ready |
| two short flashes | ready, with the previous settings file (the current one is not usable) |
| fast blinking | nothing is sent: no usable settings, the wrong pedal, or the installation failed |

With the pedal alone the Pi is connected to it, and its preset can be edited in the Pi's interface,
but the light keeps blinking slowly until a keyboard, an external sequencer or a foot controller is
there too: the Pi without screen is for playing, and "steady" means it can be played.

### The interface on the Pi

The Pi shows the same interface as the computer, for a phone or tablet in the same WLAN: open
`http://<host name>.local` (for example `http://msplusbridge.local`; on a phone that cannot resolve
`.local`, the Pi's address). What is changed there is written into the settings file on the card
at once (the one before it is kept as `expression-bridge.prev.yaml`), so it holds after the next
start too; a change that breaks a rule of the file is refused and undone, so the next one is kept
again. The effects the pedal reports there are written into the file as well, to be chosen after
the next start. Writing the card on the computer again replaces it with the computer's settings: to
keep what was changed on the Pi, click "Export…" → "Backup" in the Pi's interface and "Import…" in the
computer's before writing the card (devices and approvals are not in that file). The Pi does not
hand out its settings file ("File for the Pi" under "Export…" is only on the computer): it holds the
password of the Pi's own WLAN, and the interface is open to everyone in the WLAN.

**The Pi's own WLAN.** Where the WLAN set in Raspberry Pi Imager is not around when it starts, the
Pi opens a WLAN of its own, so that the phone always finds it: "MS Plus Bridge", with a password of
its own that `export.py` makes once and keeps in `config.yaml` under `pi:` (shown in the computer's
interface under "Devices", and printed when the card is written). Join it with the phone and open
`http://<host name>.local` or `http://10.42.0.1`. The Pi decides at its start: once it has opened
its own WLAN it stays there until it is started again.

### Bluetooth MIDI

A keyboard or foot controller with Bluetooth MIDI (BLE MIDI, for example the M-Vave Chocolate
Plus) needs no cable: switch it on near the Pi, and within a few seconds the Pi connects it
(`pi/bluetooth-midi.sh`, its own service). It is then a MIDI port like one on USB, under a name of
its own: choose it under "Devices" in the Pi's interface (or "Any keyboard" for a keyboard).
While a Bluetooth MIDI device is connected the Pi does not search for others, as searching
shares the radio with the connection and the WLAN. Expect a few milliseconds more delay than
over USB, and a little jitter. **Not tried yet on the Pi.**

### Changing settings later

- **In the Pi's interface**, see above.
- **The card:** take it out of the Pi (switch it off first), put it into this computer and write
  it again (`Write Pi Card.command`). Settings and program are brought up to date together. The
  settings file that was there is kept as `expression-bridge.prev.yaml`; the Pi falls back to it
  if the new one turns out to be unusable.
- **A USB stick**, without taking the card out: save "Export…" → "File for the Pi" in the interface
  (under "Devices") or run `.venv/bin/python export.py --to /Volumes/STICK`, and put the stick into
  the running Pi. Within two seconds the bridge takes the file over onto the card and starts again
  with it; take the stick out then (left in, it is taken over again at the next start). It is
  mounted read-only, and a file that breaks a rule is not used. **A stick carries the settings
  only:** once the program on this computer is newer than the Pi's, write the card instead; an
  older Pi refuses settings of a newer program (from 2026-10-20 on it says so).

### What is different on the Pi

- **The log** goes to the system's journal; with SSH: `journalctl -u expression-bridge -f`. The
  interface on the Pi shows the most recent lines under "Activity".
- **Port names.** Linux names a MIDI port "client:port number:number", for example
  `SINCO:SINCO MIDI 1 32:0`. A device is found by the name chosen on the Mac in its client or
  port name; of a device with several ports (the A61 has a second one for DAW control) the first
  is taken. A port chosen in the Pi's interface is found again when its numbers change (Bluetooth
  devices are numbered as they connect, USB ones as they are plugged in); the loop-back port "Midi
  Through" and the bridge's own ports are not offered. If a device is not found, the journal lists
  the ports there are. Two identical devices that fit one name are left out (said once) rather than
  guessed. A pedal whose cable was out and in again between two checks of the ports keeps its
  name on Linux but delivers nothing: when it does not answer edit enable any more, the bridge
  connects anew. Not checked: the
  external sequencer through the ZOOM UAC-232, whose Linux name may not contain the Mac's.
- **Where it runs.** The program stays on the card's boot partition and runs from there, as a
  systemd service (`pi/expression-bridge.service`) that starts at power-on and again two seconds
  after any end. It runs as root, for the light, the interface on port 80 and for taking a
  settings file over from a stick; all of the system except the boot partition is read-only for it.
- **Pulling the plug.** Program and settings are only read, and written only when they are changed
  (a new file in full, then renamed, and the folder written to the card at once). The system's log is kept in memory, and the daily package
  and manual-page jobs are off, so the card is written to as little as possible. The system itself
  is not made read-only (overlay file system): on the current Raspberry Pi OS that is reported
  unreliable. It can be tried by hand under `sudo raspi-config` → Performance Options → Overlay
  File System.
- **Power.** The Pi 4's USB ports share 1.2 A. The pedal belongs on its own power supply; the Pi
  needs its official 5.1 V / 3 A one. A keyboard powered over USB has not been tried on it.

Which Pi: memory is not what decides. The bridge takes about 40 MB and 2–3 % of one core on a
Mac mini, and Raspberry Pi OS Lite is reported to idle at 120–250 MB, so 1 GB is plenty, with the
interface too. What matters is four USB ports, a 64-bit system and a good power supply: a Pi 4
Model B fits, a Pi 3 Model B+ should.

Still to try on the real device: the interface on the Pi, its own WLAN, Bluetooth MIDI, the light,
the external sequencer's port name on Linux, and pulling the plug while it runs.

## Safety

- **Allowlist.** Every SysEx message is built in `zoom_sysex.py` and checked before sending.
  Only sixteen message kinds exist: identity request, parameter edit enable/disable, set
  parameter, query current program, query current patch, switch effect, rename preset, change
  effect, move effect, add effect, delete effect, choose preset, read a stored preset, save
  preset, read the pedal. Commands for firmware mode, factory reset, file access, system settings, the original
  MS series' store command and the reference's command for deleting effects are explicitly
  refused, and so is sending a patch, into the edit buffer or into a place, except the one
  prepared for a move, an added place, a deleted effect or a save (below).
- **Approvals.** Nothing is sent until you have confirmed a backup and approved each message
  kind once (`approvals.json`).
- **Learned ranges are strict.** A learned parameter is only ever set within the range the
  pedal reported for it by its assignments and scenes. The editor sets a knob of an effect in
  the chain within the knob's whole range as its effect file gives it, and only for the effect
  its slider was made for (never a synth effect's Key knobs), and not for an effect whose build
  on the pedal does not fit the table. A value that waited for an effect which is no longer in
  the chain is dropped, not sent to the one in its place; a scene recalled before the pedal has
  reported its chain, or while an effect is changed or moved, waits for it.
- **A synth effect's Key knobs are notes.** Only the note logic writes them: no assignment of a
  controller, scene or editor value reaches the Key knobs of any synth effect in the chain,
  played or not.
- **A refusal of the allowlist never stops the bridge.** It is logged ("WARNING: not sent") and
  the message is dropped.
- **Parameters that were not learned** are sent with the range you enter. Until the pedal has
  confirmed such a parameter once, it gets one message at a time; after five unconfirmed
  messages the bridge stops sending to it and the interface says so.
- **The keyboard synth writes the synth effect's knobs only.** Notes and bends are built with the
  parameter fixed to one of the four Key knobs (parameters 2–5, 0–1000); a controller writes the
  knob it was assigned to, within that knob's own range. Before a new note the bridge also sets
  KeyBand's Vel and Pluck's Trig itself, within their ranges and only while nothing assigned sets
  them. All of it passes the allowlist only while the pedal's own patch dump shows one of the
  seven synth effects in that slot, and only with the knobs of the one it shows (another build
  than the table describes: its Key knobs only). An expression assignment that points at a Key
  knob, or whose range is wider than the knob it points at, is not sent.
- **Switching an effect** is a message kind of its own with its own approval: parameter 0 of an
  effect, with the value 0 or 1 and nothing else. It passes the allowlist only for the places the
  pedal's own patch dump lists for the current preset.
- **Changing an effect** is a message kind of its own with its own approval: parameter 1 of a
  place, the effect type, with an effect's id. It passes the allowlist only for a place the
  pedal's own patch dump shows an effect in, and only with an effect the pedal itself reported in
  one of its presets, so one it has. Through "set parameter", parameter 1 can never be written.
- **Moving an effect** is a message kind of its own with its own approval: the whole patch into
  the edit buffer (`28 …`), which is otherwise refused. It passes the allowlist only as the one
  message the bridge has just made from the patch the pedal reported last, read afresh for the
  move, with neighbouring effects swapped and nothing else changed. Before it is sent, and again
  in the allowlist, the patch has to be laid out as the pedal's own are: 1–6 effects, a settings
  block per effect that names its id, and the bits of "PRM2" that mark amp, BPM and line selector
  effects matching the chain (as in all 1223 patches of the MS-60B+ in its log). While it is on
  its way, no values are sent to the places of the chain, and no notes.
- **Adding a place** is a message kind of its own with its own approval, the same patch upload
  under the same rules: the one message the bridge has just made from the patch the pedal
  reported last, with an empty place inserted (at most six) and nothing else changed.
- **Deleting an effect** is a message kind of its own with its own approval, the same patch
  upload under the same rules: the one message the bridge has just made from the patch the pedal
  reported last, read afresh for it, with one effect taken out (at least one is left) and nothing
  else changed but which effect is selected on the pedal, where that one is gone.
- **Saving a preset** is a message kind of its own with its own approval: a patch into one of
  the 100 places (ten banks of ten), otherwise refused, only as the one message the bridge has just
  made from the patch the pedal reported last, read afresh for it, laid out as the pedal's own
  are and with its CRC; the place is read back afterwards. Into another place than its own only
  after the interface asked. **Choosing a preset** (bank select and program change, no SysEx)
  and **reading a stored preset** (`46 …`, places 0–99 only) have their own approvals too.
- **Reading the pedal** ("Read Pedal") is a message kind of its own with its own approval, the one
  place where the bridge touches the pedal's file access: exactly the messages the installer uses
  to list the files (PC mode on and off, the first and next entry of the list of everything, the
  two messages that end the file session), and only while the list is read. **Reading an effect
  file** (`read_effect_file`, its own approval) is only possible in that time too: opening for
  reading the one `.ZD2` file of the list just read that is being read (an 8.3 name, as the pedal
  lists them), asking for its status and its next 512 bytes – the installer's own messages for a
  backup. Each part is checked against its CRC. Opening any other file, writing, deleting or
  naming a file stays refused.
- **Renaming a preset** is a message kind of its own with its own approval: one character of
  the name per message, places 0–27 and the characters Zoom lists for names only, the same form
  the pedal sends when a name is edited on it. It changes the preset selected on the pedal, and
  stays only once that is saved on the pedal.
- **Not tested:** what the pedal does with an effect or parameter number that does not exist
  in the preset. The reference documentation mentions a related system command whose invalid
  values freeze the pedal, so the firmware does not validate everything.
- Parameter changes are not stored on the pedal as long as Auto Save is off.

## What was measured on the MS-60B+ (firmware 1.20)

The protocol reference is the README of [zoom-explorer](https://github.com/thammer/zoom-explorer),
written for the MS-50G+. These points were confirmed on the MS-60B+; all bytes are hex.

| What | Message | Observed |
|---|---|---|
| Identity request | `F0 7E 7F 06 01 F7` | Reply `F0 7E 00 06 02 52 6E 00 27 00 31 2E 32 30 F7`: device ID `6E`, model `27 00`, version "1.20" |
| Parameter edit enable / disable | `F0 52 00 6E 50 F7` / `… 51 F7` | Ack `F0 52 00 6E 00 00 F7` |
| Set parameter | `F0 52 00 6E 64 20 00 <slot> <param> <LSB> <MSB> 00 00 00 F7` | Ack `… 64 20 01 <slot> <param> <LSB> <MSB> …` after about 4–11 ms, **only if the value changed** |
| Query current program | `F0 52 00 6E 33 F7` | CC 0, CC 32, program change; also answered without edit enable |
| Switch an effect on or off | `F0 52 00 6E 64 20 00 <slot> 00 <0 or 1> 00 00 00 00 F7` | Ack `… 64 20 01 <slot> 00 <0 or 1> …` after 21–45 ms; all six slots of a preset were tried |
| A character of the preset's name | `F0 52 00 6E 64 20 00 5F <place> <character> 00 00 00 00 F7` | Sent by the pedal for each character while a name is edited on it, places 0–27, ASCII. Sent to it: ack `… 64 20 01 5F <place> <character> …`, 56 of 56 confirmed after 3–99 ms (10 ms apart, the pedal fell behind by up to 80 ms but lost none) |
| Change the effect in a place | `F0 52 00 6E 64 20 00 <slot> 01 <id, 7 bits per byte, lowest first> F7` | No ack; the pedal sends its patch (`64 12 00 …`) after 116–127 ms with the new effect in the place, its first knob at the default its effect file gives (2 of 2) |
| A patch into the edit buffer (moving, adding, deleting effects) | `F0 52 00 6E 28 <patch, 7-bit packed> F7`, 976 bytes for an 848-byte patch | No reply. Read back 0.3 s later, the pedal held the patch as sent, apart from which effect is selected on it (PRM2 bytes 10–12): 5 of 5 moves, 9 of 9 added places, 7 of 7 deletions (these with PRM2 10–12 too). After an added place it lists the places after the selected one anew |
| Query current patch | `F0 52 00 6E 64 13 F7` | Reply `… 64 12 01 <length LSB MSB> <patch, 7-bit packed> <CRC32, 5 bytes> F7`, 985 bytes in all for an 848-byte patch |

- Slots count from 0 (first effect in the chain), parameters from 2 (first knob). Per the
  zoom-explorer source, parameter 0 is effect on/off and 1 is the effect type. The bridge sends
  parameter 0 only as "switch effect" and parameter 1 only as "change effect".
- Switching effects: 44 switches, on all six slots of a preset, were all acknowledged, after 23 ms
  in the median (21–45 ms, slower than a knob). An effect switched with the pedal's own footswitch
  is reported as `64 20 00 <slot> 00 <0 or 1> …`, like a knob that was turned. The patch dump read
  afterwards showed all six effects on, as they had been left. Not measured: whether the pedal
  stays silent when it is sent the state it already holds.
- With edit mode enabled the pedal reports knob movements as `64 20 00 <slot> <param> <LSB> <MSB> …`.
- On a preset change the pedal sends `64 26 00 00 <bank LSB MSB> <program LSB MSB>`, then
  `64 20 00 64 02 …` (tempo, per the reference), then CC 0, CC 32 and a program change.
- The number shown on the pedal is `bank × 10 + program + 1` (preset 095 = bank 9, program 4).
- The value is split into 7-bit groups, lowest first. Verified up to 451 (with the KeySynth's
  Key knob); values up to 127 sit in the LSB alone.
- The very first "set parameter" after connecting was once applied without an ack.
- The patch ("PTCF") lists the effect ids in chain order from offset 36, the effect count is at
  offset 12; the pedal has 6 effect slots. Read with zoom-zt2's `decode_preset.py` layout.
- The name is in the patch twice: its first 10 characters at offset 26, all 28 in the chunk
  "NAME" (filled up with spaces, then four zeros). While a name is edited on the pedal it sends
  bursts of "program change 0" without bank select in between (the bridge ignores a program
  change without bank select). Saving a preset on the pedal sends
  `45 00 00 <bank LSB MSB> <program LSB MSB> <length LSB MSB> <patch> <CRC>`, then the preset
  change messages.
- 128 "set parameter" messages 26.8 ms apart were all acknowledged (measured with the
  KeySynth's Key knob); the ack came after 1–10 ms. In a 71-minute run, 67,200 messages
  (about 16 per second) were all acknowledged.
- Of two messages 1 ms apart the pedal acknowledges only the second. While its own knobs are
  turned or its menus are open, it answers late or not at all for 0.1 to 0.7 s.
- Two messages to different parameters at most 3 ms apart were both acknowledged (18 of 18: a
  note of the arpeggiator next to a value from the expression pedal or the mod wheel).
- Arpeggiator and step sequencer at 1/16 and 120 BPM, with KeySynth: the notes left the bridge
  125 ms apart (123–127 ms), and in the first minute every one of 508 notes and gate-offs was
  acknowledged, after 9–10 ms. With the expression pedal and the mod wheel moving as well, up to
  57 messages went out in one second without a miss. Twice the pedal acknowledged nothing for
  about 0.25 s, each time when it reported `64 20 00 64 01 …` itself; the bridge repeated the
  notes and carried on. At 1/32 and 200 BPM with tied notes, 1,114 notes 37.5 ms apart (31–44 ms)
  were all acknowledged.
- The same at gate 50, a note and a gate-off every 37.5 ms: 10,935 messages at up to 55 per
  second, 10,881 acknowledged one by one, after 3 ms in the median. The other 54 fell into the
  moments when knobs were turned on the pedal; in 68 s without that, all 2,393 were acknowledged.
- After another program had read files from the pedal (it answered with `60 …` messages), the
  pedal no longer reported changes to its effect chain. The bridge noticed with the first notes
  that stayed unconfirmed, switched edit mode on again and asked for the patch.
- Scenes: four values sent 11 ms apart to four parameters of one effect were all acknowledged; the
  scene was set after about 70 ms. Values the pedal already held were not acknowledged.
- The Chocolate Plus sends about 30 CC messages per second. The bridge sends at most one
  SysEx per 10 ms, so with four parameters each one is updated about 25 times per second.

## Tests

The tests run the bridge, its interface and the setup script against a simulated pedal and
controller. They open no real MIDI port and need no hardware.

```bash
.venv/bin/python -m unittest
```

Run them after every change. They cover the allowlist, throttling, preset changes, replugging,
learning, saving, export and import, how parameters that were not learned are handled, the
keyboard synth (with one real patch dump of the pedal as a fixture), the seven synth effects,
KeyPoly's voices, the
arpeggiator, the step sequencer, the external sequencer, the effect switches, scenes, choosing the devices
and profiles, the pedal alone without any controller, writing the Pi's card and the Pi's
installation script (against a stand-in for its system), the texts of the knobs' values and the tool that makes them,
the DSP the effects take and the budget the bridge keeps. They take about
eleven minutes.
What they cannot tell you is how a real pedal reacts, so check changes to the messages on
hardware as well.

## Files

| File | Purpose |
|---|---|
| `bridge.py` | The bridge and its main loop |
| `ui.py`, `ui.html` | The browser interface (local HTTP server, reachable from this computer only; on the Pi from its WLAN) |
| `effect_names.py`, `zoom_effects.json` | The names of the pedal's effects and knobs, read from its effect files, the texts of their values and the DSP each effect takes |
| `tools/value_labels/` | Mac only, for developers: makes the value texts of Zoom's own effects from their code (needs TI's `dis6x`); not on the Pi |
| `zoom_sysex.py` | SysEx builders, allowlist, parsers, approvals, learned ranges |
| `arpeggiator.py` | The arpeggiator's notes and settings (bridge.py runs its clock) |
| `sequencer.py` | The step sequencer's pattern: its steps, how it is written as text, and what plays at each step |
| `scenes.py` | Scenes: stored parameter values and effect switches per preset, and their rules |
| `effect_switch.py` | Which controller switches which effect of the chain: the stored switches and their rules |
| `synth_controls.py` | Which controller of the keyboard sets which knob of each synth effect, or switches the arpeggiator or the sequencer: the stored assignments and their rules |
| `config_schema.py`, `export.py` | The settings file for a bridge without screen: its rules, and writing it and the program onto the Pi's SD card |
| `status_led.py`, `pi/` | The Raspberry Pi: status light, installation script, systemd units, USB stick rule, Bluetooth MIDI |
| `Write Pi Card.command` | macOS: writes the bridge and its settings onto the Pi's SD card (`export.py`) |
| `probe.py` | Setup and exploration: ports, identity, CC measurement, learn, verify, approvals |
| `config.example.yaml` | Template for `config.yaml` |
| `tests/` | Tests against simulated devices (`sim.py` is the simulated pedal and controller; `files/` a real KeySynth build to read) |
| `Start Bridge.command` | macOS launcher: sets up the environment if needed, makes the clickable app and starts the bridge |
| `Start Bridge.bat` | The same for Windows, without the app (untested) |
| `Start Bridge.sh` | The same for Linux (untested) |
| `icon.png`, `icon.ico` | The icon of the app, and the same for a Windows shortcut |
| `tools/value_labels/` | Maintenance: the texts of Zoom's own effects' values, read from their code into `zoom_effects.json` |
| `QUICKSTART.md` | The short way to a running bridge |
| `docs/ms-plus-bridge.png` | The picture at the top (also for GitHub's social preview, 1280 × 640) |
| `NOTES.md` | Development notes (German) |

Created locally and not part of the repository: `config.yaml`, `approvals.json`,
`measurements.json`, `controls.json`, `pedal_effects.json`, `logs/sysex.log`, `MS Plus Bridge.app`, and the same
files once more per profile in `profiles/`.

## Credits

- [zoom-explorer](https://github.com/thammer/zoom-explorer) by thammer, for documenting the
  MS Plus SysEx protocol
- [stomphacks](https://github.com/thammer/stomphacks) by thammer, the toolchain KeySynth is built
  and installed with, and [zoom-zt2](https://github.com/mungewell/zoom-zt2) by mungewell
- [SYNTHESIS SYNx2](https://github.com/Leemuzhko/Zoom-ZDL-FX/blob/main/zdl/sfx/synthesis/README.md)
  by Leemuzhko, a synth effect for the older Zoom MS pedals and the model for KeySynth's set of
  features (the idea only; none of its code is used)
- [mido](https://github.com/mido/mido) and [python-rtmidi](https://github.com/SpotlightKid/python-rtmidi)

## License

MIT, see [LICENSE](../LICENSE).
