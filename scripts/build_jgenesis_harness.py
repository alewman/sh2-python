#!/usr/bin/env python3
"""Build the rung-1 referee harness around jgenesis' ``sh2-emu`` crate.

jgenesis (``reference/src/jgenesis``, fetched by scripts/fetch_references.py at the
pinned commit) is a Cargo workspace whose other members are not fetched. This script
assembles a small workspace in ``reference/build/jgenesis-sh2``:

- copies of ``cpu/sh2-emu``, ``common/jgenesis-common`` and ``common/jgenesis-proc-macros``;
- jgenesis' own ``Cargo.lock`` and its root manifest with the member list cut to those
  three crates and ours (dependency versions and lints unchanged);
- ``validation/jgenesis_harness`` (ours) as the ``harness`` member;

applies one patch to the copy of ``sh2-emu`` -- a ``registers_mut()`` accessor beside
the crate's own ``registers()`` (and ``registers`` made a public module so its type
can be named), which is how the harness sets a case's CPU state; no instruction, bus
or timing code is touched -- and runs ``cargo build --release``. jgenesis'
``backend/s32x-core`` (which embeds Sega's boot ROMs) is never fetched or built.

jgenesis at the pinned commit uses two library features that are unstable in every
Rust release found here, 1.93.1 (the system's) and 1.99.1 (rustup's stable on
2026-10-23): ``cfg_select`` in ``jgenesis-common``'s time helpers and
``float_algebraic`` in its audio resampler, neither reached by the SH-2 harness. The
build enables them for the system ``cargo`` with ``RUSTC_BOOTSTRAP=1`` and
``-Zcrate-attr=feature(...)`` in ``RUSTFLAGS``, set for this one command only.

    python scripts/build_jgenesis_harness.py          # build if missing
    python scripts/build_jgenesis_harness.py --force  # rebuild

The binary is ``reference/build/jgenesis-sh2/target/release/sh2-jgenesis-harness``.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reference" / "src" / "jgenesis"
BUILD = ROOT / "reference" / "build" / "jgenesis-sh2"
BINARY = BUILD / "target" / "release" / "sh2-jgenesis-harness"
FEATURES = ("cfg_select", "float_algebraic")
CRATES = ("cpu/sh2-emu", "common/jgenesis-common", "common/jgenesis-proc-macros")

PATCH_ANCHOR = "    pub fn registers(&self) -> &Sh2Registers {"
PATCH = """    /// Added by sh2-python's scripts/build_jgenesis_harness.py: set a test case's state.
    pub fn registers_mut(&mut self) -> &mut Sh2Registers {
        &mut self.registers
    }

"""


def assemble() -> None:
    if BUILD.exists():
        # Keep target/ (the compiled dependencies); replace the sources.
        for name in ("cpu", "common", "harness"):
            if (BUILD / name).exists():
                shutil.rmtree(BUILD / name)
    BUILD.mkdir(parents=True, exist_ok=True)
    for crate in CRATES:
        shutil.copytree(SOURCE / crate, BUILD / crate)
    shutil.copytree(
        ROOT / "validation" / "jgenesis_harness",
        BUILD / "harness",
        ignore=shutil.ignore_patterns("target"),
    )
    manifest = (SOURCE / "Cargo.toml").read_text(encoding="utf-8")
    members = '[workspace]\nmembers = ["cpu/sh2-emu", "common/*", "harness"]\nresolver = "3"\n'
    manifest = re.sub(r"\[workspace\]\n.*?resolver = \"3\"\n", members, manifest, flags=re.S)
    (BUILD / "Cargo.toml").write_text(manifest, encoding="utf-8")
    shutil.copyfile(SOURCE / "Cargo.lock", BUILD / "Cargo.lock")
    lib = BUILD / "cpu" / "sh2-emu" / "src" / "lib.rs"
    text = lib.read_text(encoding="utf-8")
    if PATCH_ANCHOR not in text:
        raise SystemExit("sh2-emu's registers() accessor moved: update the patch anchor")
    text = text.replace(PATCH_ANCHOR, PATCH + PATCH_ANCHOR, 1)
    text = text.replace("mod registers;", "pub mod registers;", 1)
    lib.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--force", action="store_true", help="rebuild even if present")
    args = parser.parse_args()
    if BINARY.exists() and not args.force:
        print(f"present: {BINARY}")
        return 0
    if not (SOURCE / "cpu" / "sh2-emu").exists():
        print("reference/src/jgenesis is missing: run scripts/fetch_references.py")
        return 1
    flags = " ".join(f"-Zcrate-attr=feature({name})" for name in FEATURES)
    env = dict(os.environ, RUSTC_BOOTSTRAP="1", RUSTFLAGS=flags)
    assemble()
    subprocess.run(
        ["nice", "cargo", "build", "--release", "-p", "sh2-jgenesis-harness"],
        cwd=BUILD,
        env=env,
        check=True,
    )
    print(f"built: {BINARY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
