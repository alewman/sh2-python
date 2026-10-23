# Undocumented behaviour, open items, and where sources contradict

Items **U1–U22** are behaviours the manuals leave unsaid, ambiguous, or say
only in passing, that a core must still decide. **C1–C20** are places where
sources contradict each other. Each says what is known, from which tier, and
what the build should do; "open" means nothing above emulator tier settles it.
Source names as in [start-here.md](start-here.md).

## U1 — PC-relative addressing in a delay slot

PM 6.x (`MOV.W/MOV.L @(disp,PC)`, `MOVA`) and SWM: "If this instruction is
placed immediately after a delayed branch instruction, the PC must point to an
address specified by (the starting address of the branch destination) + 2."
MAME sets PC to the target before executing the slot (`sh2.cpp`
`execute_run`), so its `MOVLI` reads `((target + 2) & ~3) + disp × 4`: the
manual's rule. **Build**: follow the manual; a rung-0 unit test per form.

## U2 — `BT/S`/`BF/S` not taken

PM 6.7/6.13 say, without distinguishing taken from not taken, that no
interrupt or address error is accepted between the instruction and the next
and that a branch in the next position is an illegal slot. HM table 4.10
lists `BF/S`/`BT/S` among delayed branches. MAME, Mednafen
(`CondRelDelayBranch`) and ares (`BFS`) all treat a not-taken `BF/S` as an
ordinary instruction: the next instruction is not a slot, interrupts may be
taken before it, a branch there is legal. **Open** (emulator tier only).
**Build**: follow the three emulators; record it as unverified.

## U3 — Saved PC of an illegal slot exception

HM 4.5.3: the saved PC is "the jump address of the delayed branch instruction
immediately before the undefined code or the instruction that rewrites the
PC". So `RTE` from the handler resumes at the branch target, not at the slot.
**Build**: follow HM; check MAME and Mednafen do the same before rung 1 relies
on them.

## U4 — Interrupt acceptance and the instructions that block it

HM 4.6: after a delayed branch neither interrupts nor address errors are
accepted; after `LDC`, `LDC.L`, `STC`, `STC.L`, `LDS`, `LDS.L`, `STS`,
`STS.L` interrupts are not accepted but address errors are. HM 5.6: IRL is
sampled "between executing instructions" after a noise canceller ("1011 for 1
clock due to noise" is filtered in figure 5.9). HM's interrupt usage notes
(figures 5.11–5.14) ask for a synchronising read after clearing a source
(the write sits in the write buffer) and then 1 instruction before `RTE`, or 4
before an `LDC` that lowers the mask, for external sources; for on-chip
sources the read alone before `RTE`, and 2 instructions before the `LDC`. How
many clocks the noise canceller needs is not stated. **Build**: sample at the ID of the next
instruction per PM 7.7.7; a noise-canceller latency of 0 until a
measurement exists; record it open.

## U5 — The SH-2 "2.5 cut" interrupt bug

Toshiyasu Morita's "Programming the 32x FAQ" (quoted in the 32XDK wiki,
Bugs-and-quirks): "The early revisions of the 32x use the 2.5 cut of the SH2,
which has a problem with interrupts. Specifically, if a low priority interrupt
occurs, and within a 3-cycle window a higher priority interrupt occurs, then
the 2.5 revision of the SH2 will jump to the low priority interrupt vector and
lose the high priority interrupt. The SR will still be set to the number of
the higher priority interrupt, however." Sega's 32X software works around it
with a unified handler that dispatches on SR, and with "FRT bumping" (FTOA
toggling IRL0 every 16 clocks to re-trigger). Tier: a developer's FAQ, no
measurement and no Hitachi erratum found. **Open decision** (handoff brief): a
silicon-revision option, default off.

## U6 — Double acknowledgement of one 32X interrupt

Sega 32X Technical Bulletin #27 (1994-12-08, `reference/src/32XDK/bulletins/`):
with two interrupt sources, a CMD interrupt can be taken twice (level 8, then
level 9 when FTOA changes the IRL level before SR is masked), "this problem
only occurs on the 32X. It does not occur on the Saturn." This follows from
correct IRL sampling plus the 32X's FTOA→IRL0 wiring; the core needs no
special case, but the board test that shows it is a good rung-6 check.
Manufacturer bulletin tier.

