# The bridge and the modified MS-60B+ firmware: proposed interface

Status 2026-11-25: **a proposal, not agreed.**

- **Bridge side:** built in `pedal_firmware.py`, tested against the simulated pedal only.
- **Firmware builds:**
  - **v18:** the last build confirmed on the pedal.
  - **v19:** adds one expression target by CC 11.
  - **v20:** six targets and a MIDI channel filter for the synth. Tested locally only, the live test is pending.
- **MIDI identity:** 1.20 in all of them.
- **Scene SysEx:** none of these builds has a command for it.

The bridge sends none of these messages and passes no notes or CC 11 to the pedal until three things are confirmed between both sessions:

- the command byte,
- the answer formats,
- the firmware build.

Until then:

- `pedal_firmware.COMMAND = None`;
- the message kinds are not in `zoom_sysex.KINDS`, so nobody can approve them;
- the bridge offers its own scenes and assignments as before.

The simulated pedal uses `7D` as a placeholder. `7D` must never go to a real pedal.

## Operations

Every message has the form `F0 52 00 <ID> <CMD> <op> … F7`, in 7-bit data. `<CMD>` is open.

| Op | Bridge → pedal | Pedal → bridge |
|---|---|---|
| `00` what the firmware does | `<CMD> 00` | `<CMD> 00 <version> <max values per scene> <features>` |
| `01` read scenes | `<CMD> 01 <address>` | `<CMD> 01 <status> <address> [<scene A> <scene B>]` |
| `02` write scenes | `<CMD> 02 <address> <scene A> <scene B>` | `<CMD> 02 <status> <address>` |
| `03` active scene | `<CMD> 03` (asks for the preset on the pedal) | `<CMD> 03 <address> <scene> <differs>` |
| `04` expression targets: what it keeps | `<CMD> 04` | `<CMD> 04 <max targets> <controller number>` |
| `05` read targets | `<CMD> 05 <address>` | `<CMD> 05 <status> <address> [<targets>]` |
| `06` write targets | `<CMD> 06 <address> <targets>` | `<CMD> 06 <status> <address>` |

Data in `[…]` comes only with status `00`.

- **`<features>`:** each bit says the firmware does one thing.
  - `01` keeps scenes A and B
  - `02` reports the active scene
  - `04` plays the synth from MIDI notes it receives itself
  - `08` keeps expression targets
- **Asking at the start:**
  - The bridge sends op `00` once after connecting. This question is separate from the MIDI identity, which stays 1.20.
  - Without an answer within 1.5 s, the bridge treats the pedal as having Zoom's own firmware.
  - With bit `08` set, it then asks op `04`.
