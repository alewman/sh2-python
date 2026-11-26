#!/usr/bin/env python3
"""Check and build the referees that run SH-2 code: MAME 0.285 and Mednafen 1.32.1.

Run scripts/fetch_references.py first. Nothing here needs sudo or downloads anything.

- **MAME** is the system binary (``/usr/games/mame``). This only checks its version
  and that the ``32x`` romset (the three SH-2/68000 boot ROMs MAME's ``32x`` driver
  needs) is in the local MAME rompath; it never fetches a BIOS. The trace recipe is
  scripts/mame_trace.py (docs/mame-oracle.md).
- **Mednafen** 1.32.1 (Saturn only) is built outside this repository, by emu-tools-gpl2's
  mednafen tool (GPL-2.0-only; since 2026-11-25, decisions page T3; built here in
  ``reference/build/mednafen-1.32.1`` before): this runs
  ``$EMU_TOOLS_GPL2/tools/mednafen/build.py --machine ss`` as a separate program, which fetches
  the pinned tarball and builds ``$EMU_TOOLS_GPL2/tools/mednafen/build/ss/bin/mednafen`` with the
  options this script used (needs a C++ compiler, SDL2 and zlib development files). Without
  ``EMU_TOOLS_GPL2`` it is reported missing. It emulates the Saturn, not the 32X: it runs
  Saturn homebrew such as a port of Dunkley's purge sequence (docs/validation.md), with a
  Saturn BIOS the user supplies.

    python scripts/build_referees.py            # check MAME, build Mednafen if missing
    python scripts/build_referees.py --check    # report only
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAME = "/usr/games/mame"
MAME_VERSION = "0.285"
# The local MAME romset folder: MAME_ROMPATH (unset: the check reports the romset missing).
ROMPATH = Path(os.environ["MAME_ROMPATH"]) if os.environ.get("MAME_ROMPATH") else None
MAME_32X_SET = "32x.zip"
# Mednafen's build and program, at their documented paths under EMU_TOOLS_GPL2 (emu-tools
# docs/contract.md, section 5). The tool builds the Saturn module only, as this script did.
GPL2_ROOT = os.environ.get("EMU_TOOLS_GPL2")
MEDNAFEN_BUILD_SCRIPT = "tools/mednafen/build.py"
MEDNAFEN_PROGRAM = "tools/mednafen/build/ss/bin/mednafen"


def check_mame() -> int:
    if not Path(MAME).exists():
        print(f"MAME: {MAME} not found")
        return 1
    version = subprocess.run(
        [MAME, "-version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    problems = 0
    if not version.startswith(MAME_VERSION):
        print(f"MAME: version {version!r}, the recipe was verified on {MAME_VERSION}")
        problems += 1
    if ROMPATH is None:
        print(f"MAME {version}: MAME_ROMPATH is not set; mame_trace.py needs the 32x romset")
        return problems + 1
    romset = ROMPATH / MAME_32X_SET
    if romset.exists():
        with zipfile.ZipFile(romset) as archive:
            names = ", ".join(sorted(archive.namelist()))
        print(f"MAME {version}: 32x romset present ({names})")
    else:
        print(f"MAME {version}: no {romset}; mame_trace.py cannot run the 32x driver")
        problems += 1
    return problems


def build_mednafen(check_only: bool) -> int:
    if not GPL2_ROOT:
        print("Mednafen: EMU_TOOLS_GPL2 is not set (a checkout of emu-tools-gpl2 builds it)")
        return 1
    program = Path(GPL2_ROOT) / MEDNAFEN_PROGRAM
    if program.exists():
        print(f"Mednafen: built at $EMU_TOOLS_GPL2/{MEDNAFEN_PROGRAM}")
        return 0
    if check_only:
        print("Mednafen: not built")
        return 1
    script = Path(GPL2_ROOT) / MEDNAFEN_BUILD_SCRIPT
    if not script.exists():
        print(f"Mednafen: no $EMU_TOOLS_GPL2/{MEDNAFEN_BUILD_SCRIPT}")
        return 1
    done = subprocess.run([sys.executable, "-I", str(script), "--machine", "ss"], check=False)
    if done.returncode:
        print(f"Mednafen: $EMU_TOOLS_GPL2/{MEDNAFEN_BUILD_SCRIPT} --machine ss failed")
        return 1
    print(f"Mednafen: built $EMU_TOOLS_GPL2/{MEDNAFEN_PROGRAM}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="report only; build nothing")
    args = parser.parse_args()
    if not shutil.which("make") and not args.check:
        print("make not found")
        return 1
    problems = check_mame() + build_mednafen(args.check)
    print("referees ready" if not problems else f"{problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
