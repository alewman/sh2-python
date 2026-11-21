#!/usr/bin/env python3
"""Rung 4: DN/DP's CPS3 loops and purge-order values, rebuilt and run on this core.

    python validation/rung4/dn.py                 # every loop, cold and warm, and the values
    python validation/rung4/dn.py --ids 82-90     # some loops
    python validation/rung4/dn.py --json out.json

**The evidence** (docs/validation.md "DN, DP"): one CPS3 board, a stock SH7604-family
part at 25 MHz, BCR1 = H'0020, BCR2 = H'00E8, WCR = H'AA57. P2 timed 185 loops with
the FRT (one tick = 8 clocks), eight samples each: even samples right after a requested
cache purge (cold), odd samples with the cache as the previous sample left it (warm).
Hardware-captured, one board, one author, not replicated: a **judge** for CPU-resident
behaviour (load-use, MA/IF, multiplier issue and result, purge ordering), a
**detector** for anything that crosses the CPS3's bus (decision 7, docs/handoff-brief.md).

**What is rebuilt and how** (``dn_p2.json``, extracted by scripts/extract_dn_rung4.py):

- Each loop routine is assembled (``sh2asm.py``) from its published text at its
  published address, with P2's framing. The companion gives the body, prologue and
  epilogue as text and the address of every instruction, but not the eight framing
  instructions: every routine has exactly three before its prologue and five after its
  body and epilogue, whatever its trip count (2 to 1,024). Bodies that call a
  subroutine (``JSR @R12``) need PR saved, and P1's structural counts
  (``benchmark_contracts.json``) give two loop instructions per iteration at four
  clocks (``DT`` 1 + a taken ``BF`` 3), so the five are ``DT R1``, ``BF loop``,
  ``LDS.L @R15+,PR``, ``RTS``, ``NOP`` (with ``BF/S`` and a slot there would be six),
  and the three are ``STS.L PR,@-R15`` and two this module makes ``NOP`` (the runner
  passes the trip count in R1 here). :func:`build` checks that the rebuilt
  routine's instructions and literal pool fall on exactly the halfwords the probe's
  instruction list marks, which pins every body instruction's address and so its
  longword alignment (the MA/IF rule depends on it).
- **The runner** is not published (P2CPUS calls each routine and reads the FRC before
  and after). Here: ``JSR @R12; NOP`` returning to H'06704840 (``RUNNER``), and the
  window from the JSR's step to the return. The runner's own cost is one constant
  ``R`` added to every window, fitted to the three empty loops (tests 0, 40 and 61:
  trip counts 1,024, 64 and 256; :func:`fit_runner`); every other loop is then a
  prediction. Where the call and the stack sit decides which cache sets they share
  with a loop's data, so both are placed where they push out no warm loop's lines, as
  the board's evidently do not (``RUNNER``, ``STACK``).
- **A tick reading** of a window of T clocks is floor(T/8) or ceil(T/8) depending on
  the FRT's phase, so T matches a set of warm samples when every sample is one of the
  two: T in [8 max - 7, 8 min + 7]. Where the samples spread by more than one tick, the
  interval is their own [8 min, 8 max].
- **CPU-resident** means the warm window makes no external bus cycle but the
  framing's own (the PR push), counted on the bus callbacks. Those loops are the gate;
  the rest are reported as detectors.

**The CPS3-like bus** (:class:`Cps3Board`): BCR1/BCR2/WCR written through the BSC with
DP's values (the core's defaults stay HM's), area 0 eight bits wide (DP section 5.3:
the BIOS is a 512k x 8 flash and area 0 takes its width from the mode pins), work RAM
at H'02000000 (area 1, 16 bits), program flash at H'06000000 (area 3, 32 bits). Each
area's external cycle is the BSC's (2 states + WCR's waits) plus a fixed number of
board waits read off DP's table 6: area 1 one (a 16-bit transfer of 4 states: a long
write 8, P1 7.95; a word write 4, P1 3.73-3.74), area 3 two (5 states a transfer:
"four 32-bit transfers of five clocks", a line fill 20.06). Those are the CPS3's,
fitted to its bus: they affect only cold samples and bus-crossing loops (detectors).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from sh2asm import Program, assemble, check_round_trip  # noqa: E402

from sh2_python import SH7604  # noqa: E402
from sh2_python.bus import FILL  # noqa: E402

DATA = json.loads((HERE / "dn_p2.json").read_text())
LOOPS = {loop["id"]: loop for loop in DATA["loops"]}

#: DP section 2: the CPS3's bus settings.
BCR1, BCR2, WCR = 0x0020, 0x00E8, 0xAA57
#: Board waits per area (DP table 6; module docstring). Area 2 (video, I/O) and area 0
#: (BIOS) devices are not modelled: loops that use them are not run.
BOARD_WAITS = (0, 1, 0, 2)

#: The call. P2's runner (P2CPUS, H'06704804-) is not published; the companion's model
#: runs it to H'06704840 (``model/p2_core_b26.json``, its command line), so the call
#: here returns there. At P2CPUS's own address (cache set 0) it pushed a data line of
#: the stride-1,024 conflict loops out between samples, which the board's runner does
#: not (tests 123 and 124 hit warm); here its lines are in sets 3 and 4.
RUNNER = 0x0670483C
TRAP = 0x06705820  # P2TRAP: where an exception lands (a branch to itself here)
VECTORS = 0x06000000  # the probe's VBR (P2 build header: board_vbr H'06000000)
#: A stack in work RAM (cache area). P2's is not published; this one's line (set 63)
#: is one no loop's data uses, as the board's evidently is not (the four-way conflict
#: loops hit with four lines in one set, tests 118 and 124).
STACK = 0x0207F7F8
RESULTS = 0x0207AC00  # P2's result area in work RAM

#: The empty loops the runner constant is fitted to (trip counts 1,024, 64, 256).
CALIBRATION = (0, 40, 61)

#: Loops this harness does not run: they read or write CPS3 devices it does not model
#: (video, I/O and sound in area 2; the BIOS in area 0), or the FRT, whose register
#: access timing (3-12 states by phase, HM 11.7) the core does not model yet.
NOT_MODELLED = re.compile(r"=0x2[45]|=0x2001|biosfill|frc_rd|ftcsr_rd|frt_pair|=0xFFFFFE1")


class Trapped(Exception):
    """An exception sequence reached TRAP; ``pc`` is the PC it saved."""

    def __init__(self, pc: int) -> None:
        super().__init__(f"exception taken; saved PC {pc:#010x}")
        self.pc = pc


class Cps3Board:
    """Memory, the CPS3-like bus, and a cycle count, around one :class:`SH7604`."""

    def __init__(self) -> None:
        self.ram = bytearray(0x80000)  # area 1: work RAM, H'02000000-
        self.flash = bytearray(0x800000)  # area 3: program, H'06000000-
        self.cycles = 0
        self.fills = 0
        self.chip = SH7604(self.read, self.write, area0_width=8)
        bsc = self.chip.bsc
        for address, value in ((0xFFFFFFE0, BCR1), (0xFFFFFFE4, BCR2), (0xFFFFFFE8, WCR)):
            bsc.write(address, 32, 0xA55A0000 | value)
        self.chip.vbr = VECTORS
        for vector in range(256):
            self.poke32(VECTORS + 4 * vector, TRAP)
        self.load(assemble("trap:\n bra trap\n nop\n", TRAP))

    def _memory(self, address: int) -> tuple[bytearray | None, int]:
        area = (address >> 25) & 3
        if area == 1:
            return self.ram, address & 0x7FFFF
        if area == 3:
            return self.flash, address & 0x7FFFFF
        return None, 0

    def read(self, address: int, width: int, kind: int) -> int:
        self.cycles += 1
        if kind & FILL:
            self.fills += 1
        self.chip.wait += BOARD_WAITS[(address >> 25) & 3]
        memory, offset = self._memory(address)
        if memory is None:
            return 0
        return int.from_bytes(memory[offset : offset + width // 8], "big")

    def write(self, address: int, width: int, value: int, kind: int) -> None:
        self.cycles += 1
        self.chip.wait += BOARD_WAITS[(address >> 25) & 3]
        memory, offset = self._memory(address)
        if memory is not None:
            memory[offset : offset + width // 8] = value.to_bytes(width // 8, "big")

    def load(self, program: Program) -> None:
        memory, offset = self._memory(program.origin & 0x1FFFFFFF)
        data = program.bytes()
        memory[offset : offset + len(data)] = data  # type: ignore[index]

    def poke32(self, address: int, value: int) -> None:
        memory, offset = self._memory(address & 0x1FFFFFFF)
        memory[offset : offset + 4] = value.to_bytes(4, "big")  # type: ignore[index]

    def peek32(self, address: int) -> int:
        memory, offset = self._memory(address & 0x1FFFFFFF)
        return int.from_bytes(memory[offset : offset + 4], "big")  # type: ignore[index]

    def set_ccr(self, value: int) -> None:
        """Write CCR as the runner does between samples (outside the timed window)."""
        self.chip.cache.write_ccr(value)

    def call(
        self, routine: int, registers: dict[int, int], *, runner: int = 0, limit: int = 2_000_000
    ) -> int:
        """Run ``JSR @R12; NOP`` at ``runner`` into ``routine``; return the window in clocks.

        The window is from the JSR's step to the step boundary after the return; for the
        value tests' runner it also takes the result store after it.
        """
        runner = runner or RUNNER
        stop = runner + (4 if runner == RUNNER else 8)
        chip = self.chip
        for n, value in registers.items():
            chip.R[n] = value
        chip.R[12] = routine
        chip.R[15] = STACK
        chip.delay_target = -1
        chip.pc = runner
        start = chip.clock
        steps = 0
        while chip.pc != stop or chip.delay_target >= 0:
            if chip.pc == TRAP:
                raise Trapped(self.peek32(chip.R[15]))
            chip.step()
            steps += 1
            if steps > limit:
                raise RuntimeError("no return")
        return chip.clock - start


RUNNER_CODE = assemble(" jsr @r12\n nop\n nop\n nop\n", RUNNER)


def routine_source(loop: dict) -> str:
    """The routine's text with P2's framing as this module rebuilds it (module docstring)."""
    return (
        " sts.l pr,@-r15\n nop\n nop\n"
        + loop["pro"]
        + f"p2loop{loop['id']}:\n"
        + loop["body"]
        + f" dt r1\n bf p2loop{loop['id']}\n"
        + loop["epi"]
        + " lds.l @r15+,pr\n rts\n nop\n"
    )


def called_routines() -> list[Program]:
    """RTSSTUB, STRAIGHT1K/8K, P2FETCHn: straight-line NOPs ending in RTS; NOP."""
    out = []
    for info in DATA["called"].values():
        source = " nop\n" * (info["instructions"] - 2) + " rts\n nop\n"
        out.append(assemble(source, info["address"]))
    return out


def symbols() -> dict[str, int]:
    return {name: info["address"] for name, info in DATA["called"].items()}


def build(loop: dict) -> Program:
    """Assemble one loop's routine and check it against the probe's instruction map."""
    program = assemble(routine_source(loop), loop["routine"], symbols())
    check_round_trip(program)
    expected = [
        loop["routine"] + 2 * i
        for i in range((loop["end"] - loop["routine"]) // 2)
        if 2 * i not in loop["data"]
    ]
    if program.code != expected:
        raise AssertionError(f"test {loop['id']}: rebuilt instructions are not where P2's are")
    if program.origin + 2 * len(program.words) > loop["end"]:
        raise AssertionError(f"test {loop['id']}: rebuilt routine overruns the next one")
    return program


def tick_interval(samples: list[int]) -> tuple[int, int]:
    """The windows (clocks) whose tick readings can be every one of ``samples``."""
    low, high = 8 * max(samples) - 7, 8 * min(samples) + 7
    if low > high:
        return 8 * min(samples), 8 * max(samples)
    return low, high


def board() -> Cps3Board:
    """A board with the runner and the called routines loaded."""
    b = Cps3Board()
    b.load(RUNNER_CODE)
    for program in called_routines():
        b.load(program)
    return b


@dataclass
class LoopRun:
    """One loop on the core: its cold and warm windows and bus cycles."""

    id: int
    name: str
    group: str
    cold: int
    warm: int
    warm_cycles: int
    warm_samples: list[int]
    cold_samples: list[int]
    extra: dict = field(default_factory=dict)


def run_loop(loop: dict, *, b: Cps3Board | None = None) -> LoopRun:
    """Sample 0 (cold: after a requested purge) and sample 1 (warm), as P2 ordered them."""
    b = b or board()
    b.load(build(loop))
    windows, cycles = [], []
    for sample in range(2):
        if sample == 0:
            b.set_ccr(loop["ccr"] | 0x10)  # CP: P2 purges before every even sample
        else:
            b.set_ccr(loop["ccr"])
        before = b.cycles
        windows.append(b.call(loop["routine"], {1: loop["n"], 0: 0, 2: 3, 3: 5}))
        cycles.append(b.cycles - before)
    return LoopRun(
        loop["id"], loop["name"], loop["group"], windows[0], windows[1], cycles[1],
        loop["samples"][1::2], loop["samples"][0::2],
    )  # fmt: skip


def fit_runner(runs: dict[int, LoopRun]) -> tuple[int, int]:
    """The interval of runner constants R for which every calibration loop matches."""
    low, high = -(10**9), 10**9
    for n in CALIBRATION:
        lo, hi = tick_interval(runs[n].warm_samples)
        low, high = max(low, lo - runs[n].warm), min(high, hi - runs[n].warm)
    return low, high


def runnable(loop: dict) -> bool:
    return not NOT_MODELLED.search(loop["pro"] + loop["body"] + loop["name"])


def run_all(ids: list[int] | None = None) -> dict[int, LoopRun]:
    wanted = sorted(set(ids or LOOPS) | set(CALIBRATION))
    return {n: run_loop(LOOPS[n]) for n in wanted if runnable(LOOPS[n])}


def evaluate(runs: dict[int, LoopRun], runner: int) -> list[dict]:
    framing_cycles = runs[0].warm_cycles  # the empty loop's: the PR push only
    rows = []
    for n, r in sorted(runs.items()):
        lo, hi = tick_interval(r.warm_samples)
        total = r.warm + runner
        rows.append(
            {
                "id": n,
                "name": r.name,
                "group": r.group,
                "resident": r.warm_cycles <= framing_cycles,
                "model_warm": total,
                "board_warm": r.warm_samples,
                "allowed": [lo, hi],
                "ok": lo <= total <= hi,
                "error_ticks": (total / 8) - sum(r.warm_samples) / len(r.warm_samples),
                "model_cold": r.cold + runner,
                "board_cold": r.cold_samples,
            }
        )
    return rows


# -- the purge-order values (DN section 2, tests 185 and 186) ------------------------------

A = 0x0202AA00  # the cached test line
U = A | 0x20000000  # its cache-through alias

#: DN section 4's compact body, which "preserves P2's order, including the CCR-address
#: literal load after the first store"; test 186 adds the second CCR write. P2's
#: routines are two instructions longer and use no other literal (the probe's map:
#: 17 and 19 instructions, three literals each); the body uses R8, which the Hitachi
#: calling convention preserves, so the two are taken to be its push and pop.
#: :func:`run_values` checks the rebuilt routines against the map, which places every
#: literal: A in one line, U and the CCR address in the next. The CCR-address load
#: after the store hits only because U's load filled that line before the store.
VALUE_BODY = """
 mov.l r8,@-r15
 mov.l ={a},r6
 mov.l ={u},r7
 mov #17,r0
 mov.l r0,@r7
 mov.l =0xFFFFFE92,r2
 mov #0x11,r0
 mov.b r0,@r2
 mov.l @r6,r8
 mov #34,r0
 mov.l r0,@r7
{second}
 mov.l @r6,r0
 shll8 r8
 or r8,r0
 mov.l @r15+,r8
 rts
 nop
