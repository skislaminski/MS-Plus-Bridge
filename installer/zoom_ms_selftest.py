#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for zoom_ms_installer.py - runs WITHOUT a pedal.

A simulated pedal (MockPedal) speaks the same file API. It is used to check
backup, install, remove and above all the FAILURE PATHS (lost replies, files
stored wrongly, an effect list that will not write): what the program does
when a transfer fails must never show itself for the first time on a real
pedal.

    .venv/bin/python zoom_ms_selftest.py
"""

import argparse
import collections
import hashlib
import os
import shutil
import struct
import sys
import tempfile
import zlib

import zoom_ms_installer as m

HERE = os.path.dirname(os.path.abspath(__file__))


# ------------------------------------------------------- build test data

def make_zd2(name, eid, groupname, target=0xA0, tag=b"", color=b"\x00\x00\x00",
             version=b"1.10", code=b"\x7fELF" + bytes(range(256)) * 3):
    def sec(tag4, payload):
        return tag4 + struct.pack("<I", len(payload)) + payload
    body = sec(b"ICON", b"\x00" * 32) + sec(b"TXJ1", b"") \
        + sec(b"TXE1", b"Test effect") \
        + sec(b"INFO", b"\x00" * 4 + tag.ljust(12, b"\x00")
              + struct.pack("<f", 10.0)) \
        + sec(b"DATA", code) + sec(b"PRMJ", b"{}") + sec(b"PRME", b"{}")
    head = bytearray(128)
    head[0:4] = b"ZDLF"
    struct.pack_into("<I", head, 4, 120)
    struct.pack_into("<I", head, 12, target)
    head[89:93] = version
    head[95] = (eid >> 24) & 0xFF
    struct.pack_into("<I", head, 96, eid)
    head[100:100 + len(name)] = name
    head[111:111 + len(groupname)] = groupname
    head[122:125] = color
    data = bytearray(bytes(head) + body + b"\x00" * 16)
    struct.pack_into("<I", data, 8,
                     zlib.crc32(bytes(data[12:len(data) - 16])) ^ 0xFFFFFFFF)
    return bytes(data)


def make_flst(entries, size=12324):
    """entries = [(file, version, id)] -> FLST_SEQ.ZT2 at its native size."""
    import zoomzt2
    with open(os.path.join(HERE, "EMPTY.ZT2"), "rb") as f:
        config = zoomzt2.ZT2.parse(f.read())
    del config[1][:]
    groups = collections.OrderedDict()
    for fn, ver, eid in entries:
        groups.setdefault(eid >> 24, []).append(
            dict(effect=fn, version=ver, id=eid, installed=1))
    for g, effects in groups.items():
        config[1].append(dict(group=g, groupname=g, effects=effects))
    built = zoomzt2.ZT2.build(config)
    return built + b"\x00" * (size - len(built))


# MS-60B+-like categories: (group, name, tag, colour)
CATS_60B = [(1, b"DYNAMICS", b"DYNDYNMCS", b"\x3d\x25\x8e"),
            (7, b"PITCH SHIFT", b"PTCPITCH", b"\xff\xe5\x00"),
            (9, b"SFX", b"SFXSFX", b"\xff\xe5\x00"),
            (10, b"DELAY", b"DLYDELAY", b"\xb2\xdb\x11")]


def stock_fs():
    """A small 'MS-60B+': two stock effects per category."""
    with open(os.path.join(HERE, "BLANK.ZIC"), "rb") as f:
        zic = f.read()
    fs = collections.OrderedDict()
    entries = []
    for g, gname, tag, color in CATS_60B:
        for n in (1, 2):
            # one of them has a real MS-60B+ stock file name
            fn = "DCOMP.ZD2" if (g, n) == (1, 1) else "ST%d_%d.ZD2" % (g, n)
            eid = (g << 24) | (n * 0x10)
            fs[fn] = make_zd2(b"Stock%d%d" % (g, n), eid, gname, 0xA0, tag,
                              color)
            fs[fn[:-4] + ".ZIC"] = zic
            entries.append((fn, "1.10", eid))
    fs["DUMMY.WAV"] = b""
    fs[m.FLST_NAME] = make_flst(entries)
    return fs


# The mock's made-up stock effects count as stock, like the real ones listed
# in zoom_fx_ms-plus/<model>/effects_sort.txt.
_real_stock_catalog = m.stock_catalog


def _stock_catalog(folder):
    out = _real_stock_catalog(folder)
    if out:
        for g, _, _, _ in CATS_60B:
            for n in (1, 2):
                out.setdefault("ST%d_%d.ZD2" % (g, n), (g << 24) | (n * 0x10))
    return out


m.stock_catalog = _stock_catalog


# ---------------------------------------------------------- mock pedal

class MockPedal(object):
    """Transport stand-in: send() handles a command and queues the reply,
    recv() fetches it (or None = timeout)."""

    def __init__(self, fs, model=0x27, firmware=b"1.20"):
        self.fs = fs
        self.model = model
        self.firmware = firmware
        self.out = collections.deque()
        self.rd = None            # [name, offset]
        self.rd_pending = False
        self.wr = None
        self.check_ok = True
        self.wild = None
        self.sends = 0
        self.session_open = False
        self.autosave_reply = 0
        self.free = 32 << 20      # free bytes reported by the pedal
        # fault injection
        self.silent_from = None   # no replies from this send() on
        self.silent_count = 0     # ... for this many commands (0 = forever)
        self.corrupt = {}         # name -> how often to store it wrong
        self.keep = set()         # names a delete leaves in place
        self.capacity = None      # if set, free space follows the contents
        self.repeat_listing = False   # the listing repeats its first entry
        self.wrote = []           # names in the order written

    # -- transport interface
    def recv(self, timeout):
        return bytes(self.out.popleft()) if self.out else None

    def flush(self):
        n = len(self.out)
        self.out.clear()
        return n

    def close(self):
        pass

    def _reply(self, payload):
        if self.silent_from is not None and self.sends >= self.silent_from:
            if not self.silent_count or \
                    self.sends < self.silent_from + self.silent_count:
                return
        self.out.append(bytes(payload))

    def _ack(self, sub):
        self._reply([0x52, 0x00, 0x6E, 0x60, 0x00, sub, 0, 0, 0, 0,
                     0, 0, 0, 0, 0])

    def send(self, payload):
        p = list(payload)
        self.sends += 1
        if p[:4] == [0x7E, 0x00, 0x06, 0x01]:
            self._reply([0x7E, 0x00, 0x06, 0x02, 0x52, 0x6E, 0x00,
                         self.model, 0x00] + list(self.firmware))
            return
        if p[:3] != [0x52, 0x00, 0x6E]:
            return
        cmd = p[3]
        if cmd in (0x50, 0x51):
            self._reply([0x52, 0x00, 0x6E, 0x00, 0x00])
        elif cmd in (0x52, 0x53):
            self._reply([0x52, 0x00, 0x6E, 0x00, 0x00])
        elif cmd == 0x64 and p[4:8] == [0x20, 0x00, 0x64, 0x0F]:
            if self.autosave_reply is not None:
                self._reply([0x52, 0x00, 0x6E, 0x64, 0x20, 0x01, 0x64, 0x0F,
                             self.autosave_reply, 0, 0, 0, 0])
        elif cmd == 0x44:
            self._reply([0x52, 0x00, 0x6E, 0x43, 3, 0, 8, 0, 0, 0, 3, 0])
        elif cmd == 0x46:
            data = b"PTCF" + bytes([p[8]]) * 4
            self._reply([0x52, 0x00, 0x6E, 0x45, 0, 0, 0, 0, p[8], 0,
                         len(data), 0] + list(m.pack7(data))
                        + m.crc_tail(data))
        elif cmd == 0x60:
            self._file_api(p)

    def _name(self, p, start):
        return bytes(p[start:p.index(0, start)]).decode("ascii")

    def _file_api(self, p):
        sub = p[4]
        if sub in (0x25, 0x26):
            name = self._name(p, 7)
            self.session_open = True
            if name == "*":
                if sub == 0x25:
                    self.wild = list(self.fs)
                if self.wild:
                    fn = self.wild[0] if self.repeat_listing \
                        else self.wild.pop(0)
                    size = len(self.fs.get(fn, b""))
                    # the layout the real pedal sends (39 bytes): head, name
                    # in 13 bytes, 2 zero bytes, size in five 7-bit bytes
                    self._reply([0x52, 0x00, 0x6E, 0x60, 0x04, sub,
                                 0, 0, 0x17, 0, 0, 0, 0, 0]
                                + list(fn.encode("ascii").ljust(13, b"\x00"))
                                + [0, 0]
                                + [(size >> (7 * i)) & 0x7F for i in range(5)]
                                + [0] * 5)
                else:
                    self._ack(sub)
            else:
                self.check_ok = name in self.fs
                self._ack(sub)
        elif sub == 0x27:
            self._ack(sub)
        elif sub == 0x05:
            if self.rd_pending:
                self.rd_pending = False
                name, off = self.rd if self.rd else (None, 0)
                chunk = self.fs.get(name, b"")[off:off + 512]
                if self.rd:
                    self.rd[1] += len(chunk)
                self._reply([0x52, 0x00, 0x6E, 0x60, 0x04, 0x22, 0, 0,
                             len(chunk) & 0x7F, len(chunk) >> 7]
                            + list(m.pack7(chunk)) + m.crc_tail(chunk))
            else:
                status = [0, 0, 0, 0, 0] if self.check_ok else [1, 0, 0, 0, 0]
                self._reply([0x52, 0x00, 0x6E, 0x60, 0x04, 0x05, 0, 0, 0, 0]
                            + status)
                self.check_ok = True
        elif sub == 0x20:
            name = self._name(p, 15)
            self.session_open = True
            if p[5] == 0x02:
                self.rd = [name, 0] if name in self.fs else None
            else:
                self.wr = name
                self.fs[name] = b""
                self.wrote.append(name)
            self._ack(sub)
        elif sub == 0x22:
            self.rd_pending = True
            self._ack(sub)
        elif sub == 0x23:
            length = p[10] | (p[11] << 7)
            chunk = bytes(m.unpack7(p[15:-5]))[:length]
            assert m.crc_tail(chunk) == p[-5:], "mock: block CRC wrong"
            if self.wr is not None:
                if self.corrupt.get(self.wr, 0) > 0 and not self.fs[self.wr]:
                    self.corrupt[self.wr] -= 1
                    chunk = bytes([chunk[0] ^ 0xFF]) + chunk[1:]
                self.fs[self.wr] += chunk
            self._ack(sub)
        elif sub == 0x24:
            if self._name(p, 5) not in self.keep:
                self.fs.pop(self._name(p, 5), None)
            self.session_open = True
            self._ack(sub)
        elif sub == 0x21:
            self.rd = self.wr = None
            self.rd_pending = False
            self._ack(sub)
        elif sub == 0x09:
            self.session_open = False
            self._ack(sub)
        elif sub == 0x29:
            def five(v):
                return [(v >> (7 * i)) & 0x7F for i in range(5)]
            self._reply([0x52, 0x00, 0x6E, 0x60, 0x04, 0x29, 0, 0, 0, 0]
                        + five(64 << 20) + five(
                            self.free if self.capacity is None else
                            self.capacity - sum(m.allocated(len(v))
                                                for v in self.fs.values())))


# ------------------------------------------------------------- the tests

RESULTS = []


def check(label, cond, detail=""):
    RESULTS.append(bool(cond))
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", label,
                           "  (%s)" % (detail,) if detail and not cond
                           else ""))


def opts(**kw):
    base = dict(yes=True, pedal=None, new_backup=False, adapt="auto",
                category=None, force=False, model=None, flst=None,
                write=False)
    base.update(kw)
    return argparse.Namespace(**base)


def quiet(func, *args):
    """Run a command the way the main program does."""
    return m.run(lambda o: func(o, *args[1:]), args[0])


def fresh(tmp, **kw):
    """New mock pedal + empty working folders."""
    for d in ("Backups", "Adapted"):
        shutil.rmtree(os.path.join(tmp, d), ignore_errors=True)
    m.BACKUP_ROOT = os.path.join(tmp, "Backups")
    m.ADAPT_ROOT = os.path.join(tmp, "Adapted")
    m.LOG_ROOT = os.path.join(tmp, "Logs")
    return MockPedal(stock_fs(), **kw)


def write_effect(tmp, name="MYFX.ZD2", eid=0x07000F42, groupname=b"SFX",
                 with_icon=True, title=b"MyFx"):
    """A 'DIY effect' as built for the MS-50G+/70CDR+: group 7 = SFX,
    target 0x90, no category tag."""
    path = os.path.join(tmp, name)
    with open(path, "wb") as f:
        f.write(make_zd2(title, eid, groupname, target=0x90))
    if with_icon:
        shutil.copy(os.path.join(HERE, "BLANK.ZIC"), path[:-4] + ".ZIC")
    return path


def run_tests():
    tmp = tempfile.mkdtemp(prefix="zoomtest-")
    m.RECV_TIMEOUT = 0.05
    m.ABORT_TIMEOUT = 0.05
    m.WAIT_SCALE = 0.01
    sys.stdin = open(os.devnull)      # questions in the test = "no"
    try:
        print("1. Codecs")
        for n in (0, 1, 6, 7, 8, 511, 512):
            data = bytes((i * 37 + 200) & 0xFF for i in range(n))
            check("pack7/unpack7 %d bytes" % n,
                  bytes(m.unpack7(m.pack7(data)))[:n] == data)

        print("1b. Shipped files survive the program folder being moved")
        # What happened on 2026-10-08: the app's folder was moved while the
        # app was open, then an effect without an icon was chosen.
        here, stock_root = m.HERE, m.STOCK_ROOT
        gone = os.path.join(tmp, "moved-away")
        bare_dir = os.path.join(tmp, "bare")
        os.makedirs(bare_dir, exist_ok=True)
        bare = write_effect(bare_dir, with_icon=False)
        with open(os.path.join(HERE, "BLANK.ZIC"), "rb") as f:
            blank = f.read()
        want_stock = _real_stock_catalog("ms-60bp")
        try:
            m.HERE, m.STOCK_ROOT = gone, os.path.join(gone, "zoom_fx_ms-plus")
            try:
                _, zic, notes = m.load_effect(bare)
            except Exception as e:
                zic, notes = None, [repr(e)]
            check("effect without icon still gets the blank icon",
                  zic == blank and any("blank" in n for n in notes), notes)
            check("stock list still known (%d effects)" % len(want_stock),
                  want_stock and _real_stock_catalog("ms-60bp") == want_stock)
            kept, m._BLANK_ICON = m._BLANK_ICON, None
            try:
                m.load_effect(bare)
                refused = False
            except m.Refused:
                refused = True
            except Exception:
                refused = False
            finally:
                m._BLANK_ICON = kept
            check("blank icon unreadable: refused cleanly, no crash", refused)
        finally:
            m.HERE, m.STOCK_ROOT = here, stock_root

        print("2. Backup")
        mock = fresh(tmp)
        before = dict(mock.fs)
        check("backup completes", quiet(m.cmd_backup, opts(), mock) == 0)
        path, man = m.latest_backup(model=0x27)
        same = all(open(os.path.join(path, "files", n), "rb").read() == d
                   for n, d in before.items())
        check("all %d files saved byte-identical" % len(before),
              same and len(man["files"]) == len(before))
        check("patches saved", len(man["patches"]) == 3)
        check("pedal unchanged, session closed",
              mock.fs == before and not mock.session_open and not mock.wrote)

        print("3. Install on 'MS-60B+' with adaptation")
        mock = fresh(tmp)
        fx = write_effect(tmp)
        original = open(fx, "rb").read()
        check("install reports success",
              quiet(m.cmd_install, opts(files=[fx]), mock) == 0)
        on_pedal = m.ZD2(mock.fs["MYFX.ZD2"])
        check("effect is in group 9 (SFX on the MS-60B+), id 0x09000f42",
              on_pedal.group == 9 and on_pedal.id == 0x09000F42,
              hex(on_pedal.id))
        check("tag, colour, target like the stock effects",
              on_pedal.tag_text() == "SFXSFX" and on_pedal.target == 0xA0
              and on_pedal.color == b"\xff\xe5\x00")
        check("adapted file passes the envelope check",
              not m.zd2_problems(on_pedal))
        o = m.ZD2(original)
        code = lambda z: z.data[z.sections["DATA"][0]:
                                z.sections["DATA"][0] + z.sections["DATA"][1]]
        check("effect code (DATA) unchanged", code(o) == code(on_pedal))
        check("original file on the Mac unchanged",
              open(fx, "rb").read() == original)
        entries = m.flst_entries(mock.fs[m.FLST_NAME])
        check("effect list: exactly one more entry, in group 9",
              len(entries) == 9
              and (9, "MYFX.ZD2", "1.10", 0x09000F42, 1) in entries)
        check("effect list keeps its size",
              len(mock.fs[m.FLST_NAME]) == 12324)
        check("order: icon, effect, the list last",
              mock.wrote == ["MYFX.ZIC", "MYFX.ZD2", m.FLST_NAME], mock.wrote)
        check("backup was made BEFORE writing",
              "MYFX.ZD2" not in os.listdir(os.path.join(
                  m.latest_backup(model=0x27)[0], "files")))

        mock = fresh(tmp)
        check("install with --category 10 reports success",
              quiet(m.cmd_install, opts(files=[fx], category=10), mock) == 0)
        z = m.ZD2(mock.fs["MYFX.ZD2"])
        check("  effect is in group 10 (DELAY) with DELAY's tag and name",
              z.id == 0x0A000F42 and z.groupname == "DELAY"
              and z.tag_text() == "DLYDELAY"
              and (10, "MYFX.ZD2", "1.10", 0x0A000F42, 1)
              in m.flst_entries(mock.fs[m.FLST_NAME]), hex(z.id))
        mock = fresh(tmp)
        check("unknown category number is refused, nothing written",
              quiet(m.cmd_install, opts(files=[fx], category=5), mock) == 2
              and not mock.wrote)

        # the licence that comes with an effect stays with its adapted copy
        solo = os.path.join(tmp, "solo")
        os.makedirs(solo)
        fs_ = write_effect(solo, "SOLO.ZD2", 0x07000F60, b"SFX", title=b"Solo")
        for name, text in (("LICENSE.txt", "Copyright (c) the author"),
                           ("NOTICES.txt", "notices"), ("other.txt", "x")):
            with open(os.path.join(solo, name), "w") as f:
                f.write(text)
        mock = fresh(tmp)
        quiet(m.cmd_install, opts(files=[fs_, fx]), mock)
        kept = sorted(os.listdir(os.path.join(m.ADAPT_ROOT, "ms-60bp")))
        check("licence and notice files are copied next to the adapted copy",
              "SOLO.LICENSE.txt" in kept and "SOLO.NOTICES.txt" in kept
              and "SOLO.other.txt" not in kept
              and open(os.path.join(m.ADAPT_ROOT, "ms-60bp",
                                    "SOLO.LICENSE.txt")).read()
              == "Copyright (c) the author", kept)
        check("  none are guessed for an effect from a folder of several",
              not [n for n in kept if n.startswith("MYFX.") and
                   not n.endswith((".ZD2", ".ZIC"))], kept)
        shutil.rmtree(solo)

        print("4. Install on 'MS-50G+' (no adaptation)")
        # An effect whose group fits the pedal: the file stays untouched.
        mock = fresh(tmp, model=0x23)
        fx9 = write_effect(tmp, "FX9.ZD2", eid=0x09000F42)
        check("install reports success",
              quiet(m.cmd_install, opts(files=[fx9]), mock) == 0)
        check("file byte-identical to the original",
              mock.fs["FX9.ZD2"] == open(fx9, "rb").read())

        print("4b. Questions, declined backup, replacing, full pedal")
        import io
        for text, want in (("y\n", True), ("n\n", False), ("yes\n", True),
                           ("maybe\ny\n", True), ("", False)):
            sys.stdin = io.StringIO(text)
            check("answer %r -> %s" % (text, want),
                  quiet_silent_call(lambda: m.ask_yes("q")) is want)
        # backup declined (n), then write confirmed (y): installs, no backup
        mock = fresh(tmp, model=0x23)
        sys.stdin = io.StringIO("n\ny\n")
        check("backup declined -> install still possible, unchanged file",
              quiet(m.cmd_install, opts(files=[fx9], yes=False), mock) == 0
              and mock.fs.get("FX9.ZD2") == open(fx9, "rb").read())
        check("  and no backup folder was created",
              not os.path.isdir(m.BACKUP_ROOT) or not os.listdir(m.BACKUP_ROOT))
        # everything declined: nothing written
        mock = fresh(tmp, model=0x23)
        sys.stdin = io.StringIO("n\nn\n")
        check("backup and write declined -> nothing written",
              quiet(m.cmd_install, opts(files=[fx9], yes=False), mock) == 0
              and not mock.wrote)
        sys.stdin = open(os.devnull)
        # installing the same effect again into another category replaces it
        mock = fresh(tmp)
        quiet(m.cmd_install, opts(files=[fx]), mock)
        check("re-install into category 10 reports success",
              quiet(m.cmd_install, opts(files=[fx], category=10), mock) == 0)
        ents = [e for e in m.flst_entries(mock.fs[m.FLST_NAME])
                if e[1] == "MYFX.ZD2"]
        check("  exactly one entry afterwards, in group 10",
              ents == [(10, "MYFX.ZD2", "1.10", 0x0A000F42, 1)]
              and list(mock.fs).count("MYFX.ZD2") == 1, ents)
        # no room on the pedal
        mock = fresh(tmp)
        mock.free = 1000
        check("full pedal -> refused before writing",
              quiet(m.cmd_install, opts(files=[fx]), mock) == 2
              and "MYFX.ZD2" not in mock.fs and not mock.wrote)
        # a file in a layout the program does not know is still backed up
        mock = fresh(tmp)
        mock.fs["ODD.ZD2"] = b"ZDLF" + bytes(range(200))
        check("unusual file is backed up after reading it twice",
              quiet(m.cmd_backup, opts(), mock) == 0
              and m.latest_backup(model=0x27)[1]["unusual_files"]
              == ["ODD.ZD2"])

        print("4c. Several effects in one transfer")
        batch_dir = os.path.join(tmp, "batch")
        os.makedirs(batch_dir)
        fa = write_effect(batch_dir, "FXA.ZD2", 0x07000F42, b"SFX",
                          title=b"FxA")
        fb = write_effect(batch_dir, "FXB.ZD2", 0x01000F43, b"DYNAMICS",
                          title=b"FxB")
        fc = write_effect(batch_dir, "FXC.ZD2", 0x08000F44, b"DELAY",
                          title=b"FxC")
        mock = fresh(tmp)
        check("three effects: install reports success",
              quiet(m.cmd_install, opts(files=[fa, fb, fc]), mock) == 0)
        got = sorted(e for e in m.flst_entries(mock.fs[m.FLST_NAME])
                     if e[1].startswith("FX"))
        check("  all three are in the list, each in its own category",
              got == [(1, "FXB.ZD2", "1.10", 0x01000F43, 1),
                      (9, "FXA.ZD2", "1.10", 0x09000F42, 1),
                      (10, "FXC.ZD2", "1.10", 0x0A000F44, 1)], got)
        check("  all files first, the effect list written ONCE and last",
              mock.wrote == ["FXA.ZIC", "FXA.ZD2", "FXB.ZIC", "FXB.ZD2",
                             "FXC.ZIC", "FXC.ZD2", m.FLST_NAME], mock.wrote)
        three = dict(mock.fs)
        mock = fresh(tmp)
        check("a folder stands for the .ZD2 files in it",
              quiet(m.cmd_install, opts(files=[batch_dir]), mock) == 0
              and mock.fs == three)
        # one bad file in the batch: left out, the others go through
        bad2 = os.path.join(batch_dir, "BROKEN.ZD2")
        raw = bytearray(open(fa, "rb").read())
        raw[300] ^= 1
        open(bad2, "wb").write(raw)
        mock = fresh(tmp)
        check("a broken file is left out, the others are installed",
              quiet(m.cmd_install, opts(files=[fa, bad2, fb, fc]), mock) == 0
              and mock.fs == three)
        os.remove(bad2)
        # two effects that would end up with the same id
        twin = write_effect(tmp, "TWIN.ZD2", 0x09000F42, b"SFX", title=b"Twin")
        mock = fresh(tmp)
        check("second effect with the same id is left out",
              quiet(m.cmd_install, opts(files=[fa, twin]), mock) == 0
              and "FXA.ZD2" in mock.fs and "TWIN.ZD2" not in mock.fs
              and len(m.flst_entries(mock.fs[m.FLST_NAME])) == 9)
        # interactive: backup y, categories 10 / Enter / s(kip), then ONE
        # confirmation for the whole batch
        mock = fresh(tmp)
        sys.stdin = io.StringIO("y\n10\n\ns\ny\n")
        check("categories chosen one by one, third skipped, one question",
              quiet(m.cmd_install, opts(files=[fa, fb, fc], yes=False),
                    mock) == 0)
        sys.stdin = open(os.devnull)
        got = sorted(e[:2] for e in m.flst_entries(mock.fs[m.FLST_NAME])
                     if e[1].startswith("FX"))
        check("  -> FXA in DELAY, FXB in DYNAMICS, FXC not installed",
              got == [(1, "FXB.ZD2"), (10, "FXA.ZD2")]
              and "FXC.ZD2" not in mock.fs, got)
        # the whole batch again: everything is already there, nothing written
        mock = fresh(tmp)
        quiet(m.cmd_install, opts(files=[fa, fb, fc]), mock)
        mock.wrote = []
        check("same batch again -> success, nothing is written",
              quiet(m.cmd_install, opts(files=[fa, fb, fc]), mock) == 0
              and not mock.wrote and mock.fs == three, mock.wrote)
        # ... and with one new effect added, only that one is transferred
        fd = write_effect(batch_dir, "FXD.ZD2", 0x01000F45, b"DYNAMICS",
                          title=b"FxD")
        check("batch of 3 installed + 1 new -> only the new one is written",
              quiet(m.cmd_install, opts(files=[fa, fb, fc, fd]), mock) == 0
              and mock.wrote == ["FXD.ZIC", "FXD.ZD2", m.FLST_NAME],
              mock.wrote)
        os.remove(fd)
        os.remove(fd[:-4] + ".ZIC")
        # an installed effect keeps the category it was moved to
        mock = fresh(tmp)
        quiet(m.cmd_install, opts(files=[fa], category=10), mock)
        mock.wrote = []
        check("re-install without a category keeps the current one (DELAY)",
              quiet(m.cmd_install, opts(files=[fa]), mock) == 0
              and not mock.wrote
              and m.ZD2(mock.fs["FXA.ZD2"]).group == 10, mock.wrote)

        # all or nothing: the 2nd effect is stored wrongly -> nothing stays
        mock = fresh(tmp)
        mock.corrupt["FXB.ZD2"] = 1
        check("failure in the middle of a batch -> safe abort (code 3)",
              quiet(m.cmd_install, opts(files=[fa, fb, fc]), mock) == 3)
        check("  pedal exactly in its starting state (no file left behind)",
              mock.fs == stock_fs() and not mock.session_open)
        # a batch that replaces an installed effect: two list writes in all
        mock = fresh(tmp)
        quiet(m.cmd_install, opts(files=[fa]), mock)
        mock.wrote = []
        check("batch with one replacement reports success",
              quiet(m.cmd_install, opts(files=[fa, fb], category=10),
                    mock) == 0)
        got = sorted(e for e in m.flst_entries(mock.fs[m.FLST_NAME])
                     if e[1].startswith("FX"))
        check("  both in DELAY, the old FXA entry gone",
              got == [(10, "FXA.ZD2", "1.10", 0x0A000F42, 1),
                      (10, "FXB.ZD2", "1.10", 0x0A000F43, 1)], got)
        check("  effect list written twice (remove, then add all)",
              mock.wrote.count(m.FLST_NAME) == 2
              and mock.wrote[-1] == m.FLST_NAME, mock.wrote)
        # the queue of the menu: add bit by bit, remove one, start
        sys.stdin = io.StringIO("%s\n%s %s\n%s\nr 2\n\n"
                                % (fa, fb, fc, fa))
        queue = quiet_silent_call(m.build_queue)
        sys.stdin = open(os.devnull)
        check("queue: added in two steps, duplicate ignored, one removed",
              queue == [fa, fc], queue)
        sys.stdin = io.StringIO("%s %s %s\nr 9\nr 1 3\n\n" % (fa, fb, fc))
        queue = quiet_silent_call(m.build_queue)
        check("queue: a wrong number changes nothing, 'r 1 3' takes out two",
              queue == [fb], queue)
        sys.stdin = io.StringIO("%s %s\nr a\n%s\n\n" % (fa, fb, fc))
        queue = quiet_silent_call(m.build_queue)
        sys.stdin = open(os.devnull)
        check("queue: 'r a' empties it, adding goes on afterwards",
              queue == [fc], queue)
        shutil.rmtree(batch_dir)

        # 2026-10-18: a MIDI port without a name ended every job with an
        # unexpected error until the program was restarted
        import mido
        saved = mido.get_input_names, mido.get_output_names
        mido.get_input_names = lambda: [None, "ZOOM MS Plus Series"]
        mido.get_output_names = lambda: ["ZOOM MS Plus Series", None]
        try:
            pairs = m.find_ports()
        finally:
            mido.get_input_names, mido.get_output_names = saved
        check("a MIDI port without a name is skipped",
              pairs == [("ZOOM MS Plus Series", "ZOOM MS Plus Series")],
              pairs)

        print("5. Remove")
        mock = fresh(tmp)
        quiet(m.cmd_install, opts(files=[fx]), mock)
        base_fs = stock_fs()
        check("remove reports success",
              quiet(m.cmd_uninstall, opts(file="MYFX.ZD2"), mock) == 0)
        check("pedal exactly back in its starting state", mock.fs == base_fs)
        check("a stock effect is not removed",
              quiet(m.cmd_uninstall, opts(file="DCOMP.ZD2"), mock) == 2
              and mock.fs == base_fs)

        # several at once: one list write, then the files one by one
        for text, want in (("1 3", [0, 2]), ("2-4", [1, 2, 3]),
                           ("1,3-4 1", [0, 2, 3]), ("all", [0, 1, 2, 3]),
                           ("A", [0, 1, 2, 3]), ("5", None), ("0", None),
                           ("3-2", None), ("2-", None), ("x", None),
                           ("", None)):
            check("selection %r" % text,
                  m.parse_selection(text, 4) == want,
                  m.parse_selection(text, 4))
        fx_b = write_effect(tmp, "MYFXB.ZD2", 0x01000F51, b"DYNAMICS",
                            title=b"MyFxB")
        fx_c = write_effect(tmp, "MYFXC.ZD2", 0x0A000F52, b"DELAY",
                            title=b"MyFxC")
        trio = [fx, fx_b, fx_c]
        mock = fresh(tmp)
        quiet(m.cmd_install, opts(files=trio), mock)
        three_on = collections.OrderedDict(mock.fs)
        mock.wrote = []
        check("three installed, two removed in one go",
              quiet(m.cmd_uninstall,
                    opts(files=["MYFX.ZD2", "myfxc.zd2", "MYFX.ZD2"]),
                    mock) == 0)
        left = [e[1] for e in m.flst_entries(mock.fs[m.FLST_NAME])
                if e[1].startswith("MYFX")]
        check("  only the third is left, files and icons of the two gone",
              left == ["MYFXB.ZD2"]
              and sorted(n for n in mock.fs if n.startswith("MYFX"))
              == ["MYFXB.ZD2", "MYFXB.ZIC"] and not mock.session_open,
              (left, sorted(mock.fs)))
        check("  the effect list was written exactly once",
              mock.wrote == [m.FLST_NAME], mock.wrote)
        for bad_name, why in (("DCOMP.ZD2", "a stock effect"),
                              ("NOPE.ZD2", "an unknown name")):
            mock = fresh(tmp)
            mock.fs = collections.OrderedDict(three_on)
            check("%s in the selection -> nothing at all is removed" % why,
                  quiet(m.cmd_uninstall,
                        opts(files=["MYFX.ZD2", bad_name, "MYFXB.ZD2"]),
                        mock) == 2
                  and mock.fs == three_on and not mock.wrote)
        # the text menu: pick 1 and 3 from the list, no backup, yes
        mock = fresh(tmp)
        mock.fs = collections.OrderedDict(three_on)
        sys.stdin = io.StringIO("7\n1 3\nn\ny\n")
        check("menu: two picked from the list by number",
              quiet(m.cmd_uninstall, opts(files=[], yes=False), mock) == 0)
        sys.stdin = open(os.devnull)
        left = sorted(e[1] for e in m.flst_entries(mock.fs[m.FLST_NAME])
                      if e[1].startswith("MYFX"))
        added_order = [e[1] for e in m.flst_entries(three_on[m.FLST_NAME])
                       if e[1].startswith("MYFX")]
        check("  -> exactly the first and third of the list are gone",
              left == [added_order[1]], (left, added_order))
        mock = fresh(tmp)
        mock.fs = collections.OrderedDict(three_on)
        sys.stdin = io.StringIO("a\nn\nn\n")
        check("menu: 'no' at the last question -> nothing is changed",
              quiet(m.cmd_uninstall, opts(files=[], yes=False), mock) == 0
              and mock.fs == three_on and not mock.wrote)
        sys.stdin = open(os.devnull)

        stock_checks(tmp, fx)

        # The pedal goes silent / loses one reply at EVERY point of removing
        # two effects: never a false "success"; a "safe abort" always means a
        # valid list - the old one, or the new one without both effects.
        # (An up-to-date backup exists, so the sweep is about the removal
        # itself; a backup that fails on the way is covered in section 7.)
        def three():
            return MockPedal(collections.OrderedDict(three_on))

        fresh(tmp)
        quiet_silent(m.cmd_backup, opts(), three())
        both = ["MYFX.ZD2", "MYFXC.ZD2"]
        probe = three()
        sys.stdin = io.StringIO("n\ny\n")
        said = []
        saved_say = m.ui.say
        m.ui.say = said.append
        try:
            m.run(lambda o: m.cmd_uninstall(o, probe),
                  opts(files=both, yes=False))
        finally:
            m.ui.say = saved_say
        total = probe.sends
        lists = (three_on[m.FLST_NAME], probe.fs[m.FLST_NAME])
        check("a backup with the same list is pointed out, but the question "
              "is still asked ('n' = none made)",
              any("were not compared" in t for t in said)
              and len(os.listdir(m.BACKUP_ROOT)) == 1
              and lists[0] != lists[1], said[:4])
        for label, count in (("goes silent", 0), ("loses one reply", 1)):
            lied = unsafe = 0
            codes = collections.Counter()
            for cut in range(1, total + 1):
                mock = three()
                mock.silent_from, mock.silent_count = cut, count
                sys.stdin = io.StringIO("n\ny\n")
                c = quiet_silent(m.cmd_uninstall,
                                 opts(files=both, yes=False), mock)
                codes[c] += 1
                if c == 0 and (mock.fs != probe.fs or mock.session_open):
                    lied += 1
                if c in (2, 3) and mock.fs.get(m.FLST_NAME) not in lists:
                    unsafe += 1
                if c == 2 and mock.fs != three_on:
                    unsafe += 1
                if c == 3 and count and mock.session_open:
                    unsafe += 1
            check("pedal %s at each of the %d points of a removal: never a "
                  "false 'success'" % (label, total), lied == 0, dict(codes))
            check("  'refused' = unchanged, 'safe abort' = a valid effect "
                  "list", unsafe == 0, dict(codes))
            if count:
                check("  never UNRESOLVED because of a single lost reply",
                      codes[4] == 0, dict(codes))
            print("     (results: %s)" % dict(codes))
        sys.stdin = open(os.devnull)

        print("5b. Found in the review of 2026-10-23")
        review_checks(tmp, fx)

        print("6. Refusals before writing")
        mock = fresh(tmp)
        bad = os.path.join(tmp, "BADHDR.ZD2")
        raw = bytearray(original)
        struct.pack_into("<I", raw, 4, len(raw))      # the known brick case
        open(bad, "wb").write(raw)
        shutil.copy(os.path.join(HERE, "BLANK.ZIC"), bad[:-4] + ".ZIC")
        check("wrong header value @4 is refused",
              quiet(m.cmd_install, opts(files=[bad]), mock) == 2)
        raw = bytearray(original)
        raw[300] ^= 1
        open(bad, "wb").write(raw)
        check("wrong checksum is refused",
              quiet(m.cmd_install, opts(files=[bad]), mock) == 2)
        clash = write_effect(tmp, "CLASH.ZD2", eid=0x09000010)
        check("id collision with an installed effect is refused",
              quiet(m.cmd_install, opts(files=[clash]), mock) == 2)
        check("nothing was written in any refusal",
              mock.fs == stock_fs() and not mock.wrote)
        mock = fresh(tmp)
        mock.autosave_reply = 1
        check("AUTO SAVE = ON is refused",
              quiet(m.cmd_install, opts(files=[fx]), mock) == 2
              and not mock.wrote)

        print("7. Failures during the transfer")
        # File read back differs -> clean abort, pedal unchanged
        mock = fresh(tmp)
        mock.corrupt["MYFX.ZD2"] = 1
        code_ = quiet(m.cmd_install, opts(files=[fx]), mock)
        check("effect stored corrupted -> safe abort (code 3)",
              code_ == 3, code_)
        check("  pedal exactly in its starting state, session closed",
              mock.fs == stock_fs() and not mock.session_open)

        # From here on: a batch of two effects, so that the failure sweeps
        # also cover "files of several effects, then one list write".
        fx2 = write_effect(tmp, "MYFX2.ZD2", 0x01000F50, b"DYNAMICS",
                           title=b"MyFx2")
        pair = [fx, fx2]
        # A single lost reply is bridged
        probe = fresh(tmp)
        quiet(m.cmd_install, opts(files=pair), probe)
        total = probe.sends
        mock = fresh(tmp)
        mock.silent_from, mock.silent_count = total - 40, 1
        check("one lost reply -> the install still succeeds",
              quiet(m.cmd_install, opts(files=pair), mock) == 0
              and mock.fs == probe.fs)

        # The pedal goes silent at EVERY possible point: "success" must never
        # be reported, and a "safe abort" must be true.
        lied = unsafe = 0
        codes = collections.Counter()
        for cut in range(1, total + 1):
            mock = fresh(tmp)
            mock.silent_from = cut
            c = quiet_silent(m.cmd_install, opts(files=pair), mock)
            codes[c] += 1
            if c == 0 and (mock.fs != probe.fs or mock.session_open):
                lied += 1
            if c == 3 and mock.fs.get(m.FLST_NAME) != stock_fs()[m.FLST_NAME]:
                unsafe += 1
        check("pedal goes silent at each of the %d points: never a false "
              "'success'" % total, lied == 0, dict(codes))
        check("  'safe abort' only with an intact effect list",
              unsafe == 0, dict(codes))
        print("     (results: %s)" % dict(codes))

        # Exactly ONE reply is lost, at every possible point.
        lied = unsafe = 0
        codes = collections.Counter()
        for cut in range(1, total + 1):
            mock = fresh(tmp)
            mock.silent_from, mock.silent_count = cut, 1
            c = quiet_silent(m.cmd_install, opts(files=pair), mock)
            codes[c] += 1
            if c == 0 and (mock.fs != probe.fs or mock.session_open):
                lied += 1
            if c == 3 and (mock.fs != stock_fs() or mock.session_open):
                unsafe += 1
        check("one lost reply at each point: never a false 'success'",
              lied == 0, dict(codes))
        check("  'safe abort' = pedal exactly in its starting state",
              unsafe == 0, dict(codes))
        check("  never UNRESOLVED because of a single lost reply",
              codes[4] == 0, dict(codes))
        print("     (results: %s)" % dict(codes))

        # The effect list cannot be written and verified -> banner
        mock = fresh(tmp)
        mock.corrupt[m.FLST_NAME] = 99
        check("effect list stored wrong 3x -> UNRESOLVED (code 4)",
              quiet_silent(m.cmd_install, opts(files=[fx]), mock) == 4)
        good = os.path.join(tmp, "GOOD.ZT2")
        open(good, "wb").write(stock_fs()[m.FLST_NAME])
        mock.corrupt.clear()
        check("  rescue writes the list back from the backup",
              quiet_silent(m.cmd_rescue, opts(flst=good, write=True),
                           mock) == 0
              and mock.fs[m.FLST_NAME] == stock_fs()[m.FLST_NAME])

        # Wrong once, then right -> the retry saves it
        mock = fresh(tmp)
        mock.corrupt[m.FLST_NAME] = 1
        check("effect list wrong once -> retry, success",
              quiet_silent(m.cmd_install, opts(files=[fx]), mock) == 0
              and len(m.flst_entries(mock.fs[m.FLST_NAME])) == 9)

        print("8. Graphical front end (without a window)")
        gui_checks(tmp, fx, fx2)
        gui_review_checks(tmp, fx)

        print("9. Real files on this Mac (if present)")
        real_checks()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def stock_checks(tmp, fx):
    """Stock effects removed with --stock and brought back (restore-stock)."""
    print("5d. Stock effects: removed with a way back")
    base_fs = stock_fs()

    def listed(mock):
        return sorted(e for e in m.flst_entries(mock.fs[m.FLST_NAME]))

    mock = fresh(tmp)
    check("without --stock a stock effect is still refused",
          quiet(m.cmd_uninstall, opts(file="ST9_1.ZD2"), mock) == 2
          and mock.fs == base_fs)
    check("--stock with no backup at all -> refused, nothing written",
          quiet(m.cmd_uninstall, opts(file="ST9_1.ZD2", stock=True,
                                      yes=False), mock) == 2
          and mock.fs == base_fs)
    check("--stock never empties a category",
          quiet(m.cmd_uninstall, opts(files=["ST10_1.ZD2", "ST10_2.ZD2"],
                                      stock=True), mock) == 2
          and mock.fs == base_fs)
    quiet(m.cmd_install, opts(files=[fx]), mock)
    with_fx = collections.OrderedDict(mock.fs)
    check("--stock does not remove an added effect",
          quiet(m.cmd_uninstall, opts(file="MYFX.ZD2", stock=True), mock) == 2
          and mock.fs == with_fx)
    mock = fresh(tmp)
    check("--stock removes a stock effect after a backup",
          quiet(m.cmd_uninstall, opts(file="ST9_1.ZD2", stock=True),
                mock) == 0
          and "ST9_1.ZD2" not in mock.fs and "ST9_1.ZIC" not in mock.fs
          and "ST9_1.ZD2" not in [e[1] for e in listed(mock)]
          and m.flst_validate(mock.fs[m.FLST_NAME])[0]
          and len(m.backups_of()) == 1)
    print("5e. Stock effects brought back")
    check("an effect that is on the pedal is not brought back",
          quiet(m.cmd_restore_stock, opts(files=["ST9_2.ZD2"]), mock) == 2)
    check("a name that is no stock effect is refused",
          quiet(m.cmd_restore_stock, opts(files=["MYFX.ZD2"]), mock) == 2)
    check("restore-stock brings it back from the backup",
          quiet(m.cmd_restore_stock, opts(files=["st9_1.zd2"]), mock) == 0
          and all(mock.fs.get(n) == base_fs[n]
                  for n in ("ST9_1.ZD2", "ST9_1.ZIC"))
          and listed(mock) == sorted(m.flst_entries(base_fs[m.FLST_NAME]))
          and m.flst_validate(mock.fs[m.FLST_NAME])[0]
          and not mock.session_open)
    check("  at the end of its category", [
        e[1] for e in m.flst_entries(mock.fs[m.FLST_NAME]) if e[0] == 9]
        == ["ST9_2.ZD2", "ST9_1.ZD2"])

    # no backup holds it: only Zoom's file with the MD5 of effects_sort.txt
    mock = fresh(tmp)
    quiet(m.cmd_uninstall, opts(file="ST9_1.ZD2", stock=True), mock)
    shutil.rmtree(m.BACKUP_ROOT)
    source = os.path.join(tmp, "zoom-files", "ST9_1")
    os.makedirs(source)
    with open(os.path.join(source, "ST9_1.ZD2"), "wb") as f:
        f.write(base_fs["ST9_1.ZD2"])
    gone = collections.OrderedDict(mock.fs)
    folder = os.path.dirname(source)
    check("no backup and no folder -> refused",
          quiet(m.cmd_restore_stock, opts(files=["ST9_1.ZD2"], source=[]),
                mock) == 2 and mock.fs == gone)
    check("a file in the folder whose MD5 is not Zoom's -> refused",
          quiet(m.cmd_restore_stock, opts(files=["ST9_1.ZD2"],
                                          source=[folder]), mock) == 2
          and mock.fs == gone)
    real_hashes = m.stock_hashes
    m.stock_hashes = lambda f: {"ST9_1.ZD2": hashlib.md5(
        base_fs["ST9_1.ZD2"]).hexdigest()}
    try:
        check("Zoom's file from the folder (its MD5), with a blank icon",
              quiet(m.cmd_restore_stock, opts(files=["ST9_1.ZD2"],
                                              source=[folder]), mock) == 0
              and mock.fs.get("ST9_1.ZD2") == base_fs["ST9_1.ZD2"]
              and mock.fs.get("ST9_1.ZIC") == m.blank_icon())
    finally:
        m.stock_hashes = real_hashes
    check("the MD5 of the shipped list is read",
          m.stock_hashes("ms-60bp").get("DCOMP.ZD2")
          == "b12cfead3590cb610187632e4645669f")


def capture(func):
    """Run func() with the program's messages collected. -> (result, lines)"""
    lines = []
    saved = m.ui.say
    m.ui.say = lines.append
    try:
        return func(), lines
    finally:
        m.ui.say = saved


