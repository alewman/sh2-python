"""Rung 1: generate the instruction corpus, run it on the referees and on this core, compare.

docs/validation.md, rung 1. There is no SH-2 SingleStepTests, so the corpus is made
here: random register and memory states per instruction form, each run on MAME 0.285's
interpreter (``-nodrc``; ``mame_corpus.lua``) and on jgenesis' ``sh2-emu``
(``scripts/build_jgenesis_harness.py``). Cases where the two agree are the gate; cases
where they disagree are ruled from the manuals in ``overrides.json``. The tier is
**emulator-derived**: a detector of register semantics, never of timing.

A case is one line, the same for every runner (hexadecimal but the id)::

    C <id> <seed> <pc> <sr> <gbr> <vbr> <mach> <macl> <pr> <r0> .. <r15> W <a:w,...> L <a,...>

Memory is two regions, CODE (H'0600F000, H'2400 bytes) and DATA (H'06020000, H'1000
bytes), both in the 32X's SDRAM, which MAME maps there. Each is filled from ``seed``
by xorshift32 (CODE from ``seed``, DATA from ``seed ^ H'9E3779B9``), then the ``W``
words are written: the instruction under test at H'06010000, its slot or a NOP at
H'06010002, and a landing loop (``BRA $`` + ``NOP``) at every ``L`` address the case can
end at. Address registers are chosen inside DATA, branch targets inside CODE, so a
case touches nothing else. A runner sets the state, runs until PC reaches a landing
outside a delay slot, and reports::

    R <id> <status> <pc> <sr> <gbr> <vbr> <mach> <macl> <pr> <r0> .. <r15> M <a:b,...>

with every byte of CODE and DATA that changed. ``status`` is ``ok``, ``timeout`` or
``outside``.

    python validation/rung1/corpus.py generate > validation/rung1/cases.txt
    python validation/rung1/corpus.py core < cases.txt > core.txt   # this core
    python validation/rung1/corpus.py compare mame.txt jgenesis.txt  # agreement report
"""

from __future__ import annotations

import random
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sh2_python._decode import FORMS, Form, decode_index  # noqa: E402
from sh2_python.bus import FETCH  # noqa: E402

CODE_BASE, CODE_SIZE = 0x0600F000, 0x2400
DATA_BASE, DATA_SIZE = 0x06020000, 0x1000
TEST = 0x06010000  # the instruction under test
SLOT = TEST + 2
NEXT = TEST + 4  # the fall-through landing
LANDING_LOW, LANDING_HIGH = 0x0600F100, 0x0600FE00  # where branch targets are put
BRA_SELF, NOP = 0xAFFE, 0x0009
DATA_SEED_XOR = 0x9E3779B9
M32 = 0xFFFFFFFF

INDEX = decode_index()
DELAYED = ("BRA", "BSR", "BRAF", "BSRF", "JMP", "JSR", "RTS", "RTE", "BT/S", "BF/S")
PC_CHANGING = frozenset("JMP JSR BRA BSR RTS RTE BT BF TRAPA BF/S BT/S BSRF BRAF".split())
EXCLUDED = {"SLEEP"}  # waits for an interrupt: the referees cannot land (validation/rung1)


# -- memory images -------------------------------------------------------------------


def xorshift_fill(size: int, seed: int) -> bytearray:
    """The same fill as the Rust and Lua runners: xorshift32 (13, 17, 5), big-endian."""
    x = seed or 0x12345678
    out = bytearray(size)
    for i in range(0, size, 4):
        x ^= (x << 13) & M32
        x ^= x >> 17
        x ^= (x << 5) & M32
        out[i : i + 4] = x.to_bytes(4, "big")
    return out


