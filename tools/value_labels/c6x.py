"""A small TI C674x interpreter for the value-display functions of effect files.

A display function (word 9 of a knob's entry in the parameter table) is called by the pedal with
    A4 = the knob's value, B4 = a buffer for the text, A6 = the buffer's length
and returns through B3; the text is the NUL-terminated string it leaves in the buffer. Custom
builds (stomphacks) copy it from a table of 8-byte strings, Zoom's own effects copy from string
tables or format numbers (ms, Hz, dB, offsets as -12..+12, note values) with the run-time
library's integer division – or, as SolidPre, with floating point.

This runs such a function on the listing of TI's disassembler (listing.py), with the effect's
sections as memory: the pipeline (delay slots of loads, multiplies, floating point and branches),
protected loads (fetch-packet headers), the software-pipelined loops (SPLOOP, SPLOOPD, SPLOOPW)
and the run-time library's integer division (done here, by its name in the listing). It refuses
(NotModelled) what it does not model rather than guessing: an instruction or operand it does not
know, a store outside the stack and the text buffer, a read of memory the effect writes while it
runs (.far, .fardata: only its constants are read), floating point beyond the normal numbers, a
stack that is not as it was at the return.

texts() runs every value twice, the second time with other contents in the registers and memory
the call does not set and the other extreme of RCPSP's estimate: a text that depends on those is
refused too.
"""
from __future__ import annotations

import math
import re
import struct
from typing import Callable, Dict, List, Optional

M32 = 0xFFFFFFFF
RETURN = 0x0FFFFFF0                 # the return address the display function is given
STACK_TOP, STACK_SIZE = 0x10010000, 0x10000
BUFFER, BUFFER_SIZE = 0x20000000, 256
BUFFER_LENGTH = 16                  # what is passed in A6 (the pedal's own length is not known)
MAX_CYCLES = 400000
OTHER_FILL = 0x5EED5EED             # registers and stack in texts()'s second run
RCPSP_ERROR = 2.0 ** -8             # RCPSP's estimate is good to 8 bits of the mantissa

MEMORY = re.compile(r"^\*(\+\+|--|\+|-)?([AB]\d+)(\+\+|--)?(?:\[([^\]]+)\]|\(([^)]+)\))?$")
REGISTER = re.compile(r"^[AB]\d+$")
PAIR = re.compile(r"^([AB]\d+):([AB]\d+)$")
LOOP_COUNTERS = ("ILC", "RILC")
LOADS = {"LDB", "LDBU", "LDH", "LDHU", "LDW", "LDNW", "LDDW", "LDNDW"}


class NotModelled(Exception):
    """The code does something this interpreter does not model, or its text is not determined by
    the call alone."""


def s32(x: int) -> int:
    x &= M32
    return x - (1 << 32) if x & 0x80000000 else x


def parse_int(token: str) -> int:
    token = token.strip()
    if token.startswith("-"):
        return -parse_int(token[1:])
    return int(token, 16) if token.lower().startswith("0x") else int(token)


def single(bits: int) -> float:
    """An IEEE single from its bits; the C674x treats denormals as zero, which is not modelled."""
    bits &= M32
    exponent = (bits >> 23) & 0xFF
    if exponent == 0xFF or exponent == 0 and bits & 0x7FFFFF:
        raise NotModelled(f"single-precision operand {bits:#010x} (infinite, NaN or denormal)")
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def single_bits(value: float) -> int:
    """The bits of a result rounded to single precision (to nearest, as the C674x by default)."""
    try:
        if math.isinf(value) or math.isnan(value):
            raise OverflowError
        bits = struct.unpack("<I", struct.pack("<f", value))[0]
    except OverflowError:
        raise NotModelled(f"single-precision result {value} (infinite or NaN)") from None
    if bits & 0x7F800000 == 0 and bits & 0x7FFFFF:
        raise NotModelled(f"single-precision result {value} (denormal)")
    return bits


def double(lo: int, hi: int) -> float:
    exponent = (hi >> 20) & 0x7FF
    if exponent == 0x7FF or exponent == 0 and (hi & 0xFFFFF or lo):
        raise NotModelled(f"double-precision operand {hi:#010x}:{lo:#010x} (infinite, NaN or denormal)")
    return struct.unpack("<d", struct.pack("<II", lo & M32, hi & M32))[0]