def review_checks(tmp, fx):
    """One check (or a few) per defect found in the review of 2026-10-23."""
    import io
    import json
    d = os.path.join(tmp, "review")
    os.makedirs(d, exist_ok=True)
    blank = open(os.path.join(HERE, "BLANK.ZIC"), "rb").read()

    # the file listing carries sizes (used for the space check)
    mock = fresh(tmp)
    sizes = m.Pedal(mock).list_sizes()
    check("the file listing carries every file's size",
          list(sizes) == list(mock.fs)
          and all(sizes[n] == len(mock.fs[n]) for n in sizes), sizes)
    logged = [("52006e6004250000170000000000574944454348522e5a4943000000002814"
               "0000000000000000", ("WIDECHR.ZIC", 2600)),
              ("52006e60042500001700000000003136305f434f4d502e5a4432000000237c"
               "0000000000000000", ("160_COMP.ZD2", 15907)),
              ("52006e600425000017000000000044554d4d592e57415600000000000000"
               "000500000000000000", ("DUMMY.WAV", 81920))]
    check("sizes decoded from replies logged on the real MS-60B+",
          all(m.listing_entry(list(bytes.fromhex(h))) == want
              for h, want in logged),
          [m.listing_entry(list(bytes.fromhex(h))) for h, _ in logged])
    mock = fresh(tmp)
    mock.repeat_listing = True
    try:
        m.Pedal(mock).list_sizes()
        ended = False
    except m.TransferError:
        ended = True
    check("a listing that repeats itself ends with an error, not forever",
          ended)

    # backups: same list and names is no proof; ordered by real time
    mock = fresh(tmp)
    quiet_silent(m.cmd_backup, opts(), mock)
    changed = mock.fs["ST9_1.ZD2"][:-1] + b"\x01"
    mock.fs["ST9_1.ZD2"] = changed
    quiet_silent(m.cmd_install, opts(files=[fx]), mock)
    newest = m.latest_backup(model=0x27)
    check("a file changed under the same name: with --yes a new backup is "
          "made, and it is the newest",
          len(os.listdir(m.BACKUP_ROOT)) == 2 and open(os.path.join(
              newest[0], "files", "ST9_1.ZD2"), "rb").read() == changed,
          os.listdir(m.BACKUP_ROOT))
    check("backups are ordered by their timestamp (summer time ends)",
          m.backup_time({"created": "2026-10-25T02:50:00",
                         "created_ts": 100.0})
          < m.backup_time({"created": "2026-10-25T02:10:00",
                           "created_ts": 200.0})
          and m.backup_time({"created": "2026-10-25T02:10:00"}) > 0
          and m.backup_time({}) == 0.0)

    # the list rewrite counts the space of the old list
    mock = fresh(tmp)
    quiet_silent(m.cmd_install, opts(files=[fx]), mock)
    mock.free = 10000
    check("removing with only 10 KB free works",
          quiet_silent(m.cmd_uninstall, opts(files=["MYFX.ZD2"]), mock) == 0
          and "MYFX.ZD2" not in mock.fs)

    # a replacement is checked for space before anything is deleted
    mock = fresh(tmp)
    quiet_silent(m.cmd_install, opts(files=[fx]), mock)
    big = os.path.join(d, "MYFX.ZD2")
    open(big, "wb").write(make_zd2(b"MyFx", 0x07000F42, b"SFX", target=0x90,
                                   code=b"\x7fELF" + bytes(200000)))
    shutil.copy(os.path.join(HERE, "BLANK.ZIC"), big[:-4] + ".ZIC")
    before = dict(mock.fs)
    mock.wrote = []
    mock.free = 50 * 1024
    check("a replacement that cannot fit is refused before anything is "
          "deleted", quiet_silent(m.cmd_install, opts(files=[big]), mock) == 2
          and dict(mock.fs) == before and not mock.wrote)
    newer = os.path.join(d, "new", "MYFX.ZD2")
    os.makedirs(os.path.dirname(newer))
    open(newer, "wb").write(make_zd2(b"MyFxNew", 0x07000F42, b"SFX",
                                     target=0x90))
    shutil.copy(os.path.join(HERE, "BLANK.ZIC"), newer[:-4] + ".ZIC")
    mock.free = None
    mock.capacity = sum(m.allocated(len(v)) for v in mock.fs.values()) + 5735
    check("  one that fits only once the old version is deleted goes through",
          quiet_silent(m.cmd_install, opts(files=[newer]), mock) == 0
          and m.ZD2(mock.fs["MYFX.ZD2"]).name == "MyFxNew")
    # the pedal allocates 4090-byte units: a batch is counted in those
    mock = fresh(tmp)
    quiet_silent(m.cmd_install, opts(files=[fx]), mock)
    batch = [newer] + [write_effect(d, "CL%d.ZD2" % i, 0x07000F80 + i,
                                    b"SFX", title=b"Cl%d" % i)
                       for i in range(4)]
    mock.free = None
    mock.capacity = sum(m.allocated(len(v)) for v in mock.fs.values()) \
        + 5 * 2 * m.CLUSTER + m.DISK_MARGIN - 2 * m.CLUSTER - 1000
    before = dict(mock.fs)
    mock.wrote = []
    check("a batch that fits in bytes but not in the pedal's 4090-byte "
          "units is refused before anything is deleted",
          quiet_silent(m.cmd_install, opts(files=batch), mock) == 2
          and dict(mock.fs) == before and not mock.wrote, mock.wrote)

    check("every MS Plus model with a stock list is known by its number",
          all(len(m.stock_catalog(m.MODELS[n][1])) > 30
              for n in (0x23, 0x25, 0x26, 0x27, 0x28)))

    # unknown model: nothing on the pedal is replaced without --force
    mock = fresh(tmp, model=0x30)
    own = write_effect(d, "DCOMP.ZD2", 0x01000F60, b"DYNAMICS")
    check("unknown model: an effect named like one on the pedal is not "
          "replaced", quiet_silent(m.cmd_install, opts(files=[own]), mock) == 2
          and mock.fs["DCOMP.ZD2"] == stock_fs()["DCOMP.ZD2"])
    os.remove(own)
    os.remove(own[:-4] + ".ZIC")

    # names of the pedal's own files
    irfx = write_effect(d, "IR.ZD2", 0x07000F61, b"SFX")
    mock = fresh(tmp)
    mock.fs["IR.ZIC"] = b"the pedal's own"
    check("an effect named IR is refused, the pedal's IR.ZIC stays",
          quiet_silent(m.cmd_install, opts(files=[irfx]), mock) == 2
          and mock.fs["IR.ZIC"] == b"the pedal's own")

    # envelope: every stock effect has a DATA section
    z = m.ZD2(make_zd2(b"NoData", 0x07000F63, b"SFX", target=0x90))
    off, length = z.sections["DATA"]
    raw = bytearray(z.data[:off - 8] + z.data[off + length:])
    struct.pack_into("<I", raw, 8,
                     zlib.crc32(bytes(raw[12:len(raw) - 16])) ^ 0xFFFFFFFF)
    nodata = os.path.join(d, "NODATA.ZD2")
    open(nodata, "wb").write(raw)
    try:
        m.load_effect(nodata)
        refused = False
    except m.Refused as e:
        refused = "DATA" in str(e)
    check("a ZD2 without a DATA section is refused", refused)

    # --category needs a backup
    mock = fresh(tmp)
    sys.stdin = io.StringIO("n\ny\n")
    check("--category without a backup is refused, not ignored",
          quiet_silent(m.cmd_install, opts(files=[fx], category=9, yes=False),
                       mock) == 2 and "MYFX.ZD2" not in mock.fs)

    # adapted copies only for what went onto the pedal
    mock = fresh(tmp)
    sys.stdin = io.StringIO("y\n\nn\n")      # backup, category, NO
    quiet_silent(m.cmd_install, opts(files=[fx], yes=False), mock)
    copy = os.path.join(m.ADAPT_ROOT, "ms-60bp", "MYFX.ZD2")
    check("a cancelled install leaves no adapted copy",
          not os.path.exists(copy) and "MYFX.ZD2" not in mock.fs)
    sys.stdin = open(os.devnull)
    quiet_silent(m.cmd_install, opts(files=[fx]), mock)
    check("  a completed one does, equal to the file on the pedal",
          os.path.exists(copy) and open(copy, "rb").read()
          == mock.fs["MYFX.ZD2"])

    # a new icon is transferred; an effect without one keeps the pedal's
    noicon = write_effect(d, "NOICON.ZD2", 0x07000F62, b"SFX",
                          with_icon=False, title=b"NoIcon")
    mock = fresh(tmp)
    quiet_silent(m.cmd_install, opts(files=[noicon]), mock)
    icon = bytearray(blank)
    icon[-1] ^= 0x01
    icon = bytes(icon)
    open(noicon[:-4] + ".ZIC", "wb").write(icon)
    check("  (test icon is valid)", not m.zic_problems(icon),
          m.zic_problems(icon))
    mock.wrote = []
    check("a new icon for an installed effect is transferred",
          quiet_silent(m.cmd_install, opts(files=[noicon]), mock) == 0
          and mock.fs["NOICON.ZIC"] == icon, mock.wrote)
    os.remove(noicon[:-4] + ".ZIC")
    mock.wrote = []
    check("  without an icon file of its own it stays 'identical'",
          quiet_silent(m.cmd_install, opts(files=[noicon]), mock) == 0
          and not mock.wrote and mock.fs["NOICON.ZIC"] == icon, mock.wrote)

    # a failure while deleting left-overs says that the old version is gone
    mock = fresh(tmp)
    quiet_silent(m.cmd_install, opts(files=[fx]), mock)
    fx2b = write_effect(d, "MYFX2.ZD2", 0x01000F50, b"DYNAMICS",
                        title=b"MyFx2")
    mock.fs["MYFX2.ZIC"] = blank             # left from an earlier attempt
    mock.keep = {"MYFX2.ZIC"}
    code, said = capture(lambda: m.run(
        lambda o: m.cmd_install(o, mock),
        opts(files=[fx, fx2b], category=10)))
    check("left-over that cannot be deleted: safe abort that says the old "
          "version is gone", code == 3 and any(
              "had already been removed" in t for t in said), said[-3:])

    # Rescue: only a list of this pedal, without effects whose files are gone
    mock = fresh(tmp)
    quiet_silent(m.cmd_install, opts(files=[fx]), mock)
    quiet_silent(m.cmd_backup, opts(), mock)
    bdir = m.latest_backup(model=0x27)[0]
    other = os.path.join(d, "ms-50gp_backup")
    shutil.copytree(bdir, other)
    man = json.load(open(os.path.join(other, "manifest.json")))
    man.update(model="MS-50G+", model_number=0x23)
    json.dump(man, open(os.path.join(other, "manifest.json"), "w"))
    broken = b"\x00" * 100
    mock.fs[m.FLST_NAME] = broken
    check("rescue does not write the list of another model's backup",
          quiet_silent(m.cmd_rescue, opts(flst=os.path.join(
              other, "files", m.FLST_NAME), write=True), mock) == 4
          and mock.fs[m.FLST_NAME] == broken)
    del mock.fs["MYFX.ZD2"], mock.fs["MYFX.ZIC"]
    code = quiet_silent(m.cmd_rescue, opts(flst=os.path.join(
        bdir, "files", m.FLST_NAME), write=True), mock)
    ents = m.flst_entries(mock.fs[m.FLST_NAME])
    check("restore leaves out an effect whose files are gone",
          code == 0 and len(ents) == 8
          and not any(e[1] == "MYFX.ZD2" for e in ents), (code, ents))
    mock.fs[m.FLST_NAME] = b"\x00" * 5000
    mock.free = 100
    check("a rescue that cannot write the list reports UNRESOLVED (4)",
          quiet_silent(m.cmd_rescue, opts(flst=os.path.join(
              bdir, "files", m.FLST_NAME), write=True), mock) == 4)

    # A list cut off at a block boundary still parses; compared with the
    # newest backup of the pedal it is not valid.
    mock = fresh(tmp)
    quiet_silent(m.cmd_backup, opts(), mock)
    full = mock.fs[m.FLST_NAME]
    mock.fs[m.FLST_NAME] = full[:10240]
    check("rescue: a list cut off at a block boundary is NOT valid",
          m.flst_validate(full[:10240])[0]
          and quiet_silent(m.cmd_rescue, opts(), mock) == 4)
    mock.fs[m.FLST_NAME] = full
    check("  the full list is", quiet_silent(m.cmd_rescue, opts(), mock) == 0)

    # Rescue whose own read fails must still close the file session.
    left_open = []
    for k in range(1, 25):
        mock = fresh(tmp)
        mock.silent_from, mock.silent_count = k, 2
        code = quiet_silent(m.cmd_rescue, opts(), mock)
        if mock.session_open:
            left_open.append((k, code))
    check("rescue never leaves a file session open (2 lost replies)",
          not left_open, left_open)

    # A read that ends early is caught by the size in the pedal's listing.
    mock = fresh(tmp)
    p = m.Pedal(mock)
    real, cut = p.download, [1]

    def short(name):
        data = real(name)
        if name == m.FLST_NAME and cut[0]:
            cut[0] -= 1
            return data[:10240]
        return data
    p.download = short
    ident = {"name": "MS-60B+", "model": 0x27, "folder": "ms-60bp",
             "firmware": "1.20"}
    bdir, _ = capture(lambda: m.do_backup(p, ident, p.list_sizes()))
    with open(os.path.join(bdir, "files", m.FLST_NAME), "rb") as f:
        check("backup: a read that ended early is read again",
              cut == [0] and f.read() == mock.fs[m.FLST_NAME])

    # A list cut short but complete (2026-11-20): nothing may make it the
    # reference, and the restore fills it up instead of writing the older
    # list of the backup (that would drop the effect installed since).
    mock = fresh(tmp)
    quiet_silent(m.cmd_backup, opts(), mock)
    older = os.path.join(m.latest_backup()[0], "files", m.FLST_NAME)
    quiet_silent(m.cmd_install, opts(files=[fx]), mock)
    full = mock.fs[m.FLST_NAME]
    mock.fs[m.FLST_NAME] = full[:10240]
    before = dict(mock.fs)
    check("cut-short list: Backup and Install are UNRESOLVED (4), not 'safe'",
          quiet_silent(m.cmd_backup, opts(), mock) == 4
          and quiet_silent(m.cmd_install, opts(files=[fx], category=10),
                           mock) == 4
          and mock.fs == before and m.backup_flst_size(0x27) == len(full))
    check("  the restore fills it up - the newer effect stays listed",
          quiet_silent(m.cmd_rescue, opts(flst=older, write=True), mock) == 0
          and mock.fs[m.FLST_NAME] == full)

    # one lost identity reply must not skip the length check
    mock.fs[m.FLST_NAME] = full[:10240]
    send, lost = mock.send, [1]

    def no_identity(payload):
        if list(payload[:4]) == [0x7E, 0x00, 0x06, 0x01] and lost[0]:
            lost[0] -= 1
            mock.sends += 1
            return
        send(payload)
    mock.send = no_identity
    check("rescue: one lost identity reply still compares the length",
          quiet_silent(m.cmd_rescue, opts(), mock) == 4 and lost == [0])
    mock.fs[m.FLST_NAME], lost[0] = full, 1
    check("  ... and a full list with one lost reply is VALID (asked twice)",
          quiet_silent(m.cmd_rescue, opts(), mock) == 0 and lost == [0])
    lost[0] = 2
    check("  no identity at all: not called VALID (run it again)",
          quiet_silent(m.cmd_rescue, opts(), mock) == 4)
    mock.send = send

    # Restore after a replace-install was cut off: the replaced file must
    # not get its old entry (id, group) back.
    mock = fresh(tmp)
    quiet_silent(m.cmd_install, opts(files=[fx]), mock)
    quiet_silent(m.cmd_install, opts(files=[fx], category=10), mock)
    newest = os.path.join(m.latest_backup()[0], "files", m.FLST_NAME)
    old_entry = [e for e in m.flst_entries(open(newest, "rb").read())
                 if e[1] == "MYFX.ZD2"]
    mock.fs[m.FLST_NAME] = mock.fs[m.FLST_NAME][:4096]
    code = quiet_silent(m.cmd_rescue, opts(flst=newest, write=True), mock)
    ents = m.flst_entries(mock.fs[m.FLST_NAME])
    check("restore leaves out an effect replaced after the backup",
          old_entry and code == 0 and "MYFX.ZD2" in mock.fs
          and not any(e[1] == "MYFX.ZD2" for e in ents), (code, ents))
    # ... but keeps one rebuilt with the same header (new code, same id,
    # version and category): its entry still fits the file
    os.makedirs(os.path.join(tmp, "rebuild"), exist_ok=True)
    rebuilt = write_effect(os.path.join(tmp, "rebuild"), title=b"MyFxV2")
    mock = fresh(tmp)
    quiet_silent(m.cmd_install, opts(files=[fx]), mock)
    quiet_silent(m.cmd_install, opts(files=[rebuilt]), mock)
    entry = [e for e in m.flst_entries(mock.fs[m.FLST_NAME])
             if e[1] == "MYFX.ZD2"]
    newest = os.path.join(m.latest_backup()[0], "files", m.FLST_NAME)
    mock.fs[m.FLST_NAME] = mock.fs[m.FLST_NAME][:4096]
    code = quiet_silent(m.cmd_rescue, opts(flst=newest, write=True), mock)
    check("  but keeps one rebuilt with the same header",
          entry and code == 0 and [e for e in m.flst_entries(
              mock.fs[m.FLST_NAME]) if e[1] == "MYFX.ZD2"] == entry,
          (code, entry))

    # an older backup's shorter list is written at the full length
    mock = fresh(tmp)
    full = mock.fs[m.FLST_NAME]
    mock.fs[m.FLST_NAME] = full[:8502]
    quiet_silent(m.cmd_backup, opts(), mock)        # no reference yet
    older = os.path.join(m.latest_backup()[0], "files", m.FLST_NAME)
    mock.fs[m.FLST_NAME] = full
    quiet_silent(m.cmd_backup, opts(), mock)
    mock.fs[m.FLST_NAME] = full[:4096]
    check("restore from an older, shorter list writes the full length",
          len(open(older, "rb").read()) == 8502
          and quiet_silent(m.cmd_rescue, opts(flst=older, write=True),
                           mock) == 0
          and mock.fs[m.FLST_NAME] == full)

    # a restore whose own reads fail must close the file session too (an
    # added effect on the pedal: its file is read for the header check)
    mock = fresh(tmp)
    quiet_silent(m.cmd_install, opts(files=[fx]), mock)
    quiet_silent(m.cmd_backup, opts(), mock)
    bflst = os.path.join(m.latest_backup()[0], "files", m.FLST_NAME)
    base = dict(mock.fs)
    base[m.FLST_NAME] = b"\x00" * 100
    mock = MockPedal(dict(base))
    quiet_silent(m.cmd_rescue, opts(flst=bflst, write=True), mock)
    total = mock.sends
    left_open = []
    for k in range(1, total + 3):
        mock = MockPedal(dict(base))
        mock.silent_from, mock.silent_count = k, 2
        code = quiet_silent(m.cmd_rescue, opts(flst=bflst, write=True), mock)
        if mock.session_open:
            left_open.append((k, code))
    check("restore never leaves a file session open (2 lost replies, "
          "%d positions)" % (total + 2), not left_open and total > 50,
          left_open)

    # a reply behind other MIDI (clock) is found even after the deadline
    import mido

    class Port(object):
        def __init__(self, msgs):
            self.msgs = list(msgs)

        def poll(self):
            return self.msgs.pop(0) if self.msgs else None
    t = m.MidoTransport(Port([mido.Message("clock"),
                              mido.Message("sysex", data=[1, 2])]), None)
    check("a reply behind a clock message is not lost", t.recv(0) == b"\x01\x02")

    if sys.platform == "darwin":
        try:
            import Foundation   # noqa: F401  (pyobjc, as in the app)
        except ImportError:
            print("  (pyobjc not installed here - App Nap not checked; on "
                  "the Mac run the self-test with .build-venv)")
        else:
            m.keep_awake(True)
            held = m._awake.get("activity")
            m.keep_awake(False)
            check("no App Nap while working (activity held, then released)",
                  held is not None and m._awake.get("activity") is None)

    # Removing an entry IR.ZD2 (put there by another program) keeps the
    # pedal's own IR.ZIC.
    mock = fresh(tmp)
    eid = (9 << 24) | 0xF55
    mock.fs["IR.ZD2"] = make_zd2(b"IrFx", eid, b"SFX")
    mock.fs["IR.ZIC"] = b"own icon"
    mock.fs[m.FLST_NAME] = m.flst_add(mock.fs[m.FLST_NAME], "IR.ZD2",
                                      "1.10", eid)
    check("remove IR.ZD2 keeps the pedal's own IR.ZIC",
          quiet_silent(m.cmd_uninstall, opts(files=["IR.ZD2"]), mock) == 0
          and "IR.ZD2" not in mock.fs and mock.fs.get("IR.ZIC") == b"own icon")

    check("pedal names are upper case, like every ZOOM file",
          m.pedal_name("/x/totape9.zd2") == "TOTAPE9.ZD2")
    check("typed '²' is not a number (no crash)",
          m.parse_selection("²", 3) is None)

    # a MIDI port another program holds is a refusal, nothing was sent
    import mido
    saved = (mido.get_input_names, mido.get_output_names, mido.open_input)
    mido.get_input_names = lambda: ["ZOOM MS Plus Series"]
    mido.get_output_names = lambda: ["ZOOM MS Plus Series"]

    def busy(name, **kw):
        raise OSError("port in use")
    mido.open_input = busy
    try:
        code = quiet_silent_call(lambda: m.run(m.cmd_info, opts()))
    finally:
        mido.get_input_names, mido.get_output_names, mido.open_input = saved
    check("a MIDI port in use: refused (2), not 'unexpected error'",
          code == 2, code)

    # a closed terminal does not stop the program between two steps
    class Gone(object):
        encoding = "utf-8"

        def write(self, text):
            raise OSError(5, "Input/output error")

        def flush(self):
            raise OSError(5, "Input/output error")

        def isatty(self):
            return True
    mock = fresh(tmp)
    quiet_silent(m.cmd_install, opts(files=[fx]), mock)
    saved = sys.stdout
    sys.stdout = Gone()
    try:
        code = m.run(lambda o: m.cmd_install(o, mock),
                     opts(files=[fx], category=10))
        ok = True
    except Exception as e:
        ok, code = False, repr(e)
    finally:
        sys.stdout = saved
    check("terminal closed: the replacement still completes",
          ok and code == 0
          and m.ZD2(mock.fs["MYFX.ZD2"]).group == 10, code)

    # the log is UTF-8, rotation only touches the program's own logs
    saved_root = m.LOG_ROOT
    m.LOG_ROOT = os.path.join(tmp, "Logs-review")
    os.makedirs(m.LOG_ROOT)
    for i in range(25):
        open(os.path.join(m.LOG_ROOT, "20260101-0000%02d-gui.log" % i),
             "w").close()
    open(os.path.join(m.LOG_ROOT, "my notes.log"), "w").close()
    try:
        m.open_log("check")
        m.log("Łukasz – ü")
        m._logfile.close()
        logs = sorted(os.listdir(m.LOG_ROOT))
        newest = [n for n in logs if n.endswith("-check.log")]
        text = open(os.path.join(m.LOG_ROOT, newest[0]),
                    encoding="utf-8").read() if newest else ""
    finally:
        m._logfile = None
        m.LOG_ROOT = saved_root
    check("the log is UTF-8, foreign .log files are not rotated away",
          "Łukasz – ü" in text and "my notes.log" in logs
          and len([n for n in logs if n != "my notes.log"]) == m.KEEP_LOGS,
          logs[:3])

    # Windows console guard: once closing, nothing new starts, but a step
    # inside one still running (the retry of a list write) may
    import threading
    done = []

    def nested():
        m._console.update(busy=1, closing=True)
        try:
            with m.write_step():
                done.append(True)
        finally:
            m._console.update(busy=0, closing=False)
    t = threading.Thread(target=nested, daemon=True)
    t.start()
    t.join(3)
    check("closing console: a step inside a running one is not blocked",
          done == [True])

    # text menu: a vanished file, and end of input at item 5
    qa = write_effect(d, "QA.ZD2", 0x07000F70, b"SFX", title=b"QA")
    qb = write_effect(d, "QB.ZD2", 0x07000F71, b"SFX", title=b"QB")

    class Feed(object):
        def __init__(self):
            self.lines = [qa + "\n", qb + "\n", "r 1\n", "\n"]

        def readline(self):
            if len(self.lines) == 3:          # QA disappears after adding
                os.remove(qa)
            return self.lines.pop(0) if self.lines else ""
    sys.stdin = Feed()
    try:
        queue = quiet_silent_call(m.build_queue)
        ok = True
    except Exception as e:
        queue, ok = repr(e), False
    check("text menu: a file that vanished from the list is shown, not a "
          "crash", ok and queue == [qb], queue)
    sys.stdin = io.StringIO("5\n")
    try:
        ok = quiet_silent_call(m.menu) == 0
    except Exception as e:
        ok = repr(e)
    check("text menu: end of input at item 5 returns cleanly", ok is True, ok)
    sys.stdin = open(os.devnull)



