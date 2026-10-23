"""The interrupt controller (INTC): priorities and vector numbers of the on-chip sources.

HM chapter 5. NMI and the IRL pins are inputs the board drives (``chip.nmi``,
``chip.irl``); the on-chip modules' sources get their level (0-15, 0 = masked) from
IPRA/IPRB and their vector number from the VCR registers (HM 5.3). The CPU compares
the highest pending level with SR.I3-I0 between instructions (HM 4.6, 5.4); that check
is in ``_core.py``, which reads the two plain attributes this module keeps current:
``chip._onchip_level`` and ``chip._onchip_vector``.

Registers (HM table 5.5, appendix B), all on the 8-bit peripheral bus:

======  ==========  =============================================================
IPRB    H'FFFFFE60  SCIIP3-0 (bits 15-12), FRTIP3-0 (11-8)
VCRA    H'FFFFFE62  SERV (14-8), SRXV (6-0): SCI receive error, receive full
VCRB    H'FFFFFE64  STXV, STEV: SCI transmit empty, transmit end
VCRC    H'FFFFFE66  FICV, FOCV: FRT input capture, output compare
VCRD    H'FFFFFE68  FOVV: FRT overflow
ICR     H'FFFFFEE0  NMIL (15, the NMI pin, read only), NMIE (8), VECMD (0)
IPRA    H'FFFFFEE2  DIVUIP (15-12), DMACIP (11-8), WDTIP (7-4)
VCRWDT  H'FFFFFEE4  WITV (14-8): WDT interval; BCMV (6-0): BSC compare match
======  ==========  =============================================================

VCRDIV (H'FFFFFF0C) belongs to the DIVU and VCRDMA0/1 (H'FFFFFFA0/A8) to the DMAC.
"""

from sh2_python._module import Module, Register

# The byte addresses this module answers, with their reset values (HM table 5.5).
_INITIAL = {
    0xFFFFFE60: 0x00,
    0xFFFFFE61: 0x00,
    0xFFFFFE62: 0x00,
    0xFFFFFE63: 0x00,
    0xFFFFFE64: 0x00,
    0xFFFFFE65: 0x00,
    0xFFFFFE66: 0x00,
    0xFFFFFE67: 0x00,
    0xFFFFFE68: 0x00,
    0xFFFFFE69: 0x00,
    0xFFFFFEE0: 0x00,
    0xFFFFFEE1: 0x00,
    0xFFFFFEE2: 0x00,
    0xFFFFFEE3: 0x00,
    0xFFFFFEE4: 0x00,
    0xFFFFFEE5: 0x00,
}
# Bits that read 0 whatever is written (HM 5.3, the reserved bits).
_WRITABLE = {
    0xFFFFFE60: 0xFF,
    0xFFFFFE61: 0x00,
    0xFFFFFE62: 0x7F,
    0xFFFFFE63: 0x7F,
    0xFFFFFE64: 0x7F,
    0xFFFFFE65: 0x7F,
    0xFFFFFE66: 0x7F,
    0xFFFFFE67: 0x7F,
    0xFFFFFE68: 0x7F,
    0xFFFFFE69: 0x00,
    0xFFFFFEE0: 0x01,  # NMIE; NMIL (bit 7) is the pin
    0xFFFFFEE1: 0x01,  # VECMD
    0xFFFFFEE2: 0xFF,
    0xFFFFFEE3: 0xF0,
    0xFFFFFEE4: 0x7F,
    0xFFFFFEE5: 0x7F,
}


class INTC(Module):
    NAME = "intc"
    REGISTERS = (
        Register(0xFFFFFE60, "IPRB", 16, 0x0000),
        Register(0xFFFFFE62, "VCRA", 16, 0x0000),
        Register(0xFFFFFE64, "VCRB", 16, 0x0000),
        Register(0xFFFFFE66, "VCRC", 16, 0x0000),
        Register(0xFFFFFE68, "VCRD", 16, 0x0000),
        Register(0xFFFFFEE0, "ICR", 16, None),  # NMIL follows the pin
        Register(0xFFFFFEE2, "IPRA", 16, 0x0000),
        Register(0xFFFFFEE4, "VCRWDT", 16, 0x0000),
    )

    def __init__(self, chip: object) -> None:
        super().__init__(chip)
        self.regs = dict(_INITIAL)

    def reset(self) -> None:
        self.regs = dict(_INITIAL)
        self.update()

    def read8(self, address: int) -> int:
        if address == 0xFFFFFEE0:
            # NMIL: "the NMI pin's level" (HM 5.3.3, ICR bit 15).
            return (0x80 if self.chip.nmi else 0) | self.regs[address]
        return self.regs.get(address, 0)

    def write8(self, address: int, value: int) -> None:
        if address in _WRITABLE:
            self.regs[address] = value & _WRITABLE[address]
            self.update()

    # -- the fields the chip and the modules use -----------------------------------

    @property
    def nmi_rising(self) -> bool:
        """ICR.NMIE: 1 takes NMI on a rising edge, 0 on a falling edge (HM 5.3.3)."""
        return bool(self.regs[0xFFFFFEE0] & 1)

    @property
    def vecmd(self) -> bool:
        """ICR.VECMD: IRL vectors from the bus (HM 5.3.3, table 4.3 note 2)."""
        return bool(self.regs[0xFFFFFEE1] & 1)

    def level(self, name: str) -> int:
        """The IPR level of a source group: divu, dmac, wdt, sci, frt (HM 5.3.1, 5.3.2)."""
        r = self.regs
        if name == "divu":
            return r[0xFFFFFEE2] >> 4
        if name == "dmac":
            return r[0xFFFFFEE2] & 15
        if name == "wdt":
            return r[0xFFFFFEE3] >> 4
        if name == "sci":
            return r[0xFFFFFE60] >> 4
        return r[0xFFFFFE60] & 15  # frt

    def vector(self, address: int) -> int:
        """A VCR field, by the byte address that holds it (7 bits, HM 5.3.4-5.3.8)."""
        return self.regs.get(address, 0) & 0x7F

    def update(self) -> None:
        """Recompute the highest on-chip request into ``chip._onchip_level`` / ``_vector``.

        Sources at the same IPR level are ordered as HM table 5.4 lists them: DIVU,
        DMAC channel 0, DMAC channel 1, WDT, BSC compare match, SCI (ERI, RXI, TXI,
        TEI), FRT (ICI, OCI, OVI). The first source at the highest level wins.
        """
        chip = self.chip
        best_level, best_vector = 0, 0
        for level, vector in chip._onchip_requests():
            if level > best_level:
                best_level, best_vector = level, vector
        chip._onchip_level = best_level
        chip._onchip_vector = best_vector

    def state(self) -> dict[str, int]:
        r = self.regs
        out = {}
        for register in self.REGISTERS:
            out[register.name] = (r[register.address] << 8) | r[register.address + 1]
        return out

    def load_state(self, values: dict[str, int]) -> None:
        for register in self.REGISTERS:
            word = values[register.name]
            self.regs[register.address] = (word >> 8) & 0xFF
            self.regs[register.address + 1] = word & 0xFF
        self.update()


__all__ = ["INTC"]
