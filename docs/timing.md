# Timing: states, the pipeline, the multiplier, the cache and the bus

The hard part of this core. Every number here says where it came from; where
sources disagree, both are given and [undocumented-behavior.md](undocumented-behavior.md)
holds the contradiction (C*n*). Source names are as in
[start-here.md](start-here.md).

## How to read the numbers

- A **state** is one cycle of the chip's clock φ (HM: "number of states").
  On the 32X φ is 23.01 MHz.
- An instruction's **execution states** are the interval from its EX stage to
  the next instruction's EX stage (PM 7.3, SWM 7.1.4). Every table below gives
  the **minimum**, with one-state memory and no contention; HM chapter 2's
  table and PM appendix A say so in their notes ("The execution states shown in
  the table are minimums").
- The core must reproduce **the pipeline**, not the table: the table's
  minimum plus the contention rules (below) is what the manuals mean, and is
  what the only hardware measurements (DN; 32xspd) show.

## The tables

HM chapter 2's instruction table, PM appendix A, PM95 appendix A and SWM's
summary agree on every SH-2 entry below except `TRAPA` (C1). Instructions not
listed take 1 state.

| Instruction | States | Note |
| --- | --- | --- |
| `BT`, `BF` | 3 taken / 1 not | not delayed; two fetched instructions discarded when taken (PM 7.7.5) |
| `BT/S`, `BF/S` | 2 taken / 1 not | delayed when taken |
| `BRA`, `BRAF`, `BSR`, `BSRF`, `JMP`, `JSR`, `RTS` | 2 | delayed; the slot's ID stalls one slot (PM figure 7.82) |
| `RTE` | 4 | delayed |
| `TRAPA #imm` | 8 | SWM's summary table says 6; its own `TRAPA` page and every other source say 8 (C1) |
| `SLEEP` | 3 | states before the chip sleeps |
| `AND.B`, `OR.B`, `XOR.B`, `TST.B #imm,@(R0,GBR)` | 3 | read-modify-write (`TST.B` reads only) |
| `TAS.B @Rn` | 4 | read cache-through, write (HM 8.4.4) |
| `LDC.L @Rm+,SR/GBR/VBR` | 3 | |
| `STC.L SR/GBR/VBR,@-Rn` | 2 | |
| `MULS.W`, `MULU.W` | 1 to 3 | multiplier busy 2 states after MA |
| `MUL.L`, `DMULS.L`, `DMULU.L` | 2 to 4 | multiplier busy 4 states after MA; next ID stalls one slot |
| `MAC.W @Rm+,@Rn+` | 3 / (2) | two MAs; multiplier busy 2 states after the last; "(2)" when overlapped |
| `MAC.L @Rm+,@Rn+` | 3 / (2 to 4) | two MAs; multiplier busy 4 states after the last |
| Interrupt, address error | see "Exceptions" | 10-stage sequence |
| General / slot illegal | see "Exceptions" | 9-stage sequence |

"1 to 3" and similar: the normal minimum, and in parentheses or as the upper
bound the count when the instruction contends with its neighbours (PM
"Functions Listed by CPU Type" note 3). The ranges are not a free choice: they
are the outcome of the multiplier rules below.

## Pipeline rules (PM section 7, SWM section 7)

### Slots

- Five stages: IF (fetch), ID (decode), EX (execute, address calculation), MA
  (data access; also the multiplier's operand transfer), WB (register write
  from memory). Every instruction has IF, ID, EX; loads and stores have MA;
  loads have WB (PM 7.1).
- One stage per slot per instruction; no two instructions in the same stage
  in the same slot (PM 7.2, figures 7.2, 7.3).
- A slot lasts as long as its longest stage: IF = the fetch's memory cycles,
  ID = EX = WB = 1, MA = the data access's memory cycles (PM 7.2.3).

### IF/MA contention (PM 7.4, SWM 7.2.1)

- An MA and an IF in the same slot split it: MA first (WB right after it),
  then EX, ID and IF of the others. The slot costs MA's cycles **plus** IF's.
- Fetches from the cache or on-chip memory are 32 bits: when the instruction at
  4n is fetched (IF, a cycle on the cache bus), the one at 4n+2 comes with it
  and its fetch is `if`, which takes no bus cycle and always one state. An MA
  meeting an `if` does not split; it costs the MA's cycles.
- After a branch to a 4n+2 address only that instruction is fetched, so the
  next IF falls one instruction later (PM figure 7.7). SWM's table notes add
  "or when the branch destination address of a branch instruction is a 4n + 2
  address" to the list of things that lengthen execution.
- The SH-2 always fetches longwords (PM 7.7.5 note), from cache-through space
  too (HM 7.11.2); on a 16-bit external area a fetch is two bus cycles.

DN's hardware fit agrees: on the CPS3, cached loads cost 1.49–1.50 effective
clocks, and "a one-clock load-use interlock together with contention between
memory access and instruction fetch fits these nine totals within 0.3% ...
The fitted port rule charges a clock when the following instruction is at a 4n
address" (DN section 3) — which is the PM rule: the instruction after a load
at 4n is an IF, at 4n+2 an `if`.

### Load-use (PM 7.5, SWM 7.2.2)

- A load writes its register in WB. If the next instruction uses that register
  (as source **or** destination), or the load wrote SR and the next reads T
  (`ADDC`), the slot holding the load's MA and the user's EX splits: one extra
  slot of MA + IF cycles.
- No split when the next instruction is a load to the same register, or is
  `MAC @Rm+,@Rn+` with Rm the loaded register.
- A load whose result is the next instruction's address register: the next
  MA waits for the load's MA to end (SWM figure 7.10).
- A load followed by a store of the same register does not contend: the data
  is forwarded on the internal bus (SWM figure 7.14).

### The multiplier (PM 7.7.2, SWM 7.2.3)

The multiplier is a separate unit. `mm` below is a state in which it works,
"regardless of the slot":

| Instruction | Stages | `mm` after the last MA | Next instruction's ID |
| --- | --- | --- | --- |
| `MULS.W`, `MULU.W` | IF ID EX MA mm mm | 2 | normal |
| `MAC.W` | IF ID EX MA MA mm mm | 2 | stalls one slot |
| `MUL.L`, `DMULS.L`, `DMULU.L` | IF ID EX MA MA mm mm mm mm | 4 | stalls one slot |
| `MAC.L` | IF ID EX MA MA mm mm mm mm | 4 | stalls one slot |

- A multiplier instruction's MA (the second MA for `MAC.W`/`MAC.L`) that falls
  in the previous multiplier instruction's `mm` is extended until the `mm`
  ends; the extended MA is one slot. Exception: the second MA of a `MAC.W`
  does not contend with a preceding `MULS.W`'s or `MAC.W`'s `mm` (PM figures
  7.24, 7.54).
- `STS MACH/MACL,Rn` and `STS.L MACH/MACL,@-Rn` wait for the `mm` to end
  before their MA; `LDS`/`LDS.L` to MACH/MACL likewise (PM figures 7.19–7.22,
  7.30–7.33, 7.41–7.44, 7.49–7.52, 7.59–7.62, 7.70–7.73: one figure per pair).
- Two or more non-multiplier instructions between two multiplier instructions
  remove the stall (PM figures 7.35, 7.64).
- Multiplier MAs contend with IF like any MA.

What hardware says (all CPS3, DN section 3; ticks are FRT ticks of 8 clocks,
for 2,048 multiplies of 3 × 5 with loop overhead included):

| Instruction / independent instructions after it | 0 | 1 | 3 |
| --- | --- | --- | --- |
| `MUL.L`, `DMULS.L`, result not read | 1,091 | 1,155 | 1,667 |
| `MUL.L`, `DMULS.L`, then `STS MACL` + `ADD` using it | 2,307 | 2,435 | 2,435 |
| `MULS.W`, result not read | 611 | 899 | 1,411 |
| `MULS.W`, then `STS MACL` + `ADD` using it | 1,411 | 1,411 | 1,923 |

That is about 4.3 clocks per back-to-back `MUL.L` (1,091 × 8 / 2,048) and
2.4 per back-to-back `MULS.W`, before loop overhead, consistent with 4 and 2
`mm` states of unit occupancy. DP's fitted model uses "four clocks of unit
occupancy and a result deadline five or six clocks after issue, depending on
fetch alignment" and says those fitted states "are not independently measured
latencies". Tom Forsyth's recollection (32XDK wiki, Bugs-and-quirks) — "one
multiply cost 3 clocks, but if you started a second multiply it only cost 2
clocks more" — is anecdote, not measurement. The build session turns these
into a rung (validation.md rung 4): the core's PM model must reproduce DP's
CPU-resident loop totals once the loop bodies in `probe_build_info.json` are
re-assembled.

### Branches (PM 7.7.5)

- `BT`/`BF` taken: the condition is decided in ID, the target computed in EX;
  the two instructions fetched after the branch are discarded; the target's IF
  starts in the slot after the branch's EX. 3 states. Not taken: 1 state.
- `BT/S`/`BF/S` taken: the slot instruction is executed, the one after it
  fetched and discarded. 2 states. Not taken: 1.
- Unconditional delayed branches: the slot executes (its ID stalls one slot);
  the target's IF starts in the slot after the branch's EX. 2 states.
- Because fetches are longwords, a taken branch "overruns" two instructions
  when the next address is 4n (PM 7.7.5 notes): those fetches are real bus
  cycles on a cache miss or in cache-through space, and real cache fills in
  the cache area.
- jgenesis #493: changing jgenesis from 1 state per branch to 3 (`BT`/`BF`) and
  2 (`BT/S`, `BF/S`, unconditional) taken brought its 32xspd results closer to
  the hardware photograph; the remaining "cached read" gap was the MA/IF rule
  (jsgroth, 2026-01-09: "`bf/s` adds 1 cycle per loop iteration, and ... each
  loop iteration stalls for an additional 2 cycles due to MA/IF conflicts").

### System instructions (PM 7.7.6)

`LDC Rm,SR/GBR/VBR` 1, `LDC.L` 3, `STC.L` 2, `RTE` 4 (`IF ID EX MA MA`,
delayed; the slot's ID stalls 3 slots and the target's IF follows the second
MA: PM figure 7.92), `TRAPA` 8, `SLEEP` 3. Interrupts are
not accepted after `LDC`/`LDC.L`/`STC`/`STC.L`/`LDS`/`LDS.L`/`STS`/`STS.L`
(HM 4.6.2), so the instruction after them is never replaced by an interrupt.

## Exceptions (PM 7.7.7, HM 5.5)

- Interrupt and address error: `IF ID EX EX MA MA EX MA EX EX`, received in
  the ID of the instruction it replaces; an overrun fetch occurs; the handler's
  IF starts in the slot of the final EX. The two MAs are the SR and PC pushes,
  the third the vector read.
- Illegal instruction (general and slot): `IF ID EX EX MA MA MA EX EX`.
- Interrupt response (HM table 5.8), from the request to the handler's first
  fetch: IRL 10 + m1 + m2 + m3 (minimum 13), NMI 7 + m1 + m2 + m3 (minimum 10),
  plus the time to finish the current sequence X = up to 4 + m1 + m2 + m3 + m4
  (another exception sequence being the longest); m1–m4 are the states of the
  SR push, PC push, vector read and handler fetch. "Compare identified
  interrupt priority with SR mask level" takes 2 states for NMI and 5 for IRL
  and on-chip sources (HM table 5.8, figure 5.7: `5` then `3`, m1, m2, `1`, m3,
  `1`).
- Mednafen: "Interrupts are typically delayed by one instruction" (its
  `sh7095.inc` header); an emulator's acceptance point is a detector at best
  (C9).

## The cache (HM chapter 8)

| Event | Cost | Source |
| --- | --- | --- |
| Read hit | 1 state, pipelined (consecutive hits every cycle) | HM 8.4.1, figure 8.3 |
| Read miss | tag check, then 4 longword reads on the internal bus starting at address + 4 and wrapping, the requested longword last; the CPU gets it with the last | HM 8.4.1, figure 8.4 |
| Write (hit or miss) | the external write; a hit also writes the line. The CPU waits only until the write buffer accepts it | HM 8.4.2, 7.11.2 |
| Cache-through read | one extra cycle "to determine the cycle", then the read | HM 7.11.2 |
| Data array access | 1 state, cache bus only (runs in parallel with DMA) | HM 8.4.8 |
| Associative purge | 2 states per line, longword writes | HM 8.4.7 |
| CCR purge (CP = 1) | "completed in 1 cycle, but additional time is required for writing to CCR" | HM 8.4.6 |
| On-chip module register | BSC, DMAC, DIVU, UBC 3; INTC, CCR, SBYCR 4; FRT, WDT, SCI 11 states minimum, plus waiting for a bus master to finish | Sega 32X Technical Bulletin #32 via the 32XDK wiki |

Measured (hardware):

- DP/DN: a line fill on the CPS3 costs 35.73–35.94 CPU clocks per load in
  cache-thrashing loops, matching DP's independently measured 35.97-clock
  work-RAM line fill; the conflict "cliff" is at five lines per set in four-way
  mode and three in two-way mode, "the form expected from LRU replacement". The
  fill cost includes the CPS3's external bus and does not transfer to the 32X.
- 32xspd on a real 32X (photograph): cached read 31,840 KB/s, SDRAM read
  16,304, ROM read 4,704, sparse SDRAM read 6,992, sparse ROM read 1,328,
  sparse SDRAM uncached 7,008, sparse ROM uncached 4,800, SDRAM write 26,608,
  VRAM write 8,752. The cached-read figure is CPU-only (pipeline + cache hits);
  the rest mix in the 32X's SDRAM, cartridge and VDP timing (the board's).
  32XDK wiki: the cached-read loop (four `MOV.L @(n,R1)` loads, then
  `ADD`, `CMP/EQ`, `BF/S`, `ADD` in the slot) "takes 11 cycles per iteration"
  when everything is cached. 31,840 KB/s × 1,024 / 16 bytes per iteration ×
  11 = 22.4 M clocks per second (taking KB as 1,024 bytes), within 3% of
  23.01 MHz: consistent. The 11 is 8 instructions + 1 for the taken `BF/S` + 2
  for MA/IF splits, jsgroth's account in jgenesis #493.

Where sources disagree: the refill order (C2) and the purge's timing (C3).

## The divider (HM 10.3, 10.4)

- 39 states from the write of DVDNT (32 ÷ 32) or DVDNTL (64 ÷ 32); 6 on
  overflow (3 to set flags, 3 of division).
- A DIVU register read or write while it works is "continuously extended until
  the operation ends"; other instructions run in parallel.
- A DIVU read right after a DIVU write is extended one cycle.
- A DIVU register write right after the start-up write may not take effect.
- Mednafen starts the 39 at `MA_until + 1` for 32 ÷ 32 and at `timestamp` for
  64 ÷ 32 (`sh7095.inc` `DIVU_S32_S32`, `DIVU_S64_S32`): a one-state
  asymmetry the manual does not mention, unexplained.

## External bus cycles (HM chapter 7)

The board owns what is outside the pins; the BSC owns the shape of each cycle.

- **Ordinary space**: 2 states without waits (HM 7.4.1). WCR's W bits add
  0, 1 or 2 programmed waits per area, or select BCR1's long wait; any setting
  but "no wait" also samples the external WAIT pin (HM 7.2.3). WCR's IW bits
  insert 0, 1 or 2 idle cycles between a read and a following write (HM 7.9).
- **Burst ROM, SDRAM, DRAM, pseudo-SRAM** (HM 7.5–7.8): burst reads for line
  fills, bank-active mode, CAS latency, refresh. The 32X's SDRAM is driven by
  the SH-2s' BSC in bank-active mode, CAS latency 2 (jgenesis #493, from the
  32X boot ROM's settings). Its measured costs (jgenesis #493, against
  hardware): an 8-word burst read 10–12 cycles depending on the row; writes 1–3.
  Sega's 32X hardware manual (32XDK wiki): SDRAM "8 words are read in 12
  cycles", writes 2, cartridge 6–15, frame buffer reads 5–12 and writes 1–3,
  "a wait is required for all 32X blocks". These are board numbers, but the
  core's BSC produces the burst: a cache-through read of SDRAM still bursts 8
  words (32XDK wiki).