def quiet_silent(func, o, mock):
    """Like quiet(), but without screen output (many runs)."""
    devnull = open(os.devnull, "w")
    saved = sys.stdout
    sys.stdout = devnull
    try:
        return m.run(lambda oo: func(oo, mock), o)
    finally:
        sys.stdout = saved
        devnull.close()


def gui_checks(tmp, fx, fx2):
    """The graphical front end drives the same engine through its `ui`
    object. Here a robot answers its questions instead of a person."""
    import json
    import threading
    import time
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    import zoom_ms_gui as g

    console = m.ui
    seen = []

    def robot(app, stop):
        while not stop.is_set():
            with app.lock:
                q = app.question
            if q and not app.answered.is_set():
                seen.append(q["kind"])
                value = {"yesno": True, "choose": 0}.get(q["kind"])
                if q["kind"] == "category":
                    value = {"group": q["default"]} \
                        if q["default"] is not None else {"skip": True}
                with app.lock:
                    app.answer = value
                    app.answered.set()
            time.sleep(0.005)

    def run(app, name, func):
        app.start_job(name, func)
        app.job_thread.join()
        return app.result["code"]

    def new_app():
        mock = fresh(tmp)
        app = g.App(tmp, mock)
        m.ui = app
        stop = threading.Event()
        threading.Thread(target=robot, args=(app, stop), daemon=True).start()
        return app, mock, stop

    try:
        app, mock, stop = new_app()
        check("connect shows the pedal",
              run(app, "connect", app.job_connect) == 0
              and app.pedal["name"] == "MS-60B+" and app.pedal["effects"] == 8
              and app.pedal["added"] == [])
        app.add_paths([fx, fx2, os.path.join(tmp, "missing.ZD2")])
        check("effect list: two usable, one with its problem",
              [q["ok"] for q in app.queue] == [True, True, False]
              and app.queue[0]["name"] == "MyFx", app.queue)
        del seen[:]
        samples = []
        send = mock.send

        def spy(payload):
            p = list(payload)
            if p[3:4] == [0x60] and p[4] in (0x23, 0x24) and not samples:
                samples.append((os.path.exists(app.marker),
                                app.state(0)["unresolved"]))
            send(payload)
        mock.send = spy
        check("install through the front end reports success",
              run(app, "install", app.job_install) == 0)
        mock.send = send
        check("  at its first write the alarm was on disk, but not shown",
              samples == [(True, False)], samples)
        check("  its in-progress alarm is gone after the verified write",
              app.job_wrote and not app.unresolved
              and not os.path.exists(app.marker))
        # a hard end (Force Quit, crash) in the middle of a write: the
        # alarm must already be on disk for the next start
        app.writing({"model": 0x27})
        check("alarm saved as soon as writing starts (survives a hard end)",
              g.App(tmp, mock).alarm_models == [0x27])
        with open(app.marker, "w") as f:
            f.write("")
        check("  an unreadable alarm file counts as an alarm",
              g.App(tmp, mock).unresolved)
        if hasattr(getattr(g, "fcntl", None), "F_FULLFSYNC"):
            calls, real = [], g.fcntl.fcntl
            g.fcntl.fcntl = lambda fd, cmd, *a: (calls.append(cmd),
                                                 real(fd, cmd, *a))[1]
            try:
                app.writing({"model": 0x27})
            finally:
                g.fcntl.fcntl = real
            check("  the alarm file is flushed past the drive's cache (macOS)",
                  g.fcntl.F_FULLFSYNC in calls)
            # a volume that rejects F_FULLFSYNC (SMB): the alarm is saved
            # all the same
            os.remove(app.marker)

            def refuse(fd, cmd, *a):
                if cmd == g.fcntl.F_FULLFSYNC:
                    raise OSError(45, "Operation not supported")
                return real(fd, cmd, *a)
            g.fcntl.fcntl = refuse
            try:
                app.writing({"model": 0x27})
            finally:
                g.fcntl.fcntl = real
            check("  ... also where the drive refuses that (SMB)",
                  g.App(tmp, mock).alarm_models == [0x27])
        app._set_unresolved(False)
        check("  it asked: backup, a category per effect, one confirmation",
              seen == ["yesno", "category", "category", "yesno"], seen)
        check("  the pedal view shows both as added, in their categories",
              sorted((a["file"], a["label"]) for a in app.pedal["added"])
              == [("MYFX.ZD2", "SFX"), ("MYFX2.ZD2", "DYNAMICS")]
              and app.result["message"].startswith("Installed 2"),
              app.pedal["added"])
        check("remove through the front end",
              run(app, "remove", lambda: app.job_remove(["MYFX2.ZD2"])) == 0
              and [a["file"] for a in app.pedal["added"]] == ["MYFX.ZD2"])
        check("a stock effect cannot be removed through it either",
              run(app, "remove", lambda: app.job_remove(["DCOMP.ZD2"])) == 2
              and "DCOMP.ZD2" in mock.fs)
        check("  the pedal view lists its stock effects, none missing of "
              "the made-up ones",
              "ST9_1.ZD2" in [e["file"] for e in app.pedal["stock_on"]]
              and "ST9_1.ZD2" not in [e["file"]
                                      for e in app.pedal["stock_missing"]])
        check("a stock effect removed through it (after a backup)",
              run(app, "remove_stock",
                  lambda: app.job_remove(["ST9_1.ZD2"], stock=True)) == 0
              and "ST9_1.ZD2" not in mock.fs
              and "ST9_1.ZD2" in [e["file"]
                                  for e in app.pedal["stock_missing"]])
        check("  and brought back through it",
              run(app, "restore_stock",
                  lambda: app.job_restore_stock(["ST9_1.ZD2"])) == 0
              and mock.fs.get("ST9_1.ZD2") == stock_fs()["ST9_1.ZD2"]
              and "ST9_1.ZD2" in [e["file"] for e in app.pedal["stock_on"]])
        check("nothing ticked -> refused",
              run(app, "remove", lambda: app.job_remove([])) == 2)
        # several ticked: one job, one confirmation that lists them
        app.clear_queue()
        app.add_paths([fx2])
        run(app, "install", app.job_install)
        del seen[:]
        details = []
        ask = app._ask
        app._ask = lambda q: (details.append(q), ask(q))[1]
        check("two ticked effects are removed in one job",
              run(app, "remove",
                  lambda: app.job_remove(["MYFX.ZD2", "MYFX2.ZD2"])) == 0
              and app.pedal["added"] == []
              and app.result["message"].startswith("2 effects removed"),
              (app.result, app.pedal["added"]))
        last = details[-1] if details else {}
        check("  one confirmation, and it names both",
              seen.count("yesno") == len(seen) and bool(details)
              and last["text"].startswith("Remove these 2 effects")
              and any("MYFX.ZD2" in d for d in last["details"])
              and any("MYFX2.ZD2" in d for d in last["details"]),
              (seen, last))
        app._ask = ask
        check("what the page sends: a list of names, or one name",
              g.effect_names({"files": ["A.ZD2", 5, "", None, "B.ZD2"]})
              == ["A.ZD2", "B.ZD2"]
              and g.effect_names({"file": "A.ZD2"}) == ["A.ZD2"]
              and g.effect_names({}) == []
              and g.effect_names({"files": "A.ZD2"}) == [])
        # a list cut short: Backup and Install (without Connect) raise the
        # red bar instead of reporting a safe state
        full = mock.fs[m.FLST_NAME]
        mock.fs[m.FLST_NAME] = full[:10240]
        app.add_paths([fx])
        check("cut-short list: Backup raises the red bar",
              run(app, "backup", app.job_backup) == 4 and app.unresolved)
        app._set_unresolved(False)
        check("  Install too",
              run(app, "install", app.job_install) == 4 and app.unresolved)
        app._set_unresolved(False)
        mock.fs[m.FLST_NAME] = full
        # a backup that fails on the computer (here: no backup folder can
        # be made) is no reason for the red bar - it only read the pedal
        saved_root = m.BACKUP_ROOT
        with open(os.path.join(tmp, "not-a-folder"), "w") as f:
            f.write("x")
        m.BACKUP_ROOT = os.path.join(tmp, "not-a-folder", "Backups")
        try:
            code = run(app, "backup", app.job_backup)
        finally:
            m.BACKUP_ROOT = saved_root
        check("  a backup failing on the computer raises no red bar",
              code == 4 and not app.unresolved
              and not os.path.exists(app.marker), code)
        stop.set()

        # a data folder that is a link to a moved folder: a message, no
        # silent end of the program
        link = os.path.join(tmp, "moved-link")
        try:
            os.symlink(os.path.join(tmp, "gone"), link)
        except OSError:
            link = None
        if link:
            shown, show = [], g.show_error
            g.show_error = shown.append
            try:
                code = g.main(["--data", link, "--no-open"])
            finally:
                g.show_error = show
            check("a data folder that cannot be used: message, no crash",
                  code == 1 and shown and link in shown[0], shown)
        if os.name != "nt" and os.getuid() != 0:
            ro = os.path.join(tmp, "read-only")
            os.makedirs(ro, exist_ok=True)
            os.chmod(ro, 0o555)
            shown, show = [], g.show_error
            g.show_error = shown.append
            try:
                code = g.main(["--data", ro, "--no-open"])
            finally:
                g.show_error = show
                os.chmod(ro, 0o755)
            check("  ... also one that exists but takes no files",
                  code == 1 and shown and ro in shown[0], shown)
        if sys.platform == "darwin":
            # under pythonw (Windows) there is no stderr; the alert must
            # still come
            alerts, saved = [], (sys.stderr, g.subprocess.run)
            sys.stderr = None
            g.subprocess.run = lambda *a, **k: alerts.append(a)
            try:
                g.show_error("test")
            finally:
                sys.stderr, g.subprocess.run = saved
            check("the error message comes without a console too",
                  len(alerts) == 1)

        # Cancel at the backup question ends the job before anything is
        # written, and the session is closed (2026-10-09: there was no way
        # to call an install off at that point)
        mock = fresh(tmp)
        app = g.App(tmp, mock)
        m.ui = app
        app.add_paths([fx])
        before = dict(mock.fs)
        asked = []

        def canceller():
            while not asked:
                with app.lock:
                    q = app.question
                if q and not app.answered.is_set():
                    asked.append(q["text"])
                    with app.lock:
                        app.answer = "cancel"
                        app.answered.set()
                time.sleep(0.005)

        threading.Thread(target=canceller, daemon=True).start()
        code = run(app, "install", app.job_install)
        check("Cancel at the backup question: the job ends as refused",
              code == 2 and asked and asked[0].startswith("Make a backup"),
              (code, asked))
        check("  nothing written, session closed, no alarm",
              mock.fs == before and not mock.wrote and not mock.session_open
              and not app.unresolved)

        # an unverifiable transfer raises the alarm; rescue clears it
        app, mock, stop = new_app()
        app.add_paths([fx])
        mock.corrupt[m.FLST_NAME] = 99
        check("unverified transfer -> code 4 and the alarm is on",
              run(app, "install", app.job_install) == 4 and app.unresolved)
        mock.corrupt.clear()
        check("restore from the backup -> alarm off, list as in the backup",
              run(app, "restore", lambda: app.job_rescue(restore=True)) == 0
              and not app.unresolved
              and mock.fs[m.FLST_NAME] == stock_fs()[m.FLST_NAME])
        stop.set()

        # the local web server answers only with the right token and host
        app, mock, stop = new_app()
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), g.make_handler(app, "tok123", b"<html></html>"))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % server.server_address[1]

        def http(path, data=None, host=None):
            req = urllib.request.Request(base + path, data=data)
            if host:
                req.add_header("Host", host)
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, b""

        check("page and state with the token",
              http("/tok123/")[0] == 200
              and json.loads(http("/tok123/api/state?since=0")[1])["version"]
              == g.VERSION)
        check("no token, wrong token, foreign host -> nothing",
              http("/")[0] == 404 and http("/nope/api/state")[0] == 404
              and http("/tok123/api/state", host="evil.example")[0] == 404)
        ok_up = json.loads(http("/tok123/api/upload?name=..%2F..%2FUP.ZD2",
                                open(fx, "rb").read())[1])["ok"]
        bad_up = json.loads(http("/tok123/api/upload?name=notes.txt",
                                 b"x")[1])["ok"]
        check("uploads land only inside the inbox, only effect files",
              ok_up and not bad_up and os.listdir(app.inbox) == ["UP.ZD2"],
              os.listdir(app.inbox))
        http("/tok123/api/queue/rescan", b"{}")
        check("an uploaded effect appears in the list",
              [q["file"] for q in app.queue] == ["UP.ZD2"]
              and app.queue[0]["ok"] and not app.queue[0]["icon"])
        for name in ("UP2.ZD2", "UP3.ZD2"):
            http("/tok123/api/upload?name=" + name, open(fx, "rb").read())
        http("/tok123/api/queue/rescan", b"{}")
        http("/tok123/api/queue/remove",
             json.dumps({"files": ["UP.ZD2", "UP3.ZD2"]}).encode())
        check("several ticked entries are taken out of the list at once",
              [q["file"] for q in app.queue] == ["UP2.ZD2"]
              and os.listdir(app.inbox) == ["UP2.ZD2"],
              ([q["file"] for q in app.queue], os.listdir(app.inbox)))
        http("/tok123/api/queue/remove", json.dumps({"files": "UP2"}).encode())
        kept = [q["file"] for q in app.queue]
        http("/tok123/api/queue/remove",
             json.dumps({"file": "UP2.ZD2"}).encode())
        check("  a malformed request changes nothing, one name still works",
              kept == ["UP2.ZD2"] and app.queue == [], kept)
        server.shutdown()
        server.server_close()

        # window mode: files come from the native file dialog, in place
        class FakeWindow(object):
            def create_file_dialog(self, mode, **kw):
                return (fx, fx2) if kw.get("allow_multiple") else (tmp,)

        app.clear_queue()
        app.window = FakeWindow()
        try:
            __import__("webview")
            picked = app.pick("files")
            check("window mode: picked files are used where they are",
                  picked == 2 and [q["path"] for q in app.queue] == [fx, fx2]
                  and os.path.isfile(fx))
            app.clear_queue()
            check("  clearing the list never deletes picked files",
                  os.path.isfile(fx) and os.path.isfile(fx2))
        except ImportError:
            print("  (pywebview not installed here - window mode not checked)")
        app.window = None
        stop.set()
        app.shutdown()
    finally:
        m.ui = console