def double_bits(value: float) -> int:
    if math.isinf(value) or math.isnan(value) or value and abs(value) < 2.0 ** -1022:
        raise NotModelled(f"double-precision result {value}")
    lo, hi = struct.unpack("<II", struct.pack("<d", value))
    return lo | hi << 32


class Loop:
    """A software-pipelined loop: its first iteration runs as fetched and is recorded into the
    loop buffer up to SPKERNEL; a new iteration starts every ii cycles (SPLOOP, SPLOOPD: ILC times
    in all; SPLOOPW: while its predicate holds) and runs from the buffer."""

    def __init__(self, kind: str, ii: int, predicate: Optional[str], negated: bool, start: int,
                 left: Optional[int]):
        self.kind, self.ii, self.predicate, self.negated = kind, ii, predicate, negated
        self.start = start
        self.starts = [start]               # cycles in which iterations started
        self.body: List[tuple] = []         # (cycle within an iteration, instructions)
        self.recording = True
        self.stopping = False               # no more iterations start
        self.left = left                    # iterations still to start (SPLOOPD: read later)
        self.length = 0                     # cycles of one iteration
        self.after: Optional[int] = None    # the execute packet after SPKERNEL
        self.delay = 0                      # SPKERNEL's fstg/fcyc: fetching resumes that late in the epilog
        self.stop_at = self.drain_end = self.resume_at = 0
        self.fetching = False               # fetching resumed while the last iterations drain