- **The active scene (op `03`):**
  - The pedal sends `03` **on its own** after every short press that switches A/B, and as the answer to `03`.
  - `<scene>`: `00` = no scene, or none that clearly holds; `01` = A; `02` = B.
  - `<differs>`: `01` = something was changed since the scene was chosen (the star on the pedal's screen).
  - If the address is the preset on the pedal, the bridge **only shows** the report. It sends nothing for it: no scene read, no effect switches.
  - A report for another address is ignored.
  - **Proposal for asking at the start:** the bridge asks with `03` after connecting and after every preset change, as soon as the pedal has reported the preset. A firmware that sends `03` by itself after a preset change would make that question unnecessary. Please decide.
- **Write (ops `02`, `06`):** stores into the addressed preset as it is stored, not only into the edit buffer. After status `00`, the bridge reads the same address again. It reports success only if everything comes back the same as written:
  - scenes: switches, values and effect ids
  - targets: their order, effect ids, heel and toe
- **What the bridge keeps if a write fails:** The bridge keeps its unsaved edits whatever goes wrong:
  - a timeout,
  - an answer for another address (ignored, the operation then times out),
  - busy,
  - an answer it cannot read,
  - a read-back that differs.

## Fields

| Field | Form |
|---|---|
| **address** | `<bank LSB> <bank MSB> <program LSB> <program MSB>`, as in `46 00 00 …` (query preset). Bank 0–9, program 0–9; the pedal shows bank × 10 + program + 1. Every answer names its address again. |
| **slot** | 0–5 = FX 1–6 in chain order, as in `64 20 00 <slot> …`. |
| **effect id** | 32 bits in 5 bytes of 7 bits, lowest first, as the value of parameter 1 in `64 20 00 <slot> 01 …`. |
| **knob (param)** | the parameter number of `64 20 00 <slot> <param>`; 2 is the first knob. 0 (on/off) and 1 (effect type) are not knobs. |
| **value, heel, toe** | 14 bits, `<LSB> <MSB>`, from 0 up to the knob's highest value. A heel above the toe means the knob turns the other way. |
| **scene** | `<mask> <states> <count>`, then `count` × `<slot> <effect id ×5> <param> <value LSB MSB>`. `mask` bit n: FX n+1 is part of the scene; `states` bit n: on. An FX that is not in the mask stays as it is. |
| **targets** | `<count>`, then `count` × `<slot> <effect id ×5> <param> <heel LSB MSB> <toe LSB MSB>` (11 bytes each). |

- **Native scenes:** the native scenes store only the switches. The answer to `00` therefore says `max values per scene = 00`, every scene has `count = 00`, and the bridge shows no knob values in its editor for them. The bridge's own scenes, with knob values and controllers, stay in the bridge.
- **Effect id:** the firmware applies a value or target only while the effect in that slot has that effect id. Otherwise it leaves it out, and the bridge marks it as "away".
- **Key knob:** the Key knob of a synth effect is never part of a scene or a target. The bridge refuses it.

## Status codes

| Code | Meaning |
|---|---|
| `00` | done |
| `01` | no such preset |
| `02` | refused: not valid (slot, param, more values or targets than it keeps) |
| `03` | busy (a preset being saved or edited on the pedal) |
| `04` | could not store |

## Notes and CC 11: one way only

**Telling a synth in the chain from the firmware playing notes**

The bridge reports two separate things:

- whether a synth effect is in the chain (`synth.playable`), and
- whether the firmware plays notes itself (`firmware.plays_notes`, feature bit `04`).

**`config.yaml` settings**

- **`synth.via: pedal`** (default `bridge`): the keyboard's notes go to the pedal unchanged.
  - Passed on: note on/off, pitch wheel, sustain (CC 64), all notes off (CC 120/123).
  - The bridge then plays no Key knob, so no arpeggiator, no sequencer and no voices.
  - This happens only when the firmware has bit `04` set and `pass_notes` is approved. Otherwise the bridge plays the synth as before and passes no note.
- **`expression.via: pedal`** (default `bridge`): the expression pedal goes to the pedal as the controller number from op `04` (CC 11). The bridge then sets no parameter for it.
  - This happens only with bit `08`, a controller number, and `pass_expression` approved. Otherwise the bridge sets the parameters and passes no CC 11.

**CC 11 assignments the bridge has today**

The bridge has its own assignments per preset:

- up to twelve knobs, four per controller,
- each with effect id, slot, param, min, max, invert and a linear, log or exp curve.

"Take the bridge's" turns those of the expression pedal into pedal targets:

- heel = min and toe = max, swapped when inverted;
- only linear ones;
- not those of other controllers;
- never a Key knob;
- at most as many as the pedal keeps.

The bridge says what it left out. Nothing is written until "Write to pedal".

## What the simulator tests (`tests/test_pedal_firmware.py`, 29 tests)

| Area | Cases |
|---|---|
| Firmware of Zoom's own | no answer: asked once, the bridge's scenes offered |
| Not agreed / not approved | not agreed: nothing sent; not approved: nothing asked |
| Native scenes | switches only (values refused); read, edit, write and read back (with values too); empty scenes; all six FX; what a scene cannot hold (Key knob, above the highest, more than it keeps, empty slot) |
| Failed writes, edits kept | another preset's address; busy; no answer; an answer that holds no scenes; a read-back that differs |
| Preset changes | while reading (dropped); before writing (refused); while writing (read back by address) |
| Active scene | A, B, B with a star; another preset's report ignored; nothing sent for it; asked once at the start and once after a preset change |
| Expression targets | read, add, remove, write, read back; taken from the bridge's assignments (log curve, another controller, more than it keeps left out); inverted = heel above toe |
| Ways | notes via the pedal: passed on, no Key knob; by default the bridge plays and passes no note; without bit `04`, nothing passed; expression via the pedal: CC 11 passed, no parameter; by default the bridge sets parameters and passes no CC 11 |
| Messages | bytes and back; the allowlist; the pedal's answers, garbled ones refused |

## Decisions needed from the firmware session

1. **`<CMD>`:** which byte. Does the pedal stay silent to it and to unknown ops? The bridge relies on its 1.5 s timeout.
2. **Features:** does the bit layout of op `00` fit, and in which build does each bit come?
3. **Writes:**
   - Are they stored at once, or only with the preset's next save?
   - What happens to unsaved changes of that preset on the pedal?
   - Are scenes A and B written together?
4. **Active scene:**
   - Does the firmware send `03` by itself after a short press?
   - After a preset change too?
   - When exactly is the star set?
5. **Expression targets:**
   - Does the bridge send only the used targets (`count`), or always six entries with an empty marker (for example slot `7F`)?
   - Max targets per build (v19: 1, v20: 6), and the controller number (11).
   - Do they act only while the effect id matches?
6. **Notes:**
   - Which channel does v20's filter take?
   - Should the bridge pass notes on the keyboard's own channel, or one the firmware says?
   - Are pitch wheel and sustain taken?
7. **Release for real pedals:** only after both sides have checked this page against the build. Then:
   - set `pedal_firmware.COMMAND`;
   - move the kinds into `zoom_sysex.KINDS`, so that they can be approved: `query_pedal_firmware`, `read_pedal_scenes`, `write_pedal_scenes`, `read_pedal_expression`, `write_pedal_expression`, `pass_notes`, `pass_expression`.
