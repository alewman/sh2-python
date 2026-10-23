#!/usr/bin/env python3
"""Run rung-1 corpus cases on MAME 0.285's SH-2 interpreter (``-nodrc``), in parallel shards.

    python validation/rung1/run_mame.py cases.txt mame.txt [--shards 4] [--cart CART]

Each shard is one MAME process running ``mame_corpus.lua`` on the 32X driver with a
cartridge from the local romset (any 32X cartridge boots the SH-2s; the cartridge's
own code never runs on the master after the second frame). The ``32x`` romset comes
from Aubrey's MAME collection (docs/mame-oracle.md); nothing is downloaded and no ROM
is copied into the repository: the cartridge is extracted into a scratch directory.
One case takes one emulated frame, about 18 ms of wall time on an idle machine.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAME = "/usr/games/mame"
ROMPATH = "/data/emu/source/myrient.erista.me/files/MAME/ROMs (non-merged)"
CART = "/data/emu/roms/sega32x/BC Racers (USA).zip"


def extract_cart(cart: Path, into: Path) -> Path:
    if zipfile.is_zipfile(cart):
        with zipfile.ZipFile(cart) as archive:
            name = next(n for n in archive.namelist() if n.lower().endswith((".32x", ".bin")))
            target = into / Path(name).name
            target.write_bytes(archive.read(name))
            return target
    return cart


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("cases", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--shards", type=int, default=4)
    parser.add_argument("--cart", type=Path, default=Path(CART))
    parser.add_argument("--mame", default=MAME)
    parser.add_argument("--rompath", default=ROMPATH)
    args = parser.parse_args()
    lines = [line for line in args.cases.read_text().splitlines() if line.startswith("C ")]
    with tempfile.TemporaryDirectory(prefix="sh2-rung1-mame-") as scratch:
        work = Path(scratch)
        image = extract_cart(args.cart, work)
        processes = []
        for shard in range(args.shards):
            home = work / f"shard{shard}"
            home.mkdir()
            (home / "cases.txt").write_text("\n".join(lines[shard :: args.shards]) + "\n")
            env = dict(
                os.environ,
                SH2_CORPUS_IN=str(home / "cases.txt"),
                SH2_CORPUS_OUT=str(home / "results.txt"),
            )
            command = [
                "nice", args.mame, "32x", "-cart", str(image), "-rompath", args.rompath,
                "-homepath", str(home), "-video", "none", "-sound", "none", "-nothrottle",
                "-noreadconfig", "-skip_gameinfo", "-nodrc",
                "-autoboot_script", str(HERE / "mame_corpus.lua"),
            ]  # fmt: skip
            quiet = subprocess.DEVNULL
            processes.append(
                subprocess.Popen(command, cwd=home, env=env, stdout=quiet, stderr=quiet)
            )
        failed = [p.wait() for p in processes]
        results = []
        for shard in range(args.shards):
            path = work / f"shard{shard}" / "results.txt"
            if path.exists():
                results += path.read_text().splitlines()
        results.sort(key=lambda line: int(line.split()[1]))
        args.output.write_text("\n".join(results) + "\n")
    print(f"MAME: {len(results)} of {len(lines)} cases, exit codes {failed}")
    return 0 if len(results) == len(lines) else 1


if __name__ == "__main__":
    sys.exit(main())
