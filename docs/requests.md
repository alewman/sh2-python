<!-- SPDX-License-Identifier: MIT -->
# Requests to sh2-python (from its boards and its transcriptions)

Behaviour is fixed here first and carried down by merges (charter, "How change flows").
Downstream projects keep their own copies of what they sent (sh2-rust's docs/requests.md
holds R1 and R2, fixed in 8824075: docs/validation.md, last sections). This file lists
what is asked of the core and not yet built, with the evidence the builder will need.

## S1. FRT input capture on FTI (saturn-python, Phase 0; recorded 2026-11-25)

**Status: proposed, not built.** Recorded only; nothing in the core changes until a job
takes it.

**Why.** On the Saturn the two SH-2s signal each other through the FRT's input capture:
a 16-bit write to the SINIT area (H'01000000-H'017FFFFF) raises the slave's FTI
capture, and a write to MINIT (H'01800000-H'01FFFFFF) the master's. saturn-python cites
the Sega Saturn Dual CPU User's Guide, section 1.2, and its memory-map note (its
docs/memory-map.md, rows MINIT/SINIT; docs/start-here.md, "The family pieces it uses";
docs/handoff-brief.md, item 3 and open decision 9; docs/domains-and-speed.md, the
MINIT/SINIT channel). The 32X does not use the input capture. Today `frt.py` keeps
`chip.fti` as a pin attribute but does not sample it ("Not modelled yet (rung 5)" in its
docstring). FICR stays H'0000, ICF never sets, and ICI is never requested, although
`FRT.requests` already maps ICF with TIER.ICIE to VCRC.FICV.

**What the manual specifies** (HM = the SH7604 Hardware Manual, Rev. 4.0, as pinned):

- HM 11.1.3, table 11.1: FTI is the input capture input pin.
- HM 11.2.3: on the selected edge "the current FRC value is transferred to ICR. At the
  same time, the input capture flag (ICF) in FTCSR is set to 1"; the input pulse must
  be at least six φ wide to be captured reliably; ICR reads through TEMP (HM 11.3), and
  is H'0000 after a reset, in standby and in module standby.
- HM 11.2.4: TIER.ICIE enables the ICI request when ICF = 1.
- HM 11.2.5: FTCSR.ICF is set by hardware and cleared by software (written 0 after
  being read as 1; the model already keeps "read 1, then write 0" for the other flags).
- HM 11.2.6: TCR.IEDG (bit 7) picks the edge: 0, falling (initial value); 1, rising.
- HM 11.4.4, figures 11.8 and 11.9: the capture signal follows the pin on the timer's
  drive clock. When the edge comes while ICR's upper byte is being read, the capture is
  delayed by one timer drive clock.
- HM 11.4.5, figure 11.10: ICF sets and FRC goes to ICR on the same clock.
- HM 11.5, table 11.3: ICI is one of the FRT's interrupt sources, with its own vector
  (VCRC.FICV) and the FRT's priority.
- HM 15.3.5, table 15.11 (5 V; 16.3.5 and table 16.11 for 3.3 V): input capture setup
  time t_TICS of at least 80 ns (PLL on with the 1/4-cycle delay: 80 ns - 1/4 tcyc), which
  places an edge relative to the clock.

**What the board needs from the core.** Each Saturn board drives the other chip's FTI
from inside a bus callback, at the writing CPU's `chip.clock`. The core needs a way to
take that edge at a known clock: the FRT catches up to it (`run_until`), copies FRC to
FICR, sets ICF, and reschedules so that ICI is seen at the next boundary. Either sampling
`chip.fti` at timer clocks or a method the board calls with the edge's clock would do;
the builder chooses and documents it. The repro the Saturn's handoff asks for: with
TIER.ICIE = 1 and the FRT's IPR level set, an FTI edge of the selected polarity (the
other CPU's write to SINIT or MINIT) must set ICF, latch FRC into FICR, and take the ICI
vector at the next boundary.

**Evidence tags the build will carry:** DOC for everything above. Whether FTI is
synchronised on the timer drive clock or on φ, and so how many states the capture
lags the edge, is only drawn in figures 11.8-11.10, not given in states. It will be
HYPOTHESIS until a Saturn test (or Dunkley-style measurements) decides it.
