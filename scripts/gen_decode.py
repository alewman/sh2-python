#!/usr/bin/env python3
"""Generate src/sh2_python/_decode.py, the decode table, from PM appendix A and section 7.

Input: ``tests/pm_a50.py`` (PM table A.50, extracted by ``scripts/extract_pm_tables.py``),
which gives every SH-2 form its code pattern and execution states. This script adds,
for each form:

- the handler name, made from the mnemonic and the operand form, so a reader can grep
  ``_op_mov_l_load_disp`` from ``MOV.L @(disp,Rm),Rn``;
- the pipeline class, from PM table 7.2 ("Number of Instruction Stages and Execution
  States", PM pp. 177-183), transcribed below with the figure that draws it;
- the PM section 6 page that defines the instruction.

    python scripts/gen_decode.py            # rewrite src/sh2_python/_decode.py
    python scripts/gen_decode.py --check    # exit 1 if the file is stale

tests/test_decode.py regenerates the text and compares it with the file, so the table
cannot drift from the manual's data.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from pm_a50 import A50  # noqa: E402

TARGET = ROOT / "src" / "sh2_python" / "_decode.py"

# PM section 6 ("Instruction Descriptions"): where each mnemonic is defined.
SECTIONS = {
    "ADD": "6.2", "ADDC": "6.3", "ADDV": "6.4", "AND": "6.5", "AND.B": "6.5",
    "BF": "6.6", "BF/S": "6.7", "BRA": "6.8", "BRAF": "6.9", "BSR": "6.10",
    "BSRF": "6.11", "BT": "6.12", "BT/S": "6.13", "CLRMAC": "6.14", "CLRT": "6.15",
    "CMP/EQ": "6.16", "CMP/GE": "6.16", "CMP/GT": "6.16", "CMP/HI": "6.16",
    "CMP/HS": "6.16", "CMP/PL": "6.16", "CMP/PZ": "6.16", "CMP/STR": "6.16",
    "DIV0S": "6.17", "DIV0U": "6.18", "DIV1": "6.19", "DMULS.L": "6.20",
    "DMULU.L": "6.21", "DT": "6.22", "EXTS.B": "6.23", "EXTS.W": "6.23",
    "EXTU.B": "6.24", "EXTU.W": "6.24", "JMP": "6.25", "JSR": "6.26", "LDC": "6.27",
    "LDC.L": "6.27", "LDS": "6.28", "LDS.L": "6.28", "MAC.L": "6.29", "MAC.W": "6.31",
    "MOVA": "6.36", "MOVT": "6.37", "MUL.L": "6.38", "MULS.W": "6.39",
    "MULU.W": "6.40", "NEG": "6.41", "NEGC": "6.42", "NOP": "6.43", "NOT": "6.44",
    "OR": "6.45", "OR.B": "6.45", "ROTCL": "6.46", "ROTCR": "6.47", "ROTL": "6.48",
    "ROTR": "6.49", "RTE": "6.50", "RTS": "6.51", "SETT": "6.52", "SHAL": "6.53",
    "SHAR": "6.54", "SHLL": "6.55", "SHLL2": "6.56", "SHLL8": "6.56", "SHLL16": "6.56",
    "SHLR": "6.57", "SHLR2": "6.58", "SHLR8": "6.58", "SHLR16": "6.58", "SLEEP": "6.59",
    "STC": "6.60", "STC.L": "6.60", "STS": "6.61", "STS.L": "6.61", "SUB": "6.62",
    "SUBC": "6.63", "SUBV": "6.64", "SWAP.B": "6.65", "SWAP.W": "6.65", "TAS.B": "6.66",
    "TRAPA": "6.67", "TST": "6.68", "TST.B": "6.68", "XOR": "6.69", "XOR.B": "6.69",
    "XTRCT": "6.70",
}  # fmt: skip


def mov_section(operands: str) -> str:
    """PM splits MOV over four sections by addressing mode."""
    if operands.startswith("#imm") or "(disp,PC)" in operands:
        return "6.33"  # Move Immediate Data
    if "GBR" in operands:
        return "6.34"  # Move Peripheral Data
    if "(disp," in operands:
        return "6.35"  # Move Structure Data
    return "6.32"  # Move Data


# PM table 7.2, one entry per category, in the table's order: the class name used in
# the decode table, then the instructions the table lists for it ("MNEMONIC operands",
# as table A.50 writes them). Stage strings, figures, ID stalls and multiplier states
# live in src/sh2_python/pipeline.py beside the same names.
CLASS_MEMBERS: dict[str, tuple[str, ...]] = {
    "transfer": ("MOV #imm,Rn", "MOV Rm,Rn", "MOVA @(disp,PC),R0", "MOVT Rn",
                 "SWAP.B Rm,Rn", "SWAP.W Rm,Rn", "XTRCT Rm,Rn"),
    "load": ("MOV.W @(disp,PC),Rn", "MOV.L @(disp,PC),Rn", "MOV.B @Rm,Rn", "MOV.W @Rm,Rn",
             "MOV.L @Rm,Rn", "MOV.B @Rm+,Rn", "MOV.W @Rm+,Rn", "MOV.L @Rm+,Rn",
             "MOV.B @(disp,Rm),R0", "MOV.W @(disp,Rm),R0", "MOV.L @(disp,Rm),Rn",
             "MOV.B @(R0,Rm),Rn", "MOV.W @(R0,Rm),Rn", "MOV.L @(R0,Rm),Rn",
             "MOV.B @(disp,GBR),R0", "MOV.W @(disp,GBR),R0", "MOV.L @(disp,GBR),R0"),
    "store": ("MOV.B Rm,@Rn", "MOV.W Rm,@Rn", "MOV.L Rm,@Rn", "MOV.B Rm,@-Rn",
              "MOV.W Rm,@-Rn", "MOV.L Rm,@-Rn", "MOV.B R0,@(disp,Rn)", "MOV.W R0,@(disp,Rn)",
              "MOV.L Rm,@(disp,Rn)", "MOV.B Rm,@(R0,Rn)", "MOV.W Rm,@(R0,Rn)",
              "MOV.L Rm,@(R0,Rn)", "MOV.B R0,@(disp,GBR)", "MOV.W R0,@(disp,GBR)",
              "MOV.L R0,@(disp,GBR)"),
    "arith": ("ADD Rm,Rn", "ADD #imm,Rn", "ADDC Rm,Rn", "ADDV Rm,Rn", "CMP/EQ #imm,R0",
              "CMP/EQ Rm,Rn", "CMP/HS Rm,Rn", "CMP/GE Rm,Rn", "CMP/HI Rm,Rn", "CMP/GT Rm,Rn",
              "CMP/PZ Rn", "CMP/PL Rn", "CMP/STR Rm,Rn", "DIV1 Rm,Rn", "DIV0S Rm,Rn",
              "DIV0U ", "DT Rn", "EXTS.B Rm,Rn", "EXTS.W Rm,Rn", "EXTU.B Rm,Rn",
              "EXTU.W Rm,Rn", "NEG Rm,Rn", "NEGC Rm,Rn", "SUB Rm,Rn", "SUBC Rm,Rn",
              "SUBV Rm,Rn"),
    "mac_w": ("MAC.W @Rm+,@Rn+",),
    "mac_l": ("MAC.L @Rm+,@Rn+",),
    "mul_w": ("MULS.W Rm,Rn", "MULU.W Rm,Rn"),
    "mul_l": ("DMULS.L Rm,Rn", "DMULU.L Rm,Rn", "MUL.L Rm,Rn"),
    "logic": ("AND Rm,Rn", "AND #imm,R0", "NOT Rm,Rn", "OR Rm,Rn", "OR #imm,R0",
              "TST Rm,Rn", "TST #imm,R0", "XOR Rm,Rn", "XOR #imm,R0"),
    "logic_mem": ("AND.B #imm,@(R0,GBR)", "OR.B #imm,@(R0,GBR)", "TST.B #imm,@(R0,GBR)",
                  "XOR.B #imm,@(R0,GBR)"),
    "tas": ("TAS.B @Rn",),
    "shift": ("ROTL Rn", "ROTR Rn", "ROTCL Rn", "ROTCR Rn", "SHAL Rn", "SHAR Rn", "SHLL Rn",
              "SHLR Rn", "SHLL2 Rn", "SHLR2 Rn", "SHLL8 Rn", "SHLR8 Rn", "SHLL16 Rn",
              "SHLR16 Rn"),
    "branch_cond": ("BF label", "BT label"),
    "branch_cond_delayed": ("BF/S label", "BT/S label"),
    "branch": ("BRA label", "BRAF Rm", "BSR label", "BSRF Rm", "JMP @Rm", "JSR @Rm", "RTS "),
    "system": ("CLRT ", "LDC Rm,SR", "LDC Rm,GBR", "LDC Rm,VBR", "LDS Rm,PR", "NOP ",
               "SETT ", "STC SR,Rn", "STC GBR,Rn", "STC VBR,Rn", "STS PR,Rn"),
    "ldc_l": ("LDC.L @Rm+,SR", "LDC.L @Rm+,GBR", "LDC.L @Rm+,VBR"),
    "stc_l": ("STC.L SR,@-Rn", "STC.L GBR,@-Rn", "STC.L VBR,@-Rn"),
    "lds_l_pr": ("LDS.L @Rm+,PR",),
    "sts_l_pr": ("STS.L PR,@-Rn",),
    "reg_to_mac": ("CLRMAC ", "LDS Rm,MACH", "LDS Rm,MACL"),
    "mem_to_mac": ("LDS.L @Rm+,MACH", "LDS.L @Rm+,MACL"),
    "mac_to_reg": ("STS MACH,Rn", "STS MACL,Rn"),
    "mac_to_mem": ("STS.L MACH,@-Rn", "STS.L MACL,@-Rn"),
    "rte": ("RTE ",),
    "trap": ("TRAPA #imm",),
    "sleep": ("SLEEP ",),
}  # fmt: skip

# Operand forms -> handler-name suffix. Forms that are a mnemonic's only form get none.
SUFFIX = {
    "Rm,@Rn": "_store", "@Rm,Rn": "_load",
    "Rm,@-Rn": "_store_predec", "@Rm+,Rn": "_load_postinc",
    "R0,@(disp,Rn)": "_store_disp", "Rm,@(disp,Rn)": "_store_disp",
    "@(disp,Rm),R0": "_load_disp", "@(disp,Rm),Rn": "_load_disp",
    "Rm,@(R0,Rn)": "_store_indexed", "@(R0,Rm),Rn": "_load_indexed",
    "R0,@(disp,GBR)": "_store_gbr", "@(disp,GBR),R0": "_load_gbr",
    "@(disp,PC),Rn": "_load_pc",
    "#imm,Rn": "_imm", "#imm,R0": "_imm",
    "SR,Rn": "_sr", "GBR,Rn": "_gbr", "VBR,Rn": "_vbr",
    "Rm,SR": "_sr", "Rm,GBR": "_gbr", "Rm,VBR": "_vbr",
    "@Rm+,SR": "_sr", "@Rm+,GBR": "_gbr", "@Rm+,VBR": "_vbr",
    "SR,@-Rn": "_sr", "GBR,@-Rn": "_gbr", "VBR,@-Rn": "_vbr",
    "MACH,Rn": "_mach", "MACL,Rn": "_macl", "PR,Rn": "_pr",
    "Rm,MACH": "_mach", "Rm,MACL": "_macl", "Rm,PR": "_pr",
    "@Rm+,MACH": "_mach", "@Rm+,MACL": "_macl", "@Rm+,PR": "_pr",
    "MACH,@-Rn": "_mach", "MACL,@-Rn": "_macl", "PR,@-Rn": "_pr",
}  # fmt: skip


def handler_name(mnemonic: str, operands: str) -> str:
    base = mnemonic.lower().replace(".", "_").replace("/", "_")
    if mnemonic in ("MOV", "ADD", "AND", "OR", "XOR", "TST", "CMP/EQ") and operands in ("Rm,Rn",):
        return f"_op_{base}"
    if mnemonic == "MOVA":
        return "_op_mova"
    return f"_op_{base}{SUFFIX.get(operands, '')}"


def mask_match(code: str) -> tuple[int, int]:
    mask = int("".join("1" if c in "01" else "0" for c in code), 2)
    match = int("".join(c if c in "01" else "0" for c in code), 2)
    return mask, match


def generate() -> str:
    by_name = {}
    for cls, members in CLASS_MEMBERS.items():
        for member in members:
            if member in by_name:
                raise SystemExit(f"{member} is in two classes")
            by_name[member] = cls
    rows = []
    handlers = set()
    for mnemonic, operands, code, states in A50:
        key = f"{mnemonic} {operands}"
        if key not in by_name:
            raise SystemExit(f"table A.50 form {key!r} has no PM table 7.2 class")
        name = handler_name(mnemonic, operands)
        if name in handlers:
            raise SystemExit(f"handler name {name} is not unique")
        handlers.add(name)
        section = (
            mov_section(operands)
            if mnemonic.startswith("MOV.") or mnemonic == "MOV"
            else (SECTIONS[mnemonic])
        )
        mask, match = mask_match(code)
        cls = by_name.pop(key)
        rows.append((mnemonic, operands, code, mask, match, name, cls, states, section))
    if by_name:
        raise SystemExit(f"table 7.2 lists forms table A.50 does not: {sorted(by_name)}")
    out = [
        '"""The SH-2 decode table: every form of PM appendix A, its handler and pipeline class.',
        "",
        "Generated by ``scripts/gen_decode.py`` from tests/pm_a50.py (PM table A.50, pp. 276-283)",
        "and PM table 7.2 (pp. 177-183); do not edit by hand. tests/test_decode.py regenerates",
        "it and checks every one of the 65,536 encodings decodes to at most one form.",
        "",
        "Each row: mnemonic and operands as PM prints them, the code pattern (``n``/``m``",
        "register fields, ``d`` displacement, ``i`` immediate), its mask and match, the",
        "handler, the pipeline class (src/sh2_python/pipeline.py), the execution states as",
        "table A.50 prints them, and the PM section that defines the instruction.",
        "Encodings no row matches are undefined: the general illegal instruction exception,",
        "or the slot illegal exception in a delay slot (HM 4.5.4, table 4.9).",
        '"""',
        "",
        "from typing import NamedTuple",
        "",
        "",
        "class Form(NamedTuple):",
        "    mnemonic: str",
        "    operands: str",
        "    code: str",
        "    mask: int",
        "    match: int",
        "    handler: str",
        "    pipeline: str",
        "    states: str",
        "    section: str",
        "",
        "",
        "FORMS: tuple[Form, ...] = (",
    ]
    for mnemonic, operands, code, mask, match, name, cls, states, section in rows:
        out.append(
            f"    Form({mnemonic!r}, {operands!r}, {code!r}, 0x{mask:04X}, 0x{match:04X},"
            f" {name!r}, {cls!r}, {states!r}, {section!r}),"
        )
    out += [
        ")",
        "",
        "",
        "def decode_index() -> list[Form | None]:",
        '    """The form of each of the 65,536 encodings, or ``None`` when undefined."""',
        "    index: list[Form | None] = [None] * 0x10000",
        "    for form in FORMS:",
        "        for opcode in range(0x10000):",
        "            if opcode & form.mask == form.match:",
        "                if index[opcode] is not None:",
        '                    raise ValueError(f"{opcode:04X} matches two forms")',
        "                index[opcode] = form",
        "    return index",
        "",
        "",
        '__all__ = ["FORMS", "Form", "decode_index"]',
        "",
    ]
    return "\n".join(out)


def formatted(text: str) -> str:
    """The text as ``ruff format`` writes it (the repository's pinned formatter)."""
    ruff = shutil.which("ruff", path=str(Path(sys.executable).parent)) or shutil.which("ruff")
    if ruff is None:
        raise SystemExit("ruff is needed to format the table: pip install ruff==0.16.8")
    return subprocess.run(
        [ruff, "format", "--stdin-filename", str(TARGET), "-"],
        input=text, capture_output=True, text=True, check=True, cwd=ROOT,
    ).stdout  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the file is stale")
    args = parser.parse_args()
    text = formatted(generate())
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print(f"{TARGET} is stale: run scripts/gen_decode.py")
            return 1
        return 0
    TARGET.write_text(text, encoding="utf-8")
    print(f"wrote {TARGET} ({len(A50)} forms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
