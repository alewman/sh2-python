"""The direct memory access controller (DMAC): two channels.

HM chapter 9. A channel runs while CHCRn.DE = 1, DMAOR.DME = 1 and none of CHCRn.TE,
DMAOR.NMIF, DMAOR.AE is set. Each transfer moves one unit (CHCR.TS: byte, word,
longword, or a 16-byte unit of four longwords) from SARn to DARn, steps the addresses
(CHCR.SM/DM: fixed, increment, decrement), and decrements TCRn; when TCRn reaches 0,
CHCR.TE is set and, with CHCR.IE, the DEI interrupt requested (vector VCRDMAn). The
request comes from the DREQ pin (CHCR.AR = 0; DRCRn.RS selects DREQ for the 32X's
channel 0 FIFO) or is automatic (AR = 1). Cycle-steal mode (CHCR.TB = 0) gives the bus
back after each transfer, burst mode (TB = 1) keeps it until the count ends (HM 9.3.4).

DMA cycles reach the board with ``kind`` = ``DMA`` (``| DMA1`` for channel 1). DMA
writes never update the cache (HM 7.11.2, 8.5.2: no snooping).

Rungs 0-2 implement the registers and dual-address transfers between whole steps of
the CPU: one transfer per CPU step in cycle-steal mode, the whole block in burst mode,
with DREQ sampled as a level at the step boundary. Not modelled yet (rung 5 and the
board's rung 6): the bus arbitration between CPU and DMAC inside a step, single-address
mode and DACK (``on_dack``), DREQ edge detection (CHCR.DS) and DREQ1 acknowledge timing,
the priority mode of DMAOR.PR, and DMA address errors (vector 10).
"""

from sh2_python._module import LongwordModule, Register
from sh2_python.bus import DMA, DMA1

UNIT_BYTES = (1, 2, 4, 16)  # CHCR.TS1-0 (HM 9.2.4)


class Channel:
    """One channel's registers."""

    __slots__ = ("chcr", "dar", "drcr", "number", "sar", "tcr", "vcr")

    def __init__(self, number: int) -> None:
        self.number = number
        self.sar = self.dar = self.tcr = 0
        self.chcr = 0
        self.vcr = 0
        self.drcr = 0


