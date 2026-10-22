# Start here: the SH7604 for a pure-Python core

Everything a builder needs about the chip before reading the timing. Sources are
cited by short name; the files are under `reference/` after
`scripts/fetch_references.py`.

| Name | Document | Tier |
| --- | --- | --- |
| HM | SH7604 Hardware Manual, Hitachi ADE-602-085C Rev. 4.0, 2001-09-19 (`reference/docs/sh7604.pdf`) | manufacturer documentation |
| PM | SuperH RISC Engine SH-1/SH-2 Programming Manual, Hitachi America, 1996-09-03 (`reference/docs/h12p0.pdf`) | manufacturer documentation |
| PM95 | SH-1/SH-2 Programming Manual 3rd Edition, ADE-602-063B, 1995, scan (`reference/docs/1995_SH1_SH2_Programming_Manual_3rd_Edition.pdf`) | manufacturer documentation |
| SWM | SH-1/SH-2/SH-DSP Software Manual, Renesas REJ09B0171 Rev. 5.00, 2004 (`reference/docs/renesas-sh1-sh2-shdsp-software-manual.pdf`) | manufacturer documentation |
| DN, DP | J.R. Dunkley's CPS3 note (2026-10-19) and paper (2026-10-17) with their data (`reference/docs/dunkley-*.pdf`, `reference/data/`) | hardware-captured, one board |
| 32XDK | viciious's 32XDK bulletins and wiki: Sega's 32X technical bulletins, developers' hardware measurements (`reference/src/32XDK*`) | manufacturer bulletins; research |
| MAME, Mednafen, Ymir, ares, jgenesis, PicoDrive, Kronos, MiSTer RTL | the emulators in [validation.md](validation.md) | emulator |

PM and PM95 are the same text for every number checked here (timing.md
"The tables"); SWM is the newest and covers the SH-DSP too, so where it says
"SH-1/SH-2/SH-DSP" read the SH-2 column. HM chapter 2 repeats the instruction
table. **No document gives the SH-2's pipeline at the level of a bus trace**;
PM/SWM section 7 is a model for counting states, written for programmers.

## What the SH7604 is

A 32-bit RISC CPU core, **SH-2** (the second SuperH: the SH-1's instruction
set plus delayed conditional branches, `BRAF`/`BSRF`, `DT`, 32×32 multiplies
and `MAC.L`; PM "Functions Listed by CPU Type"), with on-chip modules, in one
package (HM 1.1):

| Module | What | Registers at | Belongs to |
| --- | --- | --- | --- |
| CPU | 16 general registers, SR, GBR, VBR, MACH, MACL, PR, PC; five-stage pipeline; multiplier | — | this core |
| Cache | 4 KB, 4-way, 64 entries × 16-byte lines, write-through, pseudo-LRU; or 2 KB cache + 2 KB RAM | CCR $FFFFFE92 | this core |
| INTC | NMI, user break, IRL1–15 (four pins), on-chip module interrupts, auto or external vectors | $FFFFFE60–69, $FFFFFEE0–E5 | this core |
| UBC | user break controller (two break channels) | $FFFFFF40–78 | this core |
| BSC | bus state controller: four areas (CS0–CS3), ordinary/burst-ROM/SDRAM/DRAM/pseudo-SRAM interfaces, waits, refresh, master/slave arbitration, one-level write buffer | $FFFFFFE0–FB | this core |
| DMAC | two channels, dual/single address, auto or external request (DREQ), cycle-steal or burst | $FFFFFF80–B3, $FFFFFE71–72 | this core |
| DIVU | 64÷32 and 32÷32 signed divider, 39 cycles, overflow interrupt | $FFFFFF00–3F | this core |
| FRT | 16-bit free-running timer, two compares, input capture, FTOA/FTOB outputs | $FFFFFE10–19 | this core |
| WDT | watchdog / interval timer, can reset the chip | $FFFFFE80–83 | this core |
| SCI | one serial channel, asynchronous or clocked | $FFFFFE00–05 | this core |
| Power-down | SLEEP, standby, module stop bits (SBYCR) | $FFFFFE91 | this core |

Addresses are from HM appendix B ("List of Registers"). Several have mirrors
and size rules of their own (U16).

Names: "SH7604" is Hitachi's part number (HD6417604). Mednafen calls its model
`SH7095`, the part's pre-release name in early Saturn documents. The CPS3's
Capcom DL-3229 is an encrypting SH-2 variant; DP's board had it replaced by a
stock SH7604-family part (DP section 2).

