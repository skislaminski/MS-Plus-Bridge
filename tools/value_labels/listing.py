"""The effect's code as TI's disassembler (dis6x) lists it: instructions, execute packets, labels
and the fetch-packet headers. Decoding the C674x's instructions (also the compact 16-bit ones and
the headers that say how a fetch packet is laid out) is dis6x's job; this only reads its text.

dis6x comes with TI's C6000 code generation tools (ti-cgt-c6000, free from TI); the tool looks for
it at --dis6x, then the environment variable TI_CGT_DIS6X, then on the PATH, then where TI's
installer puts it on a Mac.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_DIS6X = "/Applications/ti/ti-cgt-c6000_8.5.0.LTS/bin/dis6x"

LABEL = re.compile(r"^([0-9a-f]{8})\s+(\S+):\s*$")
# address, encoding (4 or 8 hex digits), "||" (in parallel with the one before), "^" (masked in a
# software-pipelined loop), predicate ("[!A0]"), mnemonic with unit, operands
INSTRUCTION = re.compile(r"^([0-9a-f]{8})\s+([0-9a-f]{4}|[0-9a-f]{8})\s+(\|\|)?(\^)?\s*"
                         r"(?:\[\s*(!?)([AB]\d+)\s*\])?\s*(\S+)\s*(.*?)\s*$")
TARGET = re.compile(r"\s*\(PC[+-]\d+ = 0x([0-9a-f]+)\)")     # where a branch goes, as dis6x works it out


def dis6x_path(given: Optional[str] = None) -> Optional[str]:
    """dis6x as given, else as TI_CGT_DIS6X says, else on the PATH or at TI's default place; None
    if it is not there (a path given or set is not looked past)."""
    named = given or os.environ.get("TI_CGT_DIS6X")
    for candidate in ((named,) if named else (shutil.which("dis6x"), DEFAULT_DIS6X)):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def disassemble(elf: bytes, dis6x: str) -> str:
    """The listing of an ELF's code sections (dis6x -d: without the data sections)."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder, "effect.out")
        path.write_bytes(elf)
        done = subprocess.run([dis6x, "-d", str(path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = done.stdout.decode("latin1")
    if done.returncode != 0:
        raise RuntimeError(f"dis6x failed ({done.returncode}): {text.strip()[-200:]}")
    return text


class Instruction:
    __slots__ = ("addr", "parallel", "masked", "negated", "predicate", "op", "unit", "args",
                 "operands", "target", "protected")

    def __repr__(self) -> str:
        predicate = f"[{'!' if self.negated else ''}{self.predicate}] " if self.predicate else ""
        unit = f".{self.unit}" if self.unit else ""
        return f"{self.addr:08x} {'||' if self.parallel else '  '}{predicate}{self.op}{unit} {self.args}"


class Listing:
    """What dis6x lists of the TEXT sections. eps: the execute packets in address order (lists of
    the instructions that run in one cycle), at: address of an execute packet → its index, ep_of:
    address of an instruction → the index of its execute packet, labels: address → names,
    address: name → address."""

    def __init__(self, text: str):
        self.labels: Dict[int, List[str]] = {}
        self.address: Dict[str, int] = {}
        instructions: List[Instruction] = []
        protected = set()                     # fetch packets (32 bytes) whose loads wait for their data
        in_text = False
        for line in text.splitlines():
            if line.startswith("TEXT Section"):
                in_text = True
                continue
            if line.startswith("DATA Section"):
                in_text = False
                continue
            if not in_text:
                continue
            match = LABEL.match(line)
            if match:
                addr = int(match.group(1), 16)
                self.labels.setdefault(addr, []).append(match.group(2))
                self.address.setdefault(match.group(2), addr)
                continue
            match = INSTRUCTION.match(line)
            if not match or match.group(7) == ".word":
                continue
            addr = int(match.group(1), 16)
            if match.group(7) == ".fphead":
                if match.group(8).split(",")[0].strip() == "p":
                    protected.add(addr & ~31)
                continue
            if match.group(7).startswith("."):
                continue                      # another directive
            instruction = Instruction()
            instruction.addr = addr
            instruction.parallel = bool(match.group(3))
            instruction.masked = bool(match.group(4))
            instruction.negated = match.group(5) == "!"
            instruction.predicate = match.group(6)
            instruction.op, _, instruction.unit = match.group(7).partition(".")
            instruction.args = match.group(8)
            target = TARGET.search(instruction.args)
            instruction.target = int(target.group(1), 16) if target else None
            operands = TARGET.sub("", instruction.args)
            instruction.operands = [item.strip() for item in operands.split(",")] if operands.strip() else []
            instructions.append(instruction)
        instructions.sort(key=lambda instruction: instruction.addr)
        self.eps: List[List[Instruction]] = []
        for instruction in instructions:
            instruction.protected = (instruction.addr & ~31) in protected
            if instruction.parallel and self.eps:
                self.eps[-1].append(instruction)
            else:
                self.eps.append([instruction])
        self.at = {ep[0].addr: index for index, ep in enumerate(self.eps)}
        self.ep_of = {instruction.addr: index for index, ep in enumerate(self.eps) for instruction in ep}
