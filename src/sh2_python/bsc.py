"""The bus state controller (BSC): areas, port widths, wait states, the refresh timer.

HM chapter 7. The external space is four areas selected by A26-A25 (HM table 7.3):
CS0 H'00000000-, CS1 H'02000000-, CS2 H'04000000-, CS3 H'06000000- (the same in the
cache-through alias at +H'20000000). Each area's port width comes from BCR2 (areas 1-3)
or the MD3/MD4 pins (area 0), its waits from WCR and BCR1, its interface (ordinary,
burst ROM, synchronous DRAM, DRAM, pseudo-SRAM) from BCR1 and MCR.

The registers are 16 bits wide at H'FFFFFFE0-H'FFFFFFFB, one per longword: "If read as
32 bits, the upper 16 bits are 0 ... 32-bit writes are accepted only when the value of
the upper 16 bits of the write data is H'A55A; no other writes are performed", and a
16-bit access (at the longword's address + 2) "is for read only" (HM 7.1.4, table 7.2).

What this module does for rungs 0-2: holds the registers, gives the chip each area's
port width (to split accesses into port-width bus cycles, HM 7.4.1) and the length of
an ordinary cycle (2 states + programmed waits + the board's ``chip.wait``, HM 7.2.3,
7.4.1). Not yet modelled, for later rungs: SDRAM/DRAM/burst-ROM cycle shapes (HM
7.5-7.8), idle cycles (WCR.IW, HM 7.9), the write buffer (HM 7.11.2), the refresh
timer and its REFRESH cycles (RTCSR/RTCNT/RTCOR, HM 7.2.5-7.2.7), bus arbitration
(HM 7.10).
"""

from sh2_python._module import LongwordModule, Register

KEY = 0xA55A0000

# Long-wait counts selected by BCR1's AHLW/A1LW/A0LW fields (HM 7.2.1): 3, 4, 5, 6.
LONG_WAITS = (3, 4, 5, 6)
# BCR2's AnSZ field -> port width in bits (00 is reserved; HM 7.2.2).
PORT_WIDTHS = (32, 8, 16, 32)


class BSC(LongwordModule):
    NAME = "bsc"
    REGISTERS = (
        Register(0xFFFFFFE0, "BCR1", 16, 0x03F0),
        Register(0xFFFFFFE4, "BCR2", 16, 0x00FC),
        Register(0xFFFFFFE8, "WCR", 16, 0xAAFF),
        Register(0xFFFFFFEC, "MCR", 16, 0x0000),
        Register(0xFFFFFFF0, "RTCSR", 16, 0x0000),
        Register(0xFFFFFFF4, "RTCNT", 16, 0x0000),
        Register(0xFFFFFFF8, "RTCOR", 16, 0x0000),
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
        #: States of one ordinary-space cycle in each area before the board's waits.
        self.cycle_states = [2, 2, 2, 2]
        self.reset()

    def reset(self) -> None:
        for register in self.REGISTERS:
            self.regs[register.address] = register.initial or 0
        self._recompute()

    def _recompute(self) -> None:
        bcr1, bcr2, wcr = self.regs[0xFFFFFFE0], self.regs[0xFFFFFFE4], self.regs[0xFFFFFFE8]
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
        for area in range(4):
            w = (wcr >> (2 * area)) & 3
            # WCR Wn1/Wn0: 00 no wait and no WAIT pin; 01 one wait, 10 two, 11 BCR1's
            # long wait; every setting but 00 samples the WAIT pin (HM 7.2.3).
            self.waits[area] = long_waits[area] if w == 3 else w
            self.wait_pin[area] = w != 0
            self.cycle_states[area] = 2 + self.waits[area]

    # -- registers -------------------------------------------------------------

    def read32(self, address: int) -> int:
        value = self.regs.get(address, 0)
        if address == 0xFFFFFFE0:
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
        value &= 0xFFFF
        if address == 0xFFFFFFE0:
            value &= 0x1FF7  # MASTER is read-only; bits 14, 13 and 3 read 0
        elif address == 0xFFFFFFE4:
            value &= 0x00FC
        elif address == 0xFFFFFFEC:
            value &= 0xFEFC
        elif address == 0xFFFFFFF0:
            value &= 0x00F8
        elif address in (0xFFFFFFF4, 0xFFFFFFF8):
            value &= 0x00FF
        self.regs[address] = value
        self._recompute()

    def state(self) -> dict[str, int]:
        return {r.name: self.regs[r.address] for r in self.REGISTERS}

    def load_state(self, values: dict[str, int]) -> None:
        for register in self.REGISTERS:
            self.regs[register.address] = values[register.name]
        self._recompute()


__all__ = ["BSC"]
