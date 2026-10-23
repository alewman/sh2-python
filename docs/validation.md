# Validation: every oracle, its tier, and the plan

## Oracle tiers

The family's rule (z80-python `docs/validation.md`, arm7tdmi-python): rank an
oracle by **where its expected values came from**. The highest tier that
covers a claim decides it (a **judge**); every lower tier only **detects**
disagreements to investigate.

| Tier | Meaning here | SH-2 oracles in it |
| --- | --- | --- |
| **hardware-captured** | values recorded from a real SH7604-family chip | DN/DP's CPS3 measurements; the 32xspd photograph |
| **die-derived** | a model traced from the silicon | **none exists** for the SH-2 |
| **specification / manual** | Hitachi's and Sega's documents | HM, PM, PM95, SWM; Sega 32X technical bulletins |
| **research** | developers' write-ups from hardware experience, not all traceable to a published test | 32XDK wiki, Morita's FAQ, jgenesis #493's hardware comparisons |
| **emulator-derived** | values an emulator produces | MAME, Mednafen, Ymir, ares, jgenesis, PicoDrive, Kronos/Yabause, the MiSTer RTL, the SH-4 SingleStepTests; Ymir's DIVU/MAC vectors (origin not stated) |

Two properties of this core's evidence decide how the rungs are built:

1. **Nothing hardware-captured covers instruction semantics.** There is no
   SH-2 SingleStepTests, no published self-checking SH-2 test ROM verified on
   hardware, no die model. For what each instruction computes, the manuals are
   the judge and the emulators (where they agree) are the volume detector.
2. **What hardware evidence exists is about timing and the cache**, which is
   exactly where the manuals are least specific and the emulators weakest.

## The oracles

### HM — SH7604 Hardware Manual — manual, the judge for the chip

- Pin: `reference/docs/sh7604.pdf`, SHA-256 `262cfff2…`, from antime's Sega
  archive; ADE-602-085C Rev. 4.0, 2001-09-19, 633 pages, text PDF.
- Covers: CPU summary and instruction table (ch. 2), exceptions (4), INTC (5),
  UBC (6), BSC (7, incl. 7.11.2 "Access as Seen from the CPU or DMAC"), cache
  (8), DMAC (9), DIVU (10), FRT (11), WDT (12), SCI (13), power-down (14),
  register list (appendix B).
- Errors found: C6 (branch descriptions swapped), C7 (CP bit "write 0"; OD/OC;
  figure 8.12 typos).
- A markdown transcription is in `reference/src/32XDK/docs/sh7604-hardware-manual.md`
  for grep; cite the PDF.

### PM, PM95, SWM — the programming manuals — manual, the judge for instructions and the pipeline

- PM: `reference/docs/h12p0.pdf` (`03364fae…`), Hitachi America 1996-09-03,
  293 pages, text PDF.
- PM95: `reference/docs/1995_SH1_SH2_Programming_Manual_3rd_Edition.pdf`
  (`9e204ecf…`), ADE-602-063B, OCR'd scan from bitsavers' UK mirror
  (bitsavers.org itself refuses curl).
- SWM: `reference/docs/renesas-sh1-sh2-shdsp-software-manual.pdf`
  (`ef8dc7aa…`), REJ09B0171 Rev. 5.00, 2004, served by Renesas (live URL).
- Checked against each other here: the SH-2 state counts of every instruction
  in timing.md's table agree in all three except `TRAPA` in SWM's summary
  (C1). PM section 7 has one pipeline figure per multiplier pairing (7.14–7.73)
  and per instruction class; SWM section 7 is the same model rewritten for the
  SH-DSP, with a clearer contention table (SWM table 7.1).
- Not found: an SH-2 errata sheet, or any Hitachi document listing silicon
  revisions (U20).

### DN, DP — Dunkley's CPS3 measurements — hardware-captured, one board

