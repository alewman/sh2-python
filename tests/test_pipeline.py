"""Rung 3: PM section 7's pipeline figures, reproduced state for state by the scheduler.

Every SH-2 figure of PM 7.1-7.7 is a :class:`Figure` below: the instruction sequence it
draws, the memory it assumes, and its rows transcribed column by column. A figure's
column is one state (PM's figures assume one-state memory, PM 7.2.3), so a row such
as ``"IF - ID EX M:2 WB"`` from column 2 says: IF in state 2, a stall, ID in state 4,
EX in 5, an MA of two states (PM's dotted ``M--A``) from 6, WB in 8. ``IF:2`` and
``MA:3`` are stages of that many states (figures 7.4, 7.5); ``mm`` marks a state in
which the multiplier runs; ``-`` is a stall. Rows are in the figure's order, which is
the order of their fetches; a row of only ``IF`` is a fetched and discarded
instruction.

How a run is read back (``timeline``): from ``chip.pipeline_log``, each stage's first
state. In a slot where an MA meets an ``IF`` (or a load-use split, PM 7.5), the MA
comes first and the other stages after it (PM 7.4.1); otherwise every stage starts with
the slot (PM 7.2.3: "the instruction with the longest stage stalls others with shorter
stages", figure 7.4); WB starts when its MA ends ("WB is executed immediately after
MA", PM 7.2.1); ``mm`` starts when the multiply's MA ends (PM 7.7.2).

Two kinds of figure (PM 7.7's introduction: "When there is contention between IF and
MA, the slot will split, but the manner of the split is not described in the tables,
with a few exceptions"):

- ``contention=True``: the figures that say they assume MA and IF contention, and the
  general figures of 7.4-7.5. They run with the SH-2's real longword fetches (an
  instruction at 4n + 2 after one at 4n is an ``if``, PM 7.4.2) and are compared
  column for column, ``IF`` and ``if`` distinguished.
- ``contention=False``: the figures that draw no split. They run on a test board whose
  every fetch is an ``if`` (no IF/MA contention, as drawn) and are compared column for
  column, ``IF`` and ``if`` alike; :func:`test_contention_never_moves_a_stage_to_another_slot`
  then runs them with real fetches and checks every stage stays in its slot.

Where a figure contradicts another figure or a rule, the transcription follows the
ruling recorded in docs/undocumented-behavior.md (C13-C17) and says so in ``note``.
"""

import struct
from dataclasses import dataclass, field

import pytest

from sh2_python import SH7604
from sh2_python.bus import FETCH, FILL
from sh2_python.pipeline import CLASSES, EXCEPTIONS, RING_MASK

CODE = 0x06000000
DATA = 0x06002000
STACK = 0x06008000
HANDLER = 0x06004000

# Instructions the figures use. "Other instruction" rows are a load (EX MA WB), a store
# (EX MA) or a NOP (EX), as the row draws them; registers are kept apart so no
# load-use split appears where the figure draws none.
NOP = 0x0009
LOAD = 0x6A92  # MOV.L @R9,R10
LOAD2 = 0x6C92  # MOV.L @R9,R12
LOAD3 = 0x6D92  # MOV.L @R9,R13
STORE = 0x2B82  # MOV.L R8,@R11
MAC_W = 0x421F  # MAC.W @R1+,@R2+
MAC_L = 0x021F  # MAC.L @R1+,@R2+
MULS_W = 0x243F  # MULS.W R3,R4
DMULS_L = 0x343D  # DMULS.L R3,R4
STS_MACL = 0x051A  # STS MACL,R5
STS_L_MACL = 0x4612  # STS.L MACL,@-R6
LDS_MACL = 0x471A  # LDS R7,MACL
LDS_L_MACL = 0x4816  # LDS.L @R8+,MACL
MOV = 0x6213  # MOV R1,R2
ADD = 0x321C  # ADD R1,R2
AND = 0x2219  # AND R1,R2
SHLL = 0x4100  # SHLL R1
LDC_GBR = 0x411E  # LDC R1,GBR
AND_B = 0xCD01  # AND.B #1,@(R0,GBR)
TAS = 0x4C1B  # TAS.B @R12
LDC_L = 0x4917  # LDC.L @R9+,GBR
STC_L = 0x4B03  # STC.L SR,@-R11
LDS_L_PR = 0x4926  # LDS.L @R9+,PR
STS_L_PR = 0x4B22  # STS.L PR,@-R11
CLRMAC = 0x0028
RTE = 0x002B
SLEEP = 0x001B
TRAPA = 0xC320  # TRAPA #H'20
UNDEFINED = 0xFFFF
BT_8 = 0x8902  # BT to its address + 8
BT_S_8 = 0x8D02  # BT/S to its address + 8
BRA_8 = 0xA002  # BRA to its address + 8
LOAD_ODD = 0x62E2  # MOV.L @R14,R2 with R14 odd: an address error
LOAD_R3 = 0x6C32  # MOV.L @R3,R12
MAC_L_R5 = 0x065F  # MAC.L @R5+,@R6+ (longword-aligned after a MAC.W moved R1 and R2)

VECTORS = {4: HANDLER, 6: HANDLER, 9: HANDLER, 0x20: HANDLER, 66: HANDLER}


@dataclass
class Figure:
    number: str
    title: str
    program: list[int]
    rows: list[tuple[int, str]]
    contention: bool = False
    lead: int = 0  # program words before the figure's first instruction
    offset: int = 0  # the program's address modulo 4 (2: it starts at 4n + 2)
    upto: int = 99  # columns after this depend on instructions the figure does not draw
    sr: int = 0
    irl_after: int = -1  # raise IRL 5 after this many steps (an interrupt figure)
    waits: dict[int, int] = field(default_factory=dict)  # address -> board waits
    note: str = ""


