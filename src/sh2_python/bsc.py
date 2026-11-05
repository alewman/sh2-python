"""The bus state controller (BSC): areas, port widths, cycle shapes, the refresh timer.

HM chapter 7. The external space is four areas selected by A26-A25 (HM table 7.3):
CS0 H'00000000-, CS1 H'02000000-, CS2 H'04000000-, CS3 H'06000000- (the same in the
cache-through alias at +H'20000000). Each area's port width comes from BCR2 (areas 1-3)
or the MD3/MD4 pins (area 0), its waits from WCR and BCR1, its interface (ordinary,
burst ROM, synchronous DRAM, DRAM, pseudo-SRAM) from BCR1 and MCR.

The registers are 16 bits wide at H'FFFFFFE0-H'FFFFFFFB, one per longword: "If read as
32 bits, the upper 16 bits are 0 ... 32-bit writes are accepted only when the value of
the upper 16 bits of the write data is H'A55A; no other writes are performed", and a
16-bit access (at the longword's address + 2) "is for read only" (HM 7.1.4, table 7.2).

**Ordinary space** (every area at reset): the chip splits accesses into port-width
cycles (HM 7.4.1) of ``cycle_states[area]`` = 2 states + programmed waits, plus the
board's ``chip.wait`` (HM 7.2.3, 7.4.1).

**Synchronous DRAM** (BCR1 DRAM2-DRAM0 = 001, 100 or 101: area 3, area 2 or both; HM
7.2.1, 7.5). The port is MCR.SZ's 16 or 32 bits ("the data bus width of BCR2 is
ignored", HM 7.2.4), WCR's Wn1/Wn0 are the CAS latency (1-4), and "with synchronous
DRAM, external wait input is ignored regardless of any setting" (HM 7.2.3), so
``waits`` is 0 and ``wait_pin`` False there. Every read is one burst of 16 bytes, 8
data cycles on a 16-bit port and 4 on a 32-bit one (HM 7.5.1); a cache-through, ID/OD
or DMA read of less than 16 bytes still makes the whole burst and takes its data from
the first cycles (HM 7.5.4, "8-word-burst-read-fixed"). Writes are single (HM 7.5.5).
The states, with RCD, TRP, TRWL (1 or 2 cycles) from MCR and CL from WCR:

====================================  ==================================  =========
cycle                                 states (HM)                         32X
====================================  ==================================  =========
read, auto-precharge (RASD = 0)       Tr [Trw] Tc Tw x (CL - 1) Td x n    figs 7.15-7.17
read, bank active, bank idle          the same (figure 7.19)              3 + 8 = 11
read, bank active, same row           [Tnop if CL = 1] Tc Tw x (CL - 1)   2 + 8 = 10
                                      Td x n (figure 7.20)
read, bank active, other row          Tp [Tnop] Tr [Trw] Tc Tw x (CL - 1) 4 + 8 = 12
                                      Td x n (figure 7.21)
write, auto-precharge                 Tr [Trw] Tc (figure 7.18)           -
write, bank active, bank idle         Tr [Trw] Tc (figure 7.22)           2
write, bank active, same row          Tc (figure 7.23)                    1
write, bank active, other row         Tp [Tnop] Tr [Trw] Tc (figure 7.24) 3
auto-refresh                          Tp [Tnop] Trr, TRAS + 2 (fig. 7.25) 7
====================================  ==================================  =========

(The 32X column: its boot ROMs' BCR1 = H'0001, WCR = H'0055, MCR = H'0AB8, i.e. CS3
synchronous DRAM, 16 bits, CAS latency 2, bank active, TRP = RCD = TRWL = 1, TRAS = 3.)
The tail of an auto-precharge cycle is the bank's, not the bus's: Tap (TRP cycles after
a read with CAS latency 1, TRP - 1 with 2 or more; HM 7.5.3) and Trwl + Tap after a
write (HM 7.5.5) only hold back the next command to the same bank, which is why HM
7.5.6 says they become "invisible" when the next access comes later. In bank-active
mode TRWL holds back a PRE after a write instead (HM 7.2.4). Bank-active mode is area 3
only; area 2 is "always auto-precharge" (HM 7.2.4, RASD). Each area has two banks
(HM 7.5.1), each with one open row; the bank bit and the row's lowest bit come from
MCR's SZ and AMX (HM table 7.4: the bank is the row address bit table 7.4's note 2
marks; the row starts at the address bit on the device's A0). Banks close at a refresh
and "after the bus is released by bus arbitration" (HM 7.5.6), which only the board
sees: it calls :meth:`BSC.bus_released`.

The callbacks still see one call per port cycle: a burst's first call carries its
command states (Tr ... Tw), every call one data cycle, and the last call the burst's
remaining data cycles when the access wanted fewer than the burst holds (Td2-Td8 of
HM figure 7.17). Inside a callback ``cycle_states[area]`` is that call's own states, so
a board that computes a cycle's end as ``chip.clock + cycle_states[area] + chip.wait``
keeps working. ``chip.wait`` still lengthens an SDRAM cycle: the WAIT pin is ignored
there (a board must not add WAIT states to it), but ``chip.wait`` is also how a board
says the cycle could not start yet (bus arbitration, HM 7.10), and that delay is real
for SDRAM cycles too (docs/timing.md, "Synchronous DRAM").

**The refresh timer** (HM 7.2.5-7.2.7, 7.5.7). RTCNT counts CKIO / 4 ... / 4096
(RTCSR.CKS2-CKS0, 000 stops it) on multiples of the divider, like the WDT; when it
reaches RTCOR it is cleared, RTCSR.CMF is set (the CMI interrupt with CMIE: IPRA's WDT
level, VCRWDT's BCMV vector, HM table 5.4) and, with MCR.RFSH = 1, RMD = 0 and a
synchronous DRAM area, a refresh is requested. The chip makes it "after the access
cycle is completed" (HM 7.5.7): at the request, or when the external access under way
ends, as one ``read(address, width, REFRESH)`` call at the first SDRAM area's base
address (value ignored); it holds the bus for its states and closes every bank. A
request made while one waits replaces it (HM 7.2.7). In slave mode RFSH "is always
handled as 0" (HM 7.2.4), so only the master refreshes. RTCNT and RTCSR are read live.

Not modelled (each changes timing only): DRAM, pseudo-SRAM and burst-ROM cycle shapes
(HM 7.6-7.8) and their refresh (those areas stay ordinary cycles), self-refresh (RMD =
1: no refresh is made), the SDRAM mode-register writes (H'FFFF8000-, HM 7.5.8), idle
cycles (WCR.IW, HM 7.9), arbitration itself (HM 7.10: the board's). The write buffer
(HM 7.11.2) is the chip's (``_core.py``).
"""

