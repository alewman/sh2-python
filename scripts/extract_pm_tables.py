#!/usr/bin/env python3
"""Extract PM appendix A table A.50 (the SH-2 instruction set by code) into tests/pm_a50.py.

PM (`reference/docs/h12p0.pdf`, Hitachi America 1996-09-03) prints every SH-1/SH-2
instruction in table A.50 with its 16-bit code pattern (``0011nnnnmmmm1100``), its
operation, its execution states and its T bit. This script reads the PDF's text layer
(``pdftotext -layout`` from poppler-utils) and turns that table into one record per
form. The decode table (``src/sh2_python/_decode.py``) is generated from the result
by ``scripts/gen_decode.py``; the manual is the judge for encodings (docs/validation.md).

    python scripts/extract_pm_tables.py            # JSON on stdout
    python scripts/extract_pm_tables.py --python > tests/pm_a50.py

The text layer wraps long cells onto a second line (``CMP/STR`` / ``Rm,Rn``,
``DMULU.L`` / ``Rm,Rn*2``) and keeps footnote marks (``*2``) and en dashes; the
parser joins the operand fragments and normalises the marks. Nothing is guessed: a
row the parser cannot read stops the script with its line, and the fix goes into
``FIXES`` below with the page it was read from.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "reference" / "docs" / "h12p0.pdf"

CODE = re.compile(r"\b([01nmdi]{16})\b")
# Footnote marks the PDF leaves on mnemonics and operands: "*2", "* 2", "*1", "*".
FOOTNOTE = re.compile(r"\s*\*\s*\d?")

# Rows whose text-layer reading needs a correction, checked on the rendered page.
# Key: the code pattern; value: (mnemonic, operands).
FIXES: dict[str, tuple[str, str]] = {
    # PM p. 281 prints "SHLL16 R n" / "SHLR16 R n" with a space inside "Rn".
    "0100nnnn00101000": ("SHLL16", "Rn"),
    "0100nnnn00101001": ("SHLR16", "Rn"),
}


# State cells the text layer splits across two lines, read on the rendered page.
WRAPPED_STATES: dict[str, str] = {
    # PM p. 277 prints MUL.L's states as "2" over "(to 4)*1".
    "0000nnnnmmmm0111": "2to4",
}


def table_text() -> list[str]:
    """The lines of table A.50, from its title to table A.51."""
    text = subprocess.run(
        ["pdftotext", "-layout", str(PDF), "-"], check=True, capture_output=True, text=True
    ).stdout
    lines = text.splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if line.strip() == "Table A.50 Instruction Set by Instruction Code"
    )
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("A.4 "))
    return lines[start:end]


def normalise(text: str) -> str:
    text = text.replace("\u2013", "-").replace("\u2212", "-")
    return FOOTNOTE.sub("", text).strip()


STATE = re.compile(r"\d+(?:\s*to\s*\d+|\s*/\s*\(?\s*\d+(?:\s*to\s*\d+)?)?")


def parse(lines: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    t_column = 0
    for index, line in enumerate(lines):
        if line.lstrip().startswith("Instruction") and "State" in line:
            # Each page repeats the header; its "T Bit" column ends the state cell.
            t_column = line.index("T Bit")
            continue
        match = CODE.search(line)
        if match is None:
            continue
        code = match.group(1)
        head = normalise(line[: match.start()])
        parts = head.split(None, 1)
        mnemonic = parts[0]
        operands = parts[1].replace(" ", "") if len(parts) > 1 else ""
        # A wrapped operand ("CMP/STR" then "       Rm,Rn" on the next line) sits in
        # the instruction column of the following line, before any code.
        if not operands and index + 1 < len(lines):
            follow = lines[index + 1]
            if not CODE.search(follow):
                fragment = normalise(follow[: match.start()])
                if fragment and re.fullmatch(r"[A-Za-z0-9@#(),.+\-]+", fragment):
                    operands = fragment
        if code in FIXES:
            mnemonic, operands = FIXES[code]
        # The state cell is the last count before the header's "T Bit" column; the
        # operation text can run into it ("Rn x Rm -> 1 to 3*1", "disp x 4 + Rn) 1").
        window = normalise(line[match.end() : t_column])
        counts = list(STATE.finditer(window))
        if not counts:
            raise SystemExit(f"table A.50: no state count on line: {line!r}")
        state = counts[-1].group(0).replace(" ", "")
        # "3/ (2" continues as "to 4)" on the next line (MAC.L): keep the minimum,
        # the documented count without contention, and say so in the range.
        if state.endswith("(2"):
            state = state[: -len("(2")] + "(2to4)" if "MAC.L" in line else state + ")"
        state = WRAPPED_STATES.get(code, state)
        rows.append({"mnemonic": mnemonic, "operands": operands, "code": code, "states": state})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--python", action="store_true", help="write tests/pm_a50.py's text")
    args = parser.parse_args()
    rows = parse(table_text())
    if not args.python:
        json.dump(rows, sys.stdout, indent=1)
        print()
        return 0
    print('"""PM appendix A table A.50, the SH-1/SH-2 instruction set by code, as data.')
    print()
    print("Generated by ``scripts/extract_pm_tables.py --python`` from PM (Hitachi America,")
    print("1996-09-03, ``reference/docs/h12p0.pdf``) pages 276-283; do not edit by hand.")
    print("``states`` is the table's cell as printed (footnote marks removed): ``3/1`` is")
    print("taken/not taken, ``1to3`` a range set by contention (PM 7.7.2).")
    print('"""')
    print()
    print("#: (mnemonic, operands, code pattern, states)")
    print("A50: tuple[tuple[str, str, str, str], ...] = (")
    for row in rows:
        print(
            f"    ({row['mnemonic']!r}, {row['operands']!r}, {row['code']!r}, {row['states']!r}),"
        )
    print(")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
