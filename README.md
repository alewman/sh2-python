# sh2-python

A readable, pure-Python **Hitachi SH-2** core, as the **SH7604** chip: the CPU
of the Sega 32X (two of them) and the Sega Saturn (two more), and of the ST-V
arcade board. The SH7604 is a whole microcontroller: the SH-2 CPU core plus a
4 KB cache, a two-channel DMA controller, a divider, a free-running timer, a
watchdog, a serial port, a bus state controller and an interrupt controller.
They are part of the chip, so they are part of this core, each a module beside
the CPU (the same call that put the 2A03's sound and DMA into 6502-python).
The boards, [sega32x-python](../sega32x-python) first and Saturn/ST-V later,
own everything outside the package.

**Status (2026-10-21): groundwork only, no core code.** The documents in
[`docs/`](docs/README.md) gather what the build needs, each fact with its
source and tier: the CPU, its five-stage pipeline and delay slots, the
multiplier, the cache and its address spaces, every on-chip module, where the
manuals contradict themselves and where emulators disagree with them, the
oracles with what they can and cannot decide, a verified MAME recipe, and the
brief for the session that builds the core
([docs/handoff-brief.md](docs/handoff-brief.md)).

The core will follow [z80-python](https://github.com/alewman/z80-python)'s
shape: the constructor takes the bus as callables (no subclassing), `step()`
returns clocks, no dependencies, CPython and PyPy, Python 3.11+. Like the GBA's
ARM7TDMI it will make **one callback per external bus cycle**, carrying the
address, the width, whether it is a fetch, a data access, a cache line fill or
a DMA transfer, and the clock it starts on, so that the board owns external
wait states and the arbitration between two SH-2s.

There is **no hardware-captured test corpus for the SH-2** and no die-derived
model. The strongest evidence is Hitachi's manuals, one board's worth of CPS3
measurements published two days before this was written, and a photograph of a
32X benchmark: [docs/validation.md](docs/validation.md) says what each can
decide.

## What is fetched

Nothing third-party is committed. `scripts/fetch_references.py` rebuilds
`reference/` (about 70 MB) from `scripts/pinned_files.json` (10 files pinned by
SHA-256: the SH7604 hardware manual, three editions of the SH-1/SH-2
programming manual, Dunkley's CPS3 measurements and their data, the 32xspd
benchmark and its hardware photograph, Mednafen 1.32.1) and
`scripts/pinned_repos.json` (12 repositories at full commit SHAs, sparse:
MAME 0.285, ares, PicoDrive, jgenesis, Kronos, Yabause, Ymir, the Saturn and
32X MiSTer SH-2 RTL, the 32XDK bulletins and wiki, and the optional SH-4
SingleStepTests corpus).

```sh
python scripts/fetch_references.py              # everything missing (~70 MB)
python scripts/fetch_references.py --optional   # plus the 350 MB SH-4 corpus
python scripts/fetch_references.py --verify     # check what is present
python scripts/build_referees.py                # check MAME 0.285 + 32x romset, build Mednafen
```

They need `git`, `curl`, `make`, a C++ compiler and SDL2/zlib development
files (for Mednafen). No sudo. No BIOS or game ROM is downloaded: MAME's `32x`
romset and the cartridges come from the user's own collection.

## Scripts

| Script | What |
| --- | --- |
| `scripts/fetch_references.py` | the pinned manuals, measurements, test ROM and source trees |
| `scripts/build_referees.py` | checks MAME 0.285 and its `32x` romset; builds Mednafen 1.32.1 (Saturn only) |
| `scripts/mame_trace.py` | MAME 0.285 per-instruction trace of either 32X SH-2 with its on-chip I/O accesses ([docs/mame-oracle.md](docs/mame-oracle.md)) |

## Licence

MIT (see `LICENSE`), for what is in this repository. The fetched material keeps
its own terms, recorded per entry in the pin lists.