In the **32X** two SH7604s run at MCLK × 3/7 (23.01 MHz NTSC, 22.80 MHz PAL),
one in master mode and one in slave mode on the same bus (32XDK wiki "SH2 on
the 32X"; jgenesis `backend/s32x-core/README.md`). In the **Saturn** two run at
about 28.6 or 26.8 MHz, depending on the video clock the board selects. The
board decides the clock; the core counts in its own clock, φ.

## Registers

HM 2.1, PM section 2:

- **R0–R15**, 32 bits. R15 is the stack pointer for exceptions. R0 is the
  implicit operand of many forms (`MOV.B @(disp,Rn),R0`, `AND #imm,R0`, the
  `@(R0,Rn)` and `@(R0,GBR)` addressing modes).
- **SR**: T (bit 0), S (bit 1, MAC saturation), I3–I0 (bits 7–4, interrupt
  mask), Q (bit 8) and M (bit 9, for `DIV0S`/`DIV0U`/`DIV1`); the other bits
  read 0 and are written as 0.
- **GBR** (base for `@(disp,GBR)` addressing), **VBR** (vector base).
- **MACH:MACL**, the 64-bit multiply-accumulate register. On the SH-2 MACH is a
  full 32 bits; the SH-1's MACH kept 10 bits and sign-extended (PM "Functions
  Listed by CPU Type": the SH-1 `MAC.W` is 16 × 16 + 42 → 42).
- **PR**, the procedure register (`BSR`, `BSRF`, `JSR` write it; `RTS` reads it).
- **PC**. It reads as the instruction's address + 4 in PC-relative forms
  (`MOV.W/MOV.L @(disp,PC)`, `MOVA`, branch displacements), with the low two
  bits cleared for the longword forms (PM 6.x, `MOV.L @(disp,PC),Rn`, `MOVA`).
  In a delay slot it is the branch target + 2 (U1).

Initial values at power-on or manual reset (HM table 2.1): PC and R15 from the
vector table (vectors 0/1 at power-on, 2/3 at manual reset, read at absolute
addresses: VBR is not applied), SR's I3–I0 = 1111, its reserved bits 0, its
other bits **undefined**; VBR = 0; R0–R14, GBR, MACH, MACL, PR **undefined**.
The core needs a documented choice for "undefined" (handoff brief).

## The instruction set

All instructions are 16 bits; the opcode map has 142 SH-2 mnemonics and forms
in PM appendix A (A.1 by addressing mode with SH-2/SH-1 counts, A.3 by code,
A.4 the code map). Machine-readable sources: MAME's `sh.cpp`/`sh2.cpp`
dispatch, Mednafen's `sh7095_idecodetab.inc` and `sh7095_opdefs.inc`, ares'
`decoder.hpp`, jgenesis' `instructions.rs`, and the MiSTer RTL's
`SH opcodes.xlsx`. Generate the core's decode table from PM appendix A and
check it against two of these.

Undefined encodings (and SH-2-only encodings on an SH-1) take the general
illegal instruction exception, vector 4 (HM 4.5.4, PM 6.1 note 2). The
"illegal slot" exception (vector 6) is taken when the delay slot holds an
undefined encoding **or any instruction that changes PC**: `JMP`, `JSR`, `BRA`,
`BSR`, `RTS`, `RTE`, `BT`, `BF`, `TRAPA`, `BF/S`, `BT/S`, `BSRF`, `BRAF`
(HM table 4.9).

## The pipeline

Five stages, IF, ID, EX, MA, WB; one stage per slot; a slot lasts as long as its
slowest stage (PM 7.1–7.3, SWM 7.1). An instruction's **execution states** are
the interval between its EX and the next instruction's EX. What lengthens
that interval:

1. **IF and MA contention.** Instruction fetch and data access share the bus;
   a slot holding both splits, MA first, and costs MA's cycles plus IF's (PM
   7.4). Fetches from the cache or on-chip memory are 32 bits wide, so the
   instruction at a 4n+2 address arrives with its neighbour and has no bus
   cycle of its own (PM writes it `if`); an MA that meets an `if` does not
   split.
2. **Load-use.** A load's register is written in WB; an instruction that uses
   it in the very next EX waits one slot (PM 7.5), with exceptions for a second
   load to the same register and for `MAC @Rm+,@Rn+`.
3. **The multiplier.** `MULS.W`/`MULU.W` and `MAC.W` keep the multiplier busy
   for 2 cycles after their last MA; `MUL.L`, `DMULS.L`, `DMULU.L` and `MAC.L`
   for 4. A following multiplier instruction's MA, or an `STS`/`LDS` of
   MACH/MACL, waits for that to end; `MAC.W`, `MAC.L` and the double-length
   multiplies also stall the next instruction's ID one slot (PM 7.7.2).
4. **Branches.** Taken branches discard fetched instructions and restart IF;
   delayed branches execute their slot instruction first (PM 7.7.5).
5. **The bus.** Every external MA or IF lasts as long as the BSC and the
   board make it (timing.md "External bus cycles").

[timing.md](timing.md) gives the numbers; where they come from and where they
disagree is the hard part of this core.

## Delay slots

`BRA`, `BSR`, `BRAF`, `BSRF`, `JMP`, `JSR`, `RTS`, `RTE`, `BT/S`, `BF/S` are
delayed: the next instruction executes before the target (PM 6.x). Rules:

- The branch reads its registers before the slot runs: "even if the register in
  which the branch destination address has been loaded is changed by the delay
  slot instruction, the branch will still be made using the value of the
  register prior to the change" (PM 4.1.5).
- PC-relative instructions in the slot see PC = target + 2 (U1).
- Neither interrupts nor address errors are accepted between a delayed branch
  and its slot (HM 4.6.1).
- A PC-changing instruction in the slot is an illegal slot exception, whose
  saved PC is the branch's target (HM 4.5.3), not the slot's address.
- `BT/S`/`BF/S` **not taken**: PM 6.7's note (no interrupts or address errors
  between it and the next instruction; a branch there is an illegal slot) does
  not say whether it holds when the branch is not taken (U2).