- Pins: `reference/docs/dunkley-sh7604-cache-purge-note.pdf` (Zenodo
  10.5281/zenodo.23477988, 2026-10-19, CC-BY-4.0), `reference/docs/dunkley-cps3-cache-bus-paper.pdf`
  (Zenodo record 23435894, 2026-10-17), `reference/data/dunkley-cps3-data-and-code.zip`
  (its companion, unzipped in place).
- What: one CPS3 board whose cart carries "a standard Hitachi SH7604-family
  CPU, not Capcom's custom encrypting CPU" (DP section 2), at 25 MHz, BCR1 =
  0020, BCR2 = 00E8, WCR = AA57; diagnostic builds whose results were filmed
  off the screen and recovered "bit for bit through checksummed,
  parity-protected readouts". `board/p2/lab_cpu.csv` holds 8 FRT samples (1
  tick = 8 clocks) per CPU test, with the loop bodies and addresses in
  `probe_build_info.json`; `lab_values.csv` the purge-order values.
- Decides (judge, CPU-resident behaviour): purge ordering (U9), load-use,
  MA/IF, multiplier issue vs result (timing.md), store overlap with the write
  buffer, LRU-shaped conflict misses. Detects only for anything crossing the
  CPS3's bus (line-fill cost, write latency): those numbers are the CPS3's.
- Caveats, the author's: "not measurements of either console", mechanisms "may
  transfer to related parts", no associative-purge test, the fitted timing
  model's states "are not independently measured latencies". Not replicated.

### 32xspd and its photograph — hardware-captured, aggregate

