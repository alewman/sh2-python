#!/usr/bin/env python3
"""Extract what rung 4 needs from DP's companion data into validation/rung4/dn_p2.json.

DP's companion (``reference/data/where_the_frame_goes_board_companion``, Zenodo record
23435894, CC-BY-4.0; J.R. Dunkley, board research XeD) holds P2's 185 timed CPU loops:
``board/p2/probe_build_info.json`` gives each loop's body, prologue and epilogue as
assembler text, its trip count and CCR, and the address of every routine label and of
every assembled instruction of the probe image (the image itself is not published);
``board/p2/lab_cpu.csv`` gives the eight FRT samples of each loop (even samples after a
requested purge, odd samples warm; one tick = 8 CPU clocks); ``lab_values.csv`` the
purge-order values of tests 185 and 186. This script copies the parts the rung-4
harness uses, so the tests run without the reference tree:

    python scripts/extract_dn_rung4.py          # writes validation/rung4/dn_p2.json

For each CPU loop: its text, its routine and loop-label addresses, the routine's end
(the next routine), and which halfwords of the routine the probe's instruction list
marks as data (literal pools, padding). validation/rung4/dn.py rebuilds each routine
from the text at the same address and checks that its instructions fall on exactly
those halfwords.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPANION = ROOT / "reference" / "data" / "where_the_frame_goes_board_companion"
P2 = COMPANION / "board" / "p2"
OUT = ROOT / "validation" / "rung4" / "dn_p2.json"

#: Subroutines the loop bodies call (``mov.l =NAME,r12`` then ``jsr @r12``).
CALLED = ("RTSSTUB", "STRAIGHT1K", "STRAIGHT8K", "P2FETCH128", "P2FETCH1024",
          "P2FETCH4096", "P2FETCH8192")  # fmt: skip


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    info_path = P2 / "probe_build_info.json"
    info = json.loads(info_path.read_text())
    symbols: dict[str, int] = info["symbols"]
    instructions = set(info["instructions"])
    by_address = sorted(symbols.values())

    def next_symbol(address: int) -> int:
        return min(a for a in by_address if a > address)

    def data_halfwords(first: int, end: int) -> list[int]:
        return [a - first for a in range(first, end, 2) if a not in instructions]

    samples = {int(row["id"]): row for row in csv.DictReader((P2 / "lab_cpu.csv").open())}
    loops = []
    for test in info["tests"]:
        if test["kind"] != "cpu":
            continue
        n = test["id"]
        routine = symbols[f"P2CPU{n}"]
        loop = symbols[f"p2loop{n}"]
        end = symbols.get(f"P2CPU{n + 1}", symbols["P2FETCH128"])
        row = samples[n]
        loops.append(
            {
                "id": n,
                "name": test["name"],
                "group": test["group"],
                "n": test["n"],
                "ccr": test["ccr"],
                "units": test["units"],
                "pro": test["pro"],
                "body": test["body"],
                "epi": test["epi"],
                "routine": routine,
                "loop": loop,
                "end": end,
                "data": data_halfwords(routine, end),
                "samples": [int(row[f"s{i}_{'cold' if i % 2 == 0 else 'warm'}"]) for i in range(8)],
                "overflow_mask": int(row["overflow_mask"]),
            }
        )
    called = {}
    for name in CALLED:
        first = symbols[name]
        end = next_symbol(first)
        called[name] = {
            "address": first,
            "instructions": sum(1 for a in range(first, end, 2) if a in instructions),
        }
    values = {}
    for row in csv.DictReader((P2 / "lab_values.csv").open()):
        first = symbols[f"P2VALUE_{row['name']}"]
        end = next_symbol(first)
        values[row["name"]] = {
            "id": int(row["id"]),
            "routine": first,
            "end": end,
            "data": data_halfwords(first, end),
            "values": [row[f"v{i}"] for i in range(4)],
        }
    # Test 187 trapped (DN section 2); its routine's map, for the rebuild.
    first = symbols["P2VALUE_code_alias"]
    end = next_symbol(first)
    values["code_alias"] = {
        "id": 187,
        "routine": first,
        "end": end,
        "data": data_halfwords(first, end),
        "values": [],
    }
    out = {
        "source": (
            "J.R. Dunkley (board research XeD), companion data of 'Cache, Bus, Interrupt "
            "Order and Slowdown on a CPS3', Zenodo record 23435894, CC-BY-4.0: "
            "board/p2/probe_build_info.json, lab_cpu.csv, lab_values.csv"
        ),
        "sha256": {
            name: _sha256(P2 / name)
            for name in ("probe_build_info.json", "lab_cpu.csv", "lab_values.csv")
        },
        "build": info["build"],
        "tick_clocks": 8,
        "called": called,
        "values": values,
        # DN section 2 (board): test 187 trapped at this PC.
        "code_alias_trap_pc": 0x0202AA00,
        "loops": loops,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(f"{OUT.relative_to(ROOT)}: {len(loops)} loops, {len(values)} value tests")


if __name__ == "__main__":
    main()