## Exceptions and interrupts

Vector table (HM table 4.3): 0/1 power-on PC/SP, 2/3 manual reset PC/SP,
4 general illegal, 6 slot illegal, 9 CPU address error, 10 DMA address error,
11 NMI, 12 user break, 32–63 `TRAPA`, 64–71 IRL auto-vectors (IRL1 → 64,
IRL2–3 → 65, …, IRL14–15 → 71), and on-chip modules at the vector numbers
software writes into their VCR registers. Vector addresses are VBR + 4 × n
except for resets.

Entry: push SR, push PC, fetch the vector, jump (not a delayed branch); the
pipeline is `IF ID EX EX MA MA EX MA EX EX` for an interrupt or address error
and `IF ID EX EX MA MA MA EX EX` for an illegal instruction, each with an
overrun fetch (PM 7.7.7). Interrupt response is 13 states minimum for an IRL
interrupt with one-state memory (HM table 5.8, 10 for NMI).

- **Priorities**: NMI 16 (unmaskable, sets I = 15), user break 15, IRL 1–15
  (the inverted level on IRL3–IRL0), on-chip modules 0–15 from IPRA/IPRB.
  Accepted when the level is above I3–I0; the accepted level is written to
  I3–I0 (HM 4.4).
- **Sampling**: the CPU samples interrupt requests between instructions,
  through a noise canceller on the IRL pins (HM 5.6); interrupts are not
  accepted after a delayed branch or after `LDC`, `LDC.L`, `STC`, `STC.L`,
  `LDS`, `LDS.L`, `STS`, `STS.L` (HM 4.6.2); address errors are accepted after
  those.
- **External vectors**: with ICR.VECMD = 1 the IRL interrupt's vector number
  is read from the bus in a vector-fetch cycle (HM table 4.3 note 2). The
  Saturn's SCU answers these; the 32X uses auto-vectors (32XDK). This is a bus
  cycle of its own kind in the contract.
- **Address errors**: word access to an odd address, longword access to a
  non-multiple of 4, instruction fetch from an odd address, and accesses the
  manual forbids (on-chip module space with the wrong size, among others; HM
  4.3). The exception is taken after the faulting instruction completes (HM
  4.3.2). Mednafen and ares both say their address errors are approximate
  (U15).

## The cache in one page

HM chapter 8. 64 entries × 4 ways × 16 bytes. Address bits 31–29 choose the
space (HM table 8.2):

