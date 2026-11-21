#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zoom MS Installer - safely load new effects onto the Zoom MS Plus series.

What it does:
  1. identifies the connected pedal (MS-50G+, MS-70CDR+, MS-60B+, ...),
  2. offers a complete backup (all files, the effect list, the patches),
  3. checks the effect file (.ZD2) and its icon (.ZIC) before uploading,
  4. uploads them, reads them back and compares byte for byte,
  5. adds the effect to the effect list (FLST_SEQ.ZT2) and verifies that too.

Before installing you choose the category the effect goes into. The file is
then adapted to that category on THIS pedal, because the models number their
categories differently (see GUIDE.md, "MS-60B+ and Unknown").

Run without arguments for the menu. Otherwise:
    zoom_ms_installer.py info
    zoom_ms_installer.py backup
    zoom_ms_installer.py check   EFFECT.ZD2 [--model ms-60bp]
    zoom_ms_installer.py install EFFECT.ZD2 [more.ZD2 | FOLDER ...]
    zoom_ms_installer.py remove  [EFFECT.ZD2 more.ZD2 ...]
    zoom_ms_installer.py rescue  [--flst BACKUP/files/FLST_SEQ.ZT2 --write]

The transfer protocol is exactly the one zoomzt2.py uses (same SysEx
sequences), but every reply has a timeout and every write is verified.
Not supported by ZOOM - use at your own risk.

Credits (full notices in NOTICE.md):
  * zoom-zt2 by Simon Wood (mungewell), MIT - the effect-list format and the
    SysEx sequences (zoomzt2.py, used unchanged).
  * stomphacks by Thomas Hammer (Waveformer), MIT, Copyright (c) 2026 Thomas
    Hammer (Waveformer) - the transfer safety this program follows: timeouts,
    clean abort, read-back verification, the guarded effect-list rewrite, the
    file checks before upload, the AUTO SAVE guard, the rescue. The classes
    and functions below that implement these are modelled on his
    tools-pedal/file_session.py, pedal_diy.py, pedal_common.py and
    tools/flst_check.py.
"""

import argparse
import binascii
import collections
import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import struct
import sys
import time
import zlib

# Where the files shipped with the program are (stock lists, blank icon): next
# to this file, or inside the bundle when packed into an app.
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
BACKUP_ROOT = os.path.join(HERE, "Backups")
ADAPT_ROOT = os.path.join(HERE, "Adapted")
LOG_ROOT = os.path.join(HERE, "Logs")
STOCK_ROOT = os.path.join(HERE, "zoom_fx_ms-plus")


def set_data_root(path):
    """Where Backups/, Adapted/ and Logs/ live. Default: next to the program.
    The graphical app uses a folder in the user's home instead."""
    global BACKUP_ROOT, ADAPT_ROOT, LOG_ROOT
    BACKUP_ROOT = os.path.join(path, "Backups")
    ADAPT_ROOT = os.path.join(path, "Adapted")
    LOG_ROOT = os.path.join(path, "Logs")


ZOOM_HDR = (0x52, 0x00, 0x6E)
PORT_NAMES = ("ZOOM MS Plus Series", "ZOOM G")

# model number from the MIDI identity -> (name, folder in zoom_fx_ms-plus)
MODELS = {
    0x23: ("MS-50G+", "ms-50gp"),
    0x26: ("MS-70CDR+", "ms-70cdrp"),
    0x27: ("MS-60B+", "ms-60bp"),
}
MODEL_MS60B = 0x27

FLST_NAME = "FLST_SEQ.ZT2"
FLST_GRAMMAR_SIZE = 8502     # what the ZT2 grammar in zoomzt2.py models
BLOCK = 512
RECV_TIMEOUT = 5.0
ABORT_TIMEOUT = 2.0
FLST_WRITE_ATTEMPTS = 3
DISK_MARGIN = 4096
CLUSTER = 4090               # allocation unit of the pedal's storage
BACKUP_SECONDS_PER_FILE = 0.95  # measured: 315 files in 290 s (MS-60B+)
WAIT_SCALE = 1.0             # only the self-test shortens the waits
KEEP_LOGS = 20

# F0 52 00 6E 64 20 00 64 0F <00=off> ... F7 - the value is a fixed 0 (OFF).
AUTOSAVE_OFF = [0x52, 0x00, 0x6E, 0x64, 0x20, 0x00, 0x64, 0x0F,
                0x00, 0x00, 0x00, 0x00, 0x00]


class TransferError(Exception):
    """Protocol failure during a transfer."""


class TransferTimeout(TransferError):
    """No reply from the pedal within the timeout."""


class BaselineChanged(TransferError):
    def __init__(self, msg, current):
        super().__init__(msg)
        self.current = current


class Refused(Exception):
    """Refused before anything was written to the pedal."""


class SafeAbort(Exception):
    """Failed, but the pedal was verified unchanged."""


class Unresolved(Exception):
    """Failed and the pedal's state could NOT be verified."""


# ---------------------------------------------------------------- output

_logfile = None


def log(line):
    if _logfile:
        try:
            _logfile.write("%s %s\n" % (
                datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3], line))
            _logfile.flush()
        except Exception:     # a log line must never stop a transfer
            pass


class ConsoleUI(object):
    """How the program talks to its user in a terminal. The graphical front
    end (zoom_ms_gui.py) puts its own object with these methods into `ui`."""

    _shown = 0                    # length of an unfinished progress line

    # A closed Terminal window makes every further write fail (EIO). During
    # a transfer that must not stop the program between two steps: what can
    # no longer be shown is still in the log.

    def say(self, text):
        try:
            if self._shown:       # a message takes the place of that line
                sys.stdout.write("\r" + " " * self._shown + "\r")
                self._shown = 0
            try:
                print(text)
            except UnicodeEncodeError:  # a console that cannot show it all
                enc = getattr(sys.stdout, "encoding", None) or "ascii"
                print(text.encode(enc, "replace").decode(enc))
        except (OSError, ValueError):
            pass

    def progress(self, text, done=None, total=None):
        """Progress line (terminal only); None ends the line."""
        try:
            if not sys.stdout or not sys.stdout.isatty():
                return
            if text is not None:
                sys.stdout.write("\r" + text)
            elif self._shown:
                sys.stdout.write("\n")
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
        self._shown = 0 if text is None else len(text)

    def ask_yes(self, question, details=None):
        while True:
            try:
                answer = input(question + " [y/n] ").strip().lower()
            except EOFError:
                return False
            if answer in ("y", "yes", "j", "ja"):
                return True
            if answer in ("n", "no", "nein"):
                return False
            print("  Please answer y or n.")

    def choose(self, title, options, prompt):
        """Pick one of `options`. -> index or None (cancelled)."""
        say(title)
        for i, name in enumerate(options, 1):
            say("  %d) %s" % (i, name))
        try:
            choice = input(prompt + " (number, Enter = cancel) ").strip()
        except EOFError:
            choice = ""
        if choice.isdecimal() and 1 <= int(choice) <= len(options):
            return int(choice) - 1
        return None

    def choose_many(self, title, options, prompt):
        """Pick any number of `options`. -> sorted indexes or None
        (cancelled)."""
        say(title)
        for i, name in enumerate(options, 1):
            say("  %d) %s" % (i, name))
        while True:
            try:
                text = input(prompt + " (numbers like 1 3 5 or 2-4, a = all, "
                             "Enter = cancel) ").strip()
            except EOFError:
                text = ""
            log("? %s -> %s" % (prompt, text))
            if not text:
                return None
            picked = parse_selection(text, len(options))
            if picked:
                return picked
            say("  Please enter numbers from 1 to %d." % len(options))

    def choose_category(self, zd2, table, own, current, used_ids, show_list):
        """Ask for the category of one effect. Enter keeps the category the
        effect is installed in now (`current`) or else its own, '?' shows
        the list again, 's' leaves the effect out. -> category or None."""
        default = current or own

        def taken(cat):
            return ((cat["group"] << 24) | (zd2.id & 0xFFFFFF)) in used_ids

        say("'%s' was built for the category '%s' (group %d)."
            % (zd2.name, zd2.groupname, zd2.group))
        if current is not None:
            say("It is already installed on this pedal, in '%s'."
                % current["label"])
        elif own is None:
            say("This pedal has no category of that name.")
        if show_list:
            say("Categories on this pedal:")
            print_categories(table, own, used_ids, zd2, current)
        while True:
            prompt = "Category number for '%s' [" % zd2.name
            if default:
                prompt += "Enter = %d %s, " % (default["group"],
                                               default["label"])
            prompt += "? = list, s = skip]: "
            try:
                choice = input(prompt).strip().lower()
            except EOFError:
                choice = "" if default and not taken(default) else "s"
            log("? %s -> %s" % (prompt, choice))
            if choice == "s":
                return None
            if choice == "?":
                print_categories(table, own, used_ids, zd2, current)
                continue
            if not choice and default:
                cat = default
            elif choice.isdecimal() and int(choice) in table:
                cat = table[int(choice)]
            else:
                say("  Please enter one of the numbers from the list.")
                continue
            if taken(cat):
                say("  The effect's id is already taken in %s - please "
                    "choose another category." % cat["label"])
                continue
            return cat


ui = ConsoleUI()


def say(text=""):
    ui.say(text)
    log("| " + text)


LOG_NAME = re.compile(r"^\d{8}-\d{6}-[a-z]+\.log$")


