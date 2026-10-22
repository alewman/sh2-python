# Handoff: build `sh2-python`, the SH7604 core for the 32X and the Saturn

## Context you are inheriting

The family's shape is **chips are cores, boards compose cores**. Each core is
certified on its own against its oracles and pinned by the boards that use it.
This core's first user is `sega32x-python` (`/data/emu/sega32x-python`, its
Phase 0 running in parallel): two SH7604s at MCLK × 3/7 ≈ 23.01 MHz, master
and slave on one bus. Later users are a Saturn board and ST-V. A board that
finds a corner of this core raises it **on the core** (an issue or a PR here;
re-certification is fine), never as a workaround in the board.

**The chip is the core.** The SH7604's on-die modules (cache with its
cache-through, purge, address-array and data-array spaces; DMAC; DIVU; FRT;
WDT; SCI; BSC; INTC; UBC; power-down) belong here, because they are part of
the chip: the precedent is the 2A03's APU and DMA going into 6502-python.
Structure them as modules beside the CPU, each with its own registers and
tests, composed by one chip class.

Models for the code and the tooling:

- `z80-python` (`/data/emu/z80-python`, v0.4.0 on PyPI): the readability
  contract (one instruction family per module, one mnemonic per handler
  docstring, the hardware reason as a comment on the line that encodes it),
  the **callables contract** (the constructor takes the bus callables; no
  subclassing), the tooling bar (`state.py`, `disasm.py`, `debug.py`,
  `trace.py`, `console.py`, `python -m`), `docs/` layout, oracle tiers.
- `arm7tdmi-python` (`/data/emu/arm7tdmi-python`, Phase 0 written 2026-10-21):
  one callback per bus cycle with width and an access bit set, the board owning
  wait states; its `docs/handoff-brief.md` is the template of this one.
- `sm83-python` (`/data/emu/sm83-python`): internal state visible to the board
  where the hardware makes it visible.
- `6502-python` (`/data/emu/6502-python`): on-die peripherals inside a CPU core.
- `m68000-python` (`/data/emu/m68000-python`): `docs/claims.md`, the claim
  boundary; contested rules kept on a gate.

The user's rules that bind this work:

- **Oracle tiers**: hardware-captured > die-derived > specification/manual >
  research > emulator-derived. A low-tier oracle is a **detector**, never a
  **judge**; state the tier of every claim. For the SH-2 there is no die-derived
  model and no hardware-captured corpus ([validation.md](validation.md)).
- **Python floor 3.11** (the newest PyPy supports): `requires-python = ">=3.11"`,
  ruff `target-version = "py311"`, CPython and PyPy both green.
- **Reference cores are correct, not fast.** A `-fast` fork is later work.
- **Modern over compat**: callables, dataclasses, no shims.
- **No dangerous-looking shell one-liners** in scripts or docs (no `rm -rf`,
  `sed -i`, `ln -sfn`); keep CI minimal (PyPy only, main + PRs, cancel in
  progress) if and when a GitHub repository exists. This repository is **local
  git only** until the user decides otherwise.

### Read, in this order, before writing code

1. `README.md`.
2. [start-here.md](start-here.md): the chip, registers, the instruction set,
   the pipeline, delay slots, exceptions, the cache, the bus as the board sees
   it, what the core does not own.
3. [timing.md](timing.md): the state tables, the pipeline rules, the
   multiplier, the cache, the divider, external bus cycles, DMA, with the
   hardware measurements that exist.
4. [undocumented-behavior.md](undocumented-behavior.md): U1–U20, C1–C10.
5. [validation.md](validation.md): every oracle with its tier and pin; what
   ran here; the rungs.
6. [mame-oracle.md](mame-oracle.md): the verified MAME 0.285 trace recipe and
   its traps.
7. `/data/emu/arm7tdmi-python/docs/handoff-brief.md` and
   `/data/emu/z80-python/docs/start-here.md` ("The embedding contract").

