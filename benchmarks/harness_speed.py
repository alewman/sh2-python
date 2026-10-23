#!/usr/bin/env python3
"""Instructions per second of the core on the minimal harness (docs/validation.md, "Speed").

    python benchmarks/harness_speed.py [--steps N] [--cache]

The workload is a loop that mixes what real SH-2 code does: longword and byte loads
and stores through a pointer, ALU operations, a MULS.W and an STS, a TST and a
delayed conditional branch (BF/S with work in its slot), over a 256-byte buffer in
area 3. Each run is fresh; the report gives the best of three.
"""

import argparse
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sh2_python.harness import Harness

LOOP = [
    0xD10A,  # MOV.L @(40,PC),R1     buffer
    0xE240,  # MOV   #64,R2          count (longwords)
    0xE000,  # MOV   #0,R0           sum
    # loop:
    0x6316,  # MOV.L @R1+,R3
    0x6434,  # MOV.B @R3+,R4 -> uses R3 as an address: replaced below
    0x303C,  # ADD   R3,R0
    0x234F,  # MULS.W R4,R3
    0x051A,  # STS   MACL,R5
    0x305C,  # ADD   R5,R0
    0x2338,  # TST   R3,R3
    0x4210,  # DT    R2
    0x8FF7,  # BF/S  loop
    0x7001,  # ADD   #1,R0 (slot)
    0xAFFE,  # BRA   $
    0x0009,  # NOP
]


def program() -> bytes:
    words = list(LOOP)
    words[4] = 0x6403  # MOV R0,R4 (keep the loop's memory accesses inside the buffer)
    body = struct.pack(">II", 0x100, 0x06008000) + bytes(0x100 - 8)
    code = b"".join(struct.pack(">H", w) for w in words)
    code += bytes((-len(code)) % 4) + bytes(40 - 4)
    pool = struct.pack(">I", 0x06001000)
    blob = body + code
    # Place the pool where MOV.L @(40,PC) at 0x100 reads: (0x100 + 4) & ~3 + 40.
    image = bytearray(0x200)
    image[: len(blob)] = blob
    image[0x104 + 40 : 0x104 + 44] = pool
    return bytes(image)


def run(steps: int, cache: bool) -> float:
    harness = Harness(program())
    chip = harness.chip
    chip.reset()
    # One-wait-free BSC: area 0 and 3 as 32-bit, no programmed waits (WCR = 0).
    chip._write(0xFFFFFFE8, 32, 0xA55A0000)
    if cache:
        chip._write(0xFFFFFE92, 8, 0x11)
    start = time.perf_counter()
    done = 0
    while done < steps:
        if chip.pc == 0x100 + 2 * 13:  # the BRA $: start again
            chip.pc = 0x100
        chip.step()
        done += 1
    return steps / (time.perf_counter() - start)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--steps", type=int, default=2_000_000)
    parser.add_argument("--cache", action="store_true")
    args = parser.parse_args()
    best = max(run(args.steps, args.cache) for _ in range(3))
    implementation = sys.implementation.name
    print(
        f"{implementation} {sys.version.split()[0]}: {best:,.0f} instructions/s "
        f"({'cache on' if args.cache else 'cache off'}, {args.steps:,} steps)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