class Case:
    """One generated case: the state, the words written, the landings."""

    def __init__(self, ident: int, seed: int, rng: random.Random) -> None:
        self.ident = ident
        self.seed = seed
        self.rng = rng
        self.regs = {
            "pc": TEST,
            "sr": rng.getrandbits(32) & 0x3F3,
            "gbr": rng.getrandbits(32),
            "vbr": rng.getrandbits(32),
            "mach": rng.getrandbits(32),
            "macl": rng.getrandbits(32),
            "pr": rng.getrandbits(32),
        }
        self.r = [rng.getrandbits(32) for _ in range(16)]
        self.words: dict[int, int] = {TEST: NOP, SLOT: NOP}
        self.landings: list[int] = []
        self.locked: set[str] = set()  # registers a constraint has fixed
        self.land(NEXT)

    def land(self, address: int) -> None:
        self.words[address] = BRA_SELF
        self.words[address + 2] = NOP
        if address not in self.landings:
            self.landings.append(address)

    def data_address(self, size: int, room: int = 0, low: int = 0) -> int:
        """A random address in DATA aligned to ``size``, with ``room`` bytes after it."""
        span = DATA_SIZE - room - low - size
        return (DATA_BASE + low + self.rng.randrange(0, span)) & ~(size - 1)

    def lock(self, name: str, value: int) -> bool:
        """Fix register ``name`` (r0-r15, gbr, vbr, pr); False if already fixed."""
        if name in self.locked:
            return False
        self.locked.add(name)
        if name.startswith("r") and name[1:].isdigit():
            self.r[int(name[1:])] = value & M32
        else:
            self.regs[name] = value & M32
        return True

    def landing_address(self) -> int:
        while True:
            address = self.rng.randrange(LANDING_LOW, LANDING_HIGH) & ~1
            if address not in self.words and address + 2 not in self.words:
                return address

    def line(self) -> str:
        g = self.regs
        head = [g["pc"], g["sr"], g["gbr"], g["vbr"], g["mach"], g["macl"], g["pr"], *self.r]
        words = ",".join(f"{a:X}:{w:X}" for a, w in sorted(self.words.items()))
        landings = ",".join(f"{a:X}" for a in self.landings)
        return (
            f"C {self.ident} {self.seed:X} " + " ".join(f"{v:X}" for v in head)
            + f" W {words} L {landings}"
        )  # fmt: skip


# -- constraints per form -------------------------------------------------------------


def _size(form: Form) -> int:
    m = form.mnemonic
    if m.endswith(".W"):
        return 2
    if m.endswith(".L") or m in ("LDC.L", "LDS.L", "STC.L", "STS.L"):
        return 4
    return 1


def _field(form: Form, letter: str, opcode: int) -> int:
    return (opcode >> (12 - form.code.index(letter))) & 15


def constrain(case: Case, form: Form, opcode: int, address: int) -> bool:
    """Make the instruction at ``address`` touch only DATA and land in CODE.

    Returns False when two constraints need the same register (the caller rerolls).
    """
    ops = form.operands
    size = _size(form)
    rng = case.rng
    n = _field(form, "n", opcode) if "n" in form.code else -1
    m = _field(form, "m", opcode) if "m" in form.code else -1
    mn = form.mnemonic

    if mn in ("MAC.W", "MAC.L"):
        step = 2 if mn == "MAC.W" else 4
        if n == m:
            return case.lock(f"r{n}", case.data_address(step, room=2 * step))
        return case.lock(f"r{n}", case.data_address(step, room=step)) and case.lock(
            f"r{m}", case.data_address(step, room=step)
        )
    if ops in ("@Rm,Rn", "@Rm+,Rn") or ops.startswith("@Rm+,"):
        return case.lock(f"r{m}", case.data_address(size))
    if ops in ("Rm,@Rn", "@Rn"):
        return case.lock(f"r{n}", case.data_address(size))
    if ops.endswith(",@-Rn"):
        return case.lock(f"r{n}", case.data_address(size, low=8))
    if ops in ("@(disp,Rm),R0", "@(disp,Rm),Rn"):
        return case.lock(f"r{m}", case.data_address(4, room=64))
    if ops in ("R0,@(disp,Rn)", "Rm,@(disp,Rn)"):
        return case.lock(f"r{n}", case.data_address(4, room=64))
    if ops in ("@(R0,Rm),Rn", "Rm,@(R0,Rn)"):
        base = m if ops.startswith("@") else n
        if base == 0:
            return False
        index = rng.randrange(0, 0x800) & ~(size - 1)
        return case.lock("r0", index) and case.lock(
            f"r{base}", (DATA_BASE + rng.randrange(0, 0x7F0)) & ~(size - 1)
        )
    if "GBR" in ops and "R0,GBR" not in ops:
        return case.lock("gbr", case.data_address(4, room=0x400))
    if ops == "#imm,@(R0,GBR)":
        return case.lock("r0", rng.randrange(0, 0x800)) and case.lock(
            "gbr", DATA_BASE + rng.randrange(0, 0x7F0)
        )
    if mn in ("JMP", "JSR"):
        target = case.landing_address()
        case.land(target)
        return case.lock(f"r{m}", target)
    if mn in ("BRAF", "BSRF"):
        target = case.landing_address()
        case.land(target)
        return case.lock(f"r{m}", (target - (address + 4)) & M32)
    if mn == "RTS":
        target = case.landing_address()
        case.land(target)
        return case.lock("pr", target)
    if mn == "RTE":
        sp = case.data_address(4, room=8)
        target = case.landing_address()
        case.land(target)
        case.words[sp] = target >> 16
        case.words[sp + 2] = target & 0xFFFF
        return case.lock("r15", sp)
    if mn == "TRAPA":
        return exception_frame(case, opcode & 0xFF)
    if ops == "label":
        d = opcode & (0xFF if form.code.startswith("1000") else 0xFFF)
        bits = 8 if form.code.startswith("1000") else 12
        if d & (1 << (bits - 1)):
            d -= 1 << bits
        target = (address + 4 + 2 * d) & M32
        if not CODE_BASE <= target < CODE_BASE + CODE_SIZE - 4:
            return False
        if target in (TEST, SLOT, NEXT, NEXT + 2) or target + 2 in (TEST, SLOT, NEXT):
            return False
        case.land(target)
        return True
    return True