Then `python scripts/fetch_references.py` (about 70 MB) and
`python scripts/build_referees.py` (checks MAME, builds Mednafen in about 6
minutes). Read HM chapters 2, 4, 5, 7, 8 and PM section 7 yourself; the docs
summarise them but the pipeline figures are the specification.

## Decisions already made

- **Name `sh2-python`**, import package `sh2_python`.
- **The on-die modules are in this core**, one module each, beside the CPU
  (above).
- **Callables contract** as z80-python 0.4.0: the constructor takes the bus
  callables; hosts never subclass; callables are plain attributes that may be
  replaced.
- **Big-endian only.** The SH7604 has no little-endian mode.
- **Python 3.11 floor; PyPy must work.**
- **Bus boundary shape**: one callback per external bus cycle, with address,
  width (8/16/32), read/write, and whether it is an instruction fetch, a data
  access, a cache line fill after a miss, or a DMA transfer, so that the board
  owns external wait states and arbitration (as the GBA decision for
  arm7tdmi-python). The details below are this brief's proposal; the choices
  in it that the user has not seen are listed as open decisions.

## The bus contract (proposed)

```python
from sh2_python import SH7604

chip = SH7604(read, write, mode="master")    # or "slave": the MD pin setting
#   read(address, width, kind) -> int    one external read cycle
#   write(address, width, value, kind)   one external write cycle
# address: the 29-bit external address (A28-A0) plus the area; the core has
#          already removed the cache-space bits (A31-A29) and decided the cycle
#          is external. On-chip module, cache-hit, data-array and address-array
#          accesses never reach the callbacks.
# width:   8, 16 or 32: the CPU's (or DMAC's) access size, split by the BSC
#          into port-width cycles (a longword on a 16-bit area is two calls,
#          high word first). Open decision 3 asks whether reads should carry
#          the port width instead (HM 7.4.1: read size is not on the pins).
# kind bits:
#   FETCH   = 1    an instruction fetch (always longword-sized at the CPU, HM 7.11.2)
#   FILL    = 2    one longword of a cache line fill after a miss (4 per line,
#                  in HM's wrap order; the first call of the 4 has FIRST = 4 set)
#   FIRST   = 4
#   THROUGH = 8    a cache-through access (A31-A29 = 001) or a read with the
#                  cache off
#   DMA     = 16   a DMAC cycle; channel in bit 5 (DMA1 = 32)
#   VECTOR  = 64   an external vector fetch (ICR.VECMD = 1)
#   REFRESH = 128  a BSC refresh cycle (no data; read callback, value ignored)
# Inside a callback:
#   chip.clock     the state on which this bus cycle starts (int, φ since reset)
#   chip.wait      0 on entry; the board adds WAIT-pin states (arbitration,
#                  slow devices). The core adds them to the BSC's own cycle
#                  length (2 states + WCR waits + idle cycles, or the
#                  SDRAM/burst timing).
states = chip.step()   # one instruction or one exception sequence
```

- **Cache hits make no callback**: they are not bus cycles (HM 7.11.2: cache
  reads use only the cache bus). A board that wants to see them (a debugger,
  a trace) passes `observe=fn`; `fn(address, width, kind)` is called for every
  CPU access, hits included, with `HIT = 256` set on hits. Default `None`, no
  cost. This is how the decided "cache hit/miss" field is carried; open
  decision 2 asks the user to confirm it.
- **The board owns time outside the chip.** The core never knows a memory
  map; it knows each area's interface and port width only from BCR1/BCR2/MCR,
  which software writes. Each callback's `chip.clock` lets the board order the
  two SH-2s' cycles and the 68000's, and decide who waits.
- **Arbitration** (HM 7.10): the core models its own mode (master or slave,
  from the constructor) only as far as the cycle shape needs it; the board
  running two cores resolves contention by adding `chip.wait` to the cycle that
  lost. A board that runs the cores one instruction at a time must run the one
  whose `clock` is behind.