## U7 — MAC saturation details

PM `MAC.W`: with S = 1 the addition saturates to 32 bits in MACL
(H'80000000–H'7FFFFFFF); with S = 0 the full 64-bit sum. PM `MAC.L`: with S = 1
the sum saturates at 48 bits. Whether a `MAC.W` overflow also sets MACH's LSB is
a contradiction inside the manuals (C11): the prose of PM 6.31 and SWM says "if an
overflow occurs, the LSB of the MACH register is set to 1", but the code on the
same pages marks the two lines that do it "For SH-1 CPU (these 2 lines not
needed for SH-2 CPU)". Ymir's `sh2_macwl_testdata.inc` sets the bit: MACH's other
bits keep their value and only bit 0 is set (`0x6E43A906` → `0x6E43A907` in its
first case), and its `MAC.L` saturates to `0x00007FFF_FFFFFFFF` /
`0xFFFF8000_00000000`. Ymir does not say where its vectors came from
(validation.md). MAME and jgenesis leave MACH alone (rung 1 corpus).
**Build**: the code's SH-2 annotation for `MAC.W` (MACH unchanged); PM's prose for
`MAC.L`'s range (C11); Ymir's vectors as a detector at rung 5, where a
disagreement on the MACH bit stays open until hardware shows it.

## U8 — Cache: multiple ways with one tag, two-way mode, ID/OD

- A tag present in two ways at once ("ostensibly an illegal condition",
  Mednafen's header) can be created through the address array. Hardware
  behaviour unknown. **Build**: first match in way order 0–3, recorded as a
  choice.
- Two-way mode compares tags in all four ways, so ways 0 and 1 must be
  invalidated before use (HM 8.4.5); replacement is limited to ways 2 and 3.
- CCR.ID/OD: misses read directly to the CPU without replacing (HM 8.2,
  8.4.5). The MiSTer Saturn core added these bits in 2025 (segasaturnshiro.com
  report); Mednafen implements them through its replacement table.

## U9 — Purge ordering behind a pending store (hardware)

DN section 2, measured on a CPS3: a cache-through store, a CCR purge request,
a cached read, another store, another cached read, four repetitions, gave
`1111, 1122, 2222, 2222`; with a second purge before the second read,
`2222` four times. "An ordering rule fits all eight data values: the CCR
write waits behind the pending store while hits can still use the old line."
Executing code written through the cache-through alias after a purge trapped
on the stale line's contents. Every emulator checked (Mednafen, Ymir,
jgenesis) purges inside the CCR write; srg320's RTL latches CCR without a
pending-store guard (DN section 5). Hardware-captured, one board, one author,
not replicated on a 32X or Saturn. **Build**: model the CCR write as a write
through the BSC's write buffer, so it completes after a pending store, and
let hits proceed meanwhile; rung 4 checks DN's eight values.

