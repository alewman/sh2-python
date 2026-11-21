# SPDX-License-Identifier: MIT
"""Closed-box DIVU probe: the same edge cases through sh2_python (any tree) or jgenesis.

    python validation/divu/probe.py py src     # sh2_python found under src/
    python validation/divu/probe.py jg \
        reference/build/jgenesis-sh2/target/release/sh2-jgenesis-harness
    python validation/divu/probe.py cases      # just list the cases

Written for the 2026-11-19 provenance rewrite (docs/provenance.md, U12/U13): it ran the
wave-1 build (``wave1-build.txt``, from a ``git archive`` of fed11ac) and jgenesis
d41e2d9 (``jgenesis-d41e2d9.txt``) as closed boxes; ``current.txt`` is this tree.

Each case: (name, kind, dividend_h, dividend_l, divisor, ovfie). kind 32 writes DVDNT,
kind 64 writes DVDNTH then DVDNTL. Output: name DVDNTH DVDNTL DVCR DVDNT [states].
Case names are labels only: some "normal" 64-bit cases overflow. jgenesis runs one
case per process (its harness panics on H'80000000 / -1, reported as PANIC).
"""

import subprocess
import sys

M = 0xFFFFFFFF
CASES = [
    ("32 normal 100/7", 32, 0, 100, 7, 0),
    ("32 normal -100/7", 32, 0, -100 & M, 7, 0),
    ("32 by0 +5", 32, 0, 5, 0, 0),
    ("32 by0 +5 ie", 32, 0, 5, 0, 1),
    ("32 by0 -5", 32, 0, -5 & M, 0, 0),
    ("32 by0 -5 ie", 32, 0, -5 & M, 0, 1),
    ("32 by0 0", 32, 0, 0, 0, 0),
    ("32 by0 0x7FFFFFFF", 32, 0, 0x7FFFFFFF, 0, 0),
    ("32 by0 0x80000000", 32, 0, 0x80000000, 0, 0),
    ("32 by0 0x12345678", 32, 0, 0x12345678, 0, 0),
    ("32 by0 0x12345678 ie", 32, 0, 0x12345678, 0, 1),
    ("32 min/-1", 32, 0, 0x80000000, M, 0),
    ("32 min/-1 ie", 32, 0, 0x80000000, M, 1),
    ("64 normal 1e12/7", 64, (10**12) >> 32, (10**12) & M, 7, 0),
    ("64 normal -1e12/7", 64, ((-(10**12)) >> 32) & M, (-(10**12)) & M, 7, 0),
    ("64 ovf 2^60/2", 64, 0x10000000, 0, 2, 0),
    ("64 ovf 2^60/2 ie", 64, 0x10000000, 0, 2, 1),
    ("64 ovf -2^60/2", 64, 0xF0000000, 0, 2, 0),
    ("64 ovf -2^60/2 ie", 64, 0xF0000000, 0, 2, 1),
    ("64 ovf 2^60/-2", 64, 0x10000000, 0, (-2) & M, 0),
    ("64 ovf 2^32/1", 64, 1, 0, 1, 0),
    ("64 ovf 2^31/1", 64, 0, 0x80000000, 1, 0),
    ("64 ovf 0x123456789ABC/3", 64, 0x1234, 0x56789ABC, 3, 0),
    ("64 ovf 0x123456789ABC/3 ie", 64, 0x1234, 0x56789ABC, 3, 1),
    (
        "64 ovf -0x123456789ABC/-3",
        64,
        ((-0x123456789ABC) >> 32) & M,
        (-0x123456789ABC) & M,
        (-3) & M,
        0,
    ),
    ("64 exact min -2^31/1", 64, M, 0x80000000, 1, 0),
    ("64 exact min 2^31/-1", 64, 0, 0x80000000, M, 0),
    ("64 exact min 2^32/-2", 64, 1, 0, (-2) & M, 0),
    ("64 near min (-2^31-1)/1", 64, M, 0x7FFFFFFF, 1, 0),
    (
        "64 neg/neg max+rem (-(2^31-1)*2-1)/-2",
        64,
        ((-(0x7FFFFFFF * 2 + 1)) >> 32) & M,
        (-(0x7FFFFFFF * 2 + 1)) & M,
        (-2) & M,
        0,
    ),
    ("64 neg/neg (-2^32+1)/-2", 64, M, 1, (-2) & M, 0),
    ("64 by0 0x1234_56789ABC", 64, 0x1234, 0x56789ABC, 0, 0),
    ("64 by0 0x1234_56789ABC ie", 64, 0x1234, 0x56789ABC, 0, 1),
    ("64 by0 -1", 64, M, M, 0, 0),
    ("64 by0 0", 64, 0, 0, 0, 0),
]


def run_py(src):
    sys.path.insert(0, src)
    from sh2_python import SH7604

    def read(address, width, kind):
        return 0

    def write(address, width, value, kind):
        pass

    for name, kind, h, l, d, ie in CASES:
        chip = SH7604(read, write)
        chip._write(0xFFFFFF08, 32, 2 if ie else 0)
        chip._write(0xFFFFFF00, 32, d)
        if kind == 64:
            chip._write(0xFFFFFF10, 32, h)
            start = chip.clock
            chip._write(0xFFFFFF14, 32, l)
        else:
            start = chip.clock
            chip._write(0xFFFFFF04, 32, l)
        th = chip._read(0xFFFFFF10, 32)
        states = chip.clock - start
        tl = chip._read(0xFFFFFF14, 32)
        cr = chip._read(0xFFFFFF08, 32)
        dt = chip._read(0xFFFFFF04, 32)
        print(f"{name:40s} H={th:08X} L={tl:08X} CR={cr:X} DVDNT={dt:08X} states={states}")


def run_jg(binary):
    base = 0x0600F000
    lines = []
    for i, (_name, kind, h, l, d, ie) in enumerate(CASES):
        words = [0x1192, 0x2122]
        if kind == 64:
            words += [0x1134, 0x1145]
        else:
            words += [0x1141]
        words += [0x0009, 0x5514, 0x5615, 0x5712, 0x5811, 0x0009]
        landing = base + 2 * (len(words) - 1)
        regs = [base, 0xF0, 0, 0, 0, 0, 0]
        gpr = [0] * 16
        gpr[1], gpr[2], gpr[3], gpr[4], gpr[9] = 0xFFFFFF00, d, h, l, 2 if ie else 0
        fields = ["C", str(i), "1"] + [f"{v:X}" for v in regs + gpr]
        fields += ["W", ",".join(f"{base + 2 * k:X}:{w:X}" for k, w in enumerate(words))]
        fields += ["L", f"{landing:X}"]
        lines.append(" ".join(fields))
    for (name, *_), case in zip(CASES, lines, strict=True):
        run = subprocess.run([binary], input=case + "\n", capture_output=True, text=True)
        line = run.stdout.strip() if run.returncode == 0 else "R x PANIC " + " 0" * 23
        f = line.split()
        status = f[2]
        gpr = [int(x, 16) for x in f[10:26]]
        print(
            f"{name:40s} H={gpr[5]:08X} L={gpr[6]:08X} CR={gpr[7]:X} DVDNT={gpr[8]:08X} [{status}]"
        )


if __name__ == "__main__":
    if sys.argv[1] == "py":
        run_py(sys.argv[2])
    elif sys.argv[1] == "jg":
        run_jg(sys.argv[2])
    else:
        for c in CASES:
            print(c)