- **Pins in**: `chip.irl` (0–15, the inverted IRL3–IRL0 level), `chip.nmi`
  (level; the core detects the edge per ICR.NMIE), `chip.dreq0/dreq1`,
  `chip.ftci`, `chip.fti`, `chip.rxd` (or a byte-level SCI input, open decision
  10). The board may change them inside any callback; the core samples them
  where HM says (IRL between instructions through the noise canceller, U4).
- **Pins out**: optional callables `on_ftoa(level, clock)`, `on_ftob`,
  `on_dack(channel, clock)`, `on_txd` / byte-level SCI out, `on_wdtovf(clock)`.
  The 32X needs FTOA (it drives IRL0, U17).
- **Writes and the write buffer**: the core makes the write callback when the
  BSC starts the external write; the CPU continues per HM 7.11.2 (U9, timing.md
  "Write buffer").
- **Reset**: `chip.reset(power_on=True)` loads PC/R15 from vectors 0/1 (2/3 for
  a manual reset) through the read callback, with VBR = 0, SR.I = 15, and the
  undefined registers zeroed (U19) unless the constructor's `undefined=` fills
  them. CCR.CE is cleared; cache contents are left as they were (HM 8.5.1).
- **State for the harness**: `chip.state` is a dataclass with R0–R15, SR, GBR,
  VBR, MACH, MACL, PR, PC, the pipeline's in-flight state needed to resume a
  step boundary exactly, and each module's registers; it loads and saves the
  layout the rung-1 corpus uses.
- Keep the contract transcribable: plain integers, no generators or
  coroutines in the hot path, so `sh2-rust` and a `-fast` fork can copy it.

## Your task

Build `src/sh2_python/` to the contract above and pass the rungs in
[validation.md](validation.md) "The plan", in order. In summary:

0. **Tables.** The decode table generated from PM appendix A (142 SH-2
   forms), every entry naming its handler and its pipeline class (PM 7.7's
   categories); a readability test; a per-class stage test from PM section 7's
   figures (stages, `mm` counts, ID stalls) and a unit test per U-item that the
   manuals settle.
1. **Instruction semantics against the referees.** There is no SH-2
   SingleStepTests. Generate a corpus (rung 1 in validation.md): random
   register/memory states per opcode, run on MAME's interpreter (`-nodrc`) and
   on jgenesis' `sh2-emu` through a small harness, keep the cases where they
   agree, list the disagreements by class with a ruling from the manuals. It
   is a detector of register semantics only, never of timing.
2. **Real code, register-exact.** MAME 0.285 traces of 32X cartridges
   (`scripts/mame_trace.py`), replayed in lockstep through a minimal 32X-like
   harness: every instruction's registers equal MAME's until the first
   difference the manuals attribute to MAME (C4's DIVU, the cache: MAME has
   none). Detector.
3. **The pipeline model.** PM section 7's figures as executable tests: every
   figure in 7.4–7.7 (MA/IF, load-use, the 64 multiplier pairs, branches,
   exceptions) reproduced state for state by the core's scheduler.
4. **Hardware numbers.** DP/DN's CPU-resident loops (load-use, MA/IF, `MUL.L`
   / `MULS.W` issue and result, store overlap with a fixed-latency bus) and
   the purge-order values `1111, 1122, 2222, 2222` / `2222 ×4`; the 32xspd
   "cached read" loop at 11 states per iteration. The only hardware-captured
   evidence; judge where it applies.
5. **Peripherals.** DIVU against HM 10 and Ymir's DIVU vectors, MAC against
   Ymir's MAC vectors, FRT/WDT/SCI/DMAC/INTC register behaviour against HM,
   cache contents against Mednafen on Saturn homebrew, with MAME/Mednafen
   disagreements ruled by HM.
6. **Through the board.** sega32x-python's milestone that runs 32X software
   (and later a Saturn board) certifies what only a board can exercise: the
   two-CPU arbitration, the FRT bumping, TB #27's double acknowledge, 32xspd's
   other rows. Its failures that are the core's come back here.