class CPU:
    """One effect's code and memory. fill: what registers and the stack hold before a call,
    blank: the byte the text buffer is filled with, estimate: -1 or +1, which end of its error
    RCPSP's estimate of a reciprocal is taken at (the C674x's own table is not known)."""

    def __init__(self, listing, segments, fill: int = 0, blank: int = 0xFF, estimate: int = -1):
        self.listing = listing
        self.image = [(segment.addr, segment.addr + len(segment.data), bytes(segment.data), segment)
                      for segment in segments]
        self.stack = bytearray(STACK_SIZE)
        self.buffer = bytearray(BUFFER_SIZE)
        self.fill, self.blank, self.estimate = fill & M32, blank, estimate
        self.helpers = {addr: RUN_TIME[name] for addr, names in listing.labels.items()
                        for name in names if name in RUN_TIME}

    # ---- a call
    def display(self, function: int, value: int, length: int = BUFFER_LENGTH) -> str:
        """The text the display function at that address writes for that value."""
        self.buffer[:] = bytes([self.blank]) * BUFFER_SIZE
        self.call(function, {"A4": value, "B4": BUFFER, "A6": length})
        text = bytes(self.buffer[:length])
        if 0 not in text:
            raise NotModelled(f"value {value}: no NUL within {length} bytes")
        return text[:text.index(0)].decode("latin1")

    def call(self, function: int, arguments: Dict[str, int]) -> None:
        self.R = {f"{side}{number}": self.fill for side in "AB" for number in range(32)}
        self.R.update({name: value & M32 for name, value in arguments.items()})
        self.R["B3"] = RETURN
        self.R["B15"] = STACK_TOP - 64
        self.control = {name: 0 for name in LOOP_COUNTERS}
        self.stack[:] = self.fill.to_bytes(4, "little") * (STACK_SIZE // 4)
        self.pending: List[tuple] = []      # (cycle it is written in, register, value)
        self.branch: Optional[tuple] = None   # (cycle it is taken in, target)
        self.loop: Optional[Loop] = None
        self.cycle = 0
        listing = self.listing
        pc = self.go(function)
        idle = 0                            # NOP cycles still to wait before the next fetch
        while pc is not None:
            if self.cycle > MAX_CYCLES:
                raise NotModelled("too many cycles")
            self.apply_pending()
            if self.branch and self.branch[0] <= self.cycle:
                if self.loop is not None:
                    raise NotModelled("a branch out of a software-pipelined loop")
                pc = self.go(self.branch[1])
                self.branch = None
                idle = 0
                if pc is None:
                    break
            fetch = True
            group: list = []                # instructions from the loop buffer in this cycle
            loop = self.loop
            if loop is not None:
                group, resume = self.loop_cycle(loop)
                if resume:
                    pc, idle = loop.after, 0
                    loop.fetching = True
                fetch = loop.recording or loop.fetching or self.loop is None
            if fetch and idle > 0:
                idle -= 1
                fetch = False
            if fetch:
                index = listing.at.get(pc)
                if index is None:
                    raise NotModelled(f"no execute packet at {pc:#x}")
                ep = listing.eps[index]
                following = listing.eps[index + 1][0].addr if index + 1 < len(listing.eps) else None
                if group:
                    masked = {unit.strip() for i in ep if i.op == "SPMASK" for unit in i.args.split(",") if unit.strip()}
                    self.execute([i for i in group if i.unit[:2] not in masked], from_buffer=True)
                    group = []
                extra = self.execute(ep)
                loop = self.loop
                if loop is not None and loop.recording and loop.start <= self.cycle:
                    loop.body.append((self.cycle - loop.start,
                                      [i for i in ep if not i.masked and i.op not in ("NOP", "SPMASK")]))
                    kernel = next((i for i in ep if i.op == "SPKERNEL"), None)
                    if kernel is not None:
                        loop.recording = False
                        loop.length = self.cycle - loop.start + 1 + extra
                        loop.after = following
                        stage = kernel.operands
                        loop.delay = parse_int(stage[0]) * loop.ii + parse_int(stage[1]) if len(stage) == 2 else 0
                        extra = 0
                        if loop.stopping:
                            self.loop_stop(loop, loop.stop_at)
                pc, idle = following, extra
            if group:
                self.execute(group, from_buffer=True)
            self.cycle += 1
        if self.R["B15"] != STACK_TOP - 64:
            raise NotModelled("the stack pointer is not as it was at the return")

    def go(self, target: int) -> Optional[int]:
        """Where execution goes on after a branch to target: None when the display function has
        returned; a call of the run-time library's division is done here and returns at once."""
        while True:
            if target == RETURN:
                self.drain()
                return None
            helper = self.helpers.get(target)
            if helper is None:
                return target
            self.drain()
            helper(self)
            target = self.R["B3"]

    def write(self, register: str, value: int, delay: int) -> None:
        self.pending.append((self.cycle + delay + 1, register, value & M32))

    def apply_pending(self) -> None:
        if not self.pending:
            return
        keep = []
        for due, register, value in self.pending:
            if due > self.cycle:
                keep.append((due, register, value))
            elif register in LOOP_COUNTERS:
                self.control[register] = value
            else:
                self.R[register] = value
        self.pending = keep

    def drain(self) -> None:
        while self.pending:
            self.cycle += 1
            self.apply_pending()

    # ---- software-pipelined loops
    def loop_stop(self, loop: Loop, epilog: int) -> None:
        loop.stopping = True
        loop.stop_at = epilog
        if loop.recording:
            return                          # fixed once the first iteration has been recorded
        loop.drain_end = loop.starts[-1] + loop.length
        loop.resume_at = epilog + loop.delay if loop.delay else loop.drain_end

    def loop_cycle(self, loop: Loop) -> tuple:
        """The loop buffer in this cycle: a new iteration at a stage boundary, the instructions of
        iterations 2.. from the buffer, the end of the loop. Returns (instructions to issue now,
        whether fetching resumes after the loop now)."""
        elapsed = self.cycle - loop.start
        if elapsed > 0 and elapsed % loop.ii == 0 and not loop.stopping:
            if loop.kind == "SPLOOPW":
                holds = (self.R[loop.predicate] if loop.predicate else 1) != 0
                if holds == loop.negated:     # the condition is false: the loop ends, no epilog
                    if loop.recording:
                        raise NotModelled("SPLOOPW ended in its first iteration")
                    self.loop = None
                    return [], True
                loop.starts.append(self.cycle)
            else:
                if loop.left is None:
                    loop.left = s32(self.control["ILC"])
                loop.left -= 1
                if loop.left > 0:
                    loop.starts.append(self.cycle)
                else:
                    self.loop_stop(loop, self.cycle)
        resume = False
        if loop.stopping and not loop.recording:
            if self.cycle >= loop.resume_at and not loop.fetching:
                resume = True
            if self.cycle >= loop.drain_end and (resume or loop.fetching):
                self.loop = None
                return [], resume
        group = []
        for start in loop.starts[1:]:
            offset = self.cycle - start
            if offset < 0 or loop.stopping and not loop.recording and offset >= loop.length:
                continue
            for at, instructions in loop.body:
                if at == offset:
                    group.extend(instructions)
        return group, resume

    def start_loop(self, ins) -> None:
        """SPLOOP, SPLOOPD (ILC iterations) or SPLOOPW (while its predicate holds, tested at each
        stage boundary): the loop buffer records from the next cycle on."""
        if self.loop is not None:
            raise NotModelled("a software-pipelined loop in another")
        if ins.predicate and ins.op != "SPLOOPW":
            raise NotModelled(f"a predicated {ins.op}")
        if ins.predicate and (self.R[ins.predicate] == 0) != ins.negated:
            raise NotModelled("an SPLOOPW whose condition does not hold at its start")
        self.loop = Loop(ins.op, parse_int(ins.operands[0]), ins.predicate, ins.negated, self.cycle + 1,
                         None if ins.op == "SPLOOPD" else s32(self.control["ILC"]))

    # ---- one execute packet
    def execute(self, ep: list, from_buffer: bool = False) -> int:
        """Issues the instructions of one cycle; returns the NOP cycles that follow it."""
        extra = 0
        protected = False
        for ins in ep:
            if ins.op in LOADS and ins.protected and not from_buffer:
                protected = True          # the fetch packet's loads wait for their data
            if ins.op in ("SPLOOP", "SPLOOPD", "SPLOOPW"):
                if not from_buffer:
                    self.start_loop(ins)
                continue
            if ins.predicate and (self.R[ins.predicate] == 0) != ins.negated:
                if ins.op == "BNOP" and len(ins.operands) > 1:
                    extra = max(extra, parse_int(ins.operands[1]))   # its NOPs are done all the same
                continue
            op = ins.op
            if op == "NOP":
                extra = max(extra, (parse_int(ins.operands[0]) if ins.operands else 1) - 1)
                continue
            if op in ("SPMASK", "SPKERNEL", "DINT", "RINT"):
                continue                    # SPMASK and SPKERNEL are seen by call()
            method: Optional[Callable] = getattr(self, "op_" + op, None)
            if method is None:
                raise NotModelled(f"instruction {op} is not modelled: {ins!r}")
            try:
                more = method(ins, ins.operands)
            except (ValueError, KeyError, IndexError, struct.error) as error:
                raise NotModelled(f"operands of {ins!r} ({type(error).__name__}: {error})") from None
            if more:
                extra = max(extra, more)
        if protected:
            extra = max(extra, 4)
        return extra

    # ---- operands
    def val(self, token: str) -> int:
        """A source operand: a register (as it is in this cycle), ILC/RILC or a constant."""
        if REGISTER.match(token):
            return self.R[token]
        if token in LOOP_COUNTERS:
            return self.control[token]
        return parse_int(token) & M32

    def pair(self, token: str) -> tuple:
        """(low, high) of a register pair "B5:B4"."""
        match = PAIR.match(token)
        if not match:
            raise NotModelled(f"{token} is no register pair")
        return self.R[match.group(2)], self.R[match.group(1)]

    def single_register(self, token: str) -> str:
        if not REGISTER.match(token):
            raise NotModelled(f"{token} is no single register")
        return token

    def dst(self, token: str, value: int, delay: int = 0) -> None:
        match = PAIR.match(token)
        if match:
            self.write(match.group(2), value & M32, delay)
            self.write(match.group(1), (value >> 32) & M32, delay)
        else:
            self.write(token, value, delay)

    def address(self, token: str, size: int) -> int:
        """The address of a memory operand (the base register's update is written as well)."""
        match = MEMORY.match(token.replace(" ", ""))
        if not match:
            raise NotModelled(f"address mode {token}")
        before, base, after, scaled, unscaled = match.groups()
        value = self.R[base]
        if scaled is not None:
            offset = s32(self.R[scaled] if REGISTER.match(scaled) else parse_int(scaled)) * size
        elif unscaled is not None:
            offset = s32(self.R[unscaled] if REGISTER.match(unscaled) else parse_int(unscaled))
        else:
            offset = size if before in ("++", "--") or after else 0
        if before in ("-", "--") or after == "--":
            offset = -offset
        if before in ("++", "--"):
            self.write(base, value + offset, 0)
            return (value + offset) & M32
        if after:
            self.write(base, value + offset, 0)
            return value
        return (value + offset) & M32

    # ---- memory
    def load(self, addr: int, size: int) -> int:
        for lo, hi, data, segment in self.image:
            if lo <= addr and addr + size <= hi:
                if segment.writable or not segment.loaded:
                    raise NotModelled(f"reads {segment.name} at {addr:#010x}, which the effect writes while it runs")
                return int.from_bytes(data[addr - lo:addr - lo + size], "little")
        return int.from_bytes(self.writable(addr, size), "little")

    def store(self, addr: int, size: int, value: int) -> None:
        self.writable(addr, size)[:] = (value & ((1 << 8 * size) - 1)).to_bytes(size, "little")

    def writable(self, addr: int, size: int) -> memoryview:
        """The stack or the text buffer at that address (nothing else is written to)."""
        for lo, data in ((STACK_TOP - STACK_SIZE, self.stack), (BUFFER, self.buffer)):
            if lo <= addr and addr + size <= lo + len(data):
                return memoryview(data)[addr - lo:addr - lo + size]
        raise NotModelled(f"memory access at {addr:#010x} ({size} bytes) outside the stack and the text buffer"
                          + (" (the effect's own memory is only read)" if self.in_image(addr) else ""))

    def in_image(self, addr: int) -> bool:
        return any(lo <= addr < hi for lo, hi, _, _ in self.image)

    def _load(self, it: list, size: int, signed: bool = False) -> None:
        value = self.load(self.address(it[0], size), size)
        if size == 8:
            self.dst(it[1], value, 4)
            return
        if signed and value & 1 << (8 * size - 1):
            value -= 1 << 8 * size
        self.write(self.single_register(it[1]), value, 4)

    def op_LDB(self, i, it): self._load(it, 1, True)
    def op_LDBU(self, i, it): self._load(it, 1)
    def op_LDH(self, i, it): self._load(it, 2, True)
    def op_LDHU(self, i, it): self._load(it, 2)
    def op_LDW(self, i, it): self._load(it, 4)
    def op_LDNW(self, i, it): self._load(it, 4)
    def op_LDDW(self, i, it): self._load(it, 8)
    def op_LDNDW(self, i, it): self._load(it, 8)

    def _store(self, it: list, size: int) -> None:
        if size == 8:
            lo, hi = self.pair(it[0])
            value = lo | hi << 32
        else:
            value = self.R[self.single_register(it[0])]
        self.store(self.address(it[1], size), size, value)

    def op_STB(self, i, it): self._store(it, 1)
    def op_STH(self, i, it): self._store(it, 2)
    def op_STW(self, i, it): self._store(it, 4)
    def op_STNW(self, i, it): self._store(it, 4)
    def op_STDW(self, i, it): self._store(it, 8)
    def op_STNDW(self, i, it): self._store(it, 8)

    # ---- branches (taken after 5 delay slots)
    def _branch(self, target: int) -> None:
        if self.branch is not None:
            raise NotModelled("a branch while another is in flight")
        self.branch = (self.cycle + 6, target)

    def _target(self, ins, it: list) -> int:
        if ins.target is not None:
            return ins.target
        if REGISTER.match(it[0]):
            return self.R[it[0]]
        if it[0] in self.listing.address:
            return self.listing.address[it[0]]
        return parse_int(it[0])

    def op_B(self, i, it):
        if it and it[0] in ("IRP", "NRP"):
            raise NotModelled("a return from an interrupt or exception")
        self._branch(self._target(i, it))

    def op_BNOP(self, i, it):
        self._branch(self._target(i, it))
        return parse_int(it[1]) if len(it) > 1 else 0

    def op_CALLP(self, i, it):
        following = self.listing.eps[self.listing.ep_of[i.addr] + 1][0].addr
        self.write(it[1], following, 0)                                # returns to the next execute packet
        self._branch(self._target(i, it))
        return 5

    def op_ADDKPC(self, i, it):
        self.write(it[1], i.target if i.target is not None else parse_int(it[0]), 0)
        return parse_int(it[2]) if len(it) > 2 else 0

    # ---- moves and constants
    def op_MV(self, i, it): self.write(self.single_register(it[1]), self.val(self.single_register(it[0])), 0)
    def op_MVK(self, i, it): self.write(it[1], parse_int(it[0]), 0)
    def op_ZERO(self, i, it): self.dst(it[0], 0)

    def op_MVKL(self, i, it):
        low = parse_int(it[0]) & 0xFFFF
        self.write(it[1], low - 0x10000 if low & 0x8000 else low, 0)

    def op_MVKH(self, i, it):           # dis6x shows the whole constant: its upper half is taken
        self.write(it[1], self.R[it[1]] & 0xFFFF | parse_int(it[0]) & 0xFFFF0000, 0)

    def op_MVKLH(self, i, it):
        self.write(it[1], self.R[it[1]] & 0xFFFF | (parse_int(it[0]) & 0xFFFF) << 16, 0)

    def op_MVC(self, i, it):
        if it[1] not in LOOP_COUNTERS:
            raise NotModelled(f"MVC to {it[1]}")
        self.control[it[1]] = self.val(it[0])            # takes effect at once (enough for ILC here)

    # ---- integer arithmetic
    def _two(self, it: list) -> tuple:
        """(src1, src2, dst) of a 32-bit operation (the compact two-operand form: src2 is dst)."""
        if any(PAIR.match(token) for token in it):
            raise NotModelled(f"{', '.join(it)}: 40-bit operands are not modelled here")
        if len(it) == 2:
            return self.val(it[0]), self.R[it[1]], it[1]
        return self.val(it[0]), self.val(it[1]), it[2]

    def op_ADD(self, i, it):
        a, b, d = self._two(it)
        self.write(d, a + b, 0)

    def op_SUB(self, i, it):
        a, b, d = self._two(it)
        self.write(d, a - b, 0)

    def op_ADDK(self, i, it): self.write(it[1], self.R[it[1]] + parse_int(it[0]), 0)

    def _adda(self, it: list, shift: int, sign: int = 1) -> None:
        self.write(it[2], self.val(it[0]) + sign * (s32(self.val(it[1])) << shift), 0)

    def op_ADDAB(self, i, it): self._adda(it, 0)
    def op_ADDAH(self, i, it): self._adda(it, 1)
    def op_ADDAW(self, i, it): self._adda(it, 2)
    def op_ADDAD(self, i, it): self._adda(it, 3)
    def op_SUBAB(self, i, it): self._adda(it, 0, -1)
    def op_SUBAH(self, i, it): self._adda(it, 1, -1)
    def op_SUBAW(self, i, it): self._adda(it, 2, -1)

    def op_AND(self, i, it):
        a, b, d = self._two(it)
        self.write(d, a & b, 0)

    def op_ANDN(self, i, it):
        a, b, d = self._two(it)
        self.write(d, a & ~b, 0)

    def op_OR(self, i, it):
        a, b, d = self._two(it)
        self.write(d, a | b, 0)

    def op_XOR(self, i, it):
        a, b, d = self._two(it)
        self.write(d, a ^ b, 0)

    def op_NOT(self, i, it): self.write(it[1], ~self.val(it[0]), 0)
    def op_NEG(self, i, it): self.write(it[1], -s32(self.val(it[0])), 0)
    def op_ABS(self, i, it): self.write(it[1], min(abs(s32(self.val(it[0]))), 2 ** 31 - 1), 0)

    def _compare(self, it: list, test: Callable, signed: bool = True) -> None:
        a, b, d = self._two(it)
        if signed:
            a, b = s32(a), s32(b)
        self.write(d, 1 if test(a, b) else 0, 0)

    def op_CMPEQ(self, i, it): self._compare(it, lambda a, b: a == b)
    def op_CMPGT(self, i, it): self._compare(it, lambda a, b: a > b)
    def op_CMPLT(self, i, it): self._compare(it, lambda a, b: a < b)
    def op_CMPGTU(self, i, it): self._compare(it, lambda a, b: a > b, False)
    def op_CMPLTU(self, i, it): self._compare(it, lambda a, b: a < b, False)

    def _shift(self, it: list) -> tuple:
        value, count, d = self._two(it)
        return value, count & 0x3F, d

    def op_SHL(self, i, it):
        value, count, d = self._shift(it)
        self.write(d, value << count if count < 32 else 0, 0)

    def op_SHR(self, i, it):
        value, count, d = self._shift(it)
        self.write(d, s32(value) >> min(count, 31), 0)

    def op_SHRU(self, i, it):
        value, count, d = self._shift(it)
        self.write(d, value >> count if count < 32 else 0, 0)

    def _field(self, it: list) -> tuple:
        """(src2, csta, cstb, dst) of EXT, EXTU, SET, CLR: constants, or both in src1's bits 9-5, 4-0."""
        if len(it) == 4:
            return self.val(it[0]), parse_int(it[1]), parse_int(it[2]), it[3]
        both = self.val(it[1])
        return self.val(it[0]), (both >> 5) & 31, both & 31, it[2]

    def op_EXT(self, i, it):
        value, a, b, d = self._field(it)
        self.write(d, s32(value << a) >> b, 0)

    def op_EXTU(self, i, it):
        value, a, b, d = self._field(it)
        self.write(d, (value << a & M32) >> b, 0)

    def _bits(self, a: int, b: int) -> int:
        if a > b:
            raise NotModelled(f"bit field {a}..{b}")
        return ((1 << b - a + 1) - 1) << a

    def op_SET(self, i, it):
        value, a, b, d = self._field(it)
        self.write(d, value | self._bits(a, b), 0)

    def op_CLR(self, i, it):
        value, a, b, d = self._field(it)
        self.write(d, value & ~self._bits(a, b), 0)

    # ---- multiplies
    @staticmethod
    def _half(value: int, high: bool, signed: bool) -> int:
        value = value >> 16 & 0xFFFF if high else value & 0xFFFF
        return value - 0x10000 if signed and value & 0x8000 else value

    def _mpy16(self, it: list, high_a: bool, signed_a: bool, high_b: bool, signed_b: bool) -> None:
        a, b, d = self._two(it)
        self.write(d, self._half(a, high_a, signed_a) * self._half(b, high_b, signed_b), 1)

    def op_MPY(self, i, it): self._mpy16(it, False, True, False, True)
    def op_MPYU(self, i, it): self._mpy16(it, False, False, False, False)
    def op_MPYSU(self, i, it): self._mpy16(it, False, True, False, False)
    def op_MPYUS(self, i, it): self._mpy16(it, False, False, False, True)
    def op_MPYH(self, i, it): self._mpy16(it, True, True, True, True)
    def op_MPYHU(self, i, it): self._mpy16(it, True, False, True, False)
    def op_MPYHL(self, i, it): self._mpy16(it, True, True, False, True)
    def op_MPYLH(self, i, it): self._mpy16(it, False, True, True, True)

    def op_MPYLI(self, i, it):           # 16 low bits of src1 times src2, a 64-bit result in a pair
        self.pair(it[2])
        product = self._half(self.val(it[0]), False, True) * s32(self.val(it[1]))
        self.dst(it[2], product & (1 << 64) - 1, 3)

    def _mpy32(self, it: list, signed_a: bool, signed_b: bool) -> None:
        """32 × 32 bits: the low 32 bits of the product, or all 64 in a pair (TI's compiler divides
        by a constant with these)."""
        a, b = self.val(it[0]), self.val(it[1])
        product = (s32(a) if signed_a else a) * (s32(b) if signed_b else b)
        self.dst(it[2], product & ((1 << 64) - 1 if PAIR.match(it[2]) else M32), 3)

    def op_MPY32(self, i, it): self._mpy32(it, True, True)
    def op_MPY32U(self, i, it): self._mpy32(it, False, False)
    def op_MPY32SU(self, i, it): self._mpy32(it, True, False)
    def op_MPY32US(self, i, it): self._mpy32(it, False, True)

    # ---- single precision
    def _single(self, token: str) -> float:
        return single(self.val(self.single_register(token)))

    def op_ADDSP(self, i, it): self.write(it[2], single_bits(self._single(it[0]) + self._single(it[1])), 3)
    def op_SUBSP(self, i, it): self.write(it[2], single_bits(self._single(it[0]) - self._single(it[1])), 3)
    def op_MPYSP(self, i, it): self.write(it[2], single_bits(self._single(it[0]) * self._single(it[1])), 3)
    def op_INTSP(self, i, it): self.write(it[1], single_bits(float(s32(self.val(it[0])))), 3)
    def op_INTSPU(self, i, it): self.write(it[1], single_bits(float(self.val(it[0]))), 3)
    def op_ABSSP(self, i, it): self.write(it[1], single_bits(abs(self._single(it[0]))), 0)

    def op_SPTRUNC(self, i, it):
        value = self._single(it[0])
        if not -2.0 ** 31 <= value < 2.0 ** 31:
            raise NotModelled(f"SPTRUNC of {value}")
        self.write(it[1], int(value), 3)

    def op_RCPSP(self, i, it):
        value = self._single(it[0])
        if not value:
            raise NotModelled("RCPSP of 0")
        self.write(it[1], single_bits(1.0 / value * (1 + self.estimate * RCPSP_ERROR)), 0)

    def _compare_single(self, it: list, test: Callable) -> None:
        self.write(it[2], 1 if test(self._single(it[0]), self._single(it[1])) else 0, 0)

    def op_CMPEQSP(self, i, it): self._compare_single(it, lambda a, b: a == b)
    def op_CMPGTSP(self, i, it): self._compare_single(it, lambda a, b: a > b)
    def op_CMPLTSP(self, i, it): self._compare_single(it, lambda a, b: a < b)

    # ---- double precision (in register pairs)
    def _double(self, token: str) -> float:
        return double(*self.pair(token))

    def op_SPDP(self, i, it): self.dst(it[1], double_bits(self._single(it[0])), 1)

    def op_DPSP(self, i, it): self.write(it[1], single_bits(self._double(it[0])), 3)

    def op_DPTRUNC(self, i, it):
        value = self._double(it[0])
        if not -2.0 ** 31 <= value < 2.0 ** 31:
            raise NotModelled(f"DPTRUNC of {value}")
        self.write(it[1], int(value), 3)

    def op_INTDP(self, i, it): self.dst(it[1], double_bits(float(s32(self.val(it[0])))), 4)
    def op_INTDPU(self, i, it): self.dst(it[1], double_bits(float(self.val(it[0]))), 4)
    def op_ADDDP(self, i, it): self.dst(it[2], double_bits(self._double(it[0]) + self._double(it[1])), 6)
    def op_SUBDP(self, i, it): self.dst(it[2], double_bits(self._double(it[0]) - self._double(it[1])), 6)
    def op_MPYDP(self, i, it): self.dst(it[2], double_bits(self._double(it[0]) * self._double(it[1])), 9)
    def op_MPYSPDP(self, i, it): self.dst(it[2], double_bits(self._single(it[0]) * self._double(it[1])), 6)
    def op_MPYSP2DP(self, i, it): self.dst(it[2], double_bits(self._single(it[0]) * self._single(it[1])), 4)


# ---- the run-time library's integer division (TI's C6000 EABI: A4 / B4), by name in the listing
def _divide(signed: bool, quotient: bool, remainder: bool) -> Callable:
    def helper(cpu: CPU) -> None:
        a, b = cpu.R["A4"], cpu.R["B4"]
        if signed:
            a, b = s32(a), s32(b)
        if b == 0:
            raise NotModelled("a division by zero")
        q = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)     # towards zero, as in C
        if quotient:
            cpu.R["A4"] = q & M32
        if remainder:
            cpu.R["A5" if quotient else "A4"] = (a - q * b) & M32      # divrem: the remainder in A5
    return helper


RUN_TIME = {
    "__divi": _divide(True, True, False), "__c6xabi_divi": _divide(True, True, False),
    "__divu": _divide(False, True, False), "__c6xabi_divu": _divide(False, True, False),
    "__remi": _divide(True, False, True), "__c6xabi_remi": _divide(True, False, True),
    "__remu": _divide(False, False, True), "__c6xabi_remu": _divide(False, False, True),
    "__c6xabi_divremi": _divide(True, True, True), "__c6xabi_divremu": _divide(False, True, True),
}


def texts(listing, segments, function: int, highest: int) -> List[str]:
    """The text a display function writes for each value 0..highest. Each value is run twice: with
    zeros in the registers and the stack, a text buffer of 0xFF and RCPSP's estimate at the low end
    of its error, then with other contents and the high end; a text that differs is refused."""
    first = CPU(listing, segments)
    second = CPU(listing, segments, fill=OTHER_FILL, blank=0, estimate=+1)
    out = []
    for value in range(highest + 1):
        text = first.display(function, value)
        other = second.display(function, value)
        if other != text:
            raise NotModelled(f"value {value}: {text!r} or {other!r} – the text depends on what the call does not set")
        out.append(text)
    return out