from sh2_python._module import LongwordModule, Register

KEY = 0xA55A0000

# Long-wait counts selected by BCR1's AHLW/A1LW/A0LW fields (HM 7.2.1): 3, 4, 5, 6.
LONG_WAITS = (3, 4, 5, 6)
# BCR2's AnSZ field -> port width in bits (00 is reserved; HM 7.2.2).
PORT_WIDTHS = (32, 8, 16, 32)
# BCR1's DRAM2-DRAM0 -> (area 2, area 3) synchronous DRAM (HM 7.2.1; 110/111 reserved).
SDRAM_AREAS = {1: (False, True), 4: (True, False), 5: (True, True)}
# MCR (SZ, AMX2-AMX0) -> (the row's lowest address bit, the bank address bit) (HM table
# 7.4). With SZ = 0 the device's A0 is the chip's A1, with SZ = 1 its A2 (HM 7.5.2), so
# the row starts at the address bit the row phase puts on that pin; the bank bit is the
# one note 2 marks. Reserved settings fall back to AMX = 000.
SDRAM_GEOMETRY = {
    (0, 0b000): (9, 20),  # 16 Mbit, 1M x 16
    (0, 0b011): (9, 18),  # 4 Mbit, 256k x 16
    (0, 0b111): (9, 17),  # 2 Mbit, 128k x 16 (the 32X's)
    (1, 0b000): (10, 21),
    (1, 0b001): (11, 22),
    (1, 0b010): (12, 23),
    (1, 0b011): (10, 19),
    (1, 0b111): (10, 18),
}
# RTCSR.CKS2-CKS0 -> CKIO divider; 0 = count-up disabled (HM 7.2.5).
REFRESH_DIVIDERS = (0, 4, 16, 64, 256, 1024, 2048, 4096)
NEVER = 1 << 62