"""
SECOND_PURGE = " mov #0x11,r0\n mov.b r0,@r2\n"

#: Test 187 (DN section 2 and the "optional fetch extension" of section 4): MOV #17,R0;
#: RTS; NOP written through the alias, a purge requested, the cached address called.
#: P2's routine is 24 instructions with five literals and is not published; this is
#: DN's description, so only its outcome is compared.
CODE_BODY = """
 sts.l pr,@-r15
 mov.l ={a},r6
 mov.l ={u},r7
 mov.l =0xE011000B,r0
 mov.l r0,@r7
 mov #9,r0
 mov.w r0,@(4,r7)
 mov.l =0xFFFFFE92,r2
 mov #0x11,r0
 mov.b r0,@r2
 jsr @r6
 nop
 lds.l @r15+,pr
 rts
 nop
"""


#: P2's repetition wrapper (p2valrep185, H'06705850): the call and the result store.
VALUE_RUNNER = assemble(" jsr @r12\n nop\n mov.l r0,@r10\n add #4,r10\n", 0x06705850)


def value_routine(name: str, source: str) -> Program:
    """Assemble a value test's routine at P2's address and check it against the map."""
    test = DATA["values"][name]
    program = assemble(source, test["routine"])
    check_round_trip(program)
    expected = [
        test["routine"] + 2 * i
        for i in range((test["end"] - test["routine"]) // 2)
        if 2 * i not in test["data"]
    ]
    if program.code != expected:
        raise AssertionError(f"{name}: rebuilt instructions are not where P2's are")
    return program


def run_values(b: Cps3Board | None = None) -> dict:
    """Tests 185 and 186, four repetitions each, the cache kept between them; then 187."""
    b = b or board()
    tests = DATA["values"]
    alias = value_routine("data_alias", VALUE_BODY.format(a=A, u=U, second=""))
    purge = value_routine("data_purge", VALUE_BODY.format(a=A, u=U, second=SECOND_PURGE))
    for program in (alias, purge, VALUE_RUNNER):
        b.load(program)
    # "Establish an invalid test line before test 185 only" (DN section 4): the cache
    # on, everything invalid.
    b.set_ccr(0x11)
    b.chip.R[10] = RESULTS
    out: dict = {}
    for name, program in (("data_alias", alias), ("data_purge", purge)):
        values = []
        for _ in range(4):
            b.call(program.origin, {}, runner=VALUE_RUNNER.origin)
            values.append(f"{b.chip.R[0]:08X}")
        out[name] = {"core": values, "board": tests[name]["values"]}
    code = assemble(CODE_BODY.format(a=A, u=U), tests["code_alias"]["routine"])
    check_round_trip(code)
    b.load(code)
    try:
        b.call(code.origin, {}, runner=VALUE_RUNNER.origin)
        out["code_alias"] = {"core": "returned", "r0": f"{b.chip.R[0]:08X}"}
    except Trapped as trap:
        out["code_alias"] = {"core": f"trap at {trap.pc:08X}"}
    out["code_alias"]["board"] = f"trap at {DATA['code_alias_trap_pc']:08X}"
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ids", help="loops to run, e.g. 82-90,151")
    parser.add_argument("--json", help="write the results here")
    args = parser.parse_args()
    ids = None
    if args.ids:
        ids = []
        for part in args.ids.split(","):
            first, _, last = part.partition("-")
            ids += list(range(int(first), int(last or first) + 1))
    runs = run_all(ids)
    low, high = fit_runner(runs)
    runner = (low + high) // 2
    print(f"runner constant R: {runner} clocks (fits the empty loops for {low}..{high})")
    rows = evaluate(runs, runner)
    for row in rows:
        mark = "ok  " if row["ok"] else "MISS"
        kind = "cpu " if row["resident"] else "bus "
        print(
            f"{mark} {kind}{row['id']:3} {row['name']:24} model {row['model_warm'] / 8:9.1f}"
            f"  board {row['board_warm']}  ({row['error_ticks']:+.1f} ticks)"
            f"  cold model {row['model_cold'] / 8:8.1f} board {row['board_cold']}"
        )
    resident = [r for r in rows if r["resident"]]
    print(
        f"CPU-resident: {sum(r['ok'] for r in resident)}/{len(resident)} within the warm spread;"
        f" bus-crossing (detector): {sum(r['ok'] for r in rows if not r['resident'])}"
        f"/{sum(not r['resident'] for r in rows)}"
    )
    values = run_values()
    for name, v in values.items():
        print(f"{name}: core {v['core']} board {v['board']}")
    if args.json:
        Path(args.json).write_text(
            json.dumps({"runner": runner, "rows": rows, "values": values}, indent=1)
        )


if __name__ == "__main__":
    main()