def gui_review_checks(tmp, fx):
    """The window's part of the review of 2026-10-23."""
    import threading
    import time
    import zoom_ms_gui as g

    console = m.ui
    apps = []

    def new_app(name, mock):
        data = os.path.join(tmp, name)
        os.makedirs(data, exist_ok=True)
        app = g.App(data, mock)
        apps.append(app)
        return app

    def run(app, name, func):
        m.ui = app
        app.start_job(name, func)
        app.job_thread.join()
        return app.result["code"]

    def robot(app, stop):
        while not stop.is_set():
            with app.lock:
                q = app.question
            if q and not app.answered.is_set():
                value = True
                if q["kind"] == "category":
                    value = {"group": q["default"]} \
                        if q["default"] is not None else {"skip": True}
                with app.lock:
                    app.answer = value
                    app.answered.set()
            time.sleep(0.005)

    stop = threading.Event()
    try:
        # rescue after a restart: alarm, restore offer, remembered
        mock = fresh(tmp)
        quiet_silent(m.cmd_backup, opts(), mock)
        mock.fs[m.FLST_NAME] = b"\x00" * 100
        app = new_app("gui-review-1", mock)
        code = run(app, "rescue", app.job_rescue)
        check("window: a rescue that finds the list broken raises the alarm "
              "and offers the backup, without Connect",
              code == 4 and app.unresolved and app.pedal is None
              and app.restore_from is not None, (code, app.restore_from))
        again = new_app("gui-review-1", mock)
        check("  the alarm comes back when the program is started again",
              again.unresolved)
        code = run(app, "restore", lambda: app.job_rescue(restore=True))
        later = new_app("gui-review-1", mock)
        check("  restoring from that backup: list valid, alarm off for good",
              code == 0 and not app.unresolved and not later.unresolved
              and m.flst_validate(mock.fs[m.FLST_NAME])[0], code)

        # a safe abort that changed the pedal: the pedal is read again
        mock = fresh(tmp)
        app = new_app("gui-review-2", mock)
        threading.Thread(target=robot, args=(app, stop), daemon=True).start()
        app.add_paths([fx])
        run(app, "install", app.job_install)
        mock.keep = {"MYFX.ZD2"}
        code = run(app, "remove", lambda: app.job_remove(["MYFX.ZD2"]))
        check("window: after a safe abort the pedal view is read again",
              code == 3 and app.pedal["added"] == [], (code, app.pedal))

        # an uploaded copy replaced in the list must not come back
        app = new_app("gui-review-3", None)
        app.save_upload("MYFX.ZD2", open(fx, "rb").read())
        app.rescan()
        inplace = os.path.join(tmp, "inplace")
        os.makedirs(inplace, exist_ok=True)
        v2 = write_effect(inplace, "MYFX.ZD2", title=b"MyFxV2")
        app.add_paths([v2])
        app.save_upload("OTHER.ZD2", make_zd2(b"Other", 0x07000F64, b"SFX",
                                              target=0x90))
        app.rescan()
        got = dict((q["file"], (q["name"], q["path"])) for q in app.queue)
        check("window: an uploaded copy replaced by a picked file does not "
              "come back with the next upload",
              got.get("MYFX.ZD2") == ("MyFxV2", v2)
              and "MYFX.ZD2" not in os.listdir(app.inbox), got)

        # the same name twice in a folder: the first one, as in the menu
        dup = os.path.join(tmp, "dup")
        os.makedirs(os.path.join(dup, "old"), exist_ok=True)
        write_effect(dup, "DUPFX.ZD2", 0x07000F65, title=b"Top")
        write_effect(os.path.join(dup, "old"), "DUPFX.ZD2", 0x07000F65,
                     title=b"Old")
        app.clear_queue()
        m.ui = app
        app.add_paths([dup])
        check("window: a folder holding the same name twice keeps the "
              "top-level file", [(q["file"], q["name"]) for q in app.queue]
              == [("DUPFX.ZD2", "Top")], app.queue)

        # two pedals: the restore offer and the alarm belong to the right one
        mock_a = fresh(tmp, model=0x23)
        quiet_silent(m.cmd_backup, opts(), mock_a)
        mock_b = MockPedal(stock_fs())
        quiet_silent(m.cmd_backup, opts(), mock_b)
        mock_b.fs[m.FLST_NAME] = b"\x00" * 100
        app = new_app("gui-review-4", mock_a)
        run(app, "connect", app.job_connect)
        app.demo_pedal = mock_b                # the other pedal now
        code = run(app, "rescue", app.job_rescue)
        check("two pedals: the restore offer is the backup of the pedal the "
              "rescue talked to", code == 4 and app.restore_from is not None
              and app.restore_from["folder"].startswith("ms-60bp"),
              app.restore_from)
        app.demo_pedal = mock_a
        run(app, "rescue", app.job_rescue)
        check("  a rescue of the OTHER pedal does not clear the alarm",
              app.unresolved and new_app("gui-review-4", mock_a).unresolved)
        app.demo_pedal = mock_b
        code = run(app, "restore", lambda: app.job_rescue(restore=True))
        check("  restoring the broken pedal clears it",
              code == 0 and not app.unresolved
              and m.flst_validate(mock_b.fs[m.FLST_NAME])[0], code)

        # browser mode: the page is opened once more while the alarm is up
        opened = []

        class FakeServer(object):
            down = False

            def shutdown(self):
                self.down = True
        saved_open = g.open_in_browser
        g.open_in_browser = opened.append
        try:
            app = new_app("gui-review-5", None)
            app.unresolved = True
            app.bye_at = time.monotonic() - 10
            srv = FakeServer()
            t = threading.Thread(target=g.wait_for_page_gone,
                                 args=(app, srv, "http://page/"))
            t.start()
            end = time.monotonic() + 10
            while not opened and time.monotonic() < end:
                time.sleep(0.1)
            reopened = opened == ["http://page/"] and not srv.down
            app.bye_at = time.monotonic() - 10
            t.join(10)
        finally:
            g.open_in_browser = saved_open
        check("browser mode with the alarm up: the page is reopened once, "
              "then the program may end", reopened and srv.down, opened)

        # an alarm for one pedal is not taken over by a failure on another
        mock_b = fresh(tmp)
        mock_b.fs[m.FLST_NAME] = b"\x00" * 100
        mock_a = MockPedal(stock_fs(), model=0x23)
        app = new_app("gui-review-7", mock_b)
        run(app, "connect", app.job_connect)             # B broken: alarm
        app.demo_pedal = mock_a
        mock_a.silent_from, mock_a.silent_count = mock_a.sends + 12, 2
        first = run(app, "rescue", app.job_rescue)       # A: a lost reply
        second = run(app, "rescue", app.job_rescue)      # A: fine
        check("two pedals: a failure on the second keeps the first's alarm, "
              "and the second's all-clear does not end it",
              (first, second) == (4, 0) and app.unresolved
              and app.alarm_models == [0x27], (first, second,
                                               app.alarm_models))

        # browser mode: each new alarm gets its own reopen
        opened = []
        saved_open = g.open_in_browser
        g.open_in_browser = opened.append
        try:
            app = new_app("gui-review-8", None)
            srv = FakeServer()
            app.unresolved = True
            app.bye_at = time.monotonic() - 10
            t = threading.Thread(target=g.wait_for_page_gone,
                                 args=(app, srv, "http://page/"))
            t.start()
            end = time.monotonic() + 10
            while len(opened) < 1 and time.monotonic() < end:
                time.sleep(0.1)
            app.unresolved = False                   # rescued
            time.sleep(1.5)
            app.unresolved = True                    # a new failure
            app.bye_at = time.monotonic() - 10
            end = time.monotonic() + 10
            while len(opened) < 2 and time.monotonic() < end:
                time.sleep(0.1)
            app.unresolved = False
            app.bye_at = time.monotonic() - 10
            t.join(10)
        finally:
            g.open_in_browser = saved_open
        check("browser mode: a second alarm in the same run reopens the page "
              "again", len(opened) == 2 and srv.down, opened)

        # a broken copy does not shut out a usable one of the same name
        mixed = os.path.join(tmp, "mixed")
        os.makedirs(os.path.join(mixed, "new"), exist_ok=True)
        open(os.path.join(mixed, "MIXFX.ZD2"), "wb").write(b"ZDLF broken")
        good = write_effect(os.path.join(mixed, "new"), "MIXFX.ZD2",
                            0x07000F66, title=b"Good")
        app = new_app("gui-review-6", None)
        m.ui = app
        app.add_paths([mixed])
        check("window: a broken copy does not shut out a usable one of the "
              "same name", [(q["file"], q["ok"], q["path"]) for q in app.queue]
              == [("MIXFX.ZD2", True, good)], app.queue)
    finally:
        stop.set()
        for app in apps:
            app.shutdown()
        m.ui = console