BCR1, BCR2, WCR, MCR = 0xFFFFFFE0, 0xFFFFFFE4, 0xFFFFFFE8, 0xFFFFFFEC
RTCSR, RTCNT, RTCOR = 0xFFFFFFF0, 0xFFFFFFF4, 0xFFFFFFF8


class BSC(LongwordModule):
    NAME = "bsc"
    REGISTERS = (
        Register(BCR1, "BCR1", 16, 0x03F0),
        Register(BCR2, "BCR2", 16, 0x00FC),
        Register(WCR, "WCR", 16, 0xAAFF),
        Register(MCR, "MCR", 16, 0x0000),
        Register(RTCSR, "RTCSR", 16, 0x0000),
        Register(RTCNT, "RTCNT", 16, 0x0000),
        Register(RTCOR, "RTCOR", 16, 0x0000),
    )

    def __init__(self, chip: object, *, slave: bool, area0_width: int) -> None:
        super().__init__(chip)
        self.slave = slave
        self.area0_width = area0_width
        self.regs = {r.address: 0 for r in self.REGISTERS}
        # The lists below are updated in place, so the chip can hold references to them.
        #: Port width of each area in bits, recomputed when BCR2 is written.
        self.widths = [area0_width, 32, 32, 32]
        #: Programmed waits of each area (ordinary space), and whether WAIT is sampled.
        self.waits = [0, 0, 0, 0]
        self.wait_pin = [True, True, True, True]
        #: States of one ordinary-space cycle in each area before the board's waits; for
        #: a synchronous DRAM area, inside a callback, the states of the call being made.
        self.cycle_states = [2, 2, 2, 2]
        #: Whether each area is synchronous DRAM (BCR1 DRAM2-DRAM0).
        self.sdram = [False, False, False, False]
        #: Data cycles of one burst read in each synchronous DRAM area (HM 7.5.1).
        self.burst = [0, 0, 0, 0]
        self.cas = [1, 1, 1, 1]  # CAS latency (WCR, HM 7.2.3)
        self.trp = self.rcd = self.trwl = 1
        self.tras = 2
        self.row_low, self.bank_bit = 9, 20
        self.bank_active = False  # RASD (area 3 only)
        # Per area and bank: the open row (-1: none), the first state the bank takes a
        # command (after an auto-precharge's Trwl/Tap), the first state it takes a PRE.
        self._rows = [[-1, -1] for _ in range(4)]
        self._ready = [[0, 0] for _ in range(4)]
        self._pre_ok = [[0, 0] for _ in range(4)]
        # The refresh timer: RTCNT is ``_count`` at state ``_count_at``.
        self._count = 0
        self._count_at = 0
        #: The state of the next refresh request not yet made (NEVER: none).
        self.refresh_at = NEVER
        self._cmf_read = False
        self.reset()

    def reset(self) -> None:
        for register in self.REGISTERS:
            self.regs[register.address] = register.initial or 0
        self._count = 0
        self._count_at = getattr(self.chip, "clock", 0)
        self._cmf_read = False
        self._recompute()
        self._schedule_refresh()

    def _recompute(self) -> None:
        bcr1, bcr2, wcr = self.regs[BCR1], self.regs[BCR2], self.regs[WCR]
        mcr = self.regs[MCR]
        self.widths[:] = [
            self.area0_width,
            PORT_WIDTHS[(bcr2 >> 2) & 3],
            PORT_WIDTHS[(bcr2 >> 4) & 3],
            PORT_WIDTHS[(bcr2 >> 6) & 3],
        ]
        long_waits = (
            LONG_WAITS[(bcr1 >> 4) & 3],  # area 0: A0LW
            LONG_WAITS[(bcr1 >> 6) & 3],  # area 1: A1LW
            LONG_WAITS[(bcr1 >> 8) & 3],  # areas 2 and 3: AHLW
            LONG_WAITS[(bcr1 >> 8) & 3],
        )
        area2, area3 = SDRAM_AREAS.get(bcr1 & 7, (False, False))
        self.sdram[:] = [False, False, area2, area3]
        sz = (mcr >> 6) & 1
        for area in range(4):
            w = (wcr >> (2 * area)) & 3
            if self.sdram[area]:
                # Wn1/Wn0 are the CAS latency; WAIT is ignored; MCR.SZ is the port
                # (HM 7.2.3, 7.2.4).
                self.cas[area] = w + 1
                self.waits[area] = 0
                self.wait_pin[area] = False
                self.widths[area] = 32 if sz else 16
                self.burst[area] = 4 if sz else 8
                self.cycle_states[area] = 1
                continue
            # WCR Wn1/Wn0: 00 no wait and no WAIT pin; 01 one wait, 10 two, 11 BCR1's
            # long wait; every setting but 00 samples the WAIT pin (HM 7.2.3).
            self.waits[area] = long_waits[area] if w == 3 else w
            self.wait_pin[area] = w != 0
            self.cycle_states[area] = 2 + self.waits[area]
            self.burst[area] = 0
        self._configure_sdram()

    def _configure_sdram(self) -> None:
        mcr = self.regs[MCR]
        sz = (mcr >> 6) & 1
        self.trp = 2 if mcr & 0x8000 else 1
        self.rcd = 2 if mcr & 0x4000 else 1
        self.trwl = 2 if mcr & 0x2000 else 1
        self.tras = 2 + min((mcr >> 11) & 3, 2)  # 11 is reserved
        self.bank_active = bool(mcr & 0x0200)
        amx = ((mcr >> 5) & 4) | ((mcr >> 4) & 3)
        self.row_low, self.bank_bit = SDRAM_GEOMETRY.get((sz, amx), SDRAM_GEOMETRY[(sz, 0)])
        self.close_banks()

    # -- synchronous DRAM cycles (HM 7.5) ---------------------------------------------

    def close_banks(self) -> None:
        """Every bank precharged: no open row (a refresh, a bus release, a new setting)."""
        for rows in self._rows:
            rows[0] = rows[1] = -1

    def bus_released(self) -> None:
        """For the board: this chip gave the bus away (HM 7.10). "Both banks will become
        inactive even in the bank active mode ... after the bus is released by bus
        arbitration" (HM 7.5.6); the precharge's own states are not modelled."""
        self.close_banks()

    def read_start(self, area: int, address: int, clock: int) -> tuple[int, int]:
        """A burst read of ``address`` wanted at ``clock``: (its first state, its command
        states before Td1). Opens the row in bank-active mode."""
        bank = (address >> self.bank_bit) & 1
        row = (address & ((1 << self.bank_bit) - 1)) >> self.row_low
        start = max(clock, self._ready[area][bank])
        cas = self.cas[area]
        # Tc, then CAS latency - 1 Tw before the first data cycle (HM 7.5.3).
        states = cas
        if self.bank_active and area == 3:
            rows = self._rows[area]
            if rows[bank] == row:
                if cas == 1:
                    states += 1  # Tnop for DQM's two-cycle latency (HM 7.5.6, figure 7.20)
            else:
                if rows[bank] >= 0:  # Tp, TRP - 1 Tnop (figure 7.21)
                    start = max(start, self._pre_ok[area][bank])
                    states += self.trp
                states += self.rcd  # Tr, RCD - 1 Trw (figure 7.19)
                rows[bank] = row
        else:
            states += self.rcd  # Tr [Trw] (figures 7.15, 7.16)
        return start, states

    def read_end(self, area: int, address: int, end: int) -> None:
        """A burst read ended at ``end``: an auto-precharge read's Tap holds the bank."""
        if not (self.bank_active and area == 3):
            bank = (address >> self.bank_bit) & 1
            tap = self.trp if self.cas[area] == 1 else self.trp - 1  # HM 7.5.3
            self._ready[area][bank] = end + tap

    def write_cycle(self, area: int, address: int, clock: int) -> tuple[int, int]:
        """A single write of ``address`` wanted at ``clock``: (its first state, its
        states). Opens the row in bank-active mode."""
        bank = (address >> self.bank_bit) & 1
        start = max(clock, self._ready[area][bank])
        if self.bank_active and area == 3:
            row = (address & ((1 << self.bank_bit) - 1)) >> self.row_low
            rows = self._rows[area]
            if rows[bank] == row:
                states = 1  # Tc (figure 7.23)
            elif rows[bank] < 0:
                states = self.rcd + 1  # Tr [Trw] Tc (figure 7.22)
            else:
                start = max(start, self._pre_ok[area][bank])
                states = self.trp + self.rcd + 1  # Tp [Tnop] Tr [Trw] Tc (figure 7.24)
            rows[bank] = row
        else:
            states = self.rcd + 1  # Tr [Trw] Tc (figure 7.18)
        return start, states

    def write_end(self, area: int, address: int, end: int) -> None:
        """A single write ended at ``end``: what it holds back, the bank's next command
        (auto-precharge: Trwl then Tap) or its next PRE (bank active: TRWL), HM 7.2.4,
        7.5.5-7.5.6."""
        bank = (address >> self.bank_bit) & 1
        if self.bank_active and area == 3:
            self._pre_ok[area][bank] = end + self.trwl
        else:
            self._ready[area][bank] = end + self.trwl + self.trp

    def refresh_cycle(self, clock: int) -> tuple[int, int]:
        """An auto-refresh wanted at ``clock``: (its first state, its states). Tp (PALL),
        TRP - 1 Tnop, Trr (REF), then TRAS + 2 with no command (HM 7.5.7, figure 7.25);
        every bank closes."""
        start = clock
        for area in (2, 3):
            if self.sdram[area]:
                start = max(start, *self._pre_ok[area])
        self.close_banks()
        return start, self.trp + 1 + self.tras + 2

    def refresh_end(self, end: int) -> None:
        """The refresh cycle ended at ``end``: the banks take commands from then on."""
        for area in (2, 3):
            ready = self._ready[area]
            ready[0] = max(ready[0], end)
            ready[1] = max(ready[1], end)

    def refresh_address(self) -> int:
        """Where a refresh cycle is reported: the first synchronous DRAM area's base."""
        return 0x04000000 if self.sdram[2] else 0x06000000

    # -- the refresh timer (HM 7.2.5-7.2.7) ------------------------------------------

    def _run_timer(self, clock: int) -> None:
        """RTCNT and CMF brought to ``clock``."""
        last = self._count_at
        if clock <= last:
            return
        self._count_at = clock
        div = REFRESH_DIVIDERS[(self.regs[RTCSR] >> 3) & 7]
        if not div:
            return
        ticks = clock // div - last // div
        if not ticks:
            return
        rtcor = self.regs[RTCOR]
        need = (rtcor - self._count) & 0xFF or 0x100
        if ticks < need:
            self._count = (self._count + ticks) & 0xFF
            return
        period = rtcor or 0x100
        self._count = (ticks - need) % period
        self.regs[RTCSR] |= 0x80  # CMF
        self.chip.intc.update()

    def _next_match(self) -> int:
        """The state at which RTCNT next reaches RTCOR (NEVER when it does not count)."""
        div = REFRESH_DIVIDERS[(self.regs[RTCSR] >> 3) & 7]
        if not div:
            return NEVER
        need = (self.regs[RTCOR] - self._count) & 0xFF or 0x100
        return (self._count_at // div + need) * div

    def _schedule_refresh(self) -> None:
        mcr = self.regs[MCR]
        if self.slave or mcr & 0x000C != 0x0008 or not (self.sdram[2] or self.sdram[3]):
            self.refresh_at = NEVER  # RFSH = 0 (always, in slave mode), or RMD = 1
        else:
            self.refresh_at = self._next_match()
        chip = self.chip
        if hasattr(chip, "_bsc_due"):
            chip._bsc_due = min(chip._bsc_due, self.refresh_at)
            chip._reschedule()

    def refresh_served(self, start: int) -> None:
        """A refresh was made at ``start``: requests up to then are answered (a request
        made while one waited replaced it, HM 7.2.7)."""
        rtcor = self.regs[RTCOR]
        period = (rtcor or 0x100) * REFRESH_DIVIDERS[(self.regs[RTCSR] >> 3) & 7]
        at = self.refresh_at
        if period <= 0:
            self.refresh_at = NEVER
            return
        while at <= start:
            at += period
        self.refresh_at = at

    def next_event(self) -> int:
        """The next state the chip must look at the BSC: a refresh, or CMI's match."""
        if self.regs[RTCSR] & 0x40 and not self.regs[RTCSR] & 0x80:
            return min(self.refresh_at, self._next_match())
        return self.refresh_at

    def run_until(self, clock: int) -> None:
        self._run_timer(clock)

    def requests(self, intc: object) -> list[tuple[int, int]]:
        # CMI: compare match with CMIE (HM 7.2.5), at IPRA's WDT level, vector VCRWDT.BCMV
        # (HM 5.3.1, 5.3.6).
        level = intc.level("wdt")
        if level and self.regs[RTCSR] & 0xC0 == 0xC0:
            return [(level, intc.vector(0xFFFFFEE5))]
        return []

    # -- registers -------------------------------------------------------------

    def read32(self, address: int) -> int:
        if address in (RTCSR, RTCNT):
            self._run_timer(self.chip.clock)
            if address == RTCNT:
                return self._count
            self._cmf_read = bool(self.regs[RTCSR] & 0x80)  # for CMF's clear
        value = self.regs.get(address, 0)
        if address == BCR1:
            # BCR1.MASTER (bit 15) reads the MD5 pin: 1 in slave mode (HM 7.2.1).
            value = (value & 0x7FFF) | (0x8000 if self.slave else 0)
        return value

    def write(self, address: int, width: int, value: int) -> None:
        # Only a longword write with H'A55A in the upper half is performed (HM 7.1.4).
        if width == 32 and value & 0xFFFF0000 == KEY:
            self.write32(address & ~3, value, 0xFFFF)

    def write32(self, address: int, value: int, mask: int) -> None:
        if address not in self.regs:
            return
        chip = self.chip
        clock = getattr(chip, "clock", 0)
        if self.refresh_at <= clock:
            chip._refresh_idle()  # requests made before this write are kept
        self._run_timer(clock)
        value &= 0xFFFF
        if address == BCR1:
            value &= 0x1FF7  # MASTER is read-only; bits 14, 13 and 3 read 0
        elif address == BCR2:
            value &= 0x00FC
        elif address == MCR:
            value &= 0xFEFC
        elif address == RTCSR:
            # CMF can only be cleared: written 0 after it was read as 1 (HM 7.2.5).
            cmf = self.regs[RTCSR] & 0x80
            if not value & 0x80 and getattr(self, "_cmf_read", False):
                cmf = 0
            self._cmf_read = False
            value = (value & 0x78) | cmf
        elif address == RTCNT:
            value &= 0x00FF
            self._count = value
        elif address == RTCOR:
            value &= 0x00FF
        self.regs[address] = value
        if address < RTCSR:
            self._recompute()
        self._schedule_refresh()
        if address == RTCSR:
            chip.intc.update()

    def state(self) -> dict[str, int]:
        self._run_timer(getattr(self.chip, "clock", 0))
        self.regs[RTCNT] = self._count
        return {r.name: self.regs[r.address] for r in self.REGISTERS}

    def load_state(self, values: dict[str, int]) -> None:
        for register in self.REGISTERS:
            self.regs[register.address] = values[register.name]
        self._count = self.regs[RTCNT]
        self._count_at = self.chip.clock
        self._recompute()
        self._schedule_refresh()


__all__ = ["BSC"]
