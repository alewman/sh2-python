#!/usr/bin/env python3
"""Check and build the referees that run SH-2 code: MAME 0.285 and Mednafen 1.32.1.

Run scripts/fetch_references.py first. Nothing here needs sudo or downloads anything.

- **MAME** is the system binary (``/usr/games/mame``). This only checks its version
  and that the ``32x`` romset (the three SH-2/68000 boot ROMs MAME's ``32x`` driver
  needs) is in the local MAME rompath; it never fetches a BIOS. The trace recipe is
  scripts/mame_trace.py (docs/mame-oracle.md).
- **Mednafen** is built from the pinned release tarball into
  ``reference/build/mednafen-1.32.1`` (configure + make, about 10 minutes on 16
  threads; needs a C++ compiler, SDL2 and zlib development files). It emulates the
  Saturn, not the 32X: it runs Saturn homebrew such as a port of Dunkley's purge
  sequence (docs/validation.md), with a Saturn BIOS the user supplies.

    python scripts/build_referees.py            # check MAME, build Mednafen if missing
    python scripts/build_referees.py --check    # report only
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAME = "/usr/games/mame"
MAME_VERSION = "0.285"
ROMPATH = Path("/data/emu/source/myrient.erista.me/files/MAME/ROMs (non-merged)")
MAME_32X_SET = "32x.zip"
MEDNAFEN_TARBALL = ROOT / "reference" / "archives" / "mednafen-1.32.1.tar.xz"
MEDNAFEN_BUILD = ROOT / "reference" / "build" / "mednafen-1.32.1"
MEDNAFEN_BINARY = MEDNAFEN_BUILD / "src" / "mednafen"
# The Saturn module only: every other system off, no ALSA/JACK, and no libflac (its
# development files are not installed here and only ripped-CD images need it).
OTHER_SYSTEMS = (
    "apple2", "gb", "gba", "lynx", "md", "nes", "ngp", "pce", "pce-fast", "pcfx",
    "psx", "sasplay", "sms", "snes", "snes-faust", "ssfplay", "vb", "wswan",
)  # fmt: skip
CONFIGURE = [
    "./configure",
    "--disable-alsa",
    "--disable-jack",
    "--without-libflac",
    *(f"--disable-{name}" for name in OTHER_SYSTEMS),
]


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
    if MEDNAFEN_BINARY.exists():
        print(f"Mednafen: built at {MEDNAFEN_BINARY.relative_to(ROOT)}")
        return 0
    if check_only:
        print("Mednafen: not built")
        return 1
    if not MEDNAFEN_TARBALL.exists():
        print("Mednafen: tarball missing; run scripts/fetch_references.py")
        return 1
    MEDNAFEN_BUILD.mkdir(parents=True, exist_ok=True)
    with tarfile.open(MEDNAFEN_TARBALL) as archive:
        members = []
        for info in archive.getmembers():
            parts = Path(info.name).parts
            if len(parts) > 1 and parts[0] == "mednafen":
                info.name = str(Path(*parts[1:]))
                members.append(info)
        archive.extractall(MEDNAFEN_BUILD, members=members, filter="data")
    jobs = str(os.cpu_count() or 4)
    log = MEDNAFEN_BUILD / "build.log"
    with log.open("w", encoding="utf-8") as handle:
        for command in (CONFIGURE, ["make", "-j", jobs]):
            result = subprocess.run(
                command, cwd=MEDNAFEN_BUILD, stdout=handle, stderr=subprocess.STDOUT, check=False
            )
            if result.returncode:
                print(f"Mednafen: {' '.join(command)} failed; see {log.relative_to(ROOT)}")
                return 1
    print(f"Mednafen: built {MEDNAFEN_BINARY.relative_to(ROOT)}")
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
