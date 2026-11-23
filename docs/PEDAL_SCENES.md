# Pedal scenes: the interface between the bridge and the firmware

Status 2026-11-24: **a proposal, not agreed.** The bridge side is built (`pedal_scenes.py`) and
tested against the simulated pedal only. The command byte and the release for real pedals stay
open until the firmware session and the bridge agree on this page. Until then the bridge sends
none of these messages (`pedal_scenes.COMMAND = None`) and offers its own scenes.

## What the firmware keeps

Every preset holds **scene A and scene B**. The pedal's menu "Scenes" takes the slot selection of
FX GROUP; a short press switches between A and B. A scene holds:

- **switches**: the on/off state of chosen effects 1–6 (an effect not chosen is left as it is),
- **values**: chosen knob values, each with the effect it belongs to.

The live Key knob of a synth effect is never part of a scene; the bridge never writes one.

## Operations

All messages are `F0 52 00 <ID> <CMD> <op> … F7`, 7-bit data. `<CMD>` is **open**; the simulated
pedal uses `7D`, which must not go to a real pedal.

| Op | Bridge → pedal | Pedal → bridge |
|---|---|---|
| `00` capability | `<CMD> 00` | `<CMD> 00 <version> <max values per scene>` |
| `01` read | `<CMD> 01 <address>` | `<CMD> 01 <status> <address> [<scene A> <scene B>]` (scenes only with status 00) |
| `02` write | `<CMD> 02 <address> <scene A> <scene B>` | `<CMD> 02 <status> <address>` |

- **Capability:** asked once after connecting, separate from the MIDI identity, which keeps saying
  1.20. Without an answer within 1.5 s the bridge treats the pedal as one without scenes.
- **Write** stores A and B together into the addressed preset as stored, not into the edit buffer
  only. Whether that needs a preset save, and what happens to unsaved edits of that preset on the
  pedal, is **open**.
- **Read-back check:** after status 00 to a write, the bridge reads the same address. It reports
  the write as done only if both scenes come back equal to what it wrote: same switches, same
  values, same effect ids.

## Fields

| Field | Form |
|---|---|
| **address** | `<bank LSB> <bank MSB> <program LSB> <program MSB>`, as in `46 00 00 …` (query preset). MS-60B+: bank 0–9, program 0–9; the pedal shows bank × 10 + program + 1. Every answer echoes the address. The bridge ignores an answer for another address. |
| **slot** | 0–5 = effect 1–6 in chain order, as in the "set parameter" message `64 20 00 <slot> …`. |
| **effect id** | 32 bits in 5 bytes of 7 bits, lowest first, as the value of parameter 1 in `64 20 00 <slot> 01 …`. |
| **knob (param)** | the parameter number of `64 20 00 <slot> <param>`: 2 is the first knob. 0 (on/off) and 1 (effect type) are not knobs. |
| **value** | 14 bits, `<LSB> <MSB>`, up to the knob's highest value. |
| **scene** | `<mask> <states> <count>`, then `count` × `<slot> <effect id ×5> <param> <value LSB> <value MSB>` (9 bytes each), in order of (slot, param). `mask`: bit n = effect n+1 is switched by the scene; `states`: bit n = on. An empty scene is `00 00 00`. |

The firmware applies a value only while the effect in that slot has that effect id. Another
effect in the place means the value is left out, and the bridge shows it as "away".

## Status codes

| Code | Meaning |
|---|---|
| `00` | done |
| `01` | no such preset |
| `02` | refused: the scenes are not valid (bad slot, param, or more values than the pedal keeps) |
| `03` | busy (a preset being saved or edited on the pedal) |
| `04` | could not store |

## Open, to agree together

1. `<CMD>`, and that the pedal stays silent to unknown commands (the bridge relies on its timeout).
2. Maximum values per scene (the capability answer carries it; the bridge has used 8).
3. Write: stored at once, or only with the preset's next save? What happens to unsaved changes of
   that preset?
4. Does the pedal report a switch between A and B over MIDI? The bridge could then show the
   active scene.
5. **Release for real pedals:** only after both sides have checked this page against the firmware
   build. Then `pedal_scenes.COMMAND` is set, and the three kinds (`query_pedal_scenes`,
   `read_pedal_scenes`, `write_pedal_scenes`) go into `zoom_sysex.KINDS`, so that they can be
   approved.