**Built at rung 4** (`cache.py` `write8`, `_core.py` `_settle_ccr`): a CCR write made
while the write buffer holds an external write is kept and lands when that write's
cycles end; the CPU goes on and its cache hits use the old lines; a miss waits for the
write and so lands the CCR write before its fill. With P2's routines rebuilt at P2's
addresses (validation/rung4/dn.py) the core gives DN's eight values exactly, and an
immediate purge on the same code gives the emulators' pattern DN predicted (1111 and
1122 on every repetition): tests/test_rung4.py. The values do not depend on the
CPS3-like bus's speed (1 to 4 board waits on work RAM give the same eight), but they
depend on where P2's literals sit: the CCR-address load right after the store hits
only because the load of U filled the same line before the store (DN: "inspect the
emitted instructions and alignment"); the rebuild matches P2's instruction map.
Test 187 (the stale fetch that trapped at H'0202AA00) is **not** reproduced by the
rebuild: there the purge lands before the call's fetch unless a work-RAM word write
takes 6 states or more; P2's routine (24 instructions, five literals) is not
published, and DN calls the trap's explanation an inference. Open.

## U10 — Associative purge space read; the reserved spaces

HM describes only writes to $40000000–. Mednafen treats a **read** there as a
purge too and returns all ones ("apparently returns open bus of some sort"),
and maps region $A0000000 (A31–A29 = 101, reserved in HM table 7.3) the same
way. DN: "Associative purge was never tested." **Open**; follow Mednafen,
recorded as emulator-tier.

## U11 — Overrun fetches are real accesses

PM 7.7.5 and 7.7.7: taken branches and exceptions fetch instructions that are
discarded, and "SH-2 always fetches instructions with a long word". In the
cache area those fetches can miss and fill lines; in cache-through space they
are bus cycles the board sees. MAME fetches only what executes
(`sh2.cpp` `execute_run`); the other emulators were not checked. **Build**: make them,
per PM; it changes cache contents and bus timing, and the 32xspd and DN loops
were expected to need them (rung 4 found one DN loop that does, below). **Built at rung 3** (decided by Aubrey: always
on): the scheduler places every fetch PM's figures draw, discarded ones included —
taken `BT`/`BF` (two, figure 7.78), taken `BT/S`/`BF/S` (one after the slot, figure
7.80; C13), TRAPA and illegal instructions (two, figures 7.93, 7.97; U21), interrupts
and address errors (the replaced instruction's and the next, figures 7.95, 7.96).
Each is a `FETCH` cycle on the bus (or a line fill in the cache area) when it opens a
new longword; tests/test_pipeline.py `test_overrun_fetches_reach_the_bus`,
`test_overrun_fetch_fills_a_cache_line`. The pipeline also fetches two instructions
ahead of execution, as figure 7.1 draws: a store to the next two instructions'
addresses does not change what runs (the hardware's behaviour, if PM's pipeline is
right; MAME executes the new code).

Rung 4 (hardware, a CPS3): every P2 loop closes with a taken `BF`. With the two fetches
it discards removed, one CPU-resident loop leaves the board's warm samples
(p1_02_ramrd_l, eight cached loads a pass, where a discarded fetch meets the last
load's MA); the other 66 are unchanged. Modest support for the overrun fetches, one
loop deep; 32xspd's `BF/S` loop does not show them (C13).

## U12 — DIVU overflow results

HM 10.3.3: on overflow OVF is set; with OVFIE = 1 the registers hold "the
result after 6 cycles of operation"; with OVFIE = 0 DVDNTH holds that partial
result and DVDNTL H'7FFFFFFF (positive quotient) or H'80000000 (negative).
Mednafen implements exactly that, with an explicit three-step partial
division (`DIVU64_Partial`). MAME sets both registers to H'7FFFFFFF whatever
the sign, never raises the interrupt ("unemulated DIVU OVF interrupt
enable"), and finishes instantly. Ymir's `sh2_divu_testdata.inc` carries
partial values in DVDNTH for overflowing cases. **Build**: HM + Mednafen's
partial algorithm; Ymir's vectors as a detector (C4).

## U13 — DIVU 32 ÷ 32 edge cases

- Writing DVDNT also sets DVDNTL and sign-extends into DVDNTH (HM 10.2.2).
- H'80000000 ÷ H'FFFFFFFF: HM's overflow definition ("when, in division
  between two negative numbers, the quotient is the maximum value and a
  remainder (negative number) is generated") does not clearly cover it.
  Mednafen returns quotient H'80000000, remainder 0, no overflow. MAME
  computes `INT_MIN / -1` in C++, which is undefined (on x86 a trap). **Open**;
  follow Mednafen.
- Division by zero in 32 ÷ 32: Mednafen sets DVDNTH = DVDNT >> 29 (arithmetic)
  and saturates DVDNTL by sign (or shifts the dividend left 3 with OVFIE = 1):
  a model of the 6-cycle partial result. Follow it.

## U14 — DIVU access rules

All DIVU registers except DVCR and VCRDIV must be accessed as longwords; word
accesses read or write "undefined values" (HM 10.4.1). MAME maps DIVU "64-bit
plus mirrors" (`sh7604.cpp`). Mednafen keeps shadow copies
(`DVDNTH_Shadow`, `DVDNTL_Shadow`) beside the registers. **Build**: longword semantics, mirrors as MAME
maps them, undefined word access returns the longword's matching half and
records it as a choice.

## U15 — Address errors

HM 4.3: misaligned word/longword data accesses, odd instruction fetches and
some forbidden-space accesses raise the CPU address error (vector 9); DMA
address errors use vector 10. Mednafen: "Misaligned memory accesses (that
cause address errors) aren't emulated correctly (for future reference, there's
a difference between how cache versus external bus is accessed for misaligned
addresses, and perhaps a difference between 32-bit and 16-bit spaces as
defined by the BSC too ...)" and "Address errors occur one instruction too
soon". ares: "unemulated: access of Purge, Address, or IO areas by PC-relative
addressing; unemulated: address errors caused by stacking of address error
exception handling". **Build**: HM's list; what the misaligned access itself
does on the bus before the exception is open.

## U16 — On-chip module register quirks

- WDT: WTCSR and WTCNT are written with a word write to H'FFFFFE80 whose upper
  byte is H'A5 (WTCSR) or H'5A (WTCNT); RSTCSR through H'FFFFFE82 (H'A5 with
  H'00 to clear WOVF); they read at FE80, FE81 and FE83 (HM 12.2.4, appendix B
  note).
- Byte-only modules at $FFFFFE00–$FFFFFEFF, 16/32-bit at $FFFFFF00–
  (HM appendix B). Mednafen: "TODO: Emulate jankiness of 16-bit reads from
  mirror addresses"; "WDT open bus of a sort".
- FRT's 16-bit registers are read and written a byte at a time through an
  8-bit TEMP register, upper byte first (HM 11.3).
- **Build**: HM; unknowns recorded as choices.

## U17 — FTOA/FTOB and the 32X's IRL0

The 32X wires FTOA to IRL0 on both SH-2s; its boot code runs the FRT so FTOA
toggles every 16 clocks ("FRT bumping", 32XDK wiki). The core must expose FTOA
as an output the board can see change, with the clock at which it changed.

## U18 — Power-down

`SLEEP` (3 states, then sleep until an interrupt or reset), standby (SBYCR.SBY
= 1 before `SLEEP`), module stop bits MSTP0–4. Mednafen: "Individual module
clocking disabling functionality of SBYCR isn't handled"; the MiSTer Saturn
core added standby in 2025. **Build**: HM chapter 14.

## U19 — Undefined register values

HM table 2.1: R0–R14, GBR, MACH, MACL, PR and SR's M, Q, S, T are undefined
after reset; the cache contents are not initialised by reset (HM 8.5.1). No
measurement exists. **Build**: zero, and a constructor option to fill them,
so that software depending on them shows up.

## U20 — Which revision is the 32X's / the Saturn's SH-2

HM Rev. 4.0 (2001) is the newest SH7604 manual; it describes no revisions. The
"2.5 cut" (U5) implies at least one earlier silicon revision with a bug. No
document lists revision differences. **Open.**

## U21 — Which fetches the illegal instruction sequences make

PM figure 7.97 draws the next instruction's IF and, in parentheses, the third's, and
says "Whether there is an IF only in the next instruction or in the one after that as
well depends on the instruction that was to be executed." Undefined code has no
"instruction that was to be executed". **Build**: both, as TRAPA (figure 7.93, the
same nine stages); for a slot illegal, the branch target's fetch (already made in the
slot's ID) and the one after it. Choice, manual-silent; it changes one overrun fetch.

## U22 — The write buffer's timing (HM 7.11.2)

HM 7.11.2: "the bus state controller has one level of write buffer"; a write is
reported complete to the CPU "without waiting for the actual writing ... to end" when
the internal bus is free; "During reads, the CPU always has to wait for the end of the
operation"; on-chip module accesses need not wait for a buffered external write. HM
does not say how many states the CPU spends handing a write over, when exactly a
second write is taken, or how a DMA write uses the buffer beyond the dual-address rule.
**Build (rung 4)**: a CPU write's access (its MA) lasts until the buffer is empty plus
one state; the buffered write's cycles start in the state the buffer takes it; a
second write and every external read (cache-through, fill, fetch miss, DMA read) wait
until the cycles end; on-chip accesses never wait (CCR aside, U9); DMA writes are not
buffered (their cycles hold the bus, rung 5). DN's store-overlap loops (hardware, a
CPS3, bus-crossing: detector) have this shape: a long store with 0 or 2 independent
ADDs after it costs the same per group, 8.00 on the core's CPS3-like bus and 8.46 on
the board, and with 8 ADDs 10.00 and 10.14, where the CPU's own instructions take
over (validation.md "What ran, rung 4").

## Contradictions

### C1 — `TRAPA` takes 8 or 6 states

8 in PM, PM95, HM chapter 2 and SWM's own `TRAPA` page (6.1.69); 6 in SWM's
summary table. PM's pipeline figure for `TRAPA` has 9 stages (SWM table 7.1:
"8 cycles, 9 stages"). **Build**: 8.

