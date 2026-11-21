"""A tiny SH-2 assembler for the rung-4 loops: exactly the forms DN's bodies and 32xspd use.

No SH toolchain is installed here (docs/validation.md "What could not be found"), so the
loops published as assembler text (DP's companion, ``probe_build_info.json``) are
encoded by this module. Every encoding comes from the core's decode table, which is
generated from PM appendix A (``scripts/gen_decode.py``): an instruction without a
label or a literal is looked up in a table built by disassembling all 65,536 words
with :mod:`sh2_python.disasm`, so ``assemble`` and the disassembler agree by
construction, and :func:`check_round_trip` disassembles the result again. The three
forms that need an address are encoded here from PM's code patterns:

- ``bt``/``bf``/``bt/s``/``bf/s label``: ``10001001``/``10001011``/``10001101``/
  ``10001111`` + an 8-bit displacement, target = address + 4 + 2 * disp (PM 6.x);
- ``bra label``: ``1010`` + a 12-bit displacement, same rule;
- ``mov.l =value,Rn``: ``1101nnnn`` + an 8-bit displacement to a literal in a pool
  after the code (``MOV.L @(disp,PC),Rn``: the literal at (address & ~3) + 4 +
  4 * disp), the GNU assembler's pseudo-instruction. The pool is longword aligned and
  starts after the last instruction, padded with one ``NOP`` word when needed
  (that padding is data, not an executed instruction).

Source syntax is DN's: one instruction per line, ``label:`` lines, registers
``r0``-``r15``, decimal or ``0x`` immediates, ``;`` comments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sh2_python.disasm import disassemble_word

M32 = 0xFFFFFFFF
NOP = 0x0009

_BRANCH8 = {"BT": 0x8900, "BF": 0x8B00, "BT/S": 0x8D00, "BF/S": 0x8F00}


def _plain_table() -> dict[str, int]:
    """Disassembly text (normalized) -> opcode, for every form without an address."""
    table: dict[str, int] = {}
    for word in range(0x10000):
        ins = disassemble_word(word, 0)
        if ins.mnemonic == ".word" or ins.target is not None:
            continue
        key = _key(ins.mnemonic, ins.operands)
        table.setdefault(key, word)
    return table


def _key(mnemonic: str, operands: str) -> str:
    return (mnemonic + " " + operands.replace(" ", "")).upper().strip()


_TABLE: dict[str, int] | None = None


def _number(text: str, symbols: dict[str, int]) -> int:
    text = text.strip()
    if text in symbols:
        return symbols[text]
    return int(text, 0)


def _normalize_operands(operands: str) -> str:
    """DN's spelling to the disassembler's: ``#17`` -> ``#$11``, ``@(4,r1)`` -> ``@($04,R1)``."""
    operands = operands.replace(" ", "").upper()

    def immediate(m: re.Match) -> str:
        return f"#${int(m.group(1), 0) & 0xFF:02X}"

    def displacement(m: re.Match) -> str:
        return f"@(${int(m.group(1), 0):02X},{m.group(2)}"

    operands = re.sub(r"#(-?(?:0X[0-9A-F]+|\d+))", immediate, operands)
    return re.sub(r"@\((0X[0-9A-F]+|\d+),(R\d+)", displacement, operands)


@dataclass
class Program:
    """Assembled code: ``words`` from ``origin``, with labels and the literal pool."""

    origin: int
    words: list[int] = field(default_factory=list)
    labels: dict[str, int] = field(default_factory=dict)
    #: Addresses of the halfwords that are instructions (not pool or padding).
    code: list[int] = field(default_factory=list)

    def bytes(self) -> bytes:
        return b"".join(w.to_bytes(2, "big") for w in self.words)


def assemble(source: str, origin: int, symbols: dict[str, int] | None = None) -> Program:
    """Assemble ``source`` at ``origin`` (two passes: labels, then encodings)."""
    global _TABLE
    if _TABLE is None:
        _TABLE = _plain_table()
    symbols = dict(symbols or {})
    lines = []
    for raw in source.splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.endswith(":"):
            lines.append(("label", line[:-1].strip()))
        else:
            lines.append(("ins", line))
    # Pass 1: addresses of labels and of the instructions; collect literals in order.
    labels: dict[str, int] = {}
    address = origin
    literals: list[str] = []
    for kind, text in lines:
        if kind == "label":
            labels[text] = address
            continue
        mnemonic, _, operands = text.partition(" ")
        if mnemonic.lower() == "mov.l" and operands.strip().startswith("="):
            value = operands.strip()[1:].rsplit(",", 1)[0]
            if value not in literals:
                literals.append(value)
        address += 2
    code_end = address
    pool = (code_end + 3) & ~3
    pool_address = {value: pool + 4 * i for i, value in enumerate(literals)}
    symbols.update(labels)
    # Pass 2: encode.
    program = Program(origin, labels=labels)
    address = origin
    for kind, text in lines:
        if kind == "label":
            continue
        program.words.append(_encode(text, address, symbols, pool_address))
        program.code.append(address)
        address += 2
    if literals and pool != code_end:
        program.words.append(NOP)  # padding to the pool's longword boundary
    for value in literals:
        literal = _number(value, symbols) & M32
        program.words += [literal >> 16, literal & 0xFFFF]
    return program


def _encode(text: str, address: int, symbols: dict[str, int], pool: dict[str, int]) -> int:
    mnemonic, _, operands = text.partition(" ")
    mnemonic = mnemonic.upper()
    operands = operands.strip()
    if mnemonic in _BRANCH8 or mnemonic == "BRA":
        target = symbols[operands]
        disp = (target - address - 4) >> 1
        if mnemonic == "BRA":
            if not -2048 <= disp < 2048:
                raise ValueError(f"{text}: target out of range")
            return 0xA000 | (disp & 0xFFF)
        if not -128 <= disp < 128:
            raise ValueError(f"{text}: target out of range")
        return _BRANCH8[mnemonic] | (disp & 0xFF)
    if mnemonic == "MOV.L" and operands.startswith("="):
        value, register = operands[1:].rsplit(",", 1)
        n = int(register.strip().lower().removeprefix("r"))
        disp = (pool[value] - ((address & ~3) + 4)) >> 2
        if not 0 <= disp < 256:
            raise ValueError(f"{text}: literal out of range")
        return 0xD000 | (n << 8) | disp
    key = mnemonic + " " + _normalize_operands(operands) if operands else mnemonic
    try:
        return _TABLE[key.strip()]  # type: ignore[index]
    except KeyError:
        raise ValueError(f"cannot assemble {text!r} (looked up {key!r})") from None


def check_round_trip(program: Program) -> None:
    """Disassemble every instruction again: its text must assemble to the same word."""
    for address in program.code:
        word = program.words[(address - program.origin) >> 1]
        ins = disassemble_word(word, address)
        if ins.mnemonic == ".word":
            raise AssertionError(f"{address:#x}: {word:04X} is undefined")
        if ins.target is None and _TABLE[_key(ins.mnemonic, ins.operands)] != word:  # type: ignore[index]
            raise AssertionError(f"{address:#x}: {word:04X} does not round-trip")


__all__ = ["Program", "assemble", "check_round_trip"]