- Pins: `reference/roms/32xspd.zip` (`dde6b0a7…`, 32xspd.32x 8,092 bytes,
  homebrew, binary only, from jgenesis issue #493), `reference/docs/32xspd-hardware.jpg`
  (`b347705d…`, EXIF 2019-05-26, a CRT photograph of the results on a real 32X).
- Numbers: timing.md "The cache". The "cached read" row is CPU-only (pipeline
  + cache hits) and is consistent with PM's model at 11 states per loop
  iteration (timing.md); the other rows are the 32X's memory timing.
- Ran here: **MAME 0.285 shows a black screen for 32xspd** after 30 and 120
  emulated seconds (a commercial cartridge, BC Racers, snapshots correctly
  with the same command), so MAME cannot be compared with the photograph.
  jgenesis runs it (#493's screenshots).

### Sega 32X technical bulletins and the 32XDK wiki — manual / research

- Pins: `reference/src/32XDK` (bulletins #9, #10, #14, #27; markdown
  transcriptions of the Hitachi and Sega manuals) and `reference/src/32XDK.wiki`.
- TB #27 (double acknowledgement, U6), TB #32 via the wiki (on-chip module
  access cycles, timing.md), Morita's "2.5 cut" description (U5), FRT bumping
  (U17), the 32X SDRAM/ROM/VDP access costs (board), bakemono's 11-cycle
  cached loop. TB #32 itself is linked from the wiki to a Google Drive file and
  was not fetched.

### MAME 0.285 — emulator; detector for register state

- Pin: `mamedev/mame` @ `f379cd060ac3` (tag `mame0285`), sparse
  `/src/devices/cpu/sh/`; the binary is the system's `/usr/games/mame` 0.285.
- No cache; a cycle count, not a pipeline; DIVU overflow wrong (C4) and
  instant; `INT_MIN / -1` undefined in its 32-bit divide (U13); manual-correct
  delay-slot PC (U1).
- Ran here: [mame-oracle.md](mame-oracle.md) (16.1 M master instructions per
  emulated second with `-nodrc`; the recompiler crashes under a trace).
- Use: rungs 1 and 2, registers only.

### Mednafen 1.32.1 — emulator; detector for cache contents and DIVU

- Pin: `mednafen-1.32.1.tar.xz` (`de7eb94a…`, 2024-04-05), `src/ss/sh7095*.inc`.
  Built here by `scripts/build_referees.py` (Saturn module only, 5 min 34 s
  on this machine); it runs Saturn software with a user-supplied BIOS, not
  the 32X.
- Its own header: "Overall timing is extremely approximate", DMA "very rough",
  master/slave sharing "very approximate", "SCI, UBC, and BSC are mostly
  unemulated", address errors approximate (C8, U15). Careful: cache refill
  order, LRU tables, ID/OD, two-way mode, incoherency warnings, DIVU overflow
  partial results (U12).
- Use: rung 5 (cache contents on Saturn homebrew; DIVU results).

### Ymir — emulator; its DIVU and MAC vectors are of unstated origin

- Pin: `StrikerX3/Ymir` @ `8cdf0a530ec1` (2026-10-18), sparse SH-2 sources,
  tests and dev notes.
- `tests/ymir-core-tests/src/hw/sh2/sh2_divu_testdata.inc` (DIVU 32- and
  64-bit results, overflow partials included) and `sh2_macwl_testdata.inc`
  (`MAC.W`/`MAC.L` with S = 0/1). The repository does not say whether they
  were captured on a Saturn or generated; treat as emulator-derived until the
  author says otherwise (open decision 8 could ask).
- `saturn.cpp` 735–756: bus timings "that pass misctest" left disabled
  because "inaccurate SH2 cache, write buffer and BSC emulation" makes them
  too slow.
- Use: rung 5 detector for DIVU and MAC.

### srg320's SH-2 RTL (Saturn and 32X MiSTer) — emulator (FPGA reimplementation)

- Pins: `MiSTer-devel/Saturn_MiSTer` @ `9aa944985a96`, `MiSTer-devel/S32X_MiSTer`
  @ `3d4209c65951`, sparse `/rtl/SH/` (the 32X's `rtl/32X/shbios.mif`, a boot
  ROM, is deliberately not fetched). No licence file in either repository.
- A cycle-level SystemVerilog model (`SH_core.sv`, `SH7604/*.sv`) tuned
  against celeriyacon's non-public Saturn test programs; the reported pass of
  "every test" (segasaturnshiro.com, 2025-08) cannot be checked because the
  tests are private. Not die-traced. The two copies have diverged (CACHE.sv
  20,952 vs 13,705 bytes); DN found the Saturn copy latches CCR without a
  pending-store guard. No Verilog simulator is installed here (oss-cad-suite
  would provide Verilator; not fetched).
- Use: a detector for cycle-level questions the manuals leave open, read as
  source; a simulation referee is possible later.

### ares, jgenesis, PicoDrive, Kronos, Yabause — emulators

- ares @ `9408cb43d494`: SH7604 modules, cache refill in the wrong order with
  a flat 12-cycle charge (C2), address errors partly unemulated (U15).
- jgenesis @ `d41e2d975771` `cpu/sh2-emu`: no pipeline timing by design (C5);
  its issue #493 is the best public record of 32X hardware-vs-emulator
  throughput. A Rust crate with a `BusInterface` trait: the easiest second
  interpreter to drive from a harness for rung 1.
- PicoDrive (irixxxx) @ `1008a98e4949`: an old MAME interpreter plus a DRC,
  `pico/32x/sh2soc.c` peripherals. Speed-oriented.
- Kronos @ `d451a55253e2`, Yabause @ `7e38821dbac2` (2018): Saturn SH-2
  interpreters and `sh2cache.c`.
- Use: tie-breakers for rung 1 disagreements; nothing here judges.

### SingleStepTests sh4 — emulator-derived, wrong CPU (optional)

- Pin: `SingleStepTests/sh4` @ `48975cb1a956` (optional, ~350 MB,
  `--optional`). Generated by Reicast's SH-4 interpreter; one memory access
  per case; MACH/MACL instructions excluded; no timing. Usable only for
  encodings the SH-2 shares, as a weak detector of ALU and addressing
  semantics.

## What could not be found

- **Any hardware-captured SH-2 instruction test corpus or self-checking
  hardware-verified SH-2 test ROM.** Searched: SingleStepTests' organisation
  (sh4 only), GitHub repository searches for SH-2/32X/Saturn test ROMs (only
  StrikerX3/saturn-tests, which tests SCU DMA), the MiSTer and Saturn
  community reports (celeriyacon's `sstests` and the "misctest" Ymir mentions
  are private).
- **A die-derived SH-2 model.** No decap or netlist is known. (J-core's J2 is
  a clean-room SH-2-compatible HDL written from manuals; not pinned.)
- **An SH7604 errata or revision list**; the "2.5 cut" is known only from
  Morita's FAQ (U5, U20).
- **Sega 32X TB #32** as a file (only the wiki's table).
- **An SH toolchain** on this machine (needed to write our own test ROMs:
  open decision 8).

## The plan

| Rung | What | Oracle (tier) | Passes when |
| --- | --- | --- | --- |
| 0 | Generated decode table (142 SH-2 forms), pipeline classes, readability test; unit tests for U1, U3, U7, U12–U14 and every manual-settled item | PM appendix A, HM (manual) | every encoding maps to one handler and class; every unit test passes |
| 1 | Instruction semantics: a generated corpus of random states per opcode, run on MAME (`-nodrc`) and a jgenesis `sh2-emu` harness; agreements are the gate, disagreements ruled from the manuals and listed in an overrides file | MAME, jgenesis (emulator) | every agreed case matches; every override has a manual citation |
| 2 | Real code: MAME traces of 32X cartridges replayed on the minimal harness, registers compared after every instruction | MAME (emulator) | exact until a difference the docs attribute to MAME (cache, DIVU) |
| 3 | The pipeline: every PM section 7 figure as a scheduler test (stages per slot, splits, `mm`, ID stalls, branch overruns, exception sequences) | PM, SWM (manual) | all figures reproduced state for state |
| 4 | Hardware numbers: DP/DN CPU-resident loops re-assembled from `probe_build_info.json` and run on the harness with a CPS3-like fixed-latency bus; the purge-order values; the 32xspd cached-read loop at 11 states | DN/DP, 32xspd (hardware-captured) | DN's eight purge values exactly; loop totals within the spread of DN's warm samples; 11 states per 32xspd iteration |
| 5 | Modules: DIVU and MAC against HM and Ymir's vectors; cache contents against Mednafen on Saturn homebrew; FRT/WDT/SCI/DMAC/INTC/UBC register behaviour per HM | HM (manual); Ymir, Mednafen (emulator) | HM's statements as tests; detector disagreements ruled and listed |
| 6 | Through the board: sega32x-python runs 32X software; two-CPU arbitration, FRT bumping, TB #27, 32xspd's other rows, games | sega32x-python's oracles | its milestone; core faults come back as issues/PRs here |

Rungs 1 and 2 are detectors only; a pass there is "agrees with MAME and
jgenesis", stated that way. Rung 4 is the only hardware-captured gate, and it
covers timing and the cache, not instruction results.

## What ran, rungs 0-2 (2026-10-23)

### Rung 0 — passed

- `tests/pm_a50.py` is PM table A.50 read from the PDF by
  `scripts/extract_pm_tables.py` (two cells fixed by hand from the rendered page:
  the `SHLL16`/`SHLR16` operand spacing and `MUL.L`'s wrapped "2 (to 4)").
  `scripts/gen_decode.py` makes `src/sh2_python/_decode.py` from it: 142 forms,
  each with handler, PM table 7.2 class and PM section; `tests/test_decode.py`
  regenerates it and checks it. 53,752 encodings are defined, 11,784 undefined,
  none twice.
- Cross-checks against two other decoders over all 65,536 words
  (`tests/test_disasm.py`): jgenesis' disassembler defines exactly the same
  words, with the same mnemonics (it spells `MOV #imm,Rn` as `mov.b`); MAME 0.285's
  `unidasm -arch sh2` text equals ours on every word both define, after six
  spellings (`BTS`, `BFS`, `STS GBR`, `JMP R0`, `JSR R0`, `TAS R0`), and it names
  2,760 words PM leaves undefined (its 0000/0100 groups decode on fewer bits).
- Readability (`tests/test_readability.py`): every handler's docstring starts with
  its form as PM prints it and cites its PM section; one module per family.
- Unit tests for U1, U3, U7 (with the C11 ruling), U12-U14 and the manual-settled
  rules (delay slots, illegal slots, interrupt acceptance, exception frames, the
  cache's LRU, fill order, purges and arrays, BSC keys and port splits, FRT, WDT,
  DIVU) in `tests/test_cpu.py` and `tests/test_modules.py`.

### Rung 1 — passed (emulator-derived detector, manual rulings)

`validation/rung1/`: 16,600 generated cases (seed 20261023; 100 per form, 2,000
delayed branches with a random slot instruction, 200 illegal slots, 300
undefined codes; SLEEP excluded) run on MAME 0.285 `-nodrc` (one case per frame
through Lua, `mame_corpus.lua`) and on jgenesis `sh2-emu` @ d41e2d97
(`validation/jgenesis_harness`). Both landed with equal registers and memory on
15,949 cases; `gate.py` sorts the other 651 into five rulings
(`overrides.json`, each with its HM/PM citation): undefined code 300 and
illegal slots 200 (manual; the emulators run them), TRAPA's saved PC 100
(MAME = HM 4.5.2; jgenesis pushes the address + 4), MAC.L with S = 1 45 (manual,
C11), DIV1 Rn,Rn 6 (MAME = PM's code). The core matches all 16,600:
CPython 3.14 69 s, PyPy 3.11 20 s (`pytest -m slow tests/test_rung1.py`; one
case in 25 runs in the normal suite).

### Rung 2 — passed (emulator detector): 129 M master instructions register-exact

`scripts/mame_trace.py CART --only master --bus --frames 120` (MAME 0.285,
`-nodrc`, cartridges from `/data/emu/roms/sega32x`, the `32x` set from the local
MAME romset), replayed by `validation/rung2/replay.py` on PyPy; registers compared
after every instruction, every byte the core writes compared with MAME's:

| Cartridge | Instructions | Interrupts injected | Attributed to MAME | First difference |
| --- | ---: | ---: | --- | --- |
| BC Racers (USA) | 30,164,032 | 25,200 | 58 `mame-interrupt-after-ldc` (C12) | none |
| Doom (Japan, USA) | 34,555,701 | 80 | 0 | none |
| Knuckles' Chaotix (Japan, USA) | 32,543,515 | 61 | 0 | none |
| Virtua Racing Deluxe (USA) | 31,791,810 | 0 | 0 | none |

The first MAME-attributed event in BC Racers is at instruction 5,292,129 (an
interrupt MAME accepts right after `LDS R1,MACL`, which HM 4.6.2 forbids); the
replay takes it at MAME's boundary and continues, and there is no other
difference in the four runs. What the replay substitutes (the module docstring
says why): MAME's values for time-dependent on-chip registers (none read in these
runs differed from the core's), MAME's DMA writes (Knuckles: 1,387 instructions
during which MAME's DMAC wrote memory), and the cache, held off by default since
MAME has none. With `--cache` and the boot ROM and cartridge preloaded
(`--seed`), see "the cache on" below. The traces themselves are not committed
(about 5 GB each); `tests/test_rung2.py -m slow` replays any found in
`tests/mame_traces`.

### Speed (the minimal harness, `benchmarks/harness_speed.py`)

On this shared, heavily loaded machine (load average ~70 on 32 cores), best of
three: CPython 3.14.4 610,000 instructions/s, PyPy 3.11 (7.3.23) 2,380,000
instructions/s with the cache off and 1,330,000 with it on.
