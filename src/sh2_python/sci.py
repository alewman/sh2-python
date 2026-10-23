"""The serial communication interface (SCI): one channel, asynchronous or clocked.

HM chapter 13. The 32X links its two SH-2s' SCIs; the Saturn uses them too. Decided
granularity (open decision 10, the recommendation): **byte-level** frames. A byte
written to TDR with SCR.TE = 1 is sent when software clears SSR.TDRE (HM 13.3.2), and
the board receives it through ``on_txd(byte, clock)``; the board delivers a received
byte with :meth:`receive`.

Registers (HM 13.2, appendix B): SMR H'FFFFFE00, BRR H'FFFFFE01 (H'FF), SCR H'FFFFFE02,
TDR H'FFFFFE03 (H'FF), SSR H'FFFFFE04 (H'84: TDRE and TEND), RDR H'FFFFFE05.

Rungs 0-2 send each byte at once (TDRE and TEND set again immediately) and do not
model the frame time from SMR/BRR, the clocked mode, multiprocessor bits, or the ERI
sources; those belong to rung 5 with a board that uses the link.
"""

from sh2_python._module import Module, Register

TDRE, RDRF, ORER, FER, PER, TEND = 0x80, 0x40, 0x20, 0x10, 0x08, 0x04


class SCI(Module):
    NAME = "sci"
    REGISTERS = (
        Register(0xFFFFFE00, "SMR", 8, 0x00),
        Register(0xFFFFFE01, "BRR", 8, 0xFF),
        Register(0xFFFFFE02, "SCR", 8, 0x00),
        Register(0xFFFFFE03, "TDR", 8, 0xFF),
        Register(0xFFFFFE04, "SSR", 8, 0x84),
        Register(0xFFFFFE05, "RDR", 8, 0x00),
    )

    def __init__(self, chip: object) -> None:
        super().__init__(chip)
        self.reset()

    def reset(self) -> None:
        self.smr, self.brr, self.scr, self.tdr, self.ssr, self.rdr = 0, 0xFF, 0, 0xFF, 0x84, 0
        self._ssr_read = 0

    def receive(self, byte: int) -> None:
        """A byte arrives on RxD: RDR, SSR.RDRF (ORER if RDRF was still set; HM 13.3.2)."""
        if not self.scr & 0x10:  # RE
            return
        if self.ssr & RDRF:
            self.ssr |= ORER
        else:
            self.rdr = byte & 0xFF
            self.ssr |= RDRF
        self.chip.intc.update()

    def requests(self, intc: object) -> list[tuple[int, int]]:
        level = intc.level("sci")
        if not level:
            return []
        out = []
        scr, ssr = self.scr, self.ssr
        if scr & 0x40 and ssr & (ORER | FER | PER):
            out.append((level, intc.vector(0xFFFFFE62)))  # ERI: VCRA.SERV
        if scr & 0x40 and ssr & RDRF:
            out.append((level, intc.vector(0xFFFFFE63)))  # RXI: VCRA.SRXV
        if scr & 0x80 and ssr & TDRE:
            out.append((level, intc.vector(0xFFFFFE64)))  # TXI: VCRB.STXV
        if scr & 0x04 and ssr & TEND:
            out.append((level, intc.vector(0xFFFFFE65)))  # TEI: VCRB.STEV
        return out

    def read8(self, address: int) -> int:
        if address == 0xFFFFFE04:
            self._ssr_read = self.ssr
        return {
            0xFFFFFE00: self.smr,
            0xFFFFFE01: self.brr,
            0xFFFFFE02: self.scr,
            0xFFFFFE03: self.tdr,
            0xFFFFFE04: self.ssr,
            0xFFFFFE05: self.rdr,
        }.get(address, 0xFF)

    def write8(self, address: int, value: int) -> None:
        if address == 0xFFFFFE00:
            self.smr = value
        elif address == 0xFFFFFE01:
            self.brr = value
        elif address == 0xFFFFFE02:
            self.scr = value
        elif address == 0xFFFFFE03:
            self.tdr = value
        elif address == 0xFFFFFE04:
            # TDRE, RDRF, ORER, FER, PER clear only where 0 is written after reading 1;
            # TEND and MPB are read-only; MPBT is read/write (HM 13.2.7).
            cleared = ~value & self._ssr_read & 0xF8
            self.ssr = (self.ssr & ~cleared & ~0x01) | (value & 0x01)
            if cleared & TDRE and self.scr & 0x20:  # TE: the byte in TDR goes out
                callback = self.chip.on_txd
                if callback is not None:
                    callback(self.tdr, self.chip.clock)
                self.ssr |= TDRE | TEND
        self.chip.intc.update()

    def state(self) -> dict[str, int]:
        return {
            "SMR": self.smr,
            "BRR": self.brr,
            "SCR": self.scr,
            "TDR": self.tdr,
            "SSR": self.ssr,
            "RDR": self.rdr,
        }

    def load_state(self, values: dict[str, int]) -> None:
        self.smr, self.brr, self.scr = values["SMR"], values["BRR"], values["SCR"]
        self.tdr, self.ssr, self.rdr = values["TDR"], values["SSR"], values["RDR"]


__all__ = ["SCI"]
