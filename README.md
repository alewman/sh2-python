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

**Status (2026-10-23): rungs 0-3 pass; stopped for review before rung 4.**
`src/sh2_python` is the SH7604: the SH-2 CPU (all 142 forms, delay slots,
exceptions, interrupts) with the cache, BSC, INTC, DMAC, DIVU, FRT (FTOA), WDT,
SCI and power-down as modules beside it (the UBC is a register map only), and the
tooling (`debug.py`, `trace.py`, `console.py`, `python -m sh2_python`). Timing is
PM section 7's pipeline: a stage scheduler (`pipeline.py`) with IF/MA splits,
load-use, the multiplier's `mm`, branch and exception overrun fetches, and bus
waits stretching the stage that owns the cycle.
What passed, with numbers, is in [docs/validation.md](docs/validation.md) ("What
ran"): the decode table from PM appendix A against MAME's and jgenesis' decoders;
16,600 generated cases agreed by MAME and jgenesis or ruled from the manuals; 129 M instructions of four 32X games replayed register-exact against MAME 0.285;
and all 80 SH-2 pipeline figures of PM section 7 reproduced state for state.

```python
from sh2_python import SH7604

chip = SH7604(read, write)        # read(address, width, kind), write(address, width, value, kind)
chip.reset()                      # PC and R15 from vectors 0 and 1, through read
states = chip.step()              # one instruction or exception sequence
```

```sh
python -m sh2_python ROM.bin                        # the minimal harness: run, print R0
python -m sh2_python ROM.bin --console              # the line debugger
pytest                                              # rungs 0-2 (fast); -m slow for all of it
```

The documents in [`docs/`](docs/README.md) gather what the build needs, each fact
with its source and tier, and the brief it was built from
([docs/handoff-brief.md](docs/handoff-brief.md)).

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
| `scripts/mame_trace.py` | MAME 0.285 per-instruction trace of either 32X SH-2 with its on-chip I/O accesses, and with `--bus` its external ones ([docs/mame-oracle.md](docs/mame-oracle.md)) |
| `scripts/extract_pm_tables.py`, `scripts/gen_decode.py` | PM table A.50 from the PDF, and the decode table from it |
| `scripts/build_jgenesis_harness.py` | the rung-1 referee harness around jgenesis' `sh2-emu` |
| `validation/rung1/` | the rung-1 corpus: generator, MAME runner, gate, rulings |
| `validation/rung2/replay.py` | the rung-2 replay of a MAME bus trace |
| `benchmarks/harness_speed.py` | instructions per second on the minimal harness |

## Licence

MIT (see `LICENSE`), for what is in this repository. The fetched material keeps
its own terms, recorded per entry in the pin lists.