def quiet_silent_call(func):
    saved = sys.stdout
    sys.stdout = open(os.devnull, "w")
    try:
        return func()
    finally:
        sys.stdout.close()
        sys.stdout = saved


def real_checks():
    """Check the file-format assumptions against real pedal files if a
    backup exists (our own or the one from nam2zoom)."""
    import glob
    dirs = glob.glob(os.path.join(HERE, "Backups", "*", "files")) + \
        glob.glob(os.path.expanduser(
            "~/Library/Application Support/nam2zoom/Backup/*/backup/files"))
    if not dirs:
        print("  (no backup found - skipped)")
        return
    for d in dirs:
        zd2s = sorted(glob.glob(os.path.join(d, "*.ZD2")))
        zics = sorted(glob.glob(os.path.join(d, "*.ZIC")))
        bad = []
        for f in zd2s:
            z = m.ZD2(open(f, "rb").read())
            # Two stock effects carry a wrong checksum from the factory;
            # everything else must hold.
            probs = [p for p in m.zd2_problems(z) if "checksum" not in p]
            if probs:
                bad.append((os.path.basename(f), probs))
        check("%d real ZD2 pass the envelope check" % len(zd2s),
              not bad, bad[:3])
        badz = [os.path.basename(f) for f in zics
                if m.zic_problems(open(f, "rb").read())]
        check("%d real icons pass the icon check" % len(zics),
              not badz, badz[:3])
        flst = os.path.join(d, m.FLST_NAME)
        if os.path.isfile(flst):
            ok, problems, entries = m.flst_validate(open(flst, "rb").read())
            check("real effect list valid (%d effects)"
                  % len(entries or []), ok, problems)
        table = m.category_table(d)
        print("     categories: %s" % ", ".join(
            "%d=%s" % (g, table[g]["label"]) for g in sorted(table)))


if __name__ == "__main__":
    run_tests()
    failed = RESULTS.count(False)
    print()
    print("Self-test: %d checks, %s" % (
        len(RESULTS), "ALL PASSED" if not failed
        else "%d FAILED" % failed))
    sys.exit(1 if failed else 0)
