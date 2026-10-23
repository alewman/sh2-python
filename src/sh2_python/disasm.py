"""A disassembler for the SH-2, from the same decode table the core runs.

Mnemonics and operand order are PM's (appendix A, section 6). Numbers follow MAME's
``sh_dasm.cpp`` so a line can be compared with MAME's debugger and ``unidasm``:
immediates as ``#$xx`` (the raw byte), displacements scaled to bytes (``@($0C,R2)``;
four digits for the PC forms and the word and longword GBR forms), branch targets and
PC-relative addresses absolute (``BRA $00014490``, ``MOV.L @($008C,PC),R1 [0001A2D4]``).
Where MAME's text differs from PM's mnemonics, PM is followed and tests/test_disasm.py
lists the
differences: MAME writes ``BTS``/``BFS`` for ``BT/S``/``BF/S``, ``STS GBR,Rn`` for
``STC GBR,Rn``, ``JMP R0``/``JSR R0`` for ``JMP @R0``/``JSR @R0`` and ``TAS R0`` for
``TAS.B @R0``. Undefined codes disassemble as ``.word $xxxx``.
"""

from collections.abc import Callable
from dataclasses import dataclass

from sh2_python._decode import Form, decode_index

WordReader = Callable[[int], int]

_INDEX = decode_index()


@dataclass(frozen=True, slots=True)
class Instruction:
    """One disassembled instruction."""

    address: int
    opcode: int
    mnemonic: str
    operands: str
    #: The branch target or PC-relative address the instruction names, if any.
    target: int | None = None

    @property
    def text(self) -> str:
        if not self.operands:
            return self.mnemonic
        return f"{self.mnemonic:<8}{self.operands}"

    @property
    def length(self) -> int:
        return 2

    @property
    def form(self) -> Form | None:
        return _INDEX[self.opcode]


def _scale(mnemonic: str) -> int:
    if mnemonic.endswith(".W"):
        return 2
    if mnemonic.endswith(".L") or mnemonic == "MOVA":
        return 4
    return 1


def _field(code: str, letter: str, opcode: int) -> int:
    first = code.index(letter)
    return (opcode >> (12 - first)) & 15


def disassemble_word(opcode: int, address: int = 0) -> Instruction:
    """Disassemble one 16-bit ``opcode`` that sits at ``address``."""
    opcode &= 0xFFFF
    form = _INDEX[opcode]
    if form is None:
        return Instruction(address, opcode, ".word", f"${opcode:04X}")
    text = form.operands
    target = None
    # The code pattern says where each register field is: "nnnn" or "mmmm" in bits
    # 11-8 or 7-4 (LDC Rm,SR has Rm in 11-8; MOV.B @(disp,Rm),R0 has it in 7-4).
    if "n" in form.code:
        text = text.replace("Rn", f"R{_field(form.code, 'n', opcode)}")
    if "m" in form.code:
        text = text.replace("Rm", f"R{_field(form.code, 'm', opcode)}")
    scale = _scale(form.mnemonic)
    if "disp" in text:
        if "PC" in text:
            disp = (opcode & 0xFF) * scale
            base = (address & ~3) if scale == 4 else address
            target = (base + 4 + disp) & 0xFFFFFFFF
            text = text.replace("disp", f"${disp:04X}") + f" [{target:08X}]"
        elif "GBR" in text:
            width = 2 if scale == 1 else 4  # MAME prints the byte form with two digits
            text = text.replace("disp", f"${(opcode & 0xFF) * scale:0{width}X}")
        else:
            text = text.replace("disp", f"${(opcode & 15) * scale:02X}")
    if "#imm" in text:
        text = text.replace("#imm", f"#${opcode & 0xFF:02X}")
    if text == "label":
        if form.code.startswith("1000"):  # BT, BF, BT/S, BF/S: 8-bit displacement
            d = opcode & 0xFF
            d = d - 0x100 if d & 0x80 else d
        else:  # BRA, BSR: 12-bit
            d = opcode & 0xFFF
            d = d - 0x1000 if d & 0x800 else d
        target = (address + 4 + 2 * d) & 0xFFFFFFFF
        text = f"${target:08X}"
    return Instruction(address, opcode, form.mnemonic, text, target)


def disassemble(peek16: WordReader, address: int) -> Instruction:
    """Disassemble the instruction at ``address`` using a side-effect-free word reader."""
    return disassemble_word(peek16(address), address)


def disassemble_range(peek16: WordReader, address: int, count: int) -> list[Instruction]:
    out = []
    for _ in range(count):
        out.append(disassemble(peek16, address))
        address = (address + 2) & 0xFFFFFFFF
    return out


__all__ = ["Instruction", "WordReader", "disassemble", "disassemble_range", "disassemble_word"]
