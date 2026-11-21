# Credits and licences

Zoom MS Installer stands on the work of others. This file says whose, and reproduces the
licence notices that have to travel with it. Keep it with the program when you pass the
program on.

## zoom-zt2 – Simon Wood (mungewell)

<https://github.com/mungewell/zoom-zt2> – MIT licence, Copyright (c) 2019 mungewell.

`zoomzt2.py` in this folder is his file, unchanged. It supplies the format of the pedal's
effect list (`FLST_SEQ.ZT2`) and the SysEx sequences of the file transfer; this program
sends exactly those sequences. His licence is the file `LICENSE` in this folder.

## stomphacks and zoom-explorer – Thomas Hammer (Waveformer)

<https://github.com/thammer/stomphacks>, <https://github.com/thammer/zoom-explorer> –
MIT licence, Copyright (c) 2026 Thomas Hammer (Waveformer).

The way this program keeps a transfer from damaging a pedal follows his work: the
`tools-pedal` scripts of stomphacks (`file_session.py`, `pedal_diy.py`, `pedal_common.py`),
`tools/flst_check.py` and `SAFETY.md`. From there come

- a timeout on every reply from the pedal, and the one tolerated lost reply,
- the clean close of the file session after any failure, and the check that follows it,
- reading every uploaded file back and comparing it byte for byte,
- the guarded rewrite of the effect list (checks before, read-back and retries after),
- the checks an effect file and its icon must pass before they are uploaded,
- switching AUTO SAVE off and requiring the pedal's confirmation,
- the rule "do not power-cycle after an unverified transfer" and the rescue that goes with it.

The code in `zoom_ms_installer.py` was written anew for this program, but it is modelled on
his closely enough that his copyright notice and licence are reproduced here in full:

```
MIT License

Copyright (c) 2026 Thomas Hammer (Waveformer)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

--------------------------------------------------------------------------
Upstream notice

This effect is a port of an Airwindows algorithm:

  Airwindows plugins
  Copyright (c) Chris Johnson (Airwindows), MIT License
  https://github.com/airwindows/airwindows

The port and the effect binary were built entirely from MIT-licensed sources
and original code by Thomas Hammer (Waveformer). This is an independent,
unofficial DIY effect: not affiliated with, endorsed by, or supported by
Zoom Corporation.
```

The SysEx commands outside the file transfer (identity request, parameter edit mode, the
AUTO SAVE setting) are documented in his zoom-explorer project.

## The effects

Effects you install are their authors' work under their authors' licences – most of the DIY
effects for the MS Plus pedals are by Thomas Hammer (Waveformer), again under the MIT
licence. This program does not contain any effect. When it makes an adapted copy of an
effect, it changes a few header bytes and leaves the effect's code and texts as they are.
When the effect is used from its own folder (the text menu, or *Add files…* / *Add folder…*
in the Mac window), the licence, notice and readme files found next to it are copied next to
the adapted copy, so the notice stays with the file; effects dropped onto the window or added
on Windows arrive without them. If you pass an adapted copy on, pass its licence files on
with it.

Zoom's own (stock) effects are Zoom's. This program neither contains nor distributes them.

## This program

The installer, its window and its self-test (`zoom_ms_installer.py`, `zoom_ms_gui.py`,
`zoom_ms_gui.html`, `zoom_ms_selftest.py`, `packaging/`) were written in October 2026 with an
AI assistant (Claude). New in them are the per-pedal category adaptation, installing several
effects in one transfer, the backup including patches, and the window. The author of
zoom-zt2 does not accept AI-written code (`ai.md`), so none of this is meant for submission
there.

Not affiliated with or supported by ZOOM Corporation. Use at your own risk.
