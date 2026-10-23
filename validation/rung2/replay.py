#!/usr/bin/env python3
"""Rung 2: replay a MAME 0.285 trace of a 32X SH-2 on this core, register-exact.

    python validation/rung2/replay.py tests/mame_traces/bus-BCRacersUSA/master.log
    python validation/rung2/replay.py LOG --cache        # keep the cache on (see below)
    python validation/rung2/replay.py LOG --json out.json

The log is ``scripts/mame_trace.py --bus``'s: an ``S`` line with every register before
each instruction, then that instruction's external accesses (``R``/``W`` address, size
in bits, value) and on-chip accesses (``IR``/``IW``). MAME is a detector here, never a
judge (docs/mame-oracle.md); the run is "exact until a difference the docs attribute
to MAME".

**The harness.** Not a 32X: the replay host is a memory that answers every external
read with what MAME's devices answered. Before each instruction it stores the values
of that instruction's logged reads (and the next instruction's fetch, because the core
fetches longwords, PM 7.7.5); after it, every value MAME wrote, which keeps the memory
MAME's as the slave SH-2, the 68000 and the master's DMAC change it. The core's own
writes are compared with MAME's, byte by byte.

**Interrupts** are injected where MAME took them (``chip.replay_interrupts``,
``inject_interrupt``): MAME's interrupt entry shows as two pushes and a vector read
after an instruction's accesses, and the next ``S`` line at the handler. The core's
own modules never raise interrupts during a replay; their timing is not MAME's.

**What the replay substitutes, and why** (each counted in the report):

- *Time-dependent on-chip registers* (FRT FRC/FTCSR/ICR, WDT, the DMAC's
  SAR/DAR/TCR/CHCR/DMAOR, SCI SSR, BSC RTCNT): MAME's clock is an instruction count,
  not the pipeline (C5), so their values at a given instruction differ by construction.
  The core reads MAME's value instead, and the report counts how often the core's own
  value differed.
- *The DMAC's transfers*: their timing is MAME's (``sh7604.cpp`` runs them on timers);
  MAME's DMA writes reach memory through the log, and the core's DMAC is idle.
- *The cache*, unless ``--cache``: MAME has none (docs/mame-oracle.md), so a cached read
  of memory the slave or DMA changed gives MAME the new value and the hardware the old.
  By default CCR.CE is held at 0 (the game's CCR value is kept and read back), so every
  read goes to the bus; ``--cache`` keeps the cache and stops at the first difference
  the cache explains.

**Attributed differences** (resynchronised from MAME's registers and counted):
``divu-overflow`` (C4: MAME's DIVU overflow results), ``mame-interrupt-after-ldc``
(MAME accepts an interrupt right after LDC/LDS/STC/STS, which HM 4.6.2 forbids: C12),
``mame-sleep`` (MAME loops on SLEEP instead of stopping: C12). Any other difference
stops the replay and is reported with its instruction and fields.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import zipfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sh2_python import SH7604  # noqa: E402
from sh2_python.disasm import disassemble_word  # noqa: E402

REGS = ("pc", "sr", "pr", "gbr", "vbr", "mach", "macl", *(f"r{i}" for i in range(16)))
MEMORY = 0x08000000  # MAME's SH-2 masks external addresses to A26-A0

# On-chip registers whose value depends on when it is read (C5): MAME's value is used.
TIME_DEPENDENT = {
    **dict.fromkeys(range(4294966800, 4294966810), "FRT"),
    **dict.fromkeys(range(4294966912, 4294966916), "WDT"),
    **dict.fromkeys((4294966788,), "SCI.SSR"),
    **dict.fromkeys(range(4294967168, 4294967220), "DMAC"),
    **dict.fromkeys((4294966897, 4294966898), "DMAC"),
    **dict.fromkeys(range(4294967284, 4294967288), "BSC.RTCNT"),
}
CCR = 0xFFFFFE92


class Where:
    """Where a replay step is, rendered only when a report needs it."""

    __slots__ = ("line", "number", "opcode", "pc")

    def __init__(self, number: int, line: int, pc: int, opcode: int) -> None:
        self.number, self.line, self.pc, self.opcode = number, line, pc, opcode

    def __str__(self) -> str:
        text = disassemble_word(self.opcode, self.pc).text
        return f"#{self.number} (log line {self.line}) {self.pc:08X} {text}"


class Group:
    """One instruction of the log: the registers before it and the accesses after."""

    __slots__ = ("accesses", "line", "regs")

    def __init__(self, regs: dict[str, int] | None, line: int) -> None:
        self.regs = regs
        self.line = line
        self.accesses: list[tuple[str, int, int, int]] = []  # (tag, address, bits, value)


def read_log(path: Path) -> Iterator[Group]:
    """The log's instruction groups, streamed (a log is gigabytes)."""
    group = Group(None, 0)
    with path.open("rb") as handle:
        for number, raw in enumerate(handle, 1):
            if not raw.endswith(b"\n"):
                break  # a log still being written ends mid-line
            tag, _, rest = raw.partition(b" ")
            if tag == b"S":
                yield group
                values = rest.split()
                v = [int(x, 16) for x in values[:23]]
                regs = {
                    "pc": v[0], "sr": v[1], "pr": v[2], "gbr": v[3], "vbr": v[4],
                    "mach": v[5], "macl": v[6], "r": v[7:23], "cycles": int(values[23]),
                }  # fmt: skip
                group = Group(regs, number)
            elif tag in (b"R", b"W", b"IR", b"IW"):
                fields = rest.split()
                group.accesses.append(
                    (tag.decode(), int(fields[0], 16), int(fields[1], 16), int(fields[2], 16))
                )
    yield group


