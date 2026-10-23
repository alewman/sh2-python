#!/usr/bin/env python3
"""Record MAME 0.285's per-instruction traces of a 32X's two SH-2s and their on-chip I/O.

MAME is a detector here, never a judge (docs/mame-oracle.md): it has no SH-2 cache,
and its interpreter charges about one cycle per instruction plus fixed extras. Its
value is register state after every instruction of real 32X code, from a machine
that runs every cartridge deterministically.

MAME evaluates a trace action against the debugger's *visible* CPU (the trap
megadrive-python found), so each SH-2 gets its own run with itself visible; the runs
are the same run for a cartridge without input. Each run's ``error.log`` is kept as
``master.log`` / ``slave.log`` in a fresh directory (default
``tests/mame_traces/32x-<cart stem>``, ignored).

    python scripts/mame_trace.py "/data/emu/roms-retrobat/sega32x/BC Racers (USA).7z"
    python scripts/mame_trace.py game.32x --seconds 2 --only master
    python scripts/mame_trace.py game.zip --only master --bus --frames 120

Each log holds, in execution order:

- ``S pc sr pr gbr vbr mach macl r0-r15 totalcycles``: one line per instruction,
  before it runs (23 hex values, then decimal);
- ``IR|IW address size data totalcycles``: each access by that SH-2 to its own
  on-chip modules ($FFFFFE00-$FFFFFFFF: SCI, FRT, WDT, INTC, DIVU, DMAC, BSC, CCR);
- with ``--bus``, ``R|W address size data``: each access by that SH-2 (or its DMAC) to
  the external space H'00000000-H'07FFFFFF, as MAME masks it: instruction fetches
  (one word each: MAME fetches what it executes), data, exception pushes and vector
  reads, DMA. Rung 2's replay (validation/rung2) needs these values: they are what
  the 32X's other devices answered.

``totalcycles`` counts the SH-2's own clock (MAME: 23.01 MHz, MCLK*3/7 NTSC).

Two traps (docs/mame-oracle.md): the SH-2 DRC crashes MAME 0.285 when a trace is
active, so every run passes ``-nodrc`` (one emulated second of the master SH-2 then
takes about 250 s and writes about 2 GB); and the device tags are
``:sega32x:32x_master_sh2`` and ``:sega32x:32x_slave_sh2`` (a wrong tag sets the
visible CPU to nil and MAME segfaults).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAME = "/usr/games/mame"
ROMPATH = "/data/emu/source/myrient.erista.me/files/MAME/ROMs (non-merged)"
DRIVERS = ("32x", "32xj", "32xe")
CART_SUFFIXES = (".32x", ".bin")
CPUS = {
    "master": ":sega32x:32x_master_sh2",
    "slave": ":sega32x:32x_slave_sh2",
}
FIELDS = (
    "pc,sr,pr,gbr,vbr,mach,macl,r0,r1,r2,r3,r4,r5,r6,r7,r8,r9,r10,r11,r12,r13,r14,r15",
    "totalcycles",
)
# (tag, start, length, access): the on-chip module window.
WATCHES = [("IR", "fffffe00", "200", "r"), ("IW", "fffffe00", "200", "w")]
# With --bus: the external space as MAME's SH-2 masks it (A31-A29 removed).
BUS_WATCHES = [("R", "0", "8000000", "r"), ("W", "0", "8000000", "w")]


def lua_script(cpu: str, bus: bool = False, frames: int = 0) -> str:
    """The autoboot script for one SH-2's run: make it visible, trace it, watch its I/O."""
    hex_count, dec_count = (len(group.split(",")) for group in FIELDS)
    trace_format = " ".join(["S"] + ["%X"] * hex_count + ["%d"] * dec_count)
    lines = [
        "local dbg = manager.machine.debugger",
        f'dbg.visible_cpu = manager.machine.devices["{cpu}"]',
        f"dbg:command('trace S.trace,{cpu},noloop,"
        f'{{logerror "{trace_format}\\\\n",{FIELDS[0]},{FIELDS[1]}}}\')',
    ]
    for tag, start, length, rw in WATCHES:
        lines.append(
            f"dbg:command('wpset {start},{length},{rw},1,"
            f'{{logerror "{tag} %X %X %X %d\\\\n",wpaddr,wpsize,wpdata,totalcycles; g}}\')'
        )
    for tag, start, length, rw in BUS_WATCHES if bus else ():
        lines.append(
            f"dbg:command('wpset {start},{length},{rw},1,"
            f'{{logerror "{tag} %X %X %X\\\\n",wpaddr,wpsize,wpdata; g}}\')'
        )
    lines.append('dbg:command("go")')
    if frames:
        # Stop after a number of frames: -seconds_to_run takes whole seconds only.
        lines += [
            "local frames = 0",
            "stop = emu.add_machine_frame_notifier(function()",
            "  frames = frames + 1",
            f"  if frames >= {frames} then manager.machine:exit() end",
            "end)",
        ]
    return "\n".join(lines) + "\n"


