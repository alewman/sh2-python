# Documents

Read in this order.

| Document | What it holds |
| --- | --- |
| [start-here.md](start-here.md) | The SH7604 and its modules; registers; the instruction set and its machine-readable sources; the pipeline; delay slots; exceptions and interrupts; the cache's address spaces; the bus as the board sees it; what the core does not own |
| [timing.md](timing.md) | The state tables; IF/MA contention, load-use, the multiplier, branches, exceptions; cache, divider, external bus and DMA costs; the hardware measurements that exist and how they compare |
| [undocumented-behavior.md](undocumented-behavior.md) | Items U1–U20 (delay-slot PC, not-taken `BT/S`, the "2.5 cut" interrupt bug, purge ordering, overrun fetches, DIVU overflow, ...), contradictions C1–C10 between sources |
| [validation.md](validation.md) | Every oracle by tier with its pin, what it can decide, what ran here; what could not be found; the rung plan |
| [mame-oracle.md](mame-oracle.md) | The verified MAME 0.285 recipe for tracing either 32X SH-2 with its on-chip accesses, what it showed (the boot ROM's BSC settings, FRT bumping, DIVU read one clock after start), and its traps |
| [handoff-brief.md](handoff-brief.md) | The task for the session that builds the core: decisions, the bus contract, the harness, rungs, tooling, open decisions |

## Findings made while gathering (2026-10-21)

These came from reading sources against each other and running things, not
from any single document:

1. **There is no hardware-captured SH-2 test corpus and no die-derived model.**
   SingleStepTests has an SH-4 corpus (from Reicast) but no SH-2; the Saturn
   test programs the MiSTer core was tuned against (celeriyacon's `sstests`) are
   not public. The best hardware evidence is J.R. Dunkley's CPS3 measurements,
   published on Zenodo on 2026-10-17 and 2026-10-19, and a 2019 photograph of
   the 32xspd benchmark on a real 32X.
2. **The hardware agrees with Hitachi's pipeline model, not with one cycle
   per instruction.** Dunkley's fitted rule for cached loads ("charges a clock
   when the following instruction is at a 4n address") is PM's IF/`if` rule;
   32xspd's cached-read loop at 11 states per 8 instructions is PM's taken
   `BF/S` plus two MA/IF splits. jgenesis, MAME and most emulators charge about
   one cycle per instruction.
3. **A CCR purge on hardware waits behind a pending cache-through store**
   (Dunkley, eight values on a CPS3); every emulator checked purges at once.
4. **Mednafen's SH-2 says of itself "Overall timing is extremely
   approximate"**; its care is in cache contents and the divider. MAME has no
   SH-2 cache at all and gets DIVU overflow wrong.
5. **The manuals contradict themselves in small places**: `TRAPA` 6 or 8
   states (Renesas 2004), CCR's purge bit described as "write 0" and "write 1"
   and bit 2 named OD and OC (HM), `BT`/`BT/S` descriptions swapped (HM
   chapter 2). None of these is hard to rule on.
6. **MAME 0.285 crashes when tracing an SH-2 with its recompiler on**;
   `-nodrc` traces work (mame-oracle.md).
7. **Real 32X code reads the divider one clock after starting it** (BC Racers,
   in MAME's trace), so the DIVU's 39-state stall is visible to games; MAME
   answers at once.
8. **MAME 0.285 shows a black screen for the 32xspd benchmark**, so the one
   hardware photograph of 32X throughput cannot be compared with MAME.