def exception_frame(case: Case, vector: int) -> bool:
    """VBR, the vector and R15 for an exception that lands at a fresh address."""
    vbr = DATA_BASE + (case.rng.randrange(0, 0xC00) & ~3)
    if vbr + 4 * vector + 4 > DATA_BASE + DATA_SIZE:
        vbr = DATA_BASE
    entry = vbr + 4 * vector
    sp = case.data_address(4, low=8)
    if entry <= sp < entry + 4 or entry <= sp - 8 < entry + 4 or sp - 8 <= entry < sp:
        return False  # the pushes would overwrite the vector
    target = case.landing_address()
    case.land(target)
    case.words[entry] = target >> 16
    case.words[entry + 2] = target & 0xFFFF
    return case.lock("vbr", vbr) and case.lock("r15", sp)


def random_opcode(form: Form, rng: random.Random) -> int:
    value = 0
    for c in form.code:
        value = (value << 1) | (int(c) if c in "01" else rng.getrandbits(1))
    return value


# -- generation ----------------------------------------------------------------------


def generate(per_form: int = 100, seed: int = 20261023) -> Iterator[str]:
    """The whole corpus: every form, delay slots, slot illegals, undefined codes."""
    master = random.Random(seed)
    ident = 0

    def new_case() -> Case:
        nonlocal ident
        ident += 1
        case_seed = master.getrandbits(32) | 1
        return Case(ident, case_seed, random.Random(case_seed))

    # 1. Every form, alone.
    for form in FORMS:
        if form.mnemonic in EXCLUDED:
            continue
        made = 0
        while made < per_form:
            case = new_case()
            opcode = random_opcode(form, case.rng)
            if not constrain(case, form, opcode, TEST):
                continue
            case.words[TEST] = opcode
            if form.mnemonic in DELAYED:
                case.words[SLOT] = NOP
            yield case.line()
            made += 1

    # 2. Delayed branches with a random instruction in the slot (U1: PC-relative slots).
    branches = [f for f in FORMS if f.mnemonic in DELAYED]
    slot_forms = [f for f in FORMS if f.mnemonic not in PC_CHANGING | EXCLUDED]
    made = 0
    while made < 20 * per_form:
        case = new_case()
        branch = case.rng.choice(branches)
        slot = case.rng.choice(slot_forms)
        bop = random_opcode(branch, case.rng)
        sop = random_opcode(slot, case.rng)
        if branch.mnemonic in ("BT/S", "BF/S"):
            # Taken: T = 1 for BT/S, 0 for BF/S; the not-taken case is section 1's.
            t = 1 if branch.mnemonic == "BT/S" else 0
            case.regs["sr"] = (case.regs["sr"] & ~1) | t
        if not constrain(case, branch, bop, TEST) or not constrain(case, slot, sop, SLOT):
            continue
        case.words[TEST], case.words[SLOT] = bop, sop
        yield case.line()
        made += 1

    # 3. Slot illegal: a PC-changing or undefined instruction in a slot (HM 4.5.3, U3).
    pc_changers = [f for f in FORMS if f.mnemonic in PC_CHANGING]
    undefined = [op for op in range(0x10000) if INDEX[op] is None]
    made = 0
    while made < 2 * per_form:
        case = new_case()
        branch = case.rng.choice([f for f in branches if f.mnemonic not in ("RTE",)])
        bop = random_opcode(branch, case.rng)
        if branch.mnemonic in ("BT/S", "BF/S"):
            t = 1 if branch.mnemonic == "BT/S" else 0
            case.regs["sr"] = (case.regs["sr"] & ~1) | t
        if case.rng.random() < 0.5:
            sop = case.rng.choice(undefined)
        else:
            sop = random_opcode(case.rng.choice(pc_changers), case.rng)
        if not constrain(case, branch, bop, TEST) or not exception_frame(case, 6):
            continue
        case.words[TEST], case.words[SLOT] = bop, sop
        yield case.line()
        made += 1

    # 4. Undefined codes outside a slot: general illegal (HM 4.5.4).
    made = 0
    while made < 3 * per_form:
        case = new_case()
        op = case.rng.choice(undefined)
        if not exception_frame(case, 4):
            continue
        case.words[TEST] = op
        yield case.line()
        made += 1