def extract(cart: Path, out: Path) -> Path:
    """The cartridge image itself, from a .32x/.bin, a .zip, or a .7z (needs 7z)."""
    if cart.suffix.lower() == ".7z":
        subprocess.run(
            ["7z", "e", "-y", f"-o{out / 'cart'}", str(cart)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        images = sorted(p for p in (out / "cart").iterdir() if p.suffix.lower() in CART_SUFFIXES)
        return images[0]
    if not zipfile.is_zipfile(cart):
        shutil.copyfile(cart, out / cart.name)
        return out / cart.name
    with zipfile.ZipFile(cart) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(CART_SUFFIXES)]
        member = names[0] if names else archive.namelist()[0]
        image = out / Path(member).name
        image.write_bytes(archive.read(member))
    return image


def run(args: argparse.Namespace, image: Path, out: Path, name: str) -> None:
    script = out / f"{name}.lua"
    script.write_text(lua_script(CPUS[name], args.bus, args.frames), encoding="utf-8")
    (out / "error.log").unlink(missing_ok=True)
    # --frames stops the run from Lua; keep -seconds_to_run as a backstop beyond it.
    seconds = max(args.seconds, args.frames // 50 + 2) if args.frames else args.seconds
    subprocess.run(
        [args.mame, args.driver, "-cart", str(image), "-rompath", args.rompath,
         "-homepath", str(out), "-snapshot_directory", str(out / f"snap-{name}"),
         "-video", "none", "-sound", "none", "-nothrottle", "-noreadconfig",
         "-skip_gameinfo", "-nodrc", "-debug", "-debugger", "none", "-log",
         "-seconds_to_run", str(seconds), "-autoboot_script", str(script)],
        cwd=out, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    (out / "error.log").replace(out / f"{name}.log")
    (out / "S.trace").unlink(missing_ok=True)  # the disassembly; the log has the state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("cart", type=Path, help="cartridge image, or a .zip/.7z holding one")
    parser.add_argument("--driver", choices=DRIVERS, default="32x")
    parser.add_argument("--seconds", type=int, default=1, help="emulated seconds")
    parser.add_argument("--only", choices=tuple(CPUS), help="record one SH-2's run only")
    parser.add_argument("--bus", action="store_true", help="also log external reads/writes")
    parser.add_argument("--frames", type=int, default=0, help="stop after this many frames")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--mame", default=MAME)
    parser.add_argument("--rompath", default=ROMPATH)
    args = parser.parse_args()
    cart = args.cart.resolve()
    out = (args.out or ROOT / "tests" / "mame_traces" / f"{args.driver}-{cart.stem}").resolve()
    if out.exists():
        print(f"{out} exists; remove it or pass --out")
        return 1
    out.mkdir(parents=True)
    image = extract(cart, out)
    for name in CPUS:
        if args.only in (None, name):
            run(args, image, out, name)
    version = subprocess.run(
        [args.mame, "-version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    for name in CPUS:
        log = out / f"{name}.log"
        if log.exists():
            counts: dict[str, int] = {}
            with log.open("rb") as handle:
                for line in handle:
                    kind = line.split(b" ", 1)[0].decode("ascii", "replace")
                    counts[kind] = counts.get(kind, 0) + 1
            summary = ", ".join(f"{k} {v:,}" for k, v in sorted(counts.items()) if len(k) <= 2)
            print(f"MAME {version}: {log} ({summary})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