| A31–A29 | Space | What happens |
| --- | --- | --- |
| 000 | cache area ($00000000–) | cached when CCR.CE = 1 |
| 001 | cache-through ($20000000–) | same memory, never cached |
| 010 | associative purge ($40000000–) | a write invalidates the line holding that address in any way |
| 011 | address array ($60000000–$600003FF) | tags, valid bits and LRU of the way CCR.W1–W0 selects; longword only |
| 110 | data array ($C0000000–$C0000FFF) | the cache RAM itself, by way; all 4 KB usable as RAM with the cache off, 2 KB in two-way mode |
| 111 | I/O ($E0000000–) | on-chip modules at $FFFFFE00–$FFFFFFFF; never cached |

- **Reads** that hit take one cycle, pipelined with the CPU (HM 8.4.1).
- **Misses** fill the whole line with four longword reads on the internal bus,
  ordered so that the longword holding the requested data comes last
  (address + 4, + 8, + 12, then the address itself, wrapping within the line),
  and the CPU gets its data when the last arrives (HM 8.4.1 figure 8.4). Byte
  and word misses and fetches at 4n+2 still fill by longwords (HM 7.11.2).
- **Writes** are write-through: always written to memory; also written into a
  hit line, never allocate (HM 8.4.2).
- **CCR.ID / CCR.OD** stop instruction or data misses from replacing lines;
  the missed data goes straight to the CPU (HM 8.2, 8.4.5).
- **TAS.B** reads cache-through regardless of area, then writes through and
  updates a hit line (HM 8.4.4).
- **Purge**: writing CCR with CP = 1 clears every valid bit and the LRU; the
  manual's own text contradicts itself about the value to write (C7), and DN
  measured the purge waiting behind a pending store (U9).
- **No snooping**: DMA writes do not update the cache (HM 7.11.2, 8.5.2).
  On the 32X and Saturn the other SH-2's writes do not either; software purges.

## The bus as the board sees it

HM 7.11.2 divides the chip into a **cache bus** (CPU, cache), an **internal
bus** (DMAC, BSC) and a **peripheral bus** (the slow modules). Only the BSC's
external cycles reach the board. Consequences the contract has to carry:

- Cache hits, data-array accesses and on-chip module accesses make **no
  external cycle**.
- A cache-through read or on-chip module read costs an extra cycle "to
  determine the cycle" before the internal bus cycle starts (HM 7.11.2).
- **Instruction fetches from cache-through space are always longwords** (HM
  7.11.2); a branch to a 4n+2 address in a 16-bit area therefore reads the
  4n halfword too. PM 7.7.5: "SH-2 always fetches instructions with a long
  word", so a taken `BT`/`BF` overruns two instructions.
- **Read size is not on the pins**: an ordinary-space read is always the full
  device width (32 bits on a 32-bit area, 16 on a 16-bit area); writes assert
  only the strobes of the bytes written (HM 7.4.1). A 32-bit access to a 16-bit
  area is two bus cycles.
- **One-level write buffer**: the BSC lets the CPU continue after a write as
  soon as the internal bus accepts it; a following read waits for it (HM
  7.11.2; DN measured the overlap on hardware).
- **Ordinary cycles** take 2 states plus programmed waits (WCR) plus the WAIT
  pin, with idle cycles between a read and a following write per WCR.IW
  (HM 7.2, 7.4.1, 7.9). SDRAM, DRAM, burst-ROM and pseudo-SRAM interfaces
  have their own timing (HM 7.5–7.8). Which areas use which interface is set by
  BCR1/BCR2/MCR, written by the boot ROM; the 32X's boot ROM sets external waits
  only and puts the SDRAM in bank-active mode with CAS latency 2 (32XDK wiki;
  jgenesis #493).
- **Arbitration**: the SH7604 itself arbitrates between a master-mode and a
  slave-mode chip through BRLS/BGR (HM 7.10); the 32X wires its two SH-2s this
  way. A board running two cores decides who waits.

## What the core does not own

The memory map outside $FFFFFE00–$FFFFFFFF and the cache's address spaces; the
devices; the second SH-2; the external WAIT pin; what drives IRL, NMI, DREQ,
FTCI/FTI and the SCI pins, and what FTOA/FTOB, DACK and TxD drive (the 32X ties
FTOA to IRL0: 32XDK "FRT bumping"); the 68000, VDPs, SCU and everything else
on the boards.
