#!/usr/bin/env python3
"""Rung 1's gate: the core against the referees' agreements and the manual's rulings.

    python validation/rung1/gate.py build cases.txt mame.txt jgenesis.txt  # writes expected.txt.gz
    python validation/rung1/gate.py check                                   # runs the core

``build`` sorts every case into:

- **agreed**: MAME and jgenesis both landed (status ``ok``) with equal registers and
  memory. That result is the expectation.
- a **rule** of ``overrides.json`` (``classify`` below): the referees disagree or fail,
  and the manual decides. With ruling ``mame``/``jgenesis`` that referee's result is the
  expectation; with ruling ``manual`` neither referee is right and the core's own
  result is recorded, the rule's tests checking the manual's statement.
- **unclassified**: a disagreement no rule explains. ``build`` refuses to write while any
  exist: a new disagreement needs a ruling first.

``expected.txt.gz`` holds one line per case: ``<id> <class> <result line>``; ``check``
runs every case on the core and requires an exact match. Tier: emulator-derived
(agreed) and manual (ruled); never timing.
"""

from __future__ import annotations

import gzip
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import corpus  # noqa: E402

CASES = HERE / "cases.txt.gz"
EXPECTED = HERE / "expected.txt.gz"
OVERRIDES = HERE / "overrides.json"


def manual_class(case: dict) -> str | None:
    """A case the manual rules whatever the referees do: undefined code, illegal slots.

    Two emulators agreeing does not outrank the manual (docs/validation.md, tiers):
    both run some undefined codes as instructions (H'0909 as a NOP) and both run a
    not-taken BT/BF in a slot, which HM table 4.9 makes an illegal slot.
    """
    first = case["words"].get(corpus.TEST, corpus.NOP)
    slot = case["words"].get(corpus.SLOT, corpus.NOP)
    form = corpus.INDEX[first]
    slot_form = corpus.INDEX[slot]
    if form is None:
        return "undefined-code"
    if form.mnemonic in corpus.DELAYED and (
        slot_form is None or slot_form.mnemonic in corpus.PC_CHANGING
    ):
        # Section 3 of the corpus: a PC-changer or undefined code in the slot. (A
        # not-taken BT/S/BF/S makes no slot; those cases are in section 1, NOP slot.)
        return "slot-illegal"
    return None


def classify(case: dict, mame: dict, jgenesis: dict) -> str | None:
    """The overrides.json rule a disagreement falls under, or ``None``."""
    first = case["words"].get(corpus.TEST, corpus.NOP)
    slot = case["words"].get(corpus.SLOT, corpus.NOP)
    form = corpus.INDEX[first]
    slot_form = corpus.INDEX[slot]
    forced = manual_class(case)
    if forced:
        return forced
    names = {form.mnemonic, slot_form.mnemonic if slot_form else ""}
    if form.mnemonic == "TRAPA":
        return "trapa-saved-pc"
    if "MAC.L" in names and (case["sr"] & 2 or mame["sr"] & 2):
        return "mac-l-saturation"
    if "DIV1" in names:
        divider = form if form.mnemonic == "DIV1" else slot_form
        opcode = first if form.mnemonic == "DIV1" else slot
        if ((opcode >> 8) & 15) == ((opcode >> 4) & 15) and divider.mnemonic == "DIV1":
            return "div1-same-register"
    return None


def build(cases_path: str, mame_path: str, jgenesis_path: str) -> int:
    rules = json.loads(OVERRIDES.read_text())["rules"]
    with open(cases_path) as handle:
        lines = [line.rstrip("\n") for line in handle if line.startswith("C ")]
    mame = corpus.load_results(mame_path)
    jgenesis = corpus.load_results(jgenesis_path)
    counts: Counter[str] = Counter()
    out = []
    unclassified = []
    for line in lines:
        case = corpus.parse_case(line)
        ident = case["id"]
        m, j = mame[ident], jgenesis[ident]
        if m["status"] == "ok" and not corpus.differences(m, j) and not manual_class(case):
            kind = "agreed"
            expected = corpus.format_result(ident, "ok", m, m["memory"])
        else:
            kind = classify(case, m, j)
            if kind is None:
                unclassified.append((ident, corpus.describe(case), corpus.differences(m, j)))
                continue
            ruling = rules[kind]["ruling"]
            if ruling == "mame":
                expected = corpus.format_result(ident, "ok", m, m["memory"])
            elif ruling == "jgenesis":
                expected = corpus.format_result(ident, "ok", j, j["memory"])
            else:
                expected = corpus.run_core(case)
        counts[kind] += 1
        out.append(f"{ident} {kind} {expected}")
    for ident, text, fields in unclassified[:40]:
        print(f"unclassified case {ident}: {text}  ({','.join(fields)})")
    if unclassified:
        print(f"{len(unclassified)} unclassified disagreements: rule them in overrides.json")
        return 1
    with gzip.open(CASES, "wt") as handle:
        handle.write("\n".join(lines) + "\n")
    with gzip.open(EXPECTED, "wt") as handle:
        handle.write("\n".join(out) + "\n")
    for kind, count in counts.most_common():
        print(f"{count:6d}  {kind}")
    print(f"{len(out)} cases")
    return 0


def check(step: int = 1) -> tuple[Counter, list[str]]:
    """Run the core on every ``step``-th case; return per-class counts and the failures."""
    with gzip.open(CASES, "rt") as handle:
        lines = [line for line in handle if line.startswith("C ")]
    cases = [corpus.parse_case(line) for line in lines[::step]]
    with gzip.open(EXPECTED, "rt") as handle:
        expected = {}
        for line in handle:
            ident, kind, result = line.rstrip("\n").split(" ", 2)
            expected[int(ident)] = (kind, result)
    counts: Counter[str] = Counter()
    failures = []
    for case in cases:
        kind, result = expected[case["id"]]
        got = corpus.run_core(case)
        if got == result:
            counts[kind] += 1
        else:
            fields = corpus.differences(corpus.parse_result(result), corpus.parse_result(got))
            failures.append(
                f"case {case['id']} ({kind}) {corpus.describe(case)}: {','.join(fields)}"
            )
    return counts, failures


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "build":
        return build(*argv[2:5])
    if len(argv) >= 2 and argv[1] == "check":
        counts, failures = check()
        for kind, count in counts.most_common():
            print(f"{count:6d}  {kind}  match")
        for failure in failures[:40]:
            print(failure)
        print(f"{sum(counts.values())} match, {len(failures)} differ")
        return 1 if failures else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