### The minimal harness (`sh2_python.harness`)

A host that is not a 32X, for rungs 1–4 and the CLI: big-endian RAM at
$00000000 and $06000000 (cache area, mirrored at the cache-through alias), a
flat ROM image loaded at $00000000 with PC/SP from its first two longwords, a
fixed-latency bus (1 state per cycle, a constructor option for more), FTOA
logged, IRL driven from a script. Stop on a branch to itself or on `SLEEP`
with interrupts masked. Report R0.

### Tooling at the family bar

`state.py` (above), `disasm.py` (check against MAME's `sh_dasm.cpp` output on
the same words), `debug.py` (`DebugSession` with breakpoints, bus watchpoints
that also see cache hits through `observe`), `trace.py` (JSONL, the family
schema with `kind` and `width`), `console.py`, `py.typed`, and
`python -m sh2_python ROM.bin [--max-states N] [--trace out.jsonl]`.

## Open decisions for Aubrey

Each has a recommendation; nothing is built that depends on them before the
user answers, except where "proceed with" says so.

1. **Chip class name.** `SH7604` (the chip, with `chip.cpu`, `chip.cache`,
   `chip.dmac`, ...) or `SH2` (the CPU name everyone uses). *Recommend:*
   `SH7604`, with `SH2` left free for a future bare-CPU core (SH7032-style
   parts have different modules). Proceed with `SH7604`.
2. **Cache hits on the bus contract.** The decided shape includes "cache
   hit/miss". Hits make no external cycle, so *recommend:* misses appear as
   `FILL` cycles on the callbacks, hits only on the optional `observe` hook
   (zero cost when unused). Alternative: a callback for every CPU access with
   a `HIT` flag (slower, and the board must ignore hits for timing). Proceed
   with the recommendation.
3. **Read width.** HM 7.4.1: reads carry no size on the pins (always the port
   width). *Recommend:* pass the CPU's access size (split to the port width),
   as MAME, Mednafen and jgenesis give their boards, and document the pin
   truth (C10). Alternative: the port width, with the size in `kind`.
4. **How the board reports wait states.** *Recommend:* `chip.wait`, zeroed
   before each callback, set by the board (plain integers, transcribable).
   Alternative: callbacks return the wait count (reads would return a pair).
5. **The "2.5 cut" interrupt bug (U5).** *Recommend:* a constructor option
   `revision="2.5"` that reproduces Morita's description, default off, until a
   hardware test shows which 32X units have it.
6. **Overrun fetches (U11).** *Recommend:* on, always: PM says they happen, and
   the hardware loops cannot be matched without them.
7. **DN as a judge.** Dunkley's CPS3 measurements are hardware-captured but
   one board, one author, two days old, unreplicated, and a CPS3 not a 32X.
   *Recommend:* judge for CPU-resident behaviour (purge ordering, load-use,
   MA/IF, multiplier issue), detector for anything that crosses the CPS3's bus;
   say so in every claim.
8. **Getting a hardware-captured oracle.** None exists for the SH-2.
   *Recommend:* in order of cost: (a) run DN's replication sequence and the
   32xspd ROM on a real 32X or Saturn if the user has a flash cart and a
   camera; (b) write small self-checking 32X test ROMs in sega32x-python
   (needs an SH toolchain: none is installed here); (c) ask celeriyacon whether
   the private Saturn `sstests`/misctest could be shared or run on request.
9. **The rung-1 corpus.** *Recommend:* generate it from MAME (`-nodrc`) and a
   jgenesis harness, keep agreements, rule disagreements from the manuals; its
   tier is emulator-derived and every claim says so. The optional SH-4
   SingleStepTests corpus is a weaker cross-check for shared encodings only.
10. **SCI granularity.** *Recommend:* byte-level frames with their clock
    (enough for the 32X's master/slave link and the Saturn's), bit-level pins
    later if a board needs them.
11. **GitHub.** Like the other cores, local until rung 3 passes, then public
    (cores-public). *Recommend:* yes, same rule.