# -- reading cases and results ---------------------------------------------------------


def parse_case(line: str) -> dict:
    fields = line.split()
    assert fields[0] == "C"
    w, l_at = fields.index("W"), fields.index("L")
    values = [int(t, 16) for t in fields[3:26]]
    words = {}
    for part in ",".join(fields[w + 1 : l_at]).split(","):
        if part:
            a, v = part.split(":")
            words[int(a, 16)] = int(v, 16)
    landings = [int(t, 16) for t in ",".join(fields[l_at + 1 :]).split(",") if t]
    return {
        "id": int(fields[1]),
        "seed": int(fields[2], 16),
        "pc": values[0],
        "sr": values[1],
        "gbr": values[2],
        "vbr": values[3],
        "mach": values[4],
        "macl": values[5],
        "pr": values[6],
        "r": values[7:23],
        "words": words,
        "landings": landings,
    }


RESULT_KEYS = ("pc", "sr", "gbr", "vbr", "mach", "macl", "pr", *(f"r{i}" for i in range(16)))


def parse_result(line: str) -> dict:
    fields = line.split()
    assert fields[0] == "R"
    m_at = fields.index("M")
    values = [int(t, 16) for t in fields[3:m_at]]
    memory = {}
    for part in ",".join(fields[m_at + 1 :]).split(","):
        if part:
            a, b = part.split(":")
            memory[int(a, 16)] = int(b, 16)
    out = {"id": int(fields[1]), "status": fields[2], "memory": memory}
    out.update(zip(RESULT_KEYS, values, strict=True))
    return out


def format_result(ident: int, status: str, regs: dict, memory: dict[int, int]) -> str:
    values = " ".join(f"{regs[k]:X}" for k in RESULT_KEYS)
    diffs = ",".join(f"{a:X}:{b:X}" for a, b in sorted(memory.items()))
    return f"R {ident} {status} {values} M {diffs}"


# -- this core as a runner --------------------------------------------------------------