def _board(fig: Figure):
    memory = bytearray(0x10000)
    low = bytearray(0x400)
    for vector, address in VECTORS.items():
        low[4 * vector : 4 * vector + 4] = address.to_bytes(4, "big")
    base = CODE + fig.offset
    code = b"".join(struct.pack(">H", w) for w in fig.program)
    memory[base - CODE : base - CODE + len(code)] = code
    memory[HANDLER - CODE : HANDLER - CODE + 64] = struct.pack(">H", NOP) * 32
    # RTE's stack: the return address, then SR.
    memory[STACK - CODE : STACK - CODE + 8] = struct.pack(">II", CODE + 0x100, 0)
    memory[CODE + 0x100 - CODE : CODE + 0x140 - CODE] = struct.pack(">H", NOP) * 32
    bus: list[tuple[int, int, int]] = []
    chip: SH7604

    def read(address: int, width: int, kind: int) -> int:
        bus.append((address, width, kind))
        chip.wait += fig.waits.get(address, 0)
        if address < len(low):
            return int.from_bytes(low[address : address + width // 8], "big")
        offset = (address & 0x1FFFFFFF) - CODE
        if 0 <= offset < len(memory):
            return int.from_bytes(memory[offset : offset + width // 8], "big")
        return 0

    def write(address: int, width: int, value: int, kind: int) -> None:
        chip.wait += fig.waits.get(address, 0)
        offset = (address & 0x1FFFFFFF) - CODE
        if 0 <= offset < len(memory):
            memory[offset : offset + width // 8] = value.to_bytes(width // 8, "big")

    chip = SH7604(read, write)
    chip.bsc.cycle_states[:] = [1, 1, 1, 1]  # PM's one-state memory
    chip.R = [DATA + 0x40 * i for i in range(16)]
    chip.R[14] = DATA + 1  # LOAD_ODD's misaligned address
    chip.R[15] = STACK
    chip.gbr = 0x10
    chip.sr = fig.sr
    chip.pc = base
    return chip, bus


def run(fig: Figure, *, contention: bool | None = None, steps: int = 30):
    """Run a figure's program; return (the chip's log, the bus reads)."""
    chip, bus = _board(fig)
    real = fig.contention if contention is None else contention
    if not real:
        place = chip._place

        def place_free(slot: int) -> None:
            # The figure draws no IF/MA contention: every fetch is an 'if' here, and
            # the opcode is read when it runs (pipeline._opcode).
            place(slot)
            chip._p_if[slot & RING_MASK] = -2

        chip._place = place_free
    chip.pipeline_log = log = []
    chip._pipeline_restart()
    for n in range(steps):
        if n == fig.irl_after:
            chip.irl = 5
        chip.step()
    return log, bus


STAGES = {
    name: [s for s in c.stages[2:] if s != "mm"] for name, c in {**CLASSES, **EXCEPTIONS}.items()
}


def timeline(log: list) -> list[dict]:
    """Rows rebuilt from the scheduler's events: (state, stage, states) per instruction.

    States are counted from the first slot of the log. A row is a dict with ``if`` (the
    fetch slot), ``address``, ``bus`` and ``stages``: a list of (state, name, length).
    """
    starts: dict[int, int] = {}
    lengths: dict[int, int] = {}
    split: dict[int, bool] = {}
    ma_len: dict[int, int] = {}
    first = None
    state = 0
    for event in log:
        if event[0] == "slot":
            _, slot, length, was_split = event
            if first is None:
                first = slot
            starts[slot] = state
            lengths[slot] = length
            split[slot] = was_split
            state += length
        elif event[0] == "ma":
            ma_len[event[1]] = event[2]

    def column(slot: int, stage: str) -> int:
        if stage == "MA":
            return starts[slot]
        return starts[slot] + (ma_len.get(slot, 0) if split.get(slot) else 0)

    fetches = [e for e in log if e[0] == "if"]
    rows: list[dict] = []
    for slot, address, bus in ((e[1], e[2], e[3]) for e in fetches):
        if slot not in starts:
            break
        length = lengths[slot] - (ma_len.get(slot, 0) if split[slot] else 0)
        rows.append(
            {
                "address": address,
                "bus": bus,
                "stages": [(column(slot, "IF"), "IF" if bus else "if", length if bus else 1)],
                "used": False,
            }
        )
    current = None
    for event in log:
        if event[0] == "issue":
            _, ex, name, pc = event
            current = next((r for r in rows if not r["used"] and r["address"] == pc), None)
            if current is None:
                continue
            current["used"] = True
            stages = current["stages"]
            if ex - 1 not in starts:
                continue
            stages.append((column(ex - 1, "ID"), "ID", 1))
            last_ma = None
            for k, stage in enumerate(STAGES[name]):
                slot = ex + k
                if slot not in starts:
                    break
                if stage == "MA":
                    last_ma = (starts[slot], ma_len.get(slot, 1))
                    stages.append((starts[slot], "MA", ma_len.get(slot, 1)))
                elif stage == "WB":
                    stages.append((last_ma[0] + last_ma[1], "WB", 1))
                else:
                    stages.append((column(slot, stage), stage, 1))
        elif event[0] == "mm" and current is not None:
            _, start, count = event
            current["stages"] += [(start + i, "mm", 1) for i in range(count)]
    # A row of a fetch the pipeline discarded keeps only its IF.
    return rows


def parse(start: int, text: str) -> list[tuple[int, str, int]]:
    out = []
    col = start
    for token in text.split():
        if token == "...":
            break
        if token == "-":
            col += 1
            continue
        name, _, count = token.partition(":")
        length = int(count) if count else 1
        if name == "M":
            name = "MA"
        out.append((col, name, length))
        col += length
    return out


def compare(fig: Figure, rows: list[dict]) -> list[str]:
    """Differences between the figure's rows and the run's, as text."""
    problems = []
    if not rows:
        return ["no rows"]
    base_row = rows[fig.lead]
    base = base_row["stages"][0][0] - 1  # the figure's column 1
    actual_rows = rows[fig.lead : fig.lead + len(fig.rows)]
    for index, ((start, text), row) in enumerate(zip(fig.rows, actual_rows, strict=False)):
        want = [t for t in parse(start, text) if t[0] <= fig.upto]
        limit = fig.upto
        if text.endswith("..."):
            # The figure leaves the row open ("....."): compare what it draws.
            limit = min(limit, max(c for c, _, _ in want))
        got = [(s - base, n, ln) for s, n, ln in row["stages"] if s - base <= limit]
        if not fig.contention:
            want = [(c, "IF", 1) if n in ("IF", "if") else (c, n, ln) for c, n, ln in want]
            got = [(c, "IF", 1) if n in ("IF", "if") else (c, n, ln) for c, n, ln in got]
        if sorted(want) != sorted(got):
            problems.append(
                f"row {index + 1} ({text!r} from {start}): figure {sorted(want)}, run {sorted(got)}"
            )
    if len(actual_rows) < len(fig.rows):
        problems.append(f"the run has {len(actual_rows)} rows, the figure {len(fig.rows)}")
    return problems


F = Figure
FIGURES: list[Figure] = [
    # -- 7.1-7.9: the rules ------------------------------------------------------------
    F("7.1", "Basic structure of pipeline flow",
      [LOAD, LOAD2, LOAD3, 0x6E92, 0x6192, 0x6392, NOP, NOP],
      [(1, "IF ID EX MA WB"), (2, "IF ID EX MA WB"), (3, "IF ID EX MA WB"),
       (4, "IF ID EX MA WB"), (5, "IF ID EX MA WB"), (6, "IF ID EX MA WB")]),
    F("7.4", "Slots requiring multiple cycles", [LOAD, LOAD_R3, NOP, NOP, NOP, NOP],
      [(1, "IF:2 ID - EX MA:3 WB"), (3, "IF:2 ID - - - EX MA WB")],
      contention=True, offset=2,
      waits={CODE: 1, CODE + 4: 1, DATA + 0x40 * 9: 2},
      note="C14: the next fetch, which the figure does not draw, splits slot 4 (PM 7.4.1)"),
    F("7.5", "How instruction execution states are counted", [LOAD, ADD, STORE, MOV, NOP, NOP],
      [(1, "IF:2 ID - EX MA:3 WB"), (3, "IF:2 ID - - - EX"),
       (5, "if - - - ID EX MA"), (9, "IF ID ...")],
      contention=True, offset=2, upto=11,
      waits={CODE: 1, CODE + 4: 1, DATA + 0x40 * 9: 2},
      note="C14: the third instruction's fetch is an 'if' on the SH-2 (PM 7.4.2), so "
           "slot 3 is 1 state, not 2: 6 states from EX to EX, not 7"),
    F("7.6", "Operation when IF and MA are in contention", [LOAD, LOAD2, NOP, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA WB"), (2, "IF ID - EX MA WB"), (3, "if - ID EX"),
       (5, "IF ID EX"), (6, "if ID EX")],
      contention=True, offset=2,
      note="C14: the fifth instruction's fetch is an 'if' on the SH-2, so slot E does "
           "not split"),
    F("7.7a", "IF from a longword boundary", [NOP] * 8,
      [(1, "IF ID EX"), (2, "if ID EX"), (3, "IF ID EX"), (4, "if ID EX"),
       (5, "IF ID EX"), (6, "if ID EX")], contention=True),
    F("7.7b", "IF from a word boundary", [NOP] * 8,
      [(1, "IF ID EX"), (2, "IF ID EX"), (3, "if ID EX"), (4, "IF ID EX"), (5, "if ID EX")],
      contention=True, offset=2),
    F("7.8", "IF location and IF/MA contention", [LOAD, LOAD2, NOP, NOP, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA WB"), (2, "if ID EX MA WB"), (3, "IF ID - EX"), (4, "if - ID EX"),
       (6, "IF ID EX"), (7, "if ID EX")], contention=True),
    F("7.9", "Effects of memory load instructions", [0x6101, ADD, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA WB"), (2, "if ID - EX"), (3, "IF - ID EX"), (5, "if ID ...")],
      contention=True),
    # -- 7.7.1-7.7.6: one class each --------------------------------------------------
    F("7.10", "Register-register transfer", [MOV, NOP, NOP, NOP],
      [(1, "IF ID EX"), (2, "IF ID EX"), (3, "IF ID EX")]),
    F("7.11", "Memory load", [LOAD, NOP, NOP, NOP],
      [(1, "IF ID EX MA WB"), (2, "IF ID EX"), (3, "IF ID EX")]),
    F("7.12", "Memory store", [STORE, NOP, NOP, NOP],
      [(1, "IF ID EX MA"), (2, "IF ID EX"), (3, "IF ID EX")]),
    F("7.13", "Arithmetic between registers", [ADD, NOP, NOP, NOP],
      [(1, "IF ID EX"), (2, "IF ID EX"), (3, "IF ID EX")]),
    F("7.74", "Register-register logic operation", [AND, NOP, NOP, NOP],
      [(1, "IF ID EX"), (2, "IF ID EX"), (3, "IF ID EX")]),
    F("7.75", "Memory logic operation", [AND_B, NOP, NOP, NOP],
      [(1, "IF ID EX MA EX MA"), (2, "IF - - ID EX"), (5, "IF ID EX")]),
    F("7.76", "TAS", [TAS, NOP, NOP, NOP],
      [(1, "IF ID EX MA EX MA"), (2, "IF - - - ID EX"), (6, "IF ID EX")]),
    F("7.77", "Shift", [SHLL, NOP, NOP, NOP],
      [(1, "IF ID EX"), (2, "IF ID EX"), (3, "IF ID EX")]),
    F("7.78", "BT, condition satisfied", [BT_8, NOP, NOP, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX"), (2, "IF"), (3, "IF"), (4, "IF ID EX"), (5, "IF ID EX")], sr=1),
    F("7.79", "BT, condition not satisfied", [BT_8, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX"), (2, "IF ID EX"), (3, "IF ID EX"), (4, "IF ID EX")]),
    F("7.80", "BT/S, condition satisfied", [BT_S_8, LOAD, NOP, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX"), (2, "IF - ID EX MA WB"), (3, "IF"), (4, "IF ID EX"), (5, "IF ID EX")],
      sr=1),
    F("7.81", "BT/S, condition not satisfied", [BT_S_8, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX"), (2, "IF ID EX"), (3, "IF ID EX"), (4, "IF ID EX")]),
    F("7.82", "Unconditional branch", [BRA_8, LOAD, NOP, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX"), (2, "IF - ID EX MA WB"), (4, "IF ID EX"), (5, "IF ID EX")]),
    F("7.83", "System control ALU", [LDC_GBR, NOP, NOP, NOP],
      [(1, "IF ID EX"), (2, "IF ID EX"), (3, "IF ID EX")]),
    F("7.84", "LDC.L", [LDC_L, NOP, NOP, NOP],
      [(1, "IF ID EX MA EX"), (2, "IF - - ID EX"), (5, "IF ID EX")]),
    F("7.85", "STC.L", [STC_L, NOP, NOP, NOP],
      [(1, "IF ID EX MA"), (2, "IF - ID EX"), (4, "IF ID EX")]),
    F("7.86", "LDS.L (PR)", [LDS_L_PR, NOP, NOP, NOP],
      [(1, "IF ID EX MA WB"), (2, "IF ID EX"), (3, "IF ID EX")]),
    F("7.87", "STS.L (PR)", [STS_L_PR, NOP, NOP, NOP],
      [(1, "IF ID EX MA"), (2, "IF ID EX"), (3, "IF ID EX")]),
    F("7.88", "Register to MAC transfer", [LDS_MACL, NOP, NOP, NOP],
      [(1, "IF ID EX MA"), (2, "IF ID EX"), (3, "IF ID EX")]),
    F("7.89", "Memory to MAC transfer", [LDS_L_MACL, NOP, NOP, NOP],
      [(1, "IF ID EX MA"), (2, "IF ID EX"), (3, "IF ID EX")]),
    F("7.90", "MAC to register transfer", [STS_MACL, NOP, NOP, NOP],
      [(1, "IF ID EX MA WB"), (2, "IF ID EX"), (3, "IF ID EX")]),
    F("7.91", "MAC to memory transfer", [STS_L_MACL, NOP, NOP, NOP],
      [(1, "IF ID EX MA"), (2, "IF ID EX"), (3, "IF ID EX")]),
    F("7.92", "RTE", [RTE, NOP, NOP, NOP],
      [(1, "IF ID EX MA MA"), (2, "IF - - - ID EX"), (6, "IF ID EX")]),
    F("7.93", "TRAPA", [TRAPA, NOP, NOP, NOP],
      [(1, "IF ID EX EX MA MA MA EX EX"), (2, "IF"), (3, "IF"), (9, "IF ID EX"),
       (10, "IF ID EX")]),
    F("7.94", "SLEEP", [SLEEP, NOP, NOP, NOP],
      [(1, "IF ID EX"), (2, "IF ...")], irl_after=3,
      note="an interrupt ends the sleep and replaces the next instruction"),
    F("7.95", "Interrupt exception processing", [NOP, NOP, NOP, NOP],
      [(1, "IF ID EX EX MA MA EX MA EX EX"), (2, "IF"), (10, "IF ID EX"), (11, "IF ID ...")],
      lead=1, irl_after=1),
    F("7.96", "Address error exception processing", [LOAD_ODD, NOP, NOP, NOP],
      [(1, "IF ID EX EX MA MA EX MA EX EX"), (2, "IF"), (10, "IF ID EX"), (11, "IF ID ...")],
      lead=1),
    F("7.97", "Illegal instruction exception processing", [UNDEFINED, NOP, NOP, NOP],
      [(1, "IF ID EX EX MA MA MA EX EX"), (2, "IF"), (3, "IF"), (9, "IF ID EX"),
       (10, "IF ID ...")]),
    # -- 7.7.2: the multiplier (SH-2; figures 7.14-7.22 and 7.45-7.52 are the SH-1's) ---
    F("7.23", "MAC.W", [MAC_W, LOAD, LOAD2, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm"), (2, "IF - ID EX MA WB"), (4, "IF ID EX MA WB")]),
    F("7.24", "MAC.W after MAC.W", [MAC_W, MAC_W, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm"), (2, "IF - ID EX MA MA mm mm"), (4, "IF - ID EX MA")]),
    F("7.25", "Consecutive MAC.Ws with misalignment", [NOP, MAC_W, MAC_W, MAC_W, MAC_W, NOP, NOP],
      [(1, "if ID EX MA MA mm mm"), (2, "IF - ID EX MA - MA mm mm"),
       (4, "if - - ID EX MA MA mm mm"), (7, "IF - ID EX MA ...")],
      contention=True, lead=1),
    F("7.26", "MAC.W after MAC.W, MA and IF contention", [MAC_W, MAC_W, STORE, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm"), (2, "if - - ID EX MA MA mm mm"),
       (5, "IF - ID - EX MA"), (7, "if - ID EX"), (9, "IF ...")],
      contention=True, note="C16: the figure draws the second split's slot arrow one column late"),
    F("7.27", "MAC.L after MAC.W", [MAC_W, MAC_L_R5, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm"), (2, "IF - ID EX MA MA mm mm mm mm"), (4, "IF - ID EX MA")]),
    F("7.28a", "MULS.W after MAC.W", [MAC_W, MULS_W, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm"), (2, "IF - ID EX M:2 mm mm"), (4, "IF ID EX - MA")]),
    F("7.28b", "MULS.W after MAC.W and one other", [MAC_W, NOP, MULS_W, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm"), (2, "IF - ID EX"), (4, "IF ID EX MA mm mm"),
       (5, "IF ID EX MA")]),
    F("7.29", "DMULS.L after MAC.W", [MAC_W, DMULS_L, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm"), (2, "IF - ID EX MA MA mm mm mm mm"), (4, "IF - ID EX MA")]),
    F("7.30a", "STS after MAC.W", [MAC_W, STS_MACL, STORE, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm"), (2, "if - - ID EX M:2 WB"), (5, "IF ID - - EX MA"),
       (6, "if - - ID EX"), (9, "IF ID EX")], contention=True),
    F("7.30b", "STS after MAC.W (MAC.W at 4n + 2)",
      [NOP, MAC_W, STS_MACL, NOP, STORE, NOP, NOP, NOP],
      [(1, "if ID EX MA MA mm mm"), (2, "IF - ID - EX MA WB"), (4, "if - ID EX"),
       (6, "IF ID EX MA"), (7, "if ID EX")], contention=True, lead=1),
    F("7.31a", "STS.L after MAC.W", [MAC_W, STS_L_MACL, STORE, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm"), (2, "if - - ID EX M:2"), (5, "IF ID - - EX MA"),
       (6, "if - - ID EX"), (9, "IF ID EX")], contention=True),
    F("7.31b", "STS.L after MAC.W (MAC.W at 4n + 2)", [NOP, MAC_W, STS_L_MACL, NOP, NOP, NOP, NOP],
      [(1, "if ID EX MA MA mm mm"), (2, "IF - ID - EX MA"), (4, "if - ID EX"),
       (6, "IF ID EX"), (7, "if ID EX")], contention=True, lead=1),
    F("7.32a", "LDS after MAC.W", [MAC_W, LDS_MACL, STORE, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm"), (2, "if - - ID EX M:2"), (5, "IF ID - - EX MA"),
       (6, "if - - ID EX"), (9, "IF ID EX")], contention=True),
    F("7.32b", "LDS after MAC.W (MAC.W at 4n + 2)", [NOP, MAC_W, LDS_MACL, NOP, NOP, NOP, NOP],
      [(1, "if ID EX MA MA mm mm"), (2, "IF - ID - EX MA"), (4, "if - ID EX"),
       (6, "IF ID EX"), (7, "if ID EX")], contention=True, lead=1),
    F("7.33a", "LDS.L after MAC.W", [MAC_W, LDS_L_MACL, NOP, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm"), (2, "if - - ID EX M:2"), (5, "IF ID - - EX"),
       (6, "if - - ID EX"), (9, "IF ID EX")], contention=True),
    F("7.33b", "LDS.L after MAC.W (MAC.W at 4n + 2)", [NOP, MAC_W, LDS_L_MACL, NOP, NOP, NOP, NOP],
      [(1, "if ID EX MA MA mm mm"), (2, "IF - ID - EX MA"), (4, "if - ID EX"),
       (6, "IF ID EX"), (7, "if ID EX")], contention=True, lead=1),
    F("7.34", "MAC.L", [MAC_L, LOAD, LOAD2, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA WB"), (4, "IF ID EX MA WB")]),
    F("7.35a", "MAC.L after MAC.L", [MAC_L, MAC_L, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA M:3 mm mm mm mm"),
       (4, "IF - ID EX - - MA")]),
    F("7.35b", "MAC.L after MAC.L and two others", [MAC_L, LOAD, LOAD2, MAC_L, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA WB"), (4, "IF ID EX MA WB"),
       (5, "IF ID EX MA MA mm mm mm mm")]),
    F("7.36", "Consecutive MAC.Ls with misalignment", [NOP, MAC_L, MAC_L, MAC_L, MAC_L, NOP, NOP],
      [(1, "if ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA - M:2 mm mm mm mm"),
       (4, "if - - ID EX - MA M:3 mm mm mm mm"), (7, "IF - - ID EX - - MA ...")],
      contention=True, lead=1),
    F("7.37", "MAC.L after MAC.L, MA and IF contention", [MAC_L, MAC_L, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm mm mm"), (2, "if - - ID EX MA M:3 mm mm mm mm"),
       (5, "IF - ID - - - EX"), (7, "if - - - ID ..."), (11, "IF ...")], contention=True),
    F("7.38a", "MAC.W after MAC.L", [MAC_L, MAC_W, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA M:3 mm mm"),
       (4, "IF - ID EX - - MA")]),
    F("7.38b", "MAC.W after MAC.L and two others", [MAC_L, LOAD, LOAD2, MAC_W, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA WB"), (4, "IF ID EX MA WB"),
       (5, "IF ID EX MA MA mm mm")]),
    F("7.39a", "DMULS.L after MAC.L", [MAC_L, DMULS_L, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA M:3 mm mm mm mm"),
       (4, "IF - ID EX - - MA")],
      note="C15: the figure draws the third instruction's EX at the end of the long slot"),
    F("7.39b", "DMULS.L after MAC.L and one other", [MAC_L, NOP, DMULS_L, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX"),
       (4, "IF ID EX MA M:2 mm mm mm mm"), (5, "IF - ID EX - MA")],
      note="C15: the figure draws the last row's EX at the end of the long slot"),
    F("7.39c", "DMULS.L after MAC.L and two others", [MAC_L, LOAD, LOAD2, DMULS_L, STORE, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA WB"), (4, "IF ID EX MA WB"),
       (5, "IF ID EX MA MA mm mm mm mm"), (6, "IF - ID EX MA")]),
    F("7.40a", "MULS.W after MAC.L", [MAC_L, MULS_W, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX M:4 mm mm"),
       (4, "IF ID EX - - - MA")],
      note="C17: the figure draws MAC.L with five mm and MULS.W with two MAs, which "
           "moves the third row a column right; ruled as figure 7.68"),
    F("7.40b", "MULS.W after MAC.L and one other", [MAC_L, NOP, MULS_W, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX"), (4, "IF ID EX M:3 mm mm"),
       (5, "IF ID EX - - MA")]),
    F("7.40c", "MULS.W after MAC.L and two others", [MAC_L, LOAD, LOAD2, MULS_W, STORE, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA WB"), (4, "IF ID EX MA WB"),
       (5, "IF ID EX M:2 mm mm"), (6, "IF ID EX - MA")]),
    F("7.40d", "MULS.W after MAC.L and three others",
      [MAC_L, LOAD, LOAD2, LOAD3, MULS_W, STORE, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA WB"), (4, "IF ID EX MA WB"),
       (5, "IF ID EX MA WB"), (6, "IF ID EX MA mm mm"), (7, "IF ID EX MA")]),
    F("7.41a", "STS after MAC.L", [MAC_L, STS_MACL, STORE, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm mm mm"), (2, "if - - ID EX M:4 WB"),
       (5, "IF ID - - - - EX MA"), (6, "if - - - - ID EX"), (11, "IF ID EX")],
      contention=True),
    F("7.41b", "STS after MAC.L (MAC.L at 4n + 2)", [NOP, MAC_L, STS_MACL, NOP, NOP, NOP, NOP, NOP],
      [(1, "if ID EX MA MA mm mm mm mm"), (2, "IF - ID - EX M:3 WB"), (4, "if - ID EX"),
       (6, "IF ID - - EX"), (7, "if - - ID EX")], contention=True, lead=1),
    F("7.42a", "STS.L after MAC.L", [MAC_L, STS_L_MACL, STORE, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm mm mm"), (2, "if - - ID EX M:4"),
       (5, "IF ID - - - - EX MA"), (6, "if - - - - ID EX"), (11, "IF ID EX")],
      contention=True),
    F("7.42b", "STS.L after MAC.L (MAC.L at 4n + 2)",
      [NOP, MAC_L, STS_L_MACL, NOP, NOP, NOP, NOP, NOP],
      [(1, "if ID EX MA MA mm mm mm mm"), (2, "IF - ID - EX M:3"), (4, "if - ID EX"),
       (6, "IF ID - - EX"), (7, "if - - ID EX")], contention=True, lead=1),
    F("7.43a", "LDS after MAC.L", [MAC_L, LDS_MACL, STORE, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm mm mm"), (2, "if - - ID EX M:4"),
       (5, "IF ID - - - - EX MA"), (6, "if - - - - ID EX"), (11, "IF ID EX")],
      contention=True),
    F("7.43b", "LDS after MAC.L (MAC.L at 4n + 2)", [NOP, MAC_L, LDS_MACL, NOP, NOP, NOP, NOP, NOP],
      [(1, "if ID EX MA MA mm mm mm mm"), (2, "IF - ID - EX M:3"), (4, "if - ID EX"),
       (6, "IF ID - - EX"), (7, "if - - ID EX")], contention=True, lead=1),
    F("7.44a", "LDS.L after MAC.L", [MAC_L, LDS_L_MACL, STORE, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm mm mm"), (2, "if - - ID EX M:4"),
       (5, "IF ID - - - - EX MA"), (6, "if - - - - ID EX"), (11, "IF ID EX")],
      contention=True),
    F("7.44b", "LDS.L after MAC.L (MAC.L at 4n + 2)",
      [NOP, MAC_L, LDS_L_MACL, NOP, NOP, NOP, NOP, NOP],
      [(1, "if ID EX MA MA mm mm mm mm"), (2, "IF - ID - EX M:3"), (4, "if - ID EX"),
       (6, "IF ID - - EX"), (7, "if - - ID EX")], contention=True, lead=1),
    F("7.53", "MULS.W", [MULS_W, LOAD, LOAD2, NOP, NOP],
      [(1, "IF ID EX MA mm mm"), (2, "IF ID EX MA WB"), (3, "IF ID EX MA WB")]),
    F("7.54", "MAC.W after MULS.W", [MULS_W, MAC_W, STORE, NOP, NOP],
      [(1, "IF ID EX MA mm mm"), (2, "IF ID EX MA MA mm mm"), (3, "IF - ID EX MA")]),
    F("7.55", "MAC.L after MULS.W", [MULS_W, MAC_L, STORE, NOP, NOP],
      [(1, "IF ID EX MA mm mm"), (2, "IF ID EX MA MA mm mm mm mm"), (3, "IF - ID EX MA")]),
    F("7.56a", "MULS.W after MULS.W", [MULS_W, MULS_W, STORE, NOP, NOP],
      [(1, "IF ID EX MA mm mm"), (2, "IF ID EX M:2 mm mm"), (3, "IF ID EX - MA")]),
    F("7.56b", "MULS.W after MULS.W and one other", [MULS_W, NOP, MULS_W, STORE, NOP, NOP],
      [(1, "IF ID EX MA mm mm"), (2, "IF ID EX"), (3, "IF ID EX MA mm mm"), (4, "IF ID EX MA")]),
    F("7.57", "MULS.W after MULS.W, MA and IF contention", [MULS_W, MULS_W, STORE, NOP, NOP, NOP],
      [(1, "IF ID EX MA mm mm"), (2, "if ID EX M:2 mm mm"), (3, "IF ID - - EX MA"),
       (4, "if - - ID EX"), (7, "IF ID ...")], contention=True),
    F("7.58", "DMULS.L after MULS.W", [MULS_W, DMULS_L, STORE, NOP, NOP],
      [(1, "IF ID EX MA mm mm"), (2, "IF ID EX MA MA mm mm mm mm"), (3, "IF - ID EX MA")]),
    F("7.59a", "STS after MULS.W", [MULS_W, STS_MACL, STORE, NOP, NOP, NOP],
      [(1, "IF ID EX MA mm mm"), (2, "if ID EX M:2 WB"), (3, "IF ID - - EX MA"),
       (4, "if - - ID EX"), (7, "IF ID EX")], contention=True),
    F("7.59b", "STS after MULS.W (MULS.W at 4n + 2)", [NOP, MULS_W, STS_MACL, NOP, NOP, NOP, NOP],
      [(1, "if ID EX MA mm mm"), (2, "IF ID - EX MA WB"), (3, "if - ID EX"), (5, "IF ID EX"),
       (6, "if ID EX")], contention=True, lead=1),
    F("7.60a", "STS.L after MULS.W", [MULS_W, STS_L_MACL, STORE, NOP, NOP, NOP],
      [(1, "IF ID EX MA mm mm"), (2, "if ID EX M:2"), (3, "IF ID - - EX MA"),
       (4, "if - - ID EX"), (7, "IF ID EX")], contention=True),
    F("7.60b", "STS.L after MULS.W (MULS.W at 4n + 2)",
      [NOP, MULS_W, STS_L_MACL, NOP, NOP, NOP, NOP],
      [(1, "if ID EX MA mm mm"), (2, "IF ID - EX MA"), (3, "if - ID EX"), (5, "IF ID EX"),
       (6, "if ID EX")], contention=True, lead=1),
    F("7.61a", "LDS after MULS.W", [MULS_W, LDS_MACL, STORE, NOP, NOP, NOP],
      [(1, "IF ID EX MA mm mm"), (2, "if ID EX M:2"), (3, "IF ID - - EX MA"),
       (4, "if - - ID EX"), (7, "IF ID EX")], contention=True),
    F("7.61b", "LDS after MULS.W (MULS.W at 4n + 2)", [NOP, MULS_W, LDS_MACL, NOP, NOP, NOP, NOP],
      [(1, "if ID EX MA mm mm"), (2, "IF ID - EX MA"), (3, "if - ID EX"), (5, "IF ID EX"),
       (6, "if ID EX")], contention=True, lead=1),
    F("7.62a", "LDS.L after MULS.W", [MULS_W, LDS_L_MACL, STORE, NOP, NOP, NOP],
      [(1, "IF ID EX MA mm mm"), (2, "if ID EX M:2"), (3, "IF ID - - EX MA"),
       (4, "if - - ID EX"), (7, "IF ID EX")], contention=True),
    F("7.62b", "LDS.L after MULS.W (MULS.W at 4n + 2)",
      [NOP, MULS_W, LDS_L_MACL, NOP, NOP, NOP, NOP],
      [(1, "if ID EX MA mm mm"), (2, "IF ID - EX MA"), (3, "if - ID EX"), (5, "IF ID EX"),
       (6, "if ID EX")], contention=True, lead=1),
    F("7.63", "DMULS.L", [DMULS_L, LOAD, LOAD2, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA WB"), (4, "IF ID EX MA WB")]),
    F("7.64a", "MAC.L after DMULS.L", [DMULS_L, MAC_L, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA M:3 mm mm mm mm"),
       (4, "IF - ID EX - - MA")]),
    F("7.64b", "MAC.L after DMULS.L and two others", [DMULS_L, LOAD, LOAD2, MAC_L, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA WB"), (4, "IF ID EX MA WB"),
       (5, "IF ID EX MA MA mm mm mm mm")]),
    F("7.65a", "MAC.W after DMULS.L", [DMULS_L, MAC_W, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA M:3 mm mm"),
       (4, "IF - ID EX - - MA")]),
    F("7.65b", "MAC.W after DMULS.L and two others", [DMULS_L, LOAD, LOAD2, MAC_W, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA WB"), (4, "IF ID EX MA WB"),
       (5, "IF ID EX MA MA mm mm")]),
    F("7.66a", "DMULS.L after DMULS.L", [DMULS_L, DMULS_L, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA M:3 mm mm mm mm"),
       (4, "IF - ID EX - - MA")]),
    F("7.66b", "DMULS.L after DMULS.L and one other", [DMULS_L, NOP, DMULS_L, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX"),
       (4, "IF ID EX MA M:2 mm mm mm mm"), (5, "IF - ID EX - MA")]),
    F("7.66c", "DMULS.L after DMULS.L and two others", [DMULS_L, LOAD, LOAD2, DMULS_L, STORE, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA WB"), (4, "IF ID EX MA WB"),
       (5, "IF ID EX MA MA mm mm mm mm"), (6, "IF - ID EX MA")]),
    F("7.67", "DMULS.L after DMULS.L, MA and IF contention", [DMULS_L, DMULS_L, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm mm mm"), (2, "if - - ID EX MA M:3 mm mm mm mm"),
       (5, "IF - ID - - - EX"), (7, "if - - - ID ..."), (11, "IF ...")], contention=True,
      note="C17: the figure's second row puts EX before ID; ruled as figure 7.37"),
    F("7.68a", "MULS.W after DMULS.L", [DMULS_L, MULS_W, STORE, NOP, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX M:4 mm mm"),
       (4, "IF ID EX - - - MA")]),
    F("7.68b", "MULS.W after DMULS.L and three others",
      [DMULS_L, LOAD, LOAD2, LOAD3, MULS_W, STORE, NOP],
      [(1, "IF ID EX MA MA mm mm mm mm"), (2, "IF - ID EX MA WB"), (4, "IF ID EX MA WB"),
       (5, "IF ID EX MA WB"), (6, "IF ID EX MA mm mm"), (7, "IF ID EX MA")],
      note="C17: the figure draws MULS.W with two MAs"),
    F("7.69", "MULS.W after DMULS.L, MA and IF contention", [DMULS_L, MULS_W, STORE, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm mm mm"), (2, "if - - ID EX M:4 mm mm"),
       (5, "IF ID - - - - EX MA"), (6, "if - - - - ID EX"), (11, "IF ID ...")],
      contention=True),
    F("7.70a", "STS after DMULS.L", [DMULS_L, STS_MACL, NOP, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm mm mm"), (2, "if - - ID EX M:4 WB"),
       (5, "IF ID - - - - EX"), (6, "if - - - - ID EX"), (11, "IF ID EX")], contention=True),
    F("7.70b", "STS after DMULS.L (DMULS.L at 4n + 2)",
      [NOP, DMULS_L, STS_MACL, NOP, NOP, NOP, NOP, NOP],
      [(1, "if ID EX MA MA mm mm mm mm"), (2, "IF - ID - EX M:3 WB"), (4, "if - ID EX"),
       (6, "IF ID - - EX"), (7, "if - - ID EX")], contention=True, lead=1),
    F("7.71a", "STS.L after DMULS.L", [DMULS_L, STS_L_MACL, STORE, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm mm mm"), (2, "if - - ID EX M:4"),
       (5, "IF ID - - - - EX MA"), (6, "if - - - - ID EX"), (11, "IF ID EX")],
      contention=True),
    F("7.71b", "STS.L after DMULS.L (DMULS.L at 4n + 2)",
      [NOP, DMULS_L, STS_L_MACL, NOP, NOP, NOP, NOP, NOP],
      [(1, "if ID EX MA MA mm mm mm mm"), (2, "IF - ID - EX M:3"), (4, "if - ID EX"),
       (6, "IF ID - - EX"), (7, "if - - ID EX")], contention=True, lead=1),
    F("7.72a", "LDS after DMULS.L", [DMULS_L, LDS_MACL, STORE, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm mm mm"), (2, "if - - ID EX M:4"),
       (5, "IF ID - - - - EX MA"), (6, "if - - - - ID EX"), (11, "IF ID EX")],
      contention=True),
    F("7.72b", "LDS after DMULS.L (DMULS.L at 4n + 2)",
      [NOP, DMULS_L, LDS_MACL, NOP, NOP, NOP, NOP, NOP],
      [(1, "if ID EX MA MA mm mm mm mm"), (2, "IF - ID - EX M:3"), (4, "if - ID EX"),
       (6, "IF ID - - EX"), (7, "if - - ID EX")], contention=True, lead=1),
    F("7.73a", "LDS.L after DMULS.L", [DMULS_L, LDS_L_MACL, STORE, NOP, NOP, NOP, NOP],
      [(1, "IF ID EX MA - MA mm mm mm mm"), (2, "if - - ID EX M:4"),
       (5, "IF ID - - - - EX MA"), (6, "if - - - - ID EX"), (11, "IF ID EX")],
      contention=True),
    F("7.73b", "LDS.L after DMULS.L (DMULS.L at 4n + 2)",
      [NOP, DMULS_L, LDS_L_MACL, NOP, NOP, NOP, NOP, NOP],
      [(1, "if ID EX MA MA mm mm mm mm"), (2, "IF - ID - EX M:3"), (4, "if - ID EX"),
       (6, "IF ID - - EX"), (7, "if - - ID EX")], contention=True, lead=1),
]  # fmt: skip


def _figure_id(fig: Figure) -> str:
    return fig.number


@pytest.mark.parametrize("fig", FIGURES, ids=_figure_id)
def test_figure(fig):
    log, _ = run(fig)
    problems = compare(fig, timeline(log))
    assert not problems, f"PM figure {fig.number} ({fig.title}):\n" + "\n".join(problems)


SH1_ONLY = ("7.14", "7.15", "7.16", "7.17", "7.18", "7.19", "7.20", "7.21", "7.22",
            "7.45", "7.46", "7.47", "7.48", "7.49", "7.50", "7.51", "7.52")  # fmt: skip


def _slots(log: list) -> tuple[list, list]:
    fetches = [(e[1], e[2]) for e in log if e[0] == "if"]
    issues = [(e[1], e[2], e[3]) for e in log if e[0] == "issue"]
    return fetches, issues


@pytest.mark.parametrize("fig", [f for f in FIGURES if not f.contention], ids=_figure_id)
def test_contention_never_moves_a_stage_to_another_slot(fig):
    """PM 7.4.1: a split makes a slot longer; every stage stays in its slot.

    So a figure drawn without IF/MA contention is the slot structure of the real run too.
    """
    free, _ = run(fig, contention=False)
    real, _ = run(fig, contention=True)
    free_fetches, free_issues = _slots(free)
    real_fetches, real_issues = _slots(real)
    n = min(len(free_issues), len(real_issues))
    assert free_issues[:n] == real_issues[:n]
    n = min(len(free_fetches), len(real_fetches))
    assert free_fetches[:n] == real_fetches[:n]


@pytest.mark.parametrize("fig", FIGURES, ids=_figure_id)
def test_no_two_stages_of_a_kind_share_a_slot(fig):
    """PM figures 7.2 and 7.3: one stage per slot per instruction, one of each kind per slot."""
    log, _ = run(fig, contention=True)
    seen: set[tuple[int, str]] = set()
    for event in log:
        if event[0] == "if":
            assert (event[1], "IF") not in seen, event
            seen.add((event[1], "IF"))
        elif event[0] == "issue":
            _, ex, name, _ = event
            stages = ["ID", *STAGES[name]]
            for k, stage in enumerate(stages):
                if stage == "WB":
                    continue  # WB runs right after its MA, beside the next MA (PM 7.2.1)
                key = (ex - 1 + k, stage)
                assert key not in seen, (fig.number, event, key)
                seen.add(key)


def ex_to_ex(program: list[int], *, sr: int = 0, index: int = 0) -> int:
    """PM 7.3: the states from the EX of ``program[index]`` to the next instruction's EX.

    One-state memory, no IF/MA contention (PM table 7.2's "no contention" column).
    """
    fig = Figure("", "", program, [], sr=sr)
    log, _ = run(fig, contention=False)
    ex = [
        min(s for s, n, _ in r["stages"] if n == "EX")
        for r in timeline(log)
        if any(n == "EX" for _, n, _ in r["stages"])
    ]
    return ex[index + 1] - ex[index]


@pytest.mark.parametrize(
    ("words", "sr", "states"),
    [
        ([NOP], 0, 1), ([LOAD], 0, 1), ([STORE], 0, 1), ([MAC_W], 0, 2), ([MAC_L], 0, 2),
        ([MULS_W], 0, 1), ([DMULS_L], 0, 2), ([AND_B], 0, 3), ([TAS], 0, 4),
        ([BT_8], 1, 3), ([BT_8], 0, 1), ([BT_S_8], 1, 2), ([BT_S_8], 0, 1), ([BRA_8], 0, 2),
        ([LDC_L], 0, 3), ([STC_L], 0, 2), ([LDS_L_PR], 0, 1), ([STS_L_PR], 0, 1),
        ([LDS_MACL], 0, 1), ([LDS_L_MACL], 0, 1), ([STS_MACL], 0, 1), ([STS_L_MACL], 0, 1),
        ([RTE], 0, 4), ([TRAPA], 0, 8), ([UNDEFINED], 0, 8),
    ],
)  # fmt: skip
def test_table_7_2_states(words, sr, states):
    """PM table 7.2 / A.50's states (no contention), measured EX to EX on the scheduler.

    Branches count to the instruction after their slot or at their target (PM 7.3), so a
    delayed branch's 2 is EX(branch) to EX(slot); TRAPA and the illegal sequence count to
    the handler's first EX. MAC.W and MAC.L are 2 here, as figures 7.23 and 7.34 draw
    them; the table's 3 is that plus the split one of their MAs meets (C18, below).
    """
    assert ex_to_ex([*words, NOP, NOP, NOP, NOP, NOP], sr=sr) == states


@pytest.mark.parametrize("mac", [MAC_W, MAC_L])
def test_c18_mac_is_three_states_in_a_sequential_stream(mac):
    # C18: table 7.2 gives MAC.W and MAC.L 3 states, figures 7.23 and 7.34 draw 2 (no
    # contention). In a sequential stream fetches alternate IF and 'if', so one of the
    # MAC's two consecutive MAs always meets an IF, and both MA slots lie before the next
    # instruction's EX (which follows the second MA's split): 2 + 1 = 3, at either
    # alignment.
    def states(offset):
        fig = Figure("", "", [mac, NOP, NOP, NOP, NOP, NOP, NOP], [], offset=offset)
        log, _ = run(fig, contention=True)
        ex = [
            min(s for s, n, _ in r["stages"] if n == "EX")
            for r in timeline(log)
            if any(n == "EX" for _, n, _ in r["stages"])
        ]
        return ex[1] - ex[0], ex[2] - ex[1]

    assert states(0)[0] == 3
    assert states(2)[0] == 3


def test_sleep_is_three_states_before_it_sleeps():
    # PM table 7.2: SLEEP 3. An interrupt then replaces the next instruction: its first
    # EX is 3 states after SLEEP's, plus the time slept.
    fig = Figure("", "", [SLEEP, NOP, NOP], [], irl_after=1)
    log, _ = run(fig, contention=False)
    issues = [e for e in log if e[0] == "issue"]
    assert issues[0][2] == "sleep" and issues[1][2] == "interrupt"
    assert issues[1][1] - issues[0][1] == 3  # slots: the interrupt's EX three after SLEEP's


# -- PM 7.5: load-use --------------------------------------------------------------------


def _first_split(program: list[int]) -> list[int]:
    log, _ = run(Figure("", "", program, [], offset=0), contention=True)
    return [e[1] for e in log if e[0] == "slot" and e[3]]


def test_load_use_splits_the_slot_of_the_loads_ma():
    # MOV.W @R0,R1 at 4n; ADD R1,R2 uses R1: the MA's slot (slot 3 from the first IF's
    # slot 0) splits, though its fetch is an 'if' (PM figure 7.9).
    assert 3 in _first_split([0x6101, ADD, NOP, NOP, NOP, NOP])
    assert 3 not in _first_split([0x6101, 0x343C, NOP, NOP, NOP, NOP])  # ADD R3,R4


def test_load_use_counts_a_destination_as_a_use():
    # "The destination register of load instruction 1 is the same as the destination
    # (not the source) of instruction 2, so it splits" (PM 7.5): MOV R3,R1 writes R1.
    assert 3 in _first_split([0x6101, 0x6133, NOP, NOP, NOP, NOP])


def test_no_load_use_split_for_a_load_to_the_same_register_or_mac():
    # PM 7.5's two exceptions: the next instruction is a load with the same destination,
    # or MAC @Rm+,@Rn+ with the loaded register.
    assert 3 not in _first_split([0x6101, 0x6131, NOP, NOP, NOP, NOP])  # MOV.L @R3,R1
    assert 3 not in _first_split([0x6101, 0x421F, NOP, NOP, NOP, NOP])  # MAC.W @R1+,@R2+
    # A load whose destination is the next load's address register still splits.
    assert 3 in _first_split([0x6101, 0x6312, NOP, NOP, NOP, NOP])  # MOV.L @R1,R3


def test_load_use_of_pr():
    # LDS.L @Rm+,PR then RTS (PM table 7.2: LDS.L (PR) contends with an instruction
    # that uses its destination).
    assert 3 in _first_split([LDS_L_PR, 0x000B, NOP, NOP, NOP, NOP])


# -- U11: overrun fetches are bus cycles ---------------------------------------------------


def test_overrun_fetches_reach_the_bus():
    # BT taken at 4n fetches the longword after its own and discards it (PM figure 7.78
    # and its note): a FETCH cycle at CODE + 4 although nothing there runs.
    fig = Figure("", "", [BT_8, NOP, 0xFFFF, 0xFFFF, NOP, NOP, NOP, NOP], [], sr=1)
    _, bus = run(fig, contention=True, steps=4)
    fetches = [a for a, _, kind in bus if kind & FETCH]
    assert CODE + 4 in fetches  # the overrun line, holding the two undefined words
    assert fetches.index(CODE + 4) < fetches.index(CODE + 8)


def test_overrun_fetch_fills_a_cache_line():
    # In the cache area the overrun fetch misses like any other: a line fill. The BT sits
    # at the end of a 16-byte line, so the longword it overruns opens the next line.
    words = [NOP] * 6 + [BT_8, NOP] + [NOP] * 8
    fig = Figure("", "", words, [], sr=1)
    chip, bus = _board(fig)
    chip.cache.write(0xFFFFFE92, 8, 0x01)  # CCR.CE = 1
    chip.pc = CODE + 0x0C
    for _ in range(4):
        chip.step()
    fills = {a & ~15 for a, _, kind in bus if kind & FILL}
    assert CODE + 0x10 in fills  # fetched in the BT's EX slot and discarded (U11)


# -- wait states stretch the stage that owns the cycle (PM 7.2.3) ----------------------------


def test_board_waits_lengthen_the_ma_and_the_if():
    fig = Figure("", "", [LOAD, NOP, NOP, NOP, NOP, NOP], [], waits={DATA + 0x40 * 9: 3})
    log, _ = run(fig, contention=True)
    assert ("ma", 3, 4) in log  # one state of the BSC's (one-state here) plus three waits
    fig = Figure("", "", [NOP] * 8, [], waits={CODE + 4: 2})
    log, _ = run(fig, contention=True)
    assert ("slot", 2, 3, False) in log  # the IF of the longword at CODE + 4


def test_state_round_trip_resumes_the_pipeline_exactly():
    fig = Figure("", "", [MAC_L, STS_MACL, LOAD, ADD, BT_8, NOP, NOP, NOP, NOP, NOP], [], sr=1)
    chip, _ = _board(fig)
    chip._pipeline_restart()
    for _ in range(2):
        chip.step()
    saved = chip.state
    first = [chip.step() for _ in range(6)]
    chip.state = saved
    chip.bsc.cycle_states[:] = [1, 1, 1, 1]  # loading the BSC's registers recomputed them
    assert [chip.step() for _ in range(6)] == first