- **Write buffer**: one level (HM 7.11.2). A write completes for the CPU when
  the internal bus takes it; a read waits for any pending write; a dummy read
  of the same address waits for the write to finish. DN measured a
  cache-through long store overlapping two independent `ADD`s at no cost
  (8.46 clocks per group for g = 0 and g = 2) on the CPS3.
- **Arbitration** (HM 7.10): master mode holds the bus and grants it on
  request; slave mode requests it for each external access; partial-share
  master shares only CS2. The 32X's slave SH-2 "will temporarily stall if they
  access the bus simultaneously" (jgenesis `s32x-core` README); Mednafen calls
  its master/slave sharing "very approximate".
- **Refresh**: the BSC's refresh timer (RTCSR/RTCNT/RTCOR) issues refresh
  cycles that occupy the bus (HM 7.2.5–7.2.7). Whether the 32X's settings make
  this visible is a board question (the core produces the cycles).

## DMA (HM chapter 9)

Cycle-steal mode releases the bus after each transfer, burst mode after the
whole block; dual-address transfers read then write (the 16-byte unit uses a
burst read: 32XDK wiki "DMA via DMAC"); the write buffer lets the next read start before the write ends
unless both sides are external (HM 7.11.2). DMA and CPU cycles interleave on
the internal bus; DMA writes never update the cache. Mednafen: "DMA timing is
very rough"; its channel priority "not correct ... for non-burst mode". The
32X feeds DREQ0 from its 68000-to-SH-2 FIFO and DREQ1 from its PWM (32XDK wiki;
jgenesis README).