def run_core(case: dict) -> str:
    """Run one case on sh2_python, through the same memory map as the referees."""
    from sh2_python import SH7604

    code = xorshift_fill(CODE_SIZE, case["seed"])
    data = xorshift_fill(DATA_SIZE, case["seed"] ^ DATA_SEED_XOR)
    regions = ((CODE_BASE, code), (DATA_BASE, data))
    for address, word in case["words"].items():
        for base, buf in regions:
            if base <= address < base + len(buf):
                buf[address - base : address - base + 2] = word.to_bytes(2, "big")
    images = [bytes(buf) for _, buf in regions]
    outside = []

    def locate(address: int, width: int, fetch: bool = False) -> tuple[bytearray | None, int]:
        address &= 0x1FFFFFFF
        for base, buf in regions:
            if base <= address and address + (width >> 3) <= base + len(buf):
                return buf, address - base
        if not fetch:
            outside.append(address)
        return None, 0

    def read(address: int, width: int, kind: int) -> int:
        # The core's pipeline fetches ahead of execution and fetches what a taken branch
        # discards (PM 7.7.5, U11): a fetch past the regions is not an access the case
        # makes; it reads 0.
        buf, offset = locate(address, width, bool(kind & FETCH))
        if buf is None:
            return 0
        return int.from_bytes(buf[offset : offset + (width >> 3)], "big")

    def write(address: int, width: int, value: int, kind: int) -> None:
        buf, offset = locate(address, width)
        if buf is not None:
            buf[offset : offset + (width >> 3)] = value.to_bytes(width >> 3, "big")

    chip = SH7604(read, write)
    chip.R = list(case["r"])
    chip.sr, chip.gbr, chip.vbr = case["sr"], case["gbr"], case["vbr"]
    chip.mach, chip.macl, chip.pr = case["mach"], case["macl"], case["pr"]
    chip.pc = case["pc"]
    status = "timeout"
    for _ in range(16):
        chip.step()
        if chip.delay_target < 0 and chip.pc in case["landings"]:
            status = "ok"
            break
    if outside:
        status = "outside"
    regs = {
        "pc": chip.pc, "sr": chip.sr, "gbr": chip.gbr, "vbr": chip.vbr, "mach": chip.mach,
        "macl": chip.macl, "pr": chip.pr,
    }  # fmt: skip
    regs.update({f"r{i}": v for i, v in enumerate(chip.R)})
    memory = {}
    for (base, buf), image in zip(regions, images, strict=True):
        for i, (a, b) in enumerate(zip(buf, image, strict=True)):
            if a != b:
                memory[base + i] = a
    return format_result(case["id"], status, regs, memory)


def describe(case: dict) -> str:
    """The case's instructions as text: the one under test, and its slot when it has one."""
    from sh2_python.disasm import disassemble_word

    first = disassemble_word(case["words"].get(TEST, NOP), TEST)
    text = first.text
    if first.mnemonic in DELAYED or INDEX[case["words"].get(TEST, NOP)] is None:
        slot = disassemble_word(case["words"].get(SLOT, NOP), SLOT)
        if slot.opcode != NOP:
            text += " ; " + slot.text
    return text


def differences(a: dict, b: dict) -> list[str]:
    """The fields two results disagree on (status, registers, memory bytes)."""
    out = [key for key in ("status", *RESULT_KEYS) if a[key] != b[key]]
    if a["memory"] != b["memory"]:
        out.append("memory")
    return out


def load_results(path: str) -> dict[int, dict]:
    with open(path) as handle:
        return {r["id"]: r for r in map(parse_result, handle) if r}


def compare(cases_path: str, *paths: str) -> int:
    """Print, per mnemonic, how often the result files disagree, with one example each."""
    with open(cases_path) as handle:
        cases = {c["id"]: c for c in map(parse_case, handle)}
    results = [load_results(p) for p in paths]
    by_kind: dict[tuple[str, tuple[str, ...]], list[int]] = {}
    for ident, case in cases.items():
        rs = [r.get(ident) for r in results]
        if any(r is None for r in rs):
            continue
        fields = sorted({f for r in rs[1:] for f in differences(rs[0], r)})
        if fields:
            mnemonic = describe(case).split()[0]
            by_kind.setdefault((mnemonic, tuple(fields)), []).append(ident)
    for (mnemonic, fields), idents in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
        print(f"{len(idents):6d}  {mnemonic:10} {','.join(fields)}  e.g. case {idents[0]}: "
              f"{describe(cases[idents[0]])}")  # fmt: skip
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "generate"
    if command == "generate":
        per_form = int(argv[2]) if len(argv) > 2 else 100
        for line in generate(per_form):
            print(line)
        return 0
    if command == "core":
        for line in sys.stdin:
            if line.strip():
                print(run_core(parse_case(line)))
        return 0
    if command == "compare":
        return compare(*argv[2:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