class DMAC(LongwordModule):
    NAME = "dmac"
    REGISTERS = (
        Register(0xFFFFFF80, "SAR0", 32, None),
        Register(0xFFFFFF84, "DAR0", 32, None),
        Register(0xFFFFFF88, "TCR0", 32, None),
        Register(0xFFFFFF8C, "CHCR0", 32, 0x00000000),
        Register(0xFFFFFF90, "SAR1", 32, None),
        Register(0xFFFFFF94, "DAR1", 32, None),
        Register(0xFFFFFF98, "TCR1", 32, None),
        Register(0xFFFFFF9C, "CHCR1", 32, 0x00000000),
        Register(0xFFFFFFA0, "VCRDMA0", 32, None),
        Register(0xFFFFFFA8, "VCRDMA1", 32, None),
        Register(0xFFFFFFB0, "DMAOR", 32, 0x00000000),
        Register(0xFFFFFE71, "DRCR0", 8, 0x00),
        Register(0xFFFFFE72, "DRCR1", 8, 0x00),
    )

    def __init__(self, chip: object) -> None:
        super().__init__(chip)
        self.channels = (Channel(0), Channel(1))
        self.dmaor = 0
        self._te_read = [0, 0]  # CHCR.TE as last read: it clears only after being read as 1
        self._ae_nmif_read = 0
        #: True while a channel may transfer; the chip checks it once per step.
        self.active = False

    def reset(self) -> None:
        for channel in self.channels:
            channel.chcr = 0
            channel.drcr = 0
        self.dmaor = 0
        self.active = False

    # -- registers -----------------------------------------------------------------

    def read(self, address: int, width: int) -> int:
        if address in (0xFFFFFE71, 0xFFFFFE72):
            return self.channels[address - 0xFFFFFE71].drcr if width == 8 else 0
        return super().read(address, width)

    def write(self, address: int, width: int, value: int) -> None:
        if address in (0xFFFFFE71, 0xFFFFFE72):
            if width == 8:
                self.channels[address - 0xFFFFFE71].drcr = value & 3  # RS1-0
            return
        super().write(address, width, value)

    def read32(self, address: int) -> int:
        if address == 0xFFFFFFB0:
            self._ae_nmif_read = self.dmaor
            return self.dmaor
        if address in (0xFFFFFFA0, 0xFFFFFFA8):
            return self.channels[(address >> 3) & 1].vcr
        channel = self.channels[(address >> 4) & 1]
        register = address & 0xC
        if register == 0x0:
            return channel.sar
        if register == 0x4:
            return channel.dar
        if register == 0x8:
            return channel.tcr
        self._te_read[channel.number] = channel.chcr & 2
        return channel.chcr

    def write32(self, address: int, value: int, mask: int) -> None:
        if address == 0xFFFFFFB0:
            # DMAOR: PR (3), AE (2), NMIF (1), DME (0); AE and NMIF clear only after
            # being read as 1 (HM 9.2.5).
            merged = (self.dmaor & ~mask) | (value & mask)
            cleared = ~merged & self._ae_nmif_read & 0x6
            self.dmaor = (self.dmaor & 0x6 & ~cleared) | (merged & 0x9)
        elif address in (0xFFFFFFA0, 0xFFFFFFA8):
            channel = self.channels[(address >> 3) & 1]
            channel.vcr = ((channel.vcr & ~mask) | (value & mask)) & 0x7F
        elif 0xFFFFFF80 <= address < 0xFFFFFFA0:
            channel = self.channels[(address >> 4) & 1]
            register = address & 0xC
            if register == 0x0:
                channel.sar = (channel.sar & ~mask) | (value & mask)
            elif register == 0x4:
                channel.dar = (channel.dar & ~mask) | (value & mask)
            elif register == 0x8:
                channel.tcr = ((channel.tcr & ~mask) | (value & mask)) & 0xFFFFFF
            else:
                merged = ((channel.chcr & ~mask) | (value & mask)) & 0xFFFF
                # TE (bit 1) clears only when written 0 after being read as 1.
                te = channel.chcr & 2
                if not merged & 2 and self._te_read[channel.number]:
                    te = 0
                channel.chcr = (merged & ~2) | te
        self._update()

    def _update(self) -> None:
        self.active = any(self._enabled(channel) for channel in self.channels)
        self.chip.intc.update()

    def _enabled(self, channel: Channel) -> bool:
        # DE, DME, and none of TE, NMIF, AE (HM 9.3.1).
        return bool(channel.chcr & 1 and not channel.chcr & 2 and self.dmaor & 7 == 1)

    def requests(self, intc: object) -> list[tuple[int, int]]:
        # DEI0/DEI1: CHCR.TE with CHCR.IE (HM 9.4), level IPRA.DMACIP.
        level = intc.level("dmac")
        if not level:
            return []
        return [
            (level, channel.vcr & 0x7F)
            for channel in self.channels
            if channel.chcr & 6 == 6  # IE (bit 2) and TE (bit 1)
        ]

    # -- transfers ----------------------------------------------------------------------

    def run(self) -> None:
        """Make the transfers due at this step boundary (see the module docstring)."""
        chip = self.chip
        for channel in self.channels:
            if not self._enabled(channel):
                continue
            chcr = channel.chcr
            # AR = 0: an external request, DREQ sampled as a level.
            if not chcr & 0x0200 and not (chip.dreq1 if channel.number else chip.dreq0):
                continue
            burst = chcr & 0x0010  # TB
            while True:
                self._transfer(channel)
                if channel.chcr & 2 or not burst:
                    break
        self._update()

    def _transfer(self, channel: Channel) -> None:
        chip = self.chip
        chcr = channel.chcr
        unit = UNIT_BYTES[(chcr >> 10) & 3]
        kind = DMA | (DMA1 if channel.number else 0)
        src, dst = channel.sar, channel.dar
        if unit == 16:
            words = [chip._dma_read(src + 4 * i, 32, kind) for i in range(4)]
            for i, word in enumerate(words):
                chip._dma_write(dst + 4 * i, 32, word, kind)
        else:
            chip._dma_write(dst, unit * 8, chip._dma_read(src, unit * 8, kind), kind)
        sm, dm = (chcr >> 12) & 3, (chcr >> 14) & 3
        # SM/DM: 00 fixed, 01 increment, 10 decrement (HM 9.2.4); "for a 16-byte
        # transfer, the [source] address is incremented by +16 regardless of the SM1
        # and SM0 values".
        if unit == 16 or sm == 1:
            channel.sar = (src + unit) & 0xFFFFFFFF
        elif sm == 2:
            channel.sar = (src - unit) & 0xFFFFFFFF
        if dm == 1:
            channel.dar = (dst + unit) & 0xFFFFFFFF
        elif dm == 2:
            channel.dar = (dst - unit) & 0xFFFFFFFF
        # TCR counts transfers; "for 16-byte transfers, set the count to 4 times the
        # number of transfers" (HM 9.2.3); 0 means 2^24.
        step = 4 if unit == 16 else 1
        channel.tcr = (channel.tcr - step) & 0xFFFFFF
        if channel.tcr == 0:
            channel.chcr |= 2  # TE

    def state(self) -> dict[str, int]:
        out = {}
        for channel in self.channels:
            n = channel.number
            out[f"SAR{n}"] = channel.sar
            out[f"DAR{n}"] = channel.dar
            out[f"TCR{n}"] = channel.tcr
            out[f"CHCR{n}"] = channel.chcr
            out[f"VCRDMA{n}"] = channel.vcr
            out[f"DRCR{n}"] = channel.drcr
        out["DMAOR"] = self.dmaor
        return out

    def load_state(self, values: dict[str, int]) -> None:
        for channel in self.channels:
            n = channel.number
            channel.sar, channel.dar = values[f"SAR{n}"], values[f"DAR{n}"]
            channel.tcr, channel.chcr = values[f"TCR{n}"], values[f"CHCR{n}"]
            channel.vcr, channel.drcr = values[f"VCRDMA{n}"], values[f"DRCR{n}"]
        self.dmaor = values["DMAOR"]
        self.active = any(self._enabled(channel) for channel in self.channels)


__all__ = ["DMAC"]