class Replay:
    def __init__(self, *, cache: bool) -> None:
        self.memory = bytearray(MEMORY)
        self.cache = cache
        self.core_writes: dict[int, int] = {}
        self.onchip_log: dict[int, list[int]] = {}
        self.counts: Counter[str] = Counter()
        self.substituted: Counter[str] = Counter()
        self.first: dict[str, str] = {}
        self.ccr = 0
        chip = SH7604(self.read, self.write, area0_width=16)
        chip.replay_interrupts = True
        chip.dmac.run = lambda: None  # transfers come from MAME's log (module docstring)
        chip.read_onchip = self.read_onchip
        chip.write_onchip = self.write_onchip
        self.chip = chip

    # -- the replay host's bus -------------------------------------------------------

    def read(self, address: int, width: int, kind: int) -> int:
        a = address % MEMORY
        return int.from_bytes(self.memory[a : a + (width >> 3)], "big")

    def write(self, address: int, width: int, value: int, kind: int) -> None:
        a = address % MEMORY
        for i in range(width >> 3):
            self.core_writes[a + i] = (value >> (8 * ((width >> 3) - 1 - i))) & 0xFF

    def store(self, address: int, bits: int, value: int) -> None:
        a = address % MEMORY
        self.memory[a : a + (bits >> 3)] = value.to_bytes(bits >> 3, "big")

    def read_onchip(self, module: object, address: int, width: int) -> int:
        if address == CCR and not self.cache:
            return self.ccr
        value = SH7604._module_read(module, address, width)
        logged = self.onchip_log.get(address)
        if logged:
            theirs = logged.pop(0)
            name = TIME_DEPENDENT.get(address)
            if name is not None:
                if theirs != value:
                    self.substituted[name] += 1
                return theirs
            if theirs != value:
                self.mismatch = (
                    f"on-chip read {address:08X}/{width}: core {value:X}, MAME {theirs:X}"
                )
        return value

    def write_onchip(self, module: object, address: int, width: int, value: int) -> None:
        if address == CCR and not self.cache:
            self.ccr = value & ~0x10  # CP reads 0
            value &= ~0x01  # the cache stays off: every read reaches the log's values
        SH7604._module_write(module, address, width, value)

    # -- the replay ---------------------------------------------------------------------

    def load(self, regs: dict[str, int]) -> None:
        chip = self.chip
        chip.R = list(regs["r"])
        chip.sr, chip.gbr, chip.vbr = regs["sr"], regs["gbr"], regs["vbr"]
        chip.mach, chip.macl, chip.pr = regs["mach"], regs["macl"], regs["pr"]
        chip.pc = regs["pc"]  # the core refetches when the host moves the PC
        chip.delay_target = -1

    def differs(self, regs: dict[str, int]) -> list[str]:
        chip = self.chip
        if (
            chip.pc == regs["pc"]
            and chip.R == regs["r"]
            and chip.sr == regs["sr"]
            and chip.macl == regs["macl"]
            and chip.mach == regs["mach"]
            and chip.pr == regs["pr"]
            and chip.gbr == regs["gbr"]
            and chip.vbr == regs["vbr"]
        ):
            return []
        mine = {
            "pc": chip.pc, "sr": chip.sr, "pr": chip.pr, "gbr": chip.gbr, "vbr": chip.vbr,
            "mach": chip.mach, "macl": chip.macl,
        }  # fmt: skip
        theirs = {name: regs[name] for name in mine}
        for i in range(16):
            mine[f"r{i}"], theirs[f"r{i}"] = chip.R[i], regs["r"][i]
        return [
            f"{name}: core {mine[name]:08X} MAME {theirs[name]:08X}"
            for name in REGS
            if mine[name] != theirs[name]
        ]

    def note(self, kind: str, text: object) -> None:
        self.counts[kind] += 1
        self.first.setdefault(kind, str(text))

    def run(self, path: Path, limit: int | None) -> dict:
        groups = read_log(path)
        reset = next(groups)
        for tag, address, bits, value in reset.accesses:
            if tag == "R":
                self.store(address, bits, value)
        current = next(groups)
        self.load(current.regs)
        chip = self.chip
        replayed = injected = 0
        started = time.time()
        stop = None
        for following in groups:
            if limit is not None and replayed >= limit:
                break
            if following.regs is None:
                break
            # One pass over the instruction's accesses: MAME's reads go into memory
            # before the step, its on-chip reads queue for comparison, its writes are
            # collected, and the last three external accesses kept (interrupt entry).
            memory = self.memory
            onchip: dict[int, list[int]] = {}
            mame_writes: dict[int, int] = {}
            tail: list[tuple[str, int, int, int]] = []
            for access in current.accesses:
                tag, address, bits, value = access
                if tag == "R":
                    a = address % MEMORY
                    memory[a : a + (bits >> 3)] = value.to_bytes(bits >> 3, "big")
                    tail.append(access)
                elif tag == "W":
                    a = address % MEMORY
                    count = bits >> 3
                    for i in range(count):
                        mame_writes[a + i] = (value >> (8 * (count - 1 - i))) & 0xFF
                    tail.append(access)
                elif tag == "IR":
                    onchip.setdefault(address, []).append(value)
            for tag, address, bits, value in following.accesses:
                if tag == "R":  # the next instruction's fetch (the core fetches longwords)
                    self.store(address, bits, value)
                    break
            self.onchip_log = onchip
            self.core_writes = {}
            self.mismatch = None
            pc = chip.pc
            opcode = int.from_bytes(memory[pc % MEMORY : pc % MEMORY + 2], "big")
            before_sleep = chip.sleeping
            # The core fetches two instructions ahead (PM 7.4) and fetches what taken
            # branches discard (U11); this memory is filled one instruction ahead, so
            # the core reads each opcode again when it runs it. Timing is not compared.
            chip._drop_fetched()
            chip.step()
            replayed += 1
            text = Where(replayed, current.line, pc, opcode)
            expected = following.regs
            entry = False
            if chip.pc != expected["pc"] and len(tail) >= 3:
                # Interrupt entry: two pushes then a vector read end the group.
                t = tail[-3:]
                entry = (t[0][0], t[1][0], t[2][0]) == ("W", "W", "R") and (
                    t[2][1] - expected["vbr"]
                ) % 4 == 0
            if entry:
                vector = ((t[2][1] - expected["vbr"]) & 0x1FFFFFFF) >> 2
                level = (expected["sr"] >> 4) & 15
                # An IRL auto-vector is left for the core to compute (HM table 5.3).
                chip.inject_interrupt(level, -1 if vector == 64 + (level >> 1) else vector)
                if chip._no_interrupt or chip.delay_target >= 0:
                    # HM 4.6: no interrupt here; MAME took one anyway.
                    self.note("mame-interrupt-after-ldc", text)
                    chip._no_interrupt = False
                chip._drop_fetched()
                chip.step()
                injected += 1
            # MAME's SLEEP: it loops on the instruction (sh2.cpp SLEEP: pc -= 2).
            if (opcode == 0x001B or before_sleep) and expected["pc"] == pc and not entry:
                self.note("mame-sleep", text)
                chip.sleeping = False
                self.load(expected)
                self.memory_apply(mame_writes)
                current = following
                continue
            problems = self.differs(expected)
            if self.mismatch:
                problems.append(self.mismatch)
            # Every byte the core wrote must be a byte MAME wrote, with MAME's value;
            # bytes only MAME wrote are its DMAC's (allowed while a channel is set up).
            core_writes = self.core_writes
            wrong = {a: v for a, v in core_writes.items() if mame_writes.get(a) != v}
            unmatched = [a for a in mame_writes if a not in core_writes] if mame_writes else []
            if wrong:
                problems.append(f"core writes MAME did not make: {sorted(wrong.items())[:4]}")
            if unmatched and not chip.dmac.active:
                problems.append(f"MAME writes the core did not make: {sorted(unmatched)[:4]}")
            elif unmatched:
                self.counts["dma-writes"] += 1
            if problems:
                kind = self.attribute(problems, opcode)
                if kind is None:
                    stop = {"instruction": str(text), "differences": problems}
                    break
                self.note(kind, f"{text}: " + "; ".join(problems[:3]))
                self.load(expected)
            self.memory_apply(mame_writes)
            current = following
        elapsed = time.time() - started
        return {
            "log": str(path),
            "cache": self.cache,
            "replayed": replayed,
            "interrupts_injected": injected,
            "seconds": round(elapsed, 1),
            "instructions_per_second": round(replayed / elapsed) if elapsed else None,
            "attributed": dict(self.counts),
            "first_attributed": self.first,
            "substituted_onchip_reads": dict(self.substituted),
            "stopped_at": stop,
        }

    def memory_apply(self, writes: dict[int, int]) -> None:
        for a, v in writes.items():
            self.memory[a] = v

    def attribute(self, problems: list[str], opcode: int) -> str | None:
        """The docs' MAME attribution for a difference, or None (C4, C12)."""
        chip = self.chip
        divu_read = any("on-chip read FFFFFF" in p for p in problems)
        if divu_read and chip.divu.dvcr & 1:
            return "divu-overflow"
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("log", type=Path)
    parser.add_argument("--cache", action="store_true", help="keep the cache on")
    parser.add_argument(
        "--seed",
        action="append",
        default=[],
        metavar="FILE[:MEMBER]@ADDRESS",
        help="preload memory (e.g. the boot ROM from the MAME romset, the cartridge), "
        "so cache line fills of words MAME has not yet read have their values",
    )
    parser.add_argument("--limit", type=int, help="stop after this many instructions")
    parser.add_argument("--json", type=Path, help="write the report here")
    args = parser.parse_args()
    runner = Replay(cache=args.cache)
    for spec in args.seed:
        source, _, where = spec.rpartition("@")
        archive, _, member = source.partition(":")
        if member:
            with zipfile.ZipFile(archive) as z:
                data = z.read(member)
        else:
            data = Path(source).read_bytes()
        address = int(where, 0)
        runner.memory[address : address + len(data)] = data
    report = runner.run(args.log, args.limit)
    text = json.dumps(report, indent=1)
    print(text)
    if args.json:
        args.json.write_text(text + "\n")
    return 0 if report["stopped_at"] is None else 1


if __name__ == "__main__":
    sys.exit(main())