### C2 — Cache line fill order

HM 8.4.1/figure 8.4: the fill starts at the requested address + 4 and wraps,
the requested longword last. Mednafen (`(A + 4 + i) & 0xC`) and srg320's RTL
(`IBADDR[3:2] + 1`) follow it. ares reads offsets 0, 4, 8, C in order and
charges a flat 12 cycles (`sh7604/cache.cpp`). The order matters only to a
board that sees it (a 32X SDRAM burst; side-effect reads). **Build**: HM.

### C3 — When a CCR purge takes effect

HM 8.4.6: "Cache purges are completed in 1 cycle, but additional time is
required for writing to CCR", and the cache keeps serving fetches while CCR is
written. DN (hardware): the purge waits behind a pending store while hits use
the old line (U9). Emulators: immediate. **Build**: DN.

### C4 — DIVU overflow results

HM and Mednafen vs MAME (U12). **Build**: HM.

### C5 — How long an ordinary instruction takes

PM/SWM: the pipeline model above. jgenesis' `sh2-emu` README: "does not
attempt to track timing. Almost all instructions take 1 cycle plus memory
access delays" (since tuned for branches, jgenesis #493). MAME's interpreter:
one cycle per instruction plus fixed extras (`sh2.cpp` `execute_run`
decrements `icount` once per instruction). Hardware (32xspd's cached-read
loop at 11 cycles per 8 instructions; DN's 1.49–1.50 clocks per cached load)
agrees with the pipeline model, not with one cycle per instruction.
**Build**: the pipeline model.

### C6 — HM chapter 2's branch rows

HM's instruction table prints `BT` with the description "Delayed branch, if
T = 1 ..." and `BT/S` with "If T = 1, disp × 2 + PC → PC; if T = 0, nop",
the two descriptions swapped (the codes and states are right: `BT` 3/1, `BT/S`
2/1). PM and SWM are correct. **Build**: PM.

### C7 — CCR in HM

- HM 8.2 CP: "When 1 is written to CP, all cache entries ... are initialized";
  HM 8.4.6: "Purges of the entire cache area can only be carried out by
  writing 0 to the CP bit". HM figure 8.12's code writes 1 (`OR #H'10,R0`).
  Writing 1 purges.
- Bit 2 is "OD" in HM 8.2 and "OC" in HM appendix B.
- HM figure 8.12 has two typos (`MOV.B #R0,@R1`, `MOV.B R0,R1`).

### C8 — Mednafen's timing reputation vs its own notes

Mednafen's SH-2 is often called the accurate one for cache and timing. Its
header says: "Overall timing is extremely approximate", "DMA timing is very
rough", "Emulation of SH-2 master/slave bus sharing is very approximate",
"SCI, UBC, and BSC are mostly unemulated". What it does carefully is cache
**contents** (refill order, LRU, ID/OD, coherency warnings) and DIVU results.
Use it as a judge of nothing; as a detector for cache contents and DIVU.

### C9 — When an interrupt is taken

HM/PM: an interrupt is received in the ID stage of an instruction and replaces
it (PM 7.7.7; HM figure 5.7). Mednafen: "Interrupts are typically delayed by
one instruction, but when the interrupt is handled, the current interrupt
state is used instead of the previous interrupt state." **Build**: HM/PM;
emulator acceptance points are detectors only.

### C10 — Read width on the external bus

HM 7.4.1: a read's size is not on the pins; it is always the device's full
width. Emulators (MAME, jgenesis, Mednafen) hand their boards the CPU's access
size. The board-facing contract has to pick one (handoff brief, open
decision).

### C11 — `MAC.W` and `MAC.L` with S = 1: the manuals' prose against their code

- `MAC.W` (PM 6.31, SWM): the prose sets MACH's LSB on overflow; the code says
  those lines are SH-1 only (U7). Followed: the code's SH-2 annotation.
- `MAC.L` (PM 6.29): the prose limits the result to H'FFFF8000_00000000 ..
  H'00007FFF_FFFFFFFF using "only the lower 48 bits" of MAC; the code adds MACH's
  low 16 bits unsigned and saturates the negative side to H'00008000_00000000 (the
  sign bits clear, outside the prose's range). MAME transcribes the code; jgenesis
  clamps the full 64-bit sum. Followed: the prose (MAC's low 48 bits sign-extended,
  the sum clamped to 48 bits and stored sign-extended). Rung 1 rules its 45
  disagreeing cases this way (validation/rung1/overrides.json, `mac-l-saturation`).

### C12 — MAME 0.285 accepts interrupts where HM 4.6 says none are accepted

HM 4.6.2 and table 4.10: after `LDC`, `LDC.L`, `STC`, `STC.L`, `LDS`, `LDS.L`,
`STS`, `STS.L` an interrupt is not accepted before the next instruction. MAME's
interpreter checks pending interrupts whenever a line changes or SR is loaded
(`sh2.cpp` `execute_set_input`, `LDCSR`'s `m_test_irq`), with no such rule: in
the rung-2 trace of BC Racers it took 58 interrupts right after an `LDS`/`STS`
class instruction in the first 20.6 M master instructions (the first after
`LDS R1,MACL` at H'0206A4EE, instruction 5,292,129). Its `SLEEP` also loops on
the instruction (`pc -= 2`) instead of stopping the CPU, so a trace shows SLEEP
repeated until the interrupt. **Build**: HM. The rung-2 replay counts these as
MAME's (`mame-interrupt-after-ldc`, `mame-sleep`) and takes MAME's interrupt at
MAME's boundary so the comparison can continue.

### C13 — Does a taken delayed branch fetch the instruction after its slot?

PM figure 7.80 (`BT/S`/`BF/S` taken) draws the instruction after the slot fetched in
the branch's EX slot and discarded; figure 7.82 (`BRA`, `BRAF`, `BSR`, `BSRF`, `JMP`,
`JSR`, `RTS`) draws no fetch there, the target's IF in the next slot. The two are the
same kind of branch once taken. The notes under figures 7.79 and 7.81 speak of
overrun fetches for the conditional branches only. **Build**: each figure as drawn —
a conditional branch fetches on before its condition redirects the stream, an
unconditional one does not. It matters only when the branch's EX slot holds an MA
(the extra fetch splits it) or the fetch is slow or opens a new cache line.
tests/test_pipeline.py figures 7.80, 7.82.

Rung 4 does not decide it. 32xspd's cached-read loop ends in a taken `BF/S`, but its EX
slot holds no MA and the discarded fetch is a one-state cache hit: the loop takes 11
states with the fetch and without it (checked by removing it). No DN loop uses `BT/S`
or `BF/S`. **Open**; a cold or cache-through `BF/S` loop timed on hardware would decide
it.

### C14 — Figures 7.4-7.6 draw a bus fetch for every instruction

PM figures 7.5 and 7.6 give each consecutive instruction its own bus fetch (7.5:
three 2-cycle IFs in a row; 7.6: the IFs of instructions 4 and 5 both split their
slots). On the SH-2 a fetch is a longword, so of two consecutive instructions one is
an `if` with no bus cycle (PM 7.4.2, figure 7.7; figure 7.8 is the SH-2 version of
figure 7.6). Figure 7.4 draws two instructions only; the third one's fetch falls in
the slot of the first one's three-cycle MA and splits it (PM 7.4.1). **Build**: the
SH-2's longword rule. The tests reproduce the figures with the cycle counts they
state and SH-2 fetches: 7.4 with the split (its second instruction's EX one column
later), 7.5 with the third fetch an `if` (6 states from the first EX to the fourth,
not 7), 7.6 with slot E unsplit.

### C15 — Where a one-state stage sits in a longer slot

Most figures draw a one-state stage at the start of a slot that an extended MA makes
longer (`ID EX — — MA` in figures 7.35, 7.38, 7.40, 7.64-7.66, 7.68), as figure 7.4
draws `EX — —` in its three-cycle slot ("the instruction with the longest stage
stalls others", PM 7.2.3). Figure 7.39 draws the third instruction's EX at the end of
that slot (`ID — — EX MA`), twice. Where EX sits changes how the states are split
between two instructions (PM 7.3), not their sum. **Build**: the slot's start
(figure 7.4, PM 7.2.3).

### C16 — Figure 7.26's slot arrows

Figure 7.26 (MAC.W after MAC.W with IF contention) draws the second split's long
arrow one column to the right of the stages it splits: the second MAC.W's second MA
is under a short arrow and the long one covers the next instruction's EX and MA,
which cannot share a slot (PM 7.2.1). Its stage columns follow PM 7.4.1 (MA, then
EX/ID/IF together). **Build**: the columns.

### C17 — Figures 7.40, 7.67 and 7.68 with impossible rows

- Figure 7.40, first case (MULS.W right after MAC.L): MAC.L has five `mm` and MULS.W
  an MA followed by an extended MA. MAC.L has four (PM 7.7.2, figure 7.34) and MULS.W
  one MA (figure 7.53); the same figure's other three cases draw both correctly.
- Figure 7.67 (DMULS.L after DMULS.L with IF contention): the second DMULS.L's row
  is `if — EX — ID MA M—A`, EX before ID.
- Figure 7.68, second case: MULS.W has two MAs, its second in the same column as the
  next instruction's MA (PM 7.2.2 forbids it).

**Build**: the rules every other multiplier figure follows; the tests use figure 7.68's
first case for 7.40's, figure 7.37's layout (MAC.L after MAC.L, same stages) for
7.67, and one MA for 7.68's MULS.W.

### C18 — MAC.W and MAC.L: 3 states or 2

PM table 7.2 and table A.50 give MAC.W and MAC.L "3/(2)" and "3/(2 to 4)"; figures
7.23 and 7.34 draw 2 states from their EX to the next instruction's when nothing
contends. In a sequential stream from on-chip memory fetches alternate `IF` and `if`,
so one of the MAC's two consecutive MAs always meets a bus `IF`; the split adds a
state, and both MA slots end before the next instruction's EX (which follows the
second MA's split). **Ruling**: no contradiction in effect — the figures are the
model, and the table's 3 is what they give in a real stream (tests/test_pipeline.py
`test_c18_mac_is_three_states_in_a_sequential_stream`, both alignments).

### C19 — SWM section 7 (the SH-DSP's pipeline) against PM

SWM section 7 describes the SH-DSP, whose pipeline has a WB/DSP stage; it agrees with
PM everywhere the scheduler was checked except two places:

- SWM figure 7.14: a load followed by a store of the loaded register does not
  contend ("the same data is simultaneously output again to the internal bus"). PM
  7.5 lists two exceptions to the load-use split and not this one, so on PM's rule
  the store uses the register and splits the slot.
- SWM figure 7.16: an STS after MAC.W waits with stalls and then makes a one-state
  MA after the `mm` ends; PM figure 7.30 extends the MA over the `mm`, one state
  sooner.

**Build**: PM, the SH-1/SH-2 manual (SH7604 is an SH-2). Open: DN's CPS3 loops (rung
4) include stores after loads and could show the forwarding; a hardware result
there would decide it.

Rung 4 does not decide it. The only P2 loops that store a just-loaded register are the
copy loops (`MOV.L @R12+,R0` then `MOV.L R0,@R13`), and their stores go to the CPS3's
work RAM: each waits for the write buffer, which hides the one state the split costs.
Run with SWM's forwarding instead of PM's split, ten of the twelve copy loops give the
same totals and the other two differ by 2-3% in loops the core misses by 20-30% for
bus reasons (detectors). **Still PM**; deciding it needs a load-then-store loop that
stays off the external bus, timed on hardware: a store to the data array (H'C0000000-,
the cache bus only, HM 8.4.8) of the register a cached load just wrote.

### C20 — HM 7.11.2 says on-chip accesses do not wait for a buffered write; DN's CCR does

HM 7.11.2: "After a write has been performed to low-speed memory off the chip,
performing a read or write with an on-chip peripheral module enables an access to the
on-chip peripheral module without having to wait for the completion of the write". CCR
is an on-chip register (H'FFFFFE92). DN's eight purge-order values (hardware, a CPS3)
fit only a CCR write that takes effect after the buffered store (U9); an immediate one
gives a different pattern. The two can both hold if the CPU's CCR write itself does not
wait (the CPU goes on, as HM says) but the purge it requests is ordered behind the
buffered write. **Build**: that (U9): every other on-chip access follows HM; CCR's
effect is ordered behind a buffered external write. Hardware-captured on one board,
the judge for purge ordering (decision 7).