def open_log(tag):
    global _logfile
    try:
        os.makedirs(LOG_ROOT, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        _logfile = open(os.path.join(LOG_ROOT, "%s-%s.log" % (stamp, tag)),
                        "w", encoding="utf-8", errors="replace")
    except OSError:
        _logfile = None
        return
    # Only this program's own logs are rotated; one that cannot be deleted
    # (on Windows: still open in another instance) is simply kept.
    try:
        old = sorted(f for f in os.listdir(LOG_ROOT) if LOG_NAME.match(f))
    except OSError:
        old = []
    for name in old[:-KEEP_LOGS]:
        try:
            os.remove(os.path.join(LOG_ROOT, name))
        except OSError:
            pass


def progress(text, done=None, total=None):
    ui.progress(text, done, total)


def ask_yes(question, assume=False, details=None):
    """`details`: lines the question refers to (already said; a graphical
    front end shows them inside its dialog)."""
    if assume:
        say(question + " -> yes (--yes)")
        return True
    answer = bool(ui.ask_yes(question, details))
    log("? %s -> %s" % (question, "y" if answer else "n"))
    return answer


def parse_selection(text, count):
    """'1 3 5', '1,3', '2-4' or 'all' -> sorted indexes (from 0) into a list
    of `count` items, or None if the text is not such a selection."""
    text = text.strip().lower()
    if text in ("a", "all", "*"):
        return list(range(count))
    picked = set()
    for part in text.replace(",", " ").split():
        first, dash, last = part.partition("-")
        if not first.isdecimal() or (dash and not last.isdecimal()):
            return None
        low, high = int(first), int(last if dash else first)
        if not 1 <= low <= high <= count:
            return None
        picked.update(range(low - 1, high))
    return sorted(picked) or None


def banner(reason):
    lines = [
        "",
        "!" * 72,
        "!!  TRANSFER FAILED - THE PEDAL'S STATE COULD NOT BE VERIFIED",
        "!!",
        "!!  *** DO NOT SWITCH THE PEDAL OFF. DO NOT UNPLUG POWER OR USB. ***",
        "!!",
        "!!  Reason: %s" % reason,
        "!!",
        "!!  The pedal may be stuck inside an open file session. While it",
        "!!  stays POWERED this may be recoverable. A restart in this state",
        "!!  can leave the pedal permanently unable to boot.",
        "!!",
        "!!  Next steps:",
        "!!   1. Leave the pedal powered and connected.",
        "!!   2. Choose 'Rescue' in the menu (or: zoom_ms_installer.py rescue).",
        "!!   3. Restart only after the effect list is reported intact.",
        "!" * 72,
        "",
    ]
    for line in lines:
        say(line)


class critical(object):
    """Ctrl-C, closing the Terminal window and `kill` are ignored during
    writes: an interruption in the middle of a transfer is exactly what can
    damage a pedal."""

    SIGNALS = tuple(getattr(signal, name)
                    for name in ("SIGINT", "SIGHUP", "SIGTERM", "SIGBREAK")
                    if hasattr(signal, name))

    def __init__(self, ident=None):
        self.ident = ident          # the pedal about to be written to

    def __enter__(self):
        # the window saves its alarm now, before the first write
        hook = getattr(ui, "writing", None)
        if hook:
            hook(self.ident)

        def handler(signum, frame):
            try:
                print("\n(Please wait - the transfer must not be "
                      "interrupted now.)")
            except Exception:
                pass                # the terminal may already be gone
        self.old = {}
        for sig in self.SIGNALS:
            try:
                self.old[sig] = signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
        console_guard(True)
        return self

    def __exit__(self, *exc):
        console_guard(False)
        for sig, old in self.old.items():
            signal.signal(sig, old)
        return False


_console = {"handler": None, "closing": False, "busy": 0}


class write_step(object):
    """One write to the pedal, from its first command to the close. On
    Windows a console window that is being closed is held off until the step
    in progress has ended, and no new step starts after that: the program
    then ends between two steps, never in the middle of one."""

    def __enter__(self):
        # Once the window is being closed nothing new starts; a step inside
        # one that is still running (the retry of a list write) may.
        while _console["closing"] and not _console["busy"]:
            time.sleep(1.0)              # the program is being ended
        _console["busy"] += 1
        return self

    def __exit__(self, *exc):
        _console["busy"] -= 1
        return False


def console_guard(on):
    """Windows: closing the console window ends the program a few seconds
    later whatever it does, and Ctrl-C/Ctrl-Break bypass the signal
    handlers. During a transfer, Ctrl-C/Ctrl-Break are ignored and a closing
    window waits for the write step in progress (Windows allows about five
    seconds). Not tried on Windows yet."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        if on and _console["handler"] is None:
            def handler(event):
                if event in (2, 5, 6):   # close, logoff, shutdown
                    _console["closing"] = True
                    end = time.monotonic() + 4.5
                    while _console["busy"] and time.monotonic() < end:
                        time.sleep(0.05)
                return True              # Ctrl-C, Ctrl-Break: ignored
            routine = ctypes.WINFUNCTYPE(wintypes.BOOL,
                                         wintypes.DWORD)(handler)
            if kernel32.SetConsoleCtrlHandler(routine, True):
                _console["handler"] = routine
        elif not on and _console["handler"] is not None:
            kernel32.SetConsoleCtrlHandler(_console["handler"], False)
            _console["handler"] = None
    except Exception:
        pass                             # a guard, never a blocker


_awake = {"depth": 0, "caffeinate": None, "activity": None}


def keep_awake(on):
    """The computer must not go to sleep while the pedal is being worked on
    (backups take minutes). Calls nest; on Windows on and off must come from
    the same thread."""
    _awake["depth"] = max(0, _awake["depth"] + (1 if on else -1))
    if _awake["depth"] != (1 if on else 0):
        return
    try:
        if os.name == "nt":
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
        elif sys.platform == "darwin":
            import subprocess
            if on and _awake["caffeinate"] is None:
                # its own session: a closed Terminal window does not end it,
                # and -w ends it together with this program
                _awake["caffeinate"] = subprocess.Popen(
                    ["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                    start_new_session=True)
            elif not on and _awake["caffeinate"] is not None:
                _awake["caffeinate"].terminate()
                _awake["caffeinate"] = None
            # no App Nap while the pedal is worked on (a hidden window would
            # slow the transfer down); needs pyobjc, which the app has
            try:
                from Foundation import NSProcessInfo
            except ImportError:
                return
            info = NSProcessInfo.processInfo()
            if on and _awake["activity"] is None:
                _awake["activity"] = info.beginActivityWithOptions_reason_(
                    0x00FFFFFF | 0xFF00000000,   # user-initiated, latency-critical
                    "transfer to the pedal")
            elif not on and _awake["activity"] is not None:
                info.endActivity_(_awake["activity"])
                _awake["activity"] = None
    except Exception:
        pass                             # a comfort feature, never a blocker


def exit_cleanly_on_hangup():
    """Outside of writes, a closed Terminal window or `kill` ends the program
    through the normal cleanup (file session closed, PC mode left)."""
    def handler(signum, frame):
        raise KeyboardInterrupt()
    for name in ("SIGHUP", "SIGTERM"):
        if not hasattr(signal, name):
            continue                # no SIGHUP on Windows
        try:
            signal.signal(getattr(signal, name), handler)
        except (ValueError, OSError):
            pass


# ---------------------------------------------------------------- codecs

def pack7(data):
    """8 bit -> 7 bit, like zoomzt2.pack()."""
    packet = bytearray()
    encode = bytearray(b"\x00")
    for byte in data:
        encode[0] = encode[0] + ((byte & 0x80) >> len(encode))
        encode.append(byte & 0x7F)
        if len(encode) > 7:
            packet += encode
            encode = bytearray(b"\x00")
    if len(encode) > 1:
        packet += encode
    return packet


def unpack7(packet):
    """7 bit -> 8 bit, like zoomzt2.unpack()."""
    data = bytearray()
    loop = -1
    hibits = 0
    for byte in packet:
        if loop != -1:
            data.append(byte | 0x80 if hibits & (1 << loop) else byte)
            loop -= 1
        else:
            hibits = byte
            loop = 6
    return data


def listing_entry(reply):
    """One reply of the file listing -> (name, size). As the MS-60B+ sends
    it: 14 bytes head, the name in 13 bytes, 2 zero bytes, the size in five
    7-bit bytes (logged: ...574944454348522e5a4943 00 0000 2814... is
    WIDECHR.ZIC with 2600 bytes). A shorter reply gives size 0."""
    name = bytes(reply[14:27]).split(b"\x00", 1)[0].decode("ascii",
                                                            "replace")
    field = reply[29:34]
    size = sum((b & 0x7F) << (7 * i) for i, b in enumerate(field)) \
        if len(field) == 5 else 0
    return name, size


def allocated(size):
    """Space a file takes on the pedal: it allocates in units of 4090 bytes
    (every change of its free space in the logs is a multiple of that)."""
    return -(-size // CLUSTER) * CLUSTER


def crc_tail(block):
    crc = binascii.crc32(bytes(block)) ^ 0xFFFFFFFF
    return [crc & 0x7F, (crc >> 7) & 0x7F, (crc >> 14) & 0x7F,
            (crc >> 21) & 0x7F, (crc >> 28) & 0x0F]


def tail_int(tail5):
    return (tail5[0] | (tail5[1] << 7) | (tail5[2] << 14)
            | (tail5[3] << 21) | ((tail5[4] & 0x0F) << 28))


def sha256(data):
    return hashlib.sha256(bytes(data)).hexdigest()


# ------------------------------------------------------------- transport

class MidoTransport(object):
    """A pair of open mido ports. recv() polls, so it never blocks forever."""

    def __init__(self, inport, outport):
        self.inport = inport
        self.outport = outport

    def send(self, payload):
        import mido
        self.outport.send(mido.Message("sysex", data=list(payload)))

    def recv(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            msg = self.inport.poll()
            if msg is not None:
                if msg.type == "sysex":
                    return bytes(msg.data)
                continue            # clock, CC ...: look behind it
            # only an empty input ends the wait: a sleep the system stretched
            # must not turn a reply that has arrived into a timeout
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.002)

    def flush(self):
        """Discard whatever is still waiting in the input. -> count."""
        n = 0
        while self.inport.poll() is not None:
            n += 1
        return n

    def close(self):
        for port in (self.inport, self.outport):
            try:
                port.close()
            except Exception:
                pass


def find_ports():
    """(input, output) port names of every Zoom pedal found. On macOS a
    pedal's two ports carry the same name; Windows appends a running number
    that differs between input and output, so there they are paired in
    order."""
    import mido
    # A port can come without a name, e.g. when the pedal was switched off or
    # unplugged while the program was open (2026-10-18).
    ins = [n for n in mido.get_input_names()
           if isinstance(n, str) and n.startswith(PORT_NAMES)]
    outs = [n for n in mido.get_output_names()
            if isinstance(n, str) and n.startswith(PORT_NAMES)]
    pairs = [(name, name) for name in ins if name in outs]
    if not pairs:
        pairs = list(zip(ins, outs))
    return pairs


VC_REDIST = "https://aka.ms/vs/17/release/vc_redist.x64.exe"


def open_transport(index=None, interactive=True):
    try:
        import mido
        pairs = find_ports()
    # not only ImportError: a damaged mido install raises e.g. InvalidVersion
    # (2026-11-19). No port is open yet, so the pedal is untouched.
    except Exception as e:
        hint = ""
        if os.name == "nt" and isinstance(e, ImportError):
            # the Windows build of the MIDI library needs MSVCP140.dll
            hint = (" On Windows this usually means that the Microsoft "
                    "Visual C++ Redistributable is missing. Install it from "
                    "%s and start the program again." % VC_REDIST)
        raise Refused("The MIDI library could not be loaded (%s: %s).%s"
                      % (type(e).__name__, e, hint))
    if not pairs:
        raise Refused("No Zoom pedal found. Connect the pedal by USB, switch "
                      "it on and close other programs that use it (browser "
                      "tab with WebMIDI, nam2zoom, Zoom Handy Guitar Lab). "
                      "If the pedal was switched off or unplugged while this "
                      "program was open, quit the program and start it "
                      "again.")
    if index is None:
        index = 0
        if len(pairs) > 1:
            if not interactive:
                raise Refused("Several pedals found - choose one with "
                              "--pedal N: %s" % [p[0] for p in pairs])
            index = ui.choose("Several pedals found:",
                              [p[0] for p in pairs], "Which pedal?")
            if index is None:
                raise Refused("no pedal chosen")
    if not 0 <= index < len(pairs):
        raise Refused("There is no pedal number %d." % (index + 1))
    inname, outname = pairs[index]
    busy = ("The pedal's MIDI port could not be opened (%s). Nothing was "
            "sent. Close other programs that use the pedal (Zoom Handy "
            "Guitar Lab, nam2zoom, a browser tab with a pedal editor) and "
            "try again.")
    try:
        inport = mido.open_input(inname)
    except Exception as e:
        raise Refused(busy % e)
    try:
        outport = mido.open_output(outname)
    except Exception as e:
        inport.close()
        raise Refused(busy % e)
    return MidoTransport(inport, outport)


# ----------------------------------------------------------------- pedal

class Pedal(object):
    """Timeout-guarded implementation of the file API (SysEx 60 xx), with the
    same sequences as zoomzt2.py. Every reply has a timeout, and a CRC error
    while reading is an error (zoomzt2 silently drops the block)."""

    def __init__(self, transport, recv_timeout=RECV_TIMEOUT):
        self.t = transport
        self.recv_timeout = recv_timeout
        self.in_pcmode = False

    # ---- primitives

    def _send_raw(self, payload):
        # The protocol is strictly request/reply. Anything still waiting in
        # the input before a new request is a late reply to an earlier one -
        # it would otherwise be read as the reply to THIS request.
        stale = self.t.flush()
        if stale:
            log("   (%d late reply/replies discarded)" % stale)
        text = bytes(payload).hex()
        log("tx " + (text if len(text) <= 96 else text[:96] + "..."))
        self.t.send(payload)

    def _send(self, body):
        self._send_raw(list(ZOOM_HDR) + list(body))

    def _recv(self, what, timeout=None):
        payload = self.t.recv(self.recv_timeout if timeout is None else timeout)
        if payload is None:
            log("TIMEOUT: " + what)
            raise TransferTimeout("no reply to '%s'" % what)
        text = bytes(payload).hex()
        log("rx " + (text if len(text) <= 96 else text[:96] + "..."))
        return payload

    def _recv60(self, what):
        deadline = time.monotonic() + self.recv_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log("TIMEOUT: " + what)
                raise TransferTimeout("no reply to '%s'" % what)
            payload = self._recv(what, remaining)
            if (len(payload) >= 5 and tuple(payload[:3]) == ZOOM_HDR
                    and payload[3] == 0x60):
                return payload
            log("   (unrelated reply skipped)")

    def _ack(self, what):
        """Wait for an acknowledgement. ONE lost reply is tolerated (a pure
        status poll is sent instead); the original command is never
        repeated."""
        try:
            return self._recv60(what)
        except TransferTimeout:
            log("no reply to %s - one status poll" % what)
            self._send([0x60, 0x05, 0x00])
            return self._recv60(what + " (recovery poll)")

    @staticmethod
    def _named(body, name):
        return list(body) + [ord(c) for c in name] + [0x00]

    def drain(self, duration=0.1):
        end = time.monotonic() + duration * WAIT_SCALE
        while time.monotonic() < end:
            if self.t.recv(0.01) is None:
                time.sleep(0.005)

    def _status(self):
        self._send([0x60, 0x05, 0x00])
        reply = self._recv60("status")
        if len(reply) < 10:
            raise TransferError("unexpected status reply: %s"
                                % bytes(reply).hex())
        return tail_int(list(reply[-5:]))

    def _status_note(self, context):
        try:
            code = self._status()
        except TransferError as e:
            log("note: status after %s unreadable (%s)" % (context, e))
            return
        if code:
            log("note: status 0x%x after %s" % (code, context))

    # ---- identity / modes

    def identify(self):
        """Standard MIDI identity request. -> dict or None."""
        self.drain(0.05)
        self._send_raw([0x7E, 0x00, 0x06, 0x01])
        deadline = time.monotonic() + 2.0 * WAIT_SCALE
        while time.monotonic() < deadline:
            payload = self.t.recv(0.2)
            if payload is None:
                continue
            log("rx " + bytes(payload).hex())
            if (len(payload) >= 13 and payload[0] == 0x7E
                    and payload[2] == 0x06 and payload[3] == 0x02
                    and payload[4] == 0x52):
                model = payload[7] | (payload[8] << 7)
                version = bytes(payload[9:13]).decode("ascii", "replace")
                name, folder = MODELS.get(model, (None, None))
                return {"family": payload[5] | (payload[6] << 7),
                        "model": model, "firmware": version,
                        "name": name or "unknown model 0x%02x" % model,
                        "folder": folder}
        return None

    def autosave_off(self):
        """Switch AUTO SAVE off and read the pedal's confirmation.
        -> 0 (off, confirmed), 1 (pedal reports ON) or None (no reply)."""
        self._send([0x50])                    # editor mode on (for the ACK)
        time.sleep(0.05 * WAIT_SCALE)
        self.drain(0.1)
        self._send_raw(AUTOSAVE_OFF)
        result = None
        deadline = time.monotonic() + 2.0 * WAIT_SCALE
        while time.monotonic() < deadline:
            b = self.t.recv(0.1)
            if b is None:
                continue
            log("rx " + bytes(b).hex())
            if (len(b) >= 10 and bytes(b[3:6]) == b"\x64\x20\x01"
                    and b[6] == 0x64 and b[7] == 0x0F):
                result = b[8] | (b[9] << 7)
                break
        self._send([0x51])                    # editor mode off again
        time.sleep(0.05 * WAIT_SCALE)
        self.drain(0.1)
        return result

    def pcmode_on(self):
        self._send([0x52])
        self._recv("PC mode on")
        self.in_pcmode = True

    def pcmode_off(self):
        self._send([0x53])
        try:
            self._recv("PC mode off")
        except TransferTimeout:
            pass
        self.in_pcmode = False

    # ---- file operations

    def file_check(self, name):
        self._send(self._named([0x60, 0x25, 0x00, 0x00], name))
        self._ack("check %s" % name)
        code = self._status()
        self._send([0x60, 0x27])
        self._ack("end check")
        return code == 0

    def list_files(self):
        return list(self.list_sizes())

    def list_sizes(self):
        """-> {name: size in bytes} of every file, in the pedal's order."""
        sizes = collections.OrderedDict()
        first = True
        replies = 0
        while True:
            self._send(self._named(
                [0x60, 0x25 if first else 0x26, 0x00, 0x00], "*"))
            reply = self._recv60("file listing")
            first = False
            if len(reply) < 16 or reply[4] != 4:
                break
            name, size = listing_entry(reply)
            if not name:
                break
            sizes[name] = size
            replies += 1
            if replies > 4000:
                raise TransferError("file listing does not end")
        self.close()
        return sizes

    def download(self, name):
        open_read = self._named([0x60, 0x20, 0x02] + [0x00] * 9, name)
        self._send(open_read)
        self._ack("open (read) %s" % name)
        self._send(open_read)                 # zoomzt2 sends this twice
        self._ack("open (read) %s, 2nd" % name)
        data = bytearray()
        while True:
            self._send([0x60, 0x05, 0x00])
            self._recv60("status before read")
            self._send([0x60, 0x22, 0x14, 0x2F, 0x60, 0x00,
                        0x0C, 0x00, 0x04, 0x00, 0x00, 0x00])
            self._recv60("read request")
            self._send([0x60, 0x05, 0x00])
            reply = self._recv60("read data")
            if len(reply) < 10 or reply[4] != 0x04:
                break
            length = reply[9] * 128 + reply[8]
            if length == 0:
                break
            block = unpack7(reply[10:10 + length + length // 7 + 1])[:length]
            if (tail_int(list(reply[-5:])) ^ 0xFFFFFFFF) \
                    != binascii.crc32(bytes(block)):
                raise TransferError("CRC error reading %s (offset %d)"
                                    % (name, len(data)))
            data += block
        return bytes(data)

    def upload(self, name, data):
        """CAUTION: starts with 60 24, which DELETES an existing entry of
        the same name. Always close() and read back afterwards."""
        data = bytes(data)
        self._send(self._named([0x60, 0x24], name))
        self._ack("replace entry %s" % name)
        self._send(self._named([0x60, 0x20, 0x01] + [0x00] * 9, name))
        self._ack("open (write) %s" % name)
        self._status_note("open %s" % name)
        sent = 0
        while sent < len(data):
            chunk = data[sent:sent + BLOCK]
            body = [0x60, 0x23, 0x40, 0x00, 0x00, 0x00, 0x00,
                    len(chunk) & 0x7F, (len(chunk) >> 7) & 0x7F,
                    0x00, 0x00, 0x00]
            body += list(pack7(chunk))
            body += crc_tail(chunk)
            self._send(body)
            self._ack("block %d of %s" % (sent, name))
            self._status_note("block %d of %s" % (sent, name))
            sent += len(chunk)

    def delete(self, name):
        self._send(self._named([0x60, 0x24], name))
        self._ack("delete %s" % name)

    def close(self):
        self._send([0x60, 0x21, 0x40, 0x00, 0x00, 0x00, 0x00])
        self._ack("close file")
        self._send([0x60, 0x09])
        self._ack("end session")

    def abort(self):
        """Close the session cleanly after a failure (both commands always
        go out). True if the pedal acknowledged both."""
        saved = self.recv_timeout
        self.recv_timeout = ABORT_TIMEOUT
        acked = True
        try:
            for body, what in (
                    ([0x60, 0x21, 0x40, 0x00, 0x00, 0x00, 0x00], "abort 1"),
                    ([0x60, 0x09], "abort 2")):
                self._send(body)
                try:
                    self._ack(what)
                except TransferError:
                    acked = False
            return acked
        finally:
            self.recv_timeout = saved

    def disk_usage(self):
        self._send([0x60, 0x29, 0x00, 0x00, 0x00, 0x00, 0x00])
        reply = self._recv60("disk usage")
        if len(reply) < 20:
            raise TransferError("unexpected disk usage reply")
        return tail_int(list(reply[10:15])), tail_int(list(reply[15:20]))

    # ---- patches (read only, for the backup)

    def patch_info(self):
        self.drain(0.05)
        self._send([0x44])
        reply = self._recv("patch info")
        if len(reply) < 12:
            raise TransferError("unexpected patch info")
        count = reply[5] * 128 + reply[4]
        psize = reply[7] * 128 + reply[6]
        bsize = reply[11] * 128 + reply[10]
        return count, psize, bsize

    def patch_download(self, location, bsize):
        bank = (location - 1) // bsize
        loc = location - bank * bsize - 1
        self._send([0x46, 0x00, 0x00, bank & 0x7F, bank >> 7,
                    loc & 0x7F, loc >> 7])
        reply = self._recv("patch %d" % location)
        if len(reply) < 12:
            raise TransferError("unexpected patch reply")
        length = reply[11] * 128 + reply[10]
        if length == 0:
            return b""
        if len(reply) < 17:
            raise TransferError("unexpected patch reply")
        data = unpack7(reply[12:12 + length + length // 7 + 1])[:length]
        if (tail_int(list(reply[-5:])) ^ 0xFFFFFFFF) \
                != binascii.crc32(bytes(data)):
            raise TransferError("CRC error in patch %d" % location)
        return bytes(data)


# --------------------------------------------------- effect list (FLST_SEQ)

def _grammar():
    import zoomzt2
    return zoomzt2.ZT2


def flst_entries(data):
    """-> [(group, filename, version, id, installed)]"""
    config = _grammar().parse(bytes(data[:FLST_GRAMMAR_SIZE]))
    entries = []
    for group in config[1]:
        for effect in group["effects"]:
            entries.append((int(group["group"]), str(effect["effect"]),
                            str(effect["version"]), int(effect["id"]),
                            int(effect["installed"])))
    return entries


def flst_validate(data, model=None):
    """-> (ok, problems, entries). Never raises. With `model`, a list
    shorter than the one in the newest backup of that model is not valid
    either: a write cut off at a block boundary still parses."""
    data = bytes(data)
    problems = []
    if len(data) < FLST_GRAMMAR_SIZE:
        return False, ["only %d bytes, expected at least %d"
                       % (len(data), FLST_GRAMMAR_SIZE)], None
    if any(data[FLST_GRAMMAR_SIZE:]):
        return False, ["data past byte %d that the grammar cannot represent"
                       % FLST_GRAMMAR_SIZE], None
    canon = data[:FLST_GRAMMAR_SIZE]
    try:
        entries = flst_entries(canon)
    except Exception as e:
        return False, ["unreadable: %s" % e], None
    if not entries:
        problems.append("contains no effects")
    try:
        rebuilt = _grammar().build(_grammar().parse(canon))
    except Exception as e:
        return False, ["cannot be rebuilt: %s" % e], entries
    if rebuilt != canon:
        problems.append("parse + build does not give the same bytes")
    for group, name, _, eid, _ in entries:
        if (eid >> 24) & 0xFF != group:
            problems.append("entry %s: id 0x%08x does not match group %d"
                            % (name, eid, group))
    size = backup_flst_size(model) if model is not None and not problems \
        else None
    if size and len(data) < size:
        problems.append("%d bytes, the newest backup of this pedal model has "
                        "%d - complete, but cut short" % (len(data), size))
    return not problems, problems, entries


def backup_flst_size(model):
    """Length of the effect list in the newest backup of `model`, or None.
    Every write of this program keeps the length it found, so a shorter
    list was cut off (or written by another tool) - the rescue's restore
    fills it up with zeros."""
    found = latest_backup(model=model)
    return next((f.get("bytes") for f in found[1].get("files", [])
                 if f.get("name") == FLST_NAME), None) if found else None


def _flst_finish(config, native_size):
    built = _grammar().build(config)
    if len(built) > native_size:
        raise Refused("the new effect list would be larger than the old one")
    return built + b"\x00" * (native_size - len(built))


def flst_add(data, filename, version, eid):
    """New list = old list + exactly this one entry."""
    old = flst_entries(data)
    if any(e[1].upper() == filename.upper() for e in old):
        raise Refused("%s is already in the effect list" % filename)
    if any(e[3] == eid for e in old):
        raise Refused("the id 0x%08x is already in the effect list" % eid)
    config = _grammar().parse(bytes(data[:FLST_GRAMMAR_SIZE]))
    group_id = (eid >> 24) & 0xFF
    entry = (group_id, filename, version, eid, 1)
    for group in config[1]:
        if group["group"] == group_id:
            group["effects"].append(
                dict(effect=filename, version=version, id=eid, installed=1))
            break
    else:
        raise Refused("group %d does not exist in the pedal's effect list"
                      % group_id)
    new = _flst_finish(config, len(data))
    ok, problems, entries = flst_validate(new)
    # The new list must be the old one, in the same order, plus the entry.
    if not ok or entries.count(entry) != 1 \
            or [e for e in entries if e != entry] != old:
        raise Refused("the computed effect list is not 'old + 1 entry' (%s)"
                      % "; ".join(problems or ["mismatch"]))
    return new


def flst_remove(data, filename):
    config = _grammar().parse(bytes(data[:FLST_GRAMMAR_SIZE]))
    removed = 0
    for group in config[1]:
        keep = [e for e in group["effects"] if e["effect"] != filename]
        removed += len(group["effects"]) - len(keep)
        del group["effects"][:]
        group["effects"].extend(keep)
    if removed != 1:
        raise Refused("%s appears %d times in the effect list (expected 1)"
                      % (filename, removed))
    new = _flst_finish(config, len(data))
    ok, problems, entries = flst_validate(new)
    expect = [e for e in flst_entries(data) if e[1] != filename]
    if not ok or entries != expect:
        raise Refused("the computed effect list is not 'old - 1 entry' (%s)"
                      % "; ".join(problems or ["mismatch"]))
    return new


# ----------------------------------------------------------- effect files

class ZD2(object):
    """The header data of a .ZD2 file (only what is needed here)."""

    def __init__(self, data):
        data = bytes(data)
        if len(data) < 144 or data[:4] != b"ZDLF":
            raise ValueError("not a ZD2 file (wrong start or too short)")
        self.data = data
        self.header4 = struct.unpack_from("<I", data, 4)[0]
        self.checksum = struct.unpack_from("<I", data, 8)[0]
        self.target = struct.unpack_from("<I", data, 12)[0]
        self.version = data[89:93].decode("ascii", "replace")
        self.group = data[95]
        self.id = struct.unpack_from("<I", data, 96)[0]
        self.name = data[100:111].split(b"\x00", 1)[0].decode(
            "ascii", "replace")
        self.groupname_raw = data[111:122]
        self.groupname = self.groupname_raw.split(b"\x00", 1)[0].decode(
            "ascii", "replace")
        self.color = data[122:125]
        self.sections = {}
        self.tiles = False
        off = 128
        end = len(data) - 16
        while off + 8 <= end:
            tag = data[off:off + 4]
            if not tag.isalnum():
                break
            length = struct.unpack_from("<I", data, off + 4)[0]
            self.sections[tag.decode("ascii")] = (off + 8, length)
            off += 8 + length
        else:
            self.tiles = off == end
        self.tag = b""
        if "INFO" in self.sections and self.sections["INFO"][1] >= 20:
            o = self.sections["INFO"][0]
            self.tag = data[o + 4:o + 16]

    def checksum_ok(self):
        d = self.data
        return self.checksum == (zlib.crc32(d[12:len(d) - 16]) ^ 0xFFFFFFFF)

    def tag_text(self):
        return self.tag.split(b"\x00", 1)[0].decode("ascii", "replace")


def zd2_problems(zd2):
    """Envelope check before uploading. Empty list = fine. These are
    properties every stock effect has; a file that deviates from them does
    not go onto the pedal."""
    d = zd2.data
    errs = []
    if zd2.header4 != 120:
        errs.append("header value @4 is %d instead of 120" % zd2.header4)
    if not zd2.checksum_ok():
        errs.append("checksum does not match")
    if not zd2.target & 0x0080:
        errs.append("target 0x%04x lacks the MS Plus bit 0x0080 (the file is "
                    "for a different Zoom device)" % zd2.target)
    if d[93:95] != b"\x00\x00" or d[125:128] != b"\x00\x00\x00":
        errs.append("header padding bytes are not zero")
    if zd2.group != (zd2.id >> 24) & 0xFF:
        errs.append("group %d does not match id 0x%08x" % (zd2.group, zd2.id))
    raw_name = d[100:111].split(b"\x00", 1)[0]
    if not raw_name or not all(0x20 <= b < 0x7F for b in raw_name):
        errs.append("effect name empty or not printable")
    if not all(0x20 <= b < 0x7F for b in d[89:93]):
        errs.append("version not printable")
    if not zd2.tiles:
        errs.append("sections do not fill the file exactly")
    for need in ("ICON", "TXJ1", "TXE1", "INFO", "DATA", "PRMJ", "PRME"):
        if need not in zd2.sections:
            errs.append("section %s missing" % need)
    if "INFO" in zd2.sections and zd2.sections["INFO"][1] < 20:
        errs.append("INFO section shorter than 20 bytes")
    return errs


def zic_problems(data):
    data = bytes(data)
    if len(data) < 12 or data[:4] != b"ZBMP":
        return ["not a ZIC icon file"]
    dlen = struct.unpack_from("<I", data, 4)[0]
    if dlen % 4 or not 8 <= dlen <= 64 or len(data) < 8 + dlen:
        return ["icon descriptor invalid"]
    frames = []
    for i in range(dlen // 4):
        w, h = struct.unpack_from("<HH", data, 8 + 4 * i)
        if w == 0 or h == 0:
            break
        frames.append((w, h))
    if not frames:
        return ["icon contains no image"]
    if any(w > 256 or h > 256 for w, h in frames):
        return ["icon size implausible"]
    total = 8 + dlen + sum(w * (((h - 1) >> 3) + 1) for w, h in frames)
    if total != len(data):
        return ["icon data does not fill the file exactly"]
    return []


def pedal_name(path):
    """File name the effect has on the pedal: upper case like every ZOOM
    file (a non-ASCII stem is kept as it is, for filename_problems)."""
    stem, ext = os.path.splitext(os.path.basename(path))
    return (stem.upper() if stem.isascii() else stem) + ext.upper()


# Files the pedal has of its own besides the stock effects, whose names an
# effect (NAME.ZD2 + NAME.ZIC) must not take: the MS-60B+ keeps IR.ZIC next
# to its IR files, and BPM_MDL.ZIC belongs to the built-in BPM module.
RESERVED_STEMS = ("IR", "BPM_MDL")


def filename_problems(base):
    stem, ext = os.path.splitext(base)
    if ext != ".ZD2":
        return ["the file extension must be .ZD2"]
    if not 1 <= len(stem) <= 8 or not all(
            c.isalnum() or c in "_-" for c in stem) or not stem.isascii():
        return ["the file name must have 1-8 characters (A-Z, 0-9, _ or -)"]
    if stem.upper() in RESERVED_STEMS:
        return ["the pedal has a file of its own named %s.ZIC - rename the "
                "effect file" % stem.upper()]
    return []


# What ships with the program (stock lists, blank icon) is read once and then
# kept: a running app must not depend on its own folder staying where it is.
# (Moved while it was open, the app could no longer find its blank icon.)
_STOCK_CACHE = {}
_BLANK_ICON = None


def blank_icon():
    """The icon used for an effect that comes without one."""
    global _BLANK_ICON
    if _BLANK_ICON is None:
        with open(os.path.join(HERE, "BLANK.ZIC"), "rb") as f:
            _BLANK_ICON = f.read()
    return _BLANK_ICON


def stock_catalog(folder):
    """Stock effects of a model from zoom_fx_ms-plus/<model>/
    effects_sort.txt -> {FILENAME: id}. Empty if unknown."""
    out = {}
    if not folder:
        return out
    if folder in _STOCK_CACHE:
        return dict(_STOCK_CACHE[folder])
    path = os.path.join(STOCK_ROOT, folder, "effects_sort.txt")
    try:
        with open(path) as f:
            for line in f:
                parts = line.split(" : ", 1)
                if len(parts) == 2 and parts[0].startswith("0x"):
                    out[parts[1].split(",", 1)[0].strip().upper()] = int(
                        parts[0], 16)
    except OSError:
        pass
    if out:
        # module built into the pedal: it is in the effect list but has no
        # ZD2 file of its own, so it is missing from effects_sort.txt
        out.setdefault("BPM_MDL.ZD2", -1)
        _STOCK_CACHE[folder] = dict(out)
    return out


def preload_shipped():
    """Read the shipped files now, not when they are first needed."""
    try:
        blank_icon()
    except OSError:
        pass
    try:
        folders = os.listdir(STOCK_ROOT)
    except OSError:
        folders = []
    for folder in folders:
        stock_catalog(folder)


preload_shipped()


# ------------------------------------------------- categories / adaptation

def category_table(files_dir, stock=None):
    """Reads from the stock effects of a backup how THIS pedal marks its
    categories. -> {group: {...}}

    Every stock effect carries its category four times: as the group number
    (top byte of the id), as a name in the header, as a short/display name
    in the INFO section and as a colour. The numbers differ between models
    (MS-60B+: 9 = SFX, 10 = DELAY, 11 = REVERB)."""
    votes = collections.defaultdict(lambda: collections.defaultdict(
        collections.Counter))
    try:
        names = sorted(os.listdir(files_dir))
    except OSError:
        return {}
    for fn in names:
        if not fn.upper().endswith(".ZD2"):
            continue
        if stock and fn.upper() not in stock:
            continue            # an added effect, not one of the pedal's own
        try:
            with open(os.path.join(files_dir, fn), "rb") as f:
                z = ZD2(f.read())
        except (OSError, ValueError):
            continue
        if not stock and not any(z.tag):
            continue            # model unknown: take only marked effects
        v = votes[z.group]
        v["name"][z.groupname_raw.split(b"\x00", 1)[0]] += 1
        v["tag"][z.tag] += 1
        v["color"][z.color] += 1
        v["target"][z.target] += 1
    table = {}
    for group, v in votes.items():
        raw = v["name"].most_common(1)[0][0]
        table[group] = {
            "group": group,
            "label": raw.decode("ascii", "replace"),
            "tag": v["tag"].most_common(1)[0][0],
            "color": v["color"].most_common(1)[0][0],
            "target": v["target"].most_common(1)[0][0],
            "count": sum(v["name"].values()),
        }
    return table


def adaptation_diffs(zd2, cat):
    """How does the file differ from the stock effects of category `cat`
    on this pedal?"""
    diffs = []
    if zd2.group != cat["group"]:
        diffs.append("group number %d -> %d (that is '%s' on this pedal)"
                     % (zd2.group, cat["group"], cat["label"]))
    if zd2.groupname != cat["label"]:
        diffs.append("category name '%s' -> '%s'"
                     % (zd2.groupname, cat["label"]))
    if zd2.tag != cat["tag"]:
        diffs.append("display tag in the INFO section '%s' -> '%s'"
                     % (zd2.tag_text() or "(empty)",
                        cat["tag"].split(b"\x00", 1)[0].decode("ascii")))
    if zd2.color != cat["color"]:
        diffs.append("category colour %s -> %s"
                     % (zd2.color.hex(), cat["color"].hex()))
    if zd2.target != cat["target"]:
        diffs.append("target bits 0x%02x -> 0x%02x"
                     % (zd2.target, cat["target"]))
    return diffs


def adapt_zd2(zd2, cat):
    """A copy of the file whose category fields match the stock effects of
    this pedal. The effect code (DATA) is untouched; only header fields that
    also vary between stock effects are changed, and the checksum is
    recomputed."""
    b = bytearray(zd2.data)
    new_id = (cat["group"] << 24) | (zd2.id & 0x00FFFFFF)
    struct.pack_into("<I", b, 12, cat["target"])
    b[95] = cat["group"]
    struct.pack_into("<I", b, 96, new_id)
    if zd2.groupname != cat["label"]:
        b[111:122] = cat["label"].encode("ascii").ljust(11, b"\x00")[:11]
    b[122:125] = cat["color"]
    o = zd2.sections["INFO"][0]
    b[o + 4:o + 16] = cat["tag"]
    struct.pack_into("<I", b, 8,
                     zlib.crc32(bytes(b[12:len(b) - 16])) ^ 0xFFFFFFFF)
    out = ZD2(bytes(b))
    # Nothing but the fields named above may have changed.
    changed = [i for i in range(len(b)) if b[i] != zd2.data[i]]
    allowed = set(range(8, 16)) | set(range(95, 100)) | set(range(111, 125)) \
        | set(range(o + 4, o + 16))
    if not set(changed) <= allowed or zd2_problems(out):
        raise Refused("the adaptation produced an unexpected result")
    return out


def default_category(zd2, table):
    """The pedal's category with the same NAME as the effect's, if any."""
    matches = [c for c in table.values()
               if c["label"].upper() == zd2.groupname.upper()]
    return matches[0] if len(matches) == 1 else None


def print_categories(table, default=None, used_ids=(), zd2=None,
                     current=None):
    """List the pedal's categories. With `zd2`, categories where the effect's
    id would collide with an installed effect are marked."""
    for g in sorted(table):
        c = table[g]
        marks = []
        if current is not None and current["group"] == g:
            marks.append("<- installed here now")
        if default is not None and default["group"] == g:
            marks.append("<- the effect's own category")
        if zd2 is not None and ((g << 24) | (zd2.id & 0xFFFFFF)) in used_ids:
            marks.append("(id already taken here)")
        say("  %2d) %-12s %3d stock effects  %s"
            % (g, c["label"], c["count"], " ".join(marks)))


def choose_category(zd2, table, want=None, interactive=True, used_ids=(),
                    show_list=True, current=None):
    """The category of this pedal the effect should go into: the one asked
    for (`want`), else chosen by the user, else - without a user - the one
    the effect is installed in now or its own. -> category dict or None."""
    if want is not None:
        if want not in table:
            raise Refused("category %d does not exist on this pedal "
                          "(available: %s)" % (want, ", ".join(
                              "%d=%s" % (g, table[g]["label"])
                              for g in sorted(table))))
        return table[want]
    own = default_category(zd2, table)
    if not interactive:
        return current or own
    return ui.choose_category(zd2, table, own, current, used_ids, show_list)


# ---------------------------------------------------------------- backup

def looks_complete(name, data):
    """Detects truncated downloads from the file structure (the file API
    reports no file size to compare against)."""
    upper = name.upper()
    if upper.endswith(".ZD2"):
        try:
            return ZD2(data).tiles
        except ValueError:
            return False
    if upper.endswith(".ZIC"):
        return not zic_problems(data)
    if upper == FLST_NAME:
        return len(data) >= FLST_GRAMMAR_SIZE
    return True


def latest_backup(model=None, folder=None):
    """Newest complete backup (optionally for one model only)."""
    best = None
    try:
        dirs = sorted(os.listdir(BACKUP_ROOT))
    except OSError:
        return None
    for d in dirs:
        path = os.path.join(BACKUP_ROOT, d)
        try:
            with open(os.path.join(path, "manifest.json")) as f:
                man = json.load(f)
        except (OSError, ValueError):
            continue
        if not man.get("complete"):
            continue
        if model is not None and man.get("model_number") != model:
            continue
        if folder is not None and man.get("model_folder") != folder:
            continue
        if best is None or backup_time(man) >= backup_time(best[1]):
            best = (path, man)
    return best


def backup_time(man):
    """When a backup was made, as a timestamp. 'created' is local time, which
    runs an hour backwards when summer time ends; 'created_ts' does not."""
    if isinstance(man.get("created_ts"), (int, float)):
        return float(man["created_ts"])
    try:
        return time.mktime(datetime.datetime.fromisoformat(
            man.get("created", "")).timetuple())
    except (ValueError, OverflowError, TypeError):
        return 0.0


def reusable_backup(ident, flst_now, names_now):
    """The newest backup of this model if its effect list and its file names
    match the current state exactly. The contents of the other files and the
    patches are NOT compared (that would take as long as a backup), so this
    is a hint, not proof that nothing changed."""
    found = latest_backup(model=ident["model"])
    if not found:
        return None
    path, man = found
    files = {f["name"]: f for f in man.get("files", [])}
    if set(files) != set(names_now):
        return None
    if files.get(FLST_NAME, {}).get("sha256") != sha256(flst_now):
        return None
    return path


def do_backup(pedal, ident, names=None):
    """Save every file on the pedal + the patches. -> backup folder."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = (ident.get("folder") or "model-%02x" % ident["model"])
    dest = os.path.join(BACKUP_ROOT, "%s_%s" % (tag, stamp))
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(BACKUP_ROOT, "%s_%s_%d" % (tag, stamp, n))
    files_dir = os.path.join(dest, "files")
    os.makedirs(files_dir)
    if names is None:
        names = pedal.list_sizes()
    # {name: size} from the pedal's own listing: a read that ended early is
    # caught by its length (only possible when the caller passed a dict)
    sizes = names if isinstance(names, dict) else {}
    if FLST_NAME not in names:
        raise TransferError("the pedal reports no %s - file listing "
                            "incomplete" % FLST_NAME)
    say("Backing up %d files to:\n  %s" % (len(names), dest))
    manifest = {
        "format": "zoom-ms-installer.backup.v2",
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "created_ts": time.time(),
        "model": ident["name"], "model_number": ident["model"],
        "model_folder": ident.get("folder"),
        "firmware": ident["firmware"],
        "complete": False, "files": [], "patches": [],
    }
    started = time.monotonic()
    total = 0
    unusual = []
    for i, name in enumerate(names, 1):
        data = None
        odd = None                  # a download that looked incomplete
        for attempt in (1, 2, 3):
            try:
                data = pedal.download(name)
                pedal.close()
                if looks_complete(name, data) \
                        and len(data) == sizes.get(name, len(data)):
                    break
                if odd is not None and data == odd:
                    # Read twice, byte-identical: this is what the pedal
                    # holds, just not in the layout this program knows.
                    unusual.append(name)
                    break
                odd = data
                raise TransferError("file looks incomplete (%d bytes)"
                                    % len(data))
            except TransferError as e:
                log("backup %s attempt %d: %s" % (name, attempt, e))
                pedal.abort()
                if attempt == 3:
                    raise TransferError("backup of %s failed: %s" % (name, e))
        with open(os.path.join(files_dir, name), "wb") as f:
            f.write(data)
        total += len(data)
        manifest["files"].append(
            {"name": name, "bytes": len(data), "sha256": sha256(data)})
        progress("  %3d/%d  %-13s %7d bytes   " % (
            i, len(names), name, len(data)), i, len(names))
    progress(None)
    with open(os.path.join(files_dir, FLST_NAME), "rb") as f:
        ok, problems, entries = flst_validate(f.read(), ident["model"])
    if not ok:
        # never "safe": a pedal with this list must not be restarted
        raise Unresolved("the effect list on the pedal is not valid (%s). "
                         "Nothing was written - leave the pedal POWERED and "
                         "run the rescue." % "; ".join(problems))
    missing = [e[1] for e in entries if e[1] not in names
               and e[1] != "BPM_MDL.ZD2"]
    if missing:
        say("  Note: in the effect list but not a file on the pedal: %s"
            % ", ".join(missing))
    if unusual:
        say("  Note: unusual file layout, saved as read (twice identical): %s"
            % ", ".join(unusual))
    manifest["unusual_files"] = unusual

    # Patches: read only, best effort - a failure here does not invalidate
    # the file backup, but it is recorded.
    try:
        count, psize, bsize = pedal.patch_info()
        if not 0 < count <= 1000 or not 0 < bsize <= 100:
            raise TransferError("implausible patch info")
        patch_dir = os.path.join(dest, "patches")
        os.makedirs(patch_dir)
        for loc in range(1, count + 1):
            data = pedal.patch_download(loc, bsize)
            fn = "%03d.zptc" % loc
            with open(os.path.join(patch_dir, fn), "wb") as f:
                f.write(data)
            manifest["patches"].append(
                {"slot": loc, "file": "patches/" + fn, "bytes": len(data),
                 "sha256": sha256(data)})
            progress("  patch %3d/%d   " % (loc, count), loc, count)
        progress(None)
        manifest["patches_complete"] = True
    except TransferError as e:
        progress(None)
        manifest["patches_complete"] = False
        say("  Note: the patches could not be backed up (completely) (%s). "
            "The effect files are backed up." % e)
        pedal.drain(0.3)

    manifest["complete"] = True
    with open(os.path.join(dest, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    say("Backup done: %d files, %.1f MB, %d patches, %.0f s."
        % (len(names), total / 1e6, len(manifest["patches"]),
           time.monotonic() - started))
    return dest


# -------------------------------------------------------- guarded writes

def verified_upload(pedal, name, data):
    with write_step():
        pedal.upload(name, data)
        pedal.close()
    back = pedal.download(name)
    pedal.close()
    if back != bytes(data):
        raise TransferError("%s read back differs (%d instead of %d bytes)"
                            % (name, len(back), len(data)))


def replace_flst(pedal, old, new):
    """Rewrite the effect list - the most delicate moment. First the link,
    the starting state and the free space are checked; afterwards the list
    is read back and the write repeated if necessary."""
    if bytes(old) == bytes(new):
        return 0
    current = pedal.download(FLST_NAME)
    pedal.close()
    if current != bytes(old):
        raise BaselineChanged("the effect list on the pedal changed in the "
                              "meantime", current)
    _, available = pedal.disk_usage()
    # upload() deletes the old list before writing the new one
    if available + allocated(len(current)) < allocated(len(new)) \
            + DISK_MARGIN:
        raise TransferError("not enough free space on the pedal (%d bytes)"
                            % available)
    last = "unknown"
    # one step from the first write to the verified list, retries included:
    # a list that was cut off is repaired before the program may end
    with write_step():
        for attempt in range(1, FLST_WRITE_ATTEMPTS + 1):
            try:
                with write_step():
                    pedal.upload(FLST_NAME, new)
                    pedal.close()
                back = pedal.download(FLST_NAME)
                pedal.close()
                if back == bytes(new):
                    return attempt - 1
                last = "the list read back differs (%d bytes)" % len(back)
            except TransferError as e:
                last = str(e)
            log("writing the effect list, attempt %d/%d: %s"
                % (attempt, FLST_WRITE_ATTEMPTS, last))
            pedal.abort()
        banner("The effect list could not be written and verified in %d "
               "attempts (%s)." % (FLST_WRITE_ATTEMPTS, last))
        raise Unresolved("effect list not verified: %s" % last)


def unwind(pedal, failure, cleanup, flst_expect):
    """After a failure OUTSIDE the list rewrite: close the session, remove
    partial files, check the effect list against the expected state."""
    log("FAILURE: %s - clean abort" % failure)
    if pedal.abort():
        try:
            for name in cleanup:
                present = pedal.file_check(name)
                with write_step():
                    if present:
                        pedal.delete(name)
                    pedal.close()
            back = pedal.download(FLST_NAME)
            pedal.close()
            if back == bytes(flst_expect):
                raise SafeAbort("%s (file session closed, effect list "
                                "verified)" % failure)
            failure += "; and the effect list differs from the expected state"
        except TransferError as e:
            failure += "; the check after the abort failed (%s)" % e
    else:
        failure += "; the pedal did not acknowledge the close"
    banner(failure)
    raise Unresolved(failure)


def install_files(pedal, files, flst_old, flst_new):
    """files = [(name, bytes)], icon first. First the files (verified), THEN
    the effect list: if it fails before that, the pedal is logically
    unchanged."""
    uploaded = []
    try:
        for name, _ in files:
            present = pedal.file_check(name)
            pedal.close()
            if present:
                raise SafeAbort("'%s' is already on the pedal - remove it "
                                "first (nothing was written)" % name)
        for name, data in files:
            uploaded.append(name)
            verified_upload(pedal, name, data)
            say("  %s uploaded and verified (%d bytes)" % (name, len(data)))
    except TransferError as e:
        unwind(pedal, "upload: %s" % e, uploaded, flst_old)
    try:
        return replace_flst(pedal, flst_old, flst_new)
    except BaselineChanged as e:
        unwind(pedal, str(e), uploaded, e.current)
    except TransferError as e:
        unwind(pedal, "preparing the effect list: %s" % e, uploaded, flst_old)


def uninstall_files(pedal, names, flst_old, flst_new):
    """First the effect list (entries removed), then the files, one after
    the other: if it fails after that, at worst orphaned files are left
    behind."""
    try:
        retries = replace_flst(pedal, flst_old, flst_new)
    except BaselineChanged as e:
        unwind(pedal, str(e), [], e.current)
    except TransferError as e:
        unwind(pedal, "preparing the effect list: %s" % e, [], flst_old)
    try:
        for i, name in enumerate(names):
            progress("  deleting %d/%d  %-13s " % (i + 1, len(names), name),
                     i, len(names))
            if pedal.file_check(name):
                with write_step():
                    pedal.delete(name)
                    pedal.close()
                gone = not pedal.file_check(name)
                pedal.close()
                if not gone:
                    raise TransferError("%s is still there after deleting"
                                        % name)
                say("  %s deleted" % name)
            else:
                pedal.close()
                say("  %s was not on the pedal" % name)
        progress(None)
    except TransferError as e:
        # The entries are already out of the (verified) list. Close the
        # session, try once more to delete what is left, and say what really
        # happened.
        progress(None)
        try:
            unwind(pedal, "deleting after the list update: %s" % e, names,
                   flst_new)
        except SafeAbort:
            raise SafeAbort("%s removed from the effect list, but deleting "
                            "the files hit an error (%s). Anything left over "
                            "is harmless and is offered for deletion the "
                            "next time you install an effect of that name."
                            % (", ".join(n for n in names
                                         if n.upper().endswith(".ZD2")), e))
    return retries


def remove_orphans(pedal, names, flst_expect):
    """Delete files that are on the pedal but in no effect list. The effect
    list is never written here."""
    try:
        for name in names:
            with write_step():
                pedal.delete(name)
                pedal.close()
            gone = not pedal.file_check(name)
            pedal.close()
            if not gone:
                raise TransferError("%s is still there after deleting" % name)
            say("  %s deleted" % name)
    except TransferError as e:
        unwind(pedal, "deleting orphaned files: %s" % e, [], flst_expect)


# -------------------------------------------------------------- commands

class Session(object):
    """Connect, identify, PC mode - and leave cleanly at the end."""

    def __init__(self, opts, transport=None):
        self.opts = opts
        self.transport = transport
        self.pedal = None
        self.ident = None

    def __enter__(self):
        if self.transport is None:
            self.transport = open_transport(
                getattr(self.opts, "pedal", None),
                interactive=not getattr(self.opts, "yes", False))
        self.pedal = Pedal(self.transport)
        self.ident = self.pedal.identify()
        if self.ident is None:
            self._close_ports()
            raise Refused("The pedal does not answer the identity request. "
                          "Is another program still connected?")
        try:
            self.opts.ident = self.ident  # the window remembers the model
        except AttributeError:
            pass
        keep_awake(True)
        say("Connected: Zoom %s, firmware %s"
            % (self.ident["name"], self.ident["firmware"]))
        return self

    def _close_ports(self):
        close = getattr(self.transport, "close", None)
        if close:
            close()

    def __exit__(self, etype, exc, tb):
        # A failure or interruption while only reading (backup, listing):
        # still close the file session cleanly.
        if etype is not None and not issubclass(
                etype, (Refused, SafeAbort, Unresolved)):
            try:
                self.pedal.abort()
            except Exception:
                pass
        # After an unverified failure do NOT keep poking the pedal.
        if self.pedal.in_pcmode and etype is not Unresolved:
            try:
                self.pedal.pcmode_off()
            except Exception:
                pass
        self._close_ports()
        keep_awake(False)
        return False


def print_effects(entries, table, stock):
    by_group = collections.OrderedDict()
    for group, name, version, eid, installed in entries:
        by_group.setdefault(group, []).append((name, version, eid, installed))
    for group, items in by_group.items():
        label = table.get(group, {}).get("label", "?")
        say("  group %2d  %-12s (%d effects)" % (group, label, len(items)))
        for name, version, eid, installed in items:
            if name.upper() not in stock and stock:
                say("      + %-13s v%s  0x%08x  (added)"
                    % (name, version, eid))


def read_state(opts, transport=None):
    """A read-only snapshot of the pedal: who it is, what is on it. -> dict"""
    with Session(opts, transport) as s:
        p = s.pedal
        p.pcmode_on()
        flst = p.download(FLST_NAME)
        p.close()
        ok, problems, entries = flst_validate(flst, s.ident["model"])
        maximum, available = p.disk_usage()
        ident = dict(s.ident)
    stock = stock_catalog(ident["folder"])
    found = latest_backup(model=ident["model"])
    table = category_table(os.path.join(found[0], "files"), stock) \
        if found else {}
    return {"ident": ident, "flst_bytes": len(flst), "flst_ok": ok,
            "problems": problems or [], "entries": entries or [],
            "storage_max": maximum, "storage_free": available,
            "stock": stock, "table": table, "backup": found}


def cmd_info(opts, transport=None):
    st = read_state(opts, transport)
    say("Effect list: %d bytes, %s" % (
        st["flst_bytes"],
        "valid" if st["flst_ok"] else "NOT valid: %s" % st["problems"]))
    say("Storage: %.1f of %.1f MB free"
        % (st["storage_free"] / 1e6, st["storage_max"] / 1e6))
    if st["entries"]:
        say("%d effects in the list:" % len(st["entries"]))
        print_effects(st["entries"], st["table"], st["stock"])
    if not st["backup"]:
        say("(No backup of this pedal yet - category names appear after "
            "the first backup.)")
    return 0


def cmd_backup(opts, transport=None):
    with Session(opts, transport) as s:
        s.pedal.pcmode_on()
        do_backup(s.pedal, s.ident)
    return 0


def ensure_backup(s, flst, opts):
    """-> (backup folder or None, file names on the pedal). The user is
    always asked before a backup is made; a backup with the same effect list
    and file names is pointed out (its contents may differ). If they
    decline, the newest older backup of this model (if any) still serves as
    the source of the pedal's categories."""
    names = s.pedal.list_sizes()     # {name: size}, also for do_backup
    minutes = max(1, int(round(len(names) * BACKUP_SECONDS_PER_FILE / 60.0)))
    same = None if getattr(opts, "new_backup", False) \
        else reusable_backup(s.ident, flst, names)
    if same:
        say("The newest backup has the same effect list and the same file "
            "names:\n  %s\nThe contents of the files and the patches were "
            "not compared - they may have changed since (a NAM bank written "
            "by nam2zoom, edited patches)." % same)
        question = "Make a backup of the pedal now anyway?"
    else:
        question = "Make a backup of the pedal now?"
    if ask_yes("%s (about %d minute%s)" % (question, minutes,
                                           "" if minutes == 1 else "s"),
               getattr(opts, "yes", False)):
        return do_backup(s.pedal, s.ident, names), names
    if same:
        say("No new backup - the one above is used.")
        return same, names
    older = latest_backup(model=s.ident["model"])
    if older:
        say("No new backup. The newest existing one does NOT contain the "
            "pedal's current state:\n  %s" % older[0])
        return older[0], names
    say("No backup. There is none of this pedal at all - if something goes "
        "wrong, nothing can be restored, and the pedal's categories are "
        "unknown (the effect is installed unchanged).")
    return None, names


def load_effect(path):
    """Read file + icon and check them offline. -> (ZD2, zic_bytes, notes)"""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise Refused("File not found: %s" % path)
    base = pedal_name(path)
    problems = filename_problems(base)
    if problems:
        raise Refused("%s: %s" % (base, "; ".join(problems)))
    with open(path, "rb") as f:
        raw = f.read()
    try:
        zd2 = ZD2(raw)
    except ValueError as e:
        raise Refused("%s: %s" % (base, e))
    problems = zd2_problems(zd2)
    if problems:
        raise Refused("%s will not be uploaded, the file deviates from the "
                      "structure of all stock effects: %s"
                      % (base, "; ".join(problems)))
    notes = []
    stem = os.path.splitext(path)[0]
    zic_path = next((c for c in (stem + ".ZIC", stem + ".zic")
                     if os.path.isfile(c)), None)
    if zic_path is None:
        zic_name = "BLANK.ZIC"
        try:
            zic = blank_icon()
        except OSError:
            raise Refused("%s has no icon (%s.ZIC) next to it, and the blank "
                          "icon that ships with this program cannot be read. "
                          "Put the icon file next to the effect file."
                          % (base, os.path.basename(stem)))
        notes.append("No icon (%s.ZIC) next to the effect file - a blank "
                     "icon is used; the effect then shows as an empty tile."
                     % os.path.basename(stem))
    else:
        zic_name = os.path.basename(zic_path)
        with open(zic_path, "rb") as f:
            zic = f.read()
    problems = zic_problems(zic)
    if problems:
        raise Refused("Icon %s: %s" % (zic_name, "; ".join(problems)))
    return zd2, zic, notes


def describe(zd2, base):
    say("  File        : %s (%d bytes)" % (base, len(zd2.data)))
    say("  Effect      : %s  v%s" % (zd2.name, zd2.version))
    say("  ID          : 0x%08x (group %d, '%s')"
        % (zd2.id, zd2.group, zd2.groupname))
    say("  Target bits : 0x%02x   display tag: %s"
        % (zd2.target, zd2.tag_text() or "(empty)"))


def plan_install(path, ident, entries, table, opts, interactive,
                 show_list=True):
    """Everything about ONE effect that can be decided WITHOUT writing.
    `entries` is the effect list as it will be when this effect is added
    (the pedal's list plus the effects planned before it).
    -> dict(path, base, zic_name, zd2, zic, notes, adapted, original_id)"""
    zd2, zic, notes = load_effect(path)
    base = pedal_name(path)
    stem = os.path.splitext(os.path.abspath(os.path.expanduser(path)))[0]
    own_icon = any(os.path.isfile(stem + ext) for ext in (".ZIC", ".zic"))
    original_id = zd2.id
    stock = stock_catalog(ident.get("folder"))
    if base.upper() in stock:
        raise Refused("%s is the file name of a stock effect of this pedal - "
                      "it will not be overwritten." % base)

    mode = getattr(opts, "adapt", "auto")
    want = getattr(opts, "category", None)
    if want is not None and (not table or mode == "never"):
        raise Refused("--category needs the pedal's categories, which come "
                      "from a backup of it (answer y to the backup question)"
                      ", and cannot be combined with --adapt never")
    used_ids = set(e[3] for e in entries if e[1].upper() != base.upper())
    installed = next((e for e in entries if e[1].upper() == base.upper()),
                     None)
    adapted = False
    cat = None
    if table and mode != "never":
        cat = choose_category(zd2, table, want, interactive, used_ids,
                              show_list,
                              table.get(installed[0]) if installed else None)
        if cat is None and interactive:
            raise Refused("skipped (no category chosen)")
    if cat is not None:
        diffs = adaptation_diffs(zd2, cat)
        wrong_group = zd2.group != cat["group"]
        wanted = mode == "always" or wrong_group or (
            mode == "auto" and ident["model"] == MODEL_MS60B)
        if diffs and wanted:
            say("Changes to match the stock effects of the category '%s' on "
                "this pedal:" % cat["label"])
            for d in diffs:
                say("    - " + d)
            zd2 = adapt_zd2(zd2, cat)
            adapted = True
    elif table and mode != "never" and zd2.group not in table:
        raise Refused("The category '%s' (group %d) does not exist on this "
                      "pedal. Choose an existing one with --category N: %s"
                      % (zd2.groupname, zd2.group, ", ".join(
                          "%d=%s" % (g, table[g]["label"])
                          for g in sorted(table))))
    elif table and mode != "never" and ident["model"] == MODEL_MS60B:
        notes.append("No category chosen - the file is installed UNCHANGED "
                     "and ends up in group %d ('%s')."
                     % (zd2.group, table[zd2.group]["label"]))

    groups = set(e[0] for e in entries)
    if zd2.group not in groups:
        raise Refused("Group %d does not exist in this pedal's effect list "
                      "(available: %s)." % (zd2.group, sorted(groups)))
    for group, name, _, eid, _ in entries:
        if eid == zd2.id and name.upper() != base.upper():
            raise Refused("The id 0x%08x is already used by %s - two effects "
                          "cannot share an id. Choose another category."
                          % (eid, name))
    if zd2.id in stock.values():
        raise Refused("The id 0x%08x belongs to a stock effect of this "
                      "model. Choose another category." % zd2.id)
    label = table.get(zd2.group, {}).get("label", "group %d" % zd2.group)
    return {"path": path, "base": base, "zic_name": base[:-4] + ".ZIC",
            "zd2": zd2, "zic": zic, "notes": notes, "adapted": adapted,
            "own_icon": own_icon, "original_id": original_id,
            "label": label}


def save_adapted(plans, ident):
    """Keep the adapted copies of what went onto the pedal, next to the
    licence files that came with them. Done after the transfer, so that the
    folder only ever holds files that are on the pedal."""
    out_dir = os.path.join(ADAPT_ROOT, ident.get("folder") or "unknown")
    for pl in plans:
        if not pl["adapted"]:
            continue
        try:
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, pl["base"]), "wb") as f:
                f.write(pl["zd2"].data)
            with open(os.path.join(out_dir, pl["zic_name"]), "wb") as f:
                f.write(pl["zic"])
            keep_notices(os.path.dirname(os.path.abspath(pl["path"])),
                         out_dir, pl["base"][:-4])
        except OSError as e:
            say("  Note: the adapted copy of %s could not be saved in %s "
                "(%s); the pedal is not affected." % (pl["base"], out_dir, e))


def zd2_files_in(folder):
    """The .ZD2 files in a folder and in its direct subfolders (a collection
    with one subfolder per effect). Deeper levels are not searched."""
    def listing(d):
        try:
            return sorted(n for n in os.listdir(d) if not n.startswith("."))
        except OSError:
            return []
    found = []
    for name in listing(folder):
        path = os.path.join(folder, name)
        if os.path.isdir(path):
            found += [os.path.join(path, n) for n in listing(path)
                      if n.upper().endswith(".ZD2")
                      and os.path.isfile(os.path.join(path, n))]
        elif name.upper().endswith(".ZD2"):
            found.append(path)
    return found


NOTICE_PREFIXES = ("LICENSE", "LICENCE", "NOTICE", "COPYING", "README")


def keep_notices(src_dir, out_dir, stem):
    """An adapted copy is still its author's effect: the licence, notice and
    readme files that came with it are copied next to the copy (as
    STEM.LICENSE.txt etc.), so the copyright notice stays with the file.
    Only done for the usual layout, a folder holding that one effect - in a
    folder of many, such a file cannot be attributed. -> names copied."""
    try:
        names = sorted(os.listdir(src_dir))
    except OSError:
        return []
    if sum(1 for n in names if n.upper().endswith(".ZD2")) != 1:
        return []
    copied = []
    for name in names:
        src = os.path.join(src_dir, name)
        if not name.upper().startswith(NOTICE_PREFIXES) \
                or not os.path.isfile(src) or os.path.getsize(src) > 512000:
            continue
        try:
            shutil.copyfile(src, os.path.join(out_dir,
                                              "%s.%s" % (stem, name)))
            copied.append(name)
        except OSError:
            pass
    return copied


def collect_effects(inputs):
    """Files and folders named by the user -> (paths, left_out). A folder
    stands for the .ZD2 files in it and in its direct subfolders. Each file is checked
    offline here; a file that fails the check, or whose name is already in
    the list, is left out with its reason."""
    paths, left_out, seen = [], [], set()
    for item in inputs:
        item = os.path.abspath(os.path.expanduser(item))
        if os.path.isdir(item):
            candidates = zd2_files_in(item)
            if not candidates:
                left_out.append("%s: the folder contains no .ZD2 files"
                                % os.path.basename(item))
        else:
            candidates = [item]
        for path in candidates:
            try:
                load_effect(path)
            except Refused as e:
                left_out.append(str(e))
                continue
            key = pedal_name(path).upper()
            if key in seen:
                left_out.append("%s: already in the list"
                                % os.path.basename(path))
                continue
            seen.add(key)
            paths.append(path)
    return paths, left_out


def same_on_pedal(pedal, name, data):
    """True if the pedal holds exactly `data` under `name` (read only)."""
    back = pedal.download(name)
    pedal.close()
    return back == bytes(data)


def icon_up_to_date(pedal, plan, names):
    """An effect that came with its own icon only counts as installed if the
    pedal has that icon too; one without keeps whatever icon it has."""
    if not plan["own_icon"]:
        return True
    on_pedal = next((n for n in names
                     if n.upper() == plan["zic_name"].upper()), None)
    return on_pedal is not None and same_on_pedal(pedal, on_pedal,
                                                  plan["zic"])


def confirm_autosave(pedal, opts):
    state = pedal.autosave_off()
    if state == 0:
        say("AUTO SAVE is off (confirmed by the pedal).")
        return
    if state == 1:
        raise Refused("The pedal reports AUTO SAVE = ON. Switch it off on "
                      "the pedal and try again.")
    say("AUTO SAVE could not be confirmed automatically.")
    if not ask_yes("Have you switched AUTO SAVE off on the pedal yourself?"):
        raise Refused("Nothing is installed without AUTO SAVE switched off.")


def cmd_install(opts, transport=None):
    """Install one or several effects in ONE transfer: every effect is
    planned first (category, adaptation, id check), then all files are
    uploaded and verified, and only then is the effect list rewritten - once
    for the whole batch. Until that last step the pedal is logically
    unchanged, so a failure on the way undoes the whole batch."""
    interactive = not opts.yes
    paths, left_out = collect_effects(opts.files)
    for reason in left_out:
        say("LEFT OUT: %s" % reason)
    if not paths:
        raise Refused("there is no effect to install")
    with Session(opts, transport) as s:
        p = s.pedal
        if s.ident["folder"] is None and not ask_yes(
                "This model is not known to the program. Continue anyway?",
                opts.yes):
            raise Refused("unknown model")
        confirm_autosave(p, opts)
        p.pcmode_on()
        flst = p.download(FLST_NAME)
        p.close()
        ok, problems, entries = flst_validate(flst, s.ident["model"])
        if not ok:
            raise Unresolved("The effect list on the pedal is not valid "
                             "(%s). Nothing was written - leave the pedal "
                             "POWERED and run the rescue."
                             % "; ".join(problems))
        backup_dir, names = ensure_backup(s, flst, opts)
        table = category_table(os.path.join(backup_dir, "files"),
                               stock_catalog(s.ident["folder"])) \
            if backup_dir else {}

        # ---- 1. plan every effect; nothing is written yet
        plans = []
        up_to_date = []
        planned = list(entries)       # the effect list as it will become
        for i, path in enumerate(paths, 1):
            say("")
            say("=== %d/%d  %s ===" % (i, len(paths), os.path.basename(path)))
            try:
                plan = plan_install(path, s.ident, planned, table, opts,
                                    interactive, show_list=(i == 1))
            except Refused as e:
                say("  LEFT OUT: %s" % e)
                left_out.append("%s: %s" % (os.path.basename(path), e))
                continue
            base, zd2 = plan["base"], plan["zd2"]
            plan["replaces"] = next(
                (e for e in entries if e[1].upper() == base.upper()), None)
            if plan["replaces"] and plan["replaces"][3] == zd2.id \
                    and same_on_pedal(p, plan["replaces"][1], zd2.data) \
                    and icon_up_to_date(p, plan, names):
                say("  ALREADY INSTALLED: %s is on the pedal in '%s', "
                    "identical - nothing to do." % (base, plan["label"]))
                up_to_date.append(base)
                continue
            planned = [e for e in planned if e[1].upper() != base.upper()]
            planned.append((zd2.group, base, zd2.version, zd2.id, 1))
            plans.append(plan)
        if not plans:
            if up_to_date and not left_out:
                say("")
                say("Nothing to transfer: %s already on the pedal."
                    % ("it is" if len(up_to_date) == 1
                       else "all %d effects are" % len(up_to_date)))
                return 0
            raise Refused("no effect is left to install")

        # ---- 2. both new effect lists, computed and validated up front
        replaced = [pl["replaces"] for pl in plans if pl["replaces"]]
        flst_without = flst
        for entry in replaced:
            flst_without = flst_remove(flst_without, entry[1])
        flst_new = flst_without
        for pl in plans:
            flst_new = flst_add(flst_new, pl["base"], pl["zd2"].version,
                                pl["zd2"].id)
        files = []
        for pl in plans:
            files += [(pl["zic_name"], pl["zic"]), (pl["base"], pl["zd2"].data)]
        old_files = []
        for entry in replaced:
            old_files += [entry[1], entry[1][:-4] + ".ZIC"]
        listed = set(e[1].upper() for e in entries)
        writing = set(n.upper() for n, _ in files)
        orphans = [n for n in names if n.upper() in writing
                   and os.path.splitext(n)[0].upper() + ".ZD2" not in listed]
        if replaced and not stock_catalog(s.ident["folder"]) \
                and not getattr(opts, "force", False):
            raise Refused("This model is not known to the program, so it "
                          "cannot tell whether %s %s stock effect%s; they are "
                          "not replaced (--force replaces them anyway)."
                          % (", ".join(e[1] for e in replaced),
                             "is a" if len(replaced) == 1 else "are",
                             "" if len(replaced) == 1 else "s"))
        needed = sum(allocated(len(d)) for _, d in files) + DISK_MARGIN
        _, available = p.disk_usage()
        freed = 0
        if available < needed and (replaced or orphans):
            # the old versions and the left-overs are deleted first
            going = set(n.upper() for n in old_files + orphans)
            freed = sum(allocated(size)
                        for name, size in p.list_sizes().items()
                        if name.upper() in going)
        if available + freed < needed:
            raise Refused("not enough free space on the pedal (%d KB free%s, "
                          "%d KB needed for %d effect(s))"
                          % (available // 1024,
                             ", %d KB more once the old versions are deleted"
                             % (freed // 1024) if freed else "",
                             needed // 1024, len(plans)))

        # ---- 3. summary and ONE question
        summary = []

        def out(line):
            summary.append(line)
            say(line)

        out("")
        out("Ready to transfer to the Zoom %s:" % s.ident["name"])
        for i, pl in enumerate(plans, 1):
            zd2 = pl["zd2"]
            remarks = []
            if pl["adapted"]:
                remarks.append("adapted copy")
            if pl["replaces"]:
                remarks.append("replaces the installed version")
                if pl["replaces"][3] != zd2.id:
                    remarks.append("saved patches using the old id 0x%08x "
                                   "lose it" % pl["replaces"][3])
            out("  %2d) %-12s %-11s v%s -> %2d %-12s id 0x%08x  %s"
                % (i, pl["base"], zd2.name, zd2.version, zd2.group,
                   pl["label"], zd2.id, ", ".join(remarks)))
            for note in pl["notes"]:
                out("        Note: " + note)
        if orphans:
            out("  Left over on the pedal from an earlier attempt, deleted "
                "first: %s" % ", ".join(orphans))
        if up_to_date:
            out("Already on the pedal, identical (not transferred): %s"
                % ", ".join(up_to_date))
        if left_out:
            out("Left out:")
            for reason in left_out:
                out("   - " + reason)
        if any(pl["adapted"] for pl in plans):
            out("Adapted copies are kept in %s (the originals stay "
                "unchanged)." % os.path.join(
                    ADAPT_ROOT, s.ident.get("folder") or "unknown"))
        out("Space: %d KB needed, %d KB free on the pedal%s."
            % (needed // 1024, available // 1024,
               " (%d KB more once the old versions are deleted)"
               % (freed // 1024) if freed else ""))
        what = "this effect" if len(plans) == 1 \
            else "these %d effects" % len(plans)
        if not ask_yes("Write %s to the Zoom %s now?"
                       % (what, s.ident["name"]), opts.yes, summary):
            out("Cancelled - nothing was written.")
            return 0

        # ---- 4. the transfer
        gone = ""
        with critical(s.ident):
            if replaced:
                uninstall_files(p, old_files, flst, flst_without)
                gone = (" - note: the previously installed %s had already "
                        "been removed and %s no longer on the pedal"
                        % (", ".join(e[1] for e in replaced),
                           "is" if len(replaced) == 1 else "are"))
            try:
                if orphans:
                    remove_orphans(p, orphans, flst_without)
                try:
                    _, available = p.disk_usage()
                except TransferError as e:
                    unwind(p, "checking the free space: %s" % e, [],
                           flst_without)
                if available < needed:
                    raise SafeAbort("not enough free space on the pedal "
                                    "(%d KB free, %d KB needed)"
                                    % (available // 1024, needed // 1024))
                retries = install_files(p, files, flst_without, flst_new)
            except SafeAbort as e:
                raise SafeAbort("%s%s" % (e, gone))
        say("  Effect list updated and verified%s."
            % (" (%d retry/retries)" % retries if retries else ""))
        save_adapted(plans, s.ident)
        say("")
        say("Installed %d effect(s): %s" % (len(plans), ", ".join(
            "%s (%s)" % (pl["zd2"].name, pl["label"]) for pl in plans)))
        say("Important:")
        say("  - Try each new effect in a patch first and do NOT save that "
            "patch until it is clear")
        say("    that the effect runs cleanly (AUTO SAVE stays off).")
        say("  - If an effect does not show up right away: switch the pedal "
            "off and on once.")
    return 0


def cmd_uninstall(opts, transport=None):
    """Remove one or several added effects in ONE go: every name is checked
    first, then the effect list is rewritten once, without all of them, and
    verified, and only then are their files deleted, one after the other.
    The list is the delicate part, so it is written once for the whole
    selection; a failure while deleting afterwards leaves at worst files
    behind that are in no list any more."""
    wanted = list(getattr(opts, "files", None) or [])
    if getattr(opts, "file", None):
        wanted.append(opts.file)
    with Session(opts, transport) as s:
        p = s.pedal
        stock = stock_catalog(s.ident.get("folder"))
        confirm_autosave(p, opts)
        p.pcmode_on()
        flst = p.download(FLST_NAME)
        p.close()
        ok, problems, entries = flst_validate(flst, s.ident["model"])
        if not ok:
            raise Unresolved("The effect list on the pedal is not valid "
                             "(%s). Nothing was written - leave the pedal "
                             "POWERED and run the rescue."
                             % "; ".join(problems))
        found = latest_backup(model=s.ident["model"])
        table = category_table(os.path.join(found[0], "files"), stock) \
            if found else {}

        def label(entry):
            return table.get(entry[0], {}).get("label", "group %d" % entry[0])

        added = [e for e in entries if stock and e[1].upper() not in stock]
        if not wanted:
            if not added:
                raise Refused("There are no added effects on this pedal.")
            picked = ui.choose_many(
                "Added effects on this pedal:",
                ["%-13s v%s  %s" % (e[1], e[2], label(e)) for e in added],
                "Which ones should be removed?")
            if not picked:
                say("Cancelled - nothing was changed.")
                return 0
            wanted = [added[i][1] for i in picked]

        # ---- 1. every name must be an added effect, or nothing is removed
        targets = []
        wrong = []
        for target in wanted:
            base = os.path.basename(target)
            match = [e for e in entries if e[1].upper() == base.upper()]
            if not match:
                wrong.append("%s is not in the effect list" % base)
            elif match[0][1].upper() in stock or (not stock
                                                  and not opts.force):
                wrong.append("%s is a stock effect (or the model is unknown)"
                             " - stock effects can NOT be brought back by a "
                             "factory reset" % match[0][1])
            elif match[0] not in targets:
                targets.append(match[0])
        if wrong:
            raise Refused("%s.%s" % ("; ".join(wrong), (
                " Added effects: %s." % ", ".join(e[1] for e in added)
                if added else "")))
        backup_dir, _ = ensure_backup(s, flst, opts)
        if backup_dir and not table:      # the first backup of this pedal
            table = category_table(os.path.join(backup_dir, "files"), stock)

        # ---- 2. the new effect list, computed and validated up front
        flst_new = flst
        files = []
        for entry in targets:
            flst_new = flst_remove(flst_new, entry[1])
            files.append(entry[1])
            # IR.ZIC / BPM_MDL.ZIC belong to the pedal, not to an effect that
            # another program named IR.ZD2
            if entry[1][:-4].upper() not in RESERVED_STEMS:
                files.append(entry[1][:-4] + ".ZIC")

        # ---- 3. summary and ONE question
        summary = []

        def out(line):
            summary.append(line)
            say(line)

        out("")
        out("Ready to remove from the Zoom %s:" % s.ident["name"])
        for i, entry in enumerate(targets, 1):
            out("  %2d) %-13s v%s  %-12s id 0x%08x"
                % (i, entry[1], entry[2], label(entry), entry[3]))
        out("Saved patches that use %s lose it."
            % ("this effect" if len(targets) == 1 else "one of these effects"))
        if len(targets) == 1:
            question = "Remove %s (and its icon)" % targets[0][1]
        else:
            question = "Remove these %d effects (and their icons)" \
                % len(targets)
        if not ask_yes("%s from the Zoom %s?" % (question, s.ident["name"]),
                       opts.yes, summary):
            out("Cancelled - nothing was changed.")
            return 0

        # ---- 4. the list once, then the files one after the other
        with critical(s.ident):
            uninstall_files(p, files, flst, flst_new)
        names = ", ".join(e[1] for e in targets)
        if len(targets) == 1:
            say("%s removed, effect list verified." % names)
        else:
            say("%d effects removed, effect list verified: %s"
                % (len(targets), names))
    return 0


def cmd_check(opts):
    """Check an effect file without a pedal and compare it with a backup."""
    zd2, zic, notes = load_effect(opts.file)
    say("The file is formally fine:")
    describe(zd2, os.path.basename(opts.file))
    for note in notes:
        say("  Note: " + note)
    found = latest_backup(folder=opts.model) if opts.model \
        else latest_backup()
    if not found:
        say("No backup to compare with (run 'Backup' first).")
        return 0
    path, man = found
    files_dir = os.path.join(path, "files")
    table = category_table(files_dir, stock_catalog(man.get("model_folder")))
    base = pedal_name(opts.file).upper()
    try:
        with open(os.path.join(files_dir, FLST_NAME), "rb") as f:
            used_ids = set(e[3] for e in flst_entries(f.read())
                           if e[1].upper() != base)
    except (OSError, ValueError):
        used_ids = set()
    say("")
    say("Categories of the %s (backup %s):"
        % (man["model"], os.path.basename(path)))
    default = default_category(zd2, table)
    print_categories(table, default, used_ids, zd2)
    if opts.category is not None and opts.category not in table:
        raise Refused("category %d does not exist on this pedal"
                      % opts.category)
    cat = table[opts.category] if opts.category is not None else default
    if cat is None:
        say("-> The category '%s' does not exist on this pedal; one has to "
            "be chosen when installing." % zd2.groupname)
        return 0
    diffs = adaptation_diffs(zd2, cat)
    if not diffs:
        say("-> The category fields match the stock effects.")
    else:
        say("-> Differences from the stock effects in '%s':" % cat["label"])
        for d in diffs:
            say("    - " + d)
    return 0


def restore_list(p, good, man, ident):
    """The backup's list `good`, fit for this pedal: entries whose file is
    gone, or is no longer the file of the backup, are left out. -> bytes,
    or None (and why) if it must not be written."""
    if man is not None and man.get("model_number") != ident["model"]:
        say("That backup is of a %s, this pedal is a %s - the list is not "
            "written. Leave the pedal POWERED and choose a backup of this "
            "pedal." % (man.get("model", "different model"), ident["name"]))
        return None
    try:
        on_pedal = {n.upper(): (n, size) for n, size in p.list_sizes().items()}
    except TransferError as e:
        p.abort()
        say("The files on the pedal cannot be listed (%s). Leave the pedal "
            "POWERED and run the rescue again." % e)
        return None
    listed = flst_entries(good)
    missing = [e[1] for e in listed if e[1].upper() not in on_pedal
               and e[1].upper() != "BPM_MDL.ZD2"]
    if len(missing) * 2 > len(listed):
        say("Most effects of that list (%d of %d) are not on this pedal, so "
            "it does not belong to it - it is not written. Leave the pedal "
            "POWERED and choose a backup of this pedal."
            % (len(missing), len(listed)))
        return None
    # A file replaced after the backup with another version or category
    # must not get its old entry back (id and group would no longer match
    # the file); a rebuild with the same header keeps it. Stock files of
    # unchanged size are not read.
    saved = dict((f.get("name", "").upper(), f)
                 for f in (man or {}).get("files", []))
    stock = stock_catalog(ident.get("folder"))
    changed = []
    for e in listed:
        name = e[1].upper()
        if name not in on_pedal or name in stock and on_pedal[name][1] \
                == saved.get(name, {}).get("bytes"):
            continue
        try:
            data = p.download(on_pedal[name][0])
            p.close()
        except TransferError as err:
            p.abort()
            say("%s cannot be read (%s). Leave the pedal POWERED and run "
                "the rescue again." % (e[1], err))
            return None
        try:
            z = ZD2(data)
            same = (z.group, z.version, z.id) == (e[0], e[2], e[3])
        except ValueError:
            same = False
        if not same:
            changed.append(e[1])
    for name in missing + changed:
        good = flst_remove(good, name)
    if missing:
        say("Left out of the restored list, because their files are no "
            "longer on the pedal: %s" % ", ".join(missing))
    if changed:
        say("Left out of the restored list, because their files were "
            "replaced after the backup (install them again): %s"
            % ", ".join(changed))
    return good


def cmd_rescue(opts, transport=None):
    """Close an open file session and check the effect list. With --flst
    and --write, a list from a backup of THIS pedal is written back; entries
    whose files are no longer on the pedal (or were replaced since) are left
    out of it. A list that is complete but cut short is filled up instead."""
    good = man = None
    if opts.flst:
        try:
            with open(opts.flst, "rb") as f:
                good = f.read()
        except OSError as e:
            raise Refused("cannot read %s: %s" % (opts.flst, e))
        ok, problems, _ = flst_validate(good)
        if not ok:
            raise Refused("The list you passed is itself not valid: %s"
                          % "; ".join(problems))
        # <backup>/files/FLST_SEQ.ZT2 -> <backup>/manifest.json
        try:
            with open(os.path.join(os.path.dirname(os.path.dirname(
                    os.path.abspath(opts.flst))), "manifest.json")) as f:
                man = json.load(f)
        except (OSError, ValueError):
            man = None
    if transport is None:
        transport = open_transport(getattr(opts, "pedal", None))
    p = Pedal(transport)
    keep_awake(True)
    try:
        try:
            p.pcmode_on()
        except TransferTimeout:
            say("PC mode not acknowledged - the close is sent anyway.")
        say("Closing any open file session ...")
        say("  %s" % ("acknowledged" if p.abort()
                      else "sent (no acknowledgement)"))
        # asked twice: one lost reply must not skip the length check below
        ident = p.identify() or p.identify()
        opts.ident = ident        # the window offers this model's backup
        if ident is None:
            say("UNRESOLVED: the pedal does not tell its model, so its effect "
                "list cannot be checked against its backup. Leave the pedal "
                "POWERED and run the rescue again.")
            return 4
        say("Pedal: Zoom %s, firmware %s" % (ident["name"], ident["firmware"]))
        try:
            flst = p.download(FLST_NAME)
            p.close()
        except TransferError as e:
            p.abort()             # never leave the read session open
            say("The effect list cannot be read (%s). Leave the pedal "
                "POWERED and run the rescue again." % e)
            return 4
        ok, problems, entries = flst_validate(flst, ident["model"])
        if ok:
            say("The effect list is VALID (%d effects). The pedal can be "
                "restarted." % len(entries))
            p.pcmode_off()
            return 0
        say("The effect list on the pedal is NOT valid: %s"
            % "; ".join(problems))
        # Cut off at a block boundary past the grammar: the list itself is
        # complete, filling it up gives exactly the list that was written.
        size = backup_flst_size(ident["model"])
        short = flst_validate(flst)[0] and size and len(flst) < size
        if short:
            say("The list itself is complete, only the zeros at its end are "
                "missing. Restoring fills it up to %d bytes - nothing else "
                "is changed." % size)
        if good is None or not opts.write:
            say(getattr(ui, "restore_hint", None) or
                "Leave the pedal POWERED and run again with\n"
                "  rescue --flst <backup>/files/FLST_SEQ.ZT2 --write\n"
                "to restore the effect list.")
            return 4
        if short:
            new = flst + bytes(size - len(flst))
            done = ("The effect list is filled up to %d bytes and verified - "
                    "nothing else was changed." % size)
        else:
            new = restore_list(p, good, man, ident)
            if new is None:
                return 4
            if size and len(new) < size:      # an older backup's shorter list
                new += bytes(size - len(new))
            done = "Effect list written back from the backup and verified."
        with critical(ident):
            try:
                replace_flst(p, flst, new)
            except TransferError as e:
                p.abort()         # never leave a file session open
                say("The effect list could not be written (%s). It is still "
                    "NOT valid - leave the pedal POWERED and run the rescue "
                    "again." % e)
                return 4
        say(done)
        p.pcmode_off()
        return 0
    finally:
        keep_awake(False)
        close = getattr(transport, "close", None)
        if close:
            close()


# ------------------------------------------------------------------ menu

def clean_path(text):
    """Paths from typed input (also dragged into the terminal)."""
    text = text.strip()
    try:
        if os.name == "nt":       # backslashes are path separators there
            parts = [p.strip('"') for p in shlex.split(text, posix=False)]
        else:
            parts = shlex.split(text)
    except ValueError:
        parts = [text]
    return [os.path.expanduser(p) for p in parts if p]


def build_queue():
    """Collect the effects to install, all at once or bit by bit. Each file
    is checked as it is added. -> list of paths (empty = cancelled)."""
    queue = []
    say("Add the effects to install: drag one or more .ZD2 files (or a folder "
        "of them) here")
    say("and press Enter. Repeat as often as you like.")
    say("  Enter on an empty line = list complete, start      c = cancel")
    say("  r 2 = take number 2 out of the list (also r 1 3, r 2-4, r a = all)")
    while True:
        try:
            text = input("add> ").strip()
        except EOFError:
            return queue
        log("? add> %s" % text)
        low = text.lower()
        if not text:
            if queue:
                return queue
            say("  The list is still empty (c = cancel).")
            continue
        if low == "c":
            return []
        numbers = low[2:].strip() if low.startswith("r ") else ""
        if numbers and (numbers in ("a", "all")
                        or all(c.isdecimal() or c in " ,-" for c in numbers)):
            picked = parse_selection(numbers, len(queue)) if queue else None
            if picked:
                gone = [queue[i] for i in picked]
                for i in reversed(picked):
                    queue.pop(i)
                say("  removed: %s" % ", ".join(os.path.basename(p)
                                                for p in gone))
            elif not queue:
                say("  The list is empty.")
            else:
                say("  Use numbers from 1 to %d (r 2, r 1 3, r 2-4 or r a)."
                    % len(queue))
        else:
            have = set(pedal_name(q).upper() for q in queue)
            paths, left_out = collect_effects(clean_path(text))
            for path in paths:
                if pedal_name(path).upper() in have:
                    left_out.append("%s: already in the list"
                                    % os.path.basename(path))
                else:
                    have.add(pedal_name(path).upper())
                    queue.append(path)
            for reason in left_out:
                say("  NOT ADDED: %s" % reason)
        if queue:
            say("  Effects to install (%d):" % len(queue))
            for i, path in enumerate(queue, 1):
                try:
                    zd2, _, notes = load_effect(path)
                except Refused as e:    # moved or changed in the meantime
                    say("    %2d) %-12s PROBLEM: %s (r %d takes it out)"
                        % (i, pedal_name(path), e, i))
                    continue
                say("    %2d) %-12s %-11s v%s  %s%s"
                    % (i, pedal_name(path), zd2.name, zd2.version,
                       zd2.groupname, "  (no icon file)" if notes else ""))


def menu():
    say("")
    say("Zoom MS Installer")
    say("=================")
    while True:
        say("")
        say("  1) Show pedal info and effect list")
        say("  2) Make a backup")
        say("  3) Install effects (one or several in one transfer)")
        say("  4) Remove added effects (one or several)")
        say("  5) Check an effect file (no pedal needed)")
        say("  6) Rescue: close the session and check the effect list")
        say("  0) Quit")
        try:
            choice = input("Choice: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        ns = argparse.Namespace(yes=False, pedal=None, new_backup=False,
                                adapt="auto", category=None, force=False,
                                model=None, flst=None, write=False)
        try:
            if choice == "0":
                return 0
            elif choice == "1":
                run(cmd_info, ns)
            elif choice == "2":
                run(cmd_backup, ns)
            elif choice == "3":
                ns.files = build_queue()
                if ns.files:
                    run(cmd_install, ns)
                else:
                    say("Cancelled - nothing was changed.")
            elif choice == "4":
                ns.files = []
                run(cmd_uninstall, ns)
            elif choice == "5":
                paths = clean_path(input("Drag the ZD2 file here and press "
                                         "Enter:\n> "))
                if paths:
                    ns.file = paths[0]
                    run(cmd_check, ns)
            elif choice == "6":
                run(cmd_rescue, ns)
        except (KeyboardInterrupt, EOFError):
            say("\n(cancelled)")


def run(func, opts):
    """Run a command and turn failures into clear messages.
    Returns: 0 ok, 2 refused, 3 safe abort, 4 unresolved."""
    try:
        return func(opts) or 0
    except Refused as e:
        say("REFUSED (nothing was changed on the pedal): %s" % e)
        return 2
    except SafeAbort as e:
        say("NOT COMPLETED, but the pedal is in a verified safe state: %s"
            % e)
        return 3
    except Unresolved as e:
        say("UNRESOLVED: %s" % e)
        return 4
    except TransferError as e:
        say("TRANSFER ERROR (no write was in progress): %s" % e)
        return 3
    except KeyboardInterrupt:
        raise
    except Exception as e:
        import traceback
        log(traceback.format_exc())
        say("UNEXPECTED ERROR: %s: %s" % (type(e).__name__, e))
        say("If this happened while writing to the pedal: leave the pedal "
            "POWERED and run 'Rescue' (menu item 6) before restarting it. "
            "Details are in the newest file in Logs/.")
        return 4


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="zoom_ms_installer",
        description="Safely load effects onto Zoom MS Plus pedals.")
    parser.add_argument("--pedal", type=lambda v: int(v) - 1, default=None,
                        help="number of the pedal if several are connected")
    parser.add_argument("--data", metavar="FOLDER",
                        help="keep Backups, Adapted and Logs in FOLDER "
                        "instead of next to the program")
    parser.add_argument("--yes", action="store_true",
                        help="answer all questions with yes (the category "
                        "is then the effect's own, or --category)")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("info", help="show the pedal and its effect list")
    sub.add_parser("backup", help="make a complete backup")

    c = sub.add_parser("check", help="check an effect file without a pedal")
    c.add_argument("file")
    c.add_argument("--model", help="compare with the backup of this model "
                   "(ms-50gp, ms-60bp, ms-70cdrp)")
    c.add_argument("--category", type=int)

    i = sub.add_parser("install", help="install one or several effects in "
                       "one transfer")
    i.add_argument("files", nargs="+",
                   help=".ZD2 files, or folders containing them")
    i.add_argument("--category", type=int,
                   help="group number on the pedal to put the effect(s) into")
    i.add_argument("--adapt", choices=("auto", "always", "never"),
                   default="auto", help="adapt the category fields to the "
                   "pedal (auto: on the MS-60B+ and when the group changes)")
    i.add_argument("--new-backup", action="store_true", dest="new_backup")
    i.add_argument("--force", action="store_true",
                   help="on an unknown model, also replace effects that are "
                   "already on the pedal")

    u = sub.add_parser("remove", help="remove one or several added effects")
    u.add_argument("files", nargs="*",
                   help="their file names on the pedal (none: pick from a "
                   "list)")
    u.add_argument("--force", action="store_true",
                   help="also remove on an unknown model")
    u.add_argument("--new-backup", action="store_true", dest="new_backup")

    r = sub.add_parser("rescue", help="close the session, check the list")
    r.add_argument("--flst", help="known-good FLST_SEQ.ZT2 from a backup")
    r.add_argument("--write", action="store_true",
                   help="write that list back if necessary")

    opts = parser.parse_args(argv)
    if opts.data:
        set_data_root(os.path.abspath(os.path.expanduser(opts.data)))
    open_log(opts.command or "menu")
    exit_cleanly_on_hangup()
    if not opts.command:
        return menu()
    func = {"info": cmd_info, "backup": cmd_backup, "check": cmd_check,
            "install": cmd_install, "remove": cmd_uninstall,
            "rescue": cmd_rescue}[opts.command]
    try:
        return run(func, opts)
    except KeyboardInterrupt:
        print("\n(cancelled)")
        return 130


if __name__ == "__main__":
    sys.exit(main())
