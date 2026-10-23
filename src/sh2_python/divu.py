"""The division unit (DIVU): 32 ÷ 32 and 64 ÷ 32 signed division in 39 states.

HM chapter 10. Writing DVDNT starts a 32 ÷ 32 division of DVDNT by DVSR; writing DVDNTL
starts a 64 ÷ 32 division of DVDNTH:DVDNTL by DVSR. The quotient goes to DVDNTL (and
DVDNT), the remainder to DVDNTH. The division takes 39 states; a DIVU register access
during it "is continuously extended until the operation ends" (HM 10.4.1), which the
chip models by stalling the access (``chip._stall``): real code reads the result one
clock after the start and depends on the stall (docs/mame-oracle.md).

Overflow (HM 10.3.3; U12, U13, C4): DVCR.OVF is set after 6 states; with DVCR.OVFIE = 1
the registers hold "the result after 6 cycles of operation" and the OVFI interrupt is
requested; with OVFIE = 0, DVDNTH holds that partial result and DVDNTL H'7FFFFFFF or
H'80000000 by the true quotient's sign. The partial result is Mednafen's three-step
non-restoring division (``DIVU64_Partial``), the only model of it found (emulator tier,
docs/undocumented-behavior.md U12); 32 ÷ 32 by zero follows Mednafen's
``DIVU_S32_S32`` (U13). H'80000000 ÷ H'FFFFFFFF in 32 ÷ 32 gives quotient H'80000000,
remainder 0, no overflow (Mednafen; U13 open).

Registers (HM table 10.2) and mirrors as MAME 0.285 maps them (U14): DVSR H'FFFFFF00,
DVDNT H'FFFFFF04, DVCR H'FFFFFF08, VCRDIV H'FFFFFF0C (the INTC's vector for OVFI),
DVDNTH H'FFFFFF10, DVDNTL H'FFFFFF14, and read-only mirrors of DVDNTH at H'FFFFFF18 and
DVDNTL at H'FFFFFF1C.

Not modelled yet (rung 5): "A DIVU read right after a DIVU write is extended one
cycle", and "a DIVU register write right after the start-up write may not take effect"
(HM 10.4.1; timing.md "The divider"); Mednafen's one-state start asymmetry.
"""

from sh2_python._module import LongwordModule, Register

DIVIDE_STATES = 39  # HM 10.1.1, 10.3.1
OVERFLOW_STATES = 6  # HM 10.3.3: "3 to set flags, 3 of division"


def _s32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - (1 << 32) if value & 0x80000000 else value


def _trunc_div(a: int, b: int) -> tuple[int, int]:
    """C-style signed division: the quotient rounds toward zero, the remainder takes a's sign."""
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    return q, a - q * b


def partial_64(dividend: int, divisor: int) -> int:
    """Mednafen's ``DIVU64_Partial``: the 64-bit state after three non-restoring steps.

    ``dividend`` is the 64-bit DVDNTH:DVDNTL, ``divisor`` the 32-bit DVSR (unsigned
    bit patterns). Each step subtracts or adds the divisor at bit 32 by the signs, then
    shifts in the next quotient bit (Q xor 1 xor M).
    """
    mask = (1 << 64) - 1
    q = dividend >> 63
    m = (divisor >> 31) & 1
    for _ in range(3):
        if not q ^ m:
            dividend = (dividend - (divisor << 32)) & mask
        else:
            dividend = (dividend + (divisor << 32)) & mask
        q = dividend >> 63
        dividend = ((dividend << 1) & mask) | (q ^ 1 ^ m)
    return dividend


class DIVU(LongwordModule):
    NAME = "divu"
    REGISTERS = (
        Register(0xFFFFFF00, "DVSR", 32, None),
        Register(0xFFFFFF04, "DVDNT", 32, None),
        Register(0xFFFFFF08, "DVCR", 32, 0x00000000),
        Register(0xFFFFFF0C, "VCRDIV", 32, None),
        Register(0xFFFFFF10, "DVDNTH", 32, None),
        Register(0xFFFFFF14, "DVDNTL", 32, None),
    )

    def __init__(self, chip: object) -> None:
        super().__init__(chip)
        self.dvsr = self.dvdnt = self.dvdnth = self.dvdntl = self.vcrdiv = 0
        self.dvcr = 0
        #: The clock at which the running division ends (no division: in the past).
        self.busy_until = 0

    def reset(self) -> None:
        # Only DVCR has a defined initial value (HM table 10.2); the others keep theirs.
        self.dvcr = 0
        self.busy_until = 0

    def requests(self, intc: object) -> list[tuple[int, int]]:
        # OVFI: DVCR.OVF with OVFIE (HM 10.3.3), level IPRA.DIVUIP, vector VCRDIV.
        level = intc.level("divu")
        if level and self.dvcr & 3 == 3:
            return [(level, self.vcrdiv & 0x7F)]
        return []

    def read32(self, address: int) -> int:
        self._wait()
        offset = address & 0x1F
        if offset == 0x00:
            return self.dvsr
        if offset == 0x04:
            return self.dvdnt
        if offset == 0x08:
            return self.dvcr
        if offset == 0x0C:
            return self.vcrdiv
        if offset in (0x10, 0x18):
            return self.dvdnth
        if offset in (0x14, 0x1C):
            return self.dvdntl
        return 0

    def write32(self, address: int, value: int, mask: int) -> None:
        self._wait()
        offset = address & 0x1F
        if offset == 0x00:
            self.dvsr = (self.dvsr & ~mask) | value
        elif offset == 0x04:
            self.dvdnt = (self.dvdnt & ~mask) | value
            self._divide_32()
        elif offset == 0x08:
            self.dvcr = ((self.dvcr & ~mask) | value) & 3
            self.chip.intc.update()
        elif offset == 0x0C:
            self.vcrdiv = ((self.vcrdiv & ~mask) | value) & 0xFFFF
            self.chip.intc.update()
        elif offset == 0x10:
            self.dvdnth = (self.dvdnth & ~mask) | value
        elif offset == 0x14:
            self.dvdntl = (self.dvdntl & ~mask) | value
            self._divide_64()

    def _wait(self) -> None:
        """An access while dividing is extended until the division ends (HM 10.4.1)."""
        remaining = self.busy_until - self.chip.clock
        if remaining > 0:
            self.chip._stall(remaining)

    def _overflow(self) -> None:
        self.dvcr |= 1
        self.busy_until = self.chip.clock + OVERFLOW_STATES
        self.chip.intc.update()

    def _divide_32(self) -> None:
        """DVDNT written: 32 ÷ 32 (HM 10.3.2). DVDNTL takes DVDNT, DVDNTH its sign."""
        dividend = _s32(self.dvdnt)
        self.dvdntl = self.dvdnt
        self.dvdnth = 0xFFFFFFFF if dividend < 0 else 0
        divisor = _s32(self.dvsr)
        if divisor == 0:
            # Mednafen DIVU_S32_S32: DVDNTH = DVDNT >> 29 (arithmetic); DVDNTL saturates
            # by the dividend's sign, or with OVFIE the dividend shifted left 3 with
            # the inverted sign shifted in (U13).
            self._overflow()
            self.dvdnth = (dividend >> 29) & 0xFFFFFFFF
            if self.dvcr & 2:
                fill = 0 if dividend < 0 else 7
                self.dvdnt = self.dvdntl = ((self.dvdnt << 3) | fill) & 0xFFFFFFFF
            else:
                self.dvdnt = self.dvdntl = 0x80000000 if dividend < 0 else 0x7FFFFFFF
            return
        self.busy_until = self.chip.clock + DIVIDE_STATES
        if divisor == -1 and dividend == -0x80000000:
            # U13: Mednafen gives quotient H'80000000, remainder 0, no overflow.
            self.dvdnt = self.dvdntl = 0x80000000
            self.dvdnth = 0
            return
        quotient, remainder = _trunc_div(dividend, divisor)
        self.dvdnth = remainder & 0xFFFFFFFF
        self.dvdnt = self.dvdntl = quotient & 0xFFFFFFFF

    def _divide_64(self) -> None:
        """DVDNTL written: 64 ÷ 32 of DVDNTH:DVDNTL by DVSR (HM 10.3.2)."""
        raw = (self.dvdnth << 32) | self.dvdntl
        dividend = raw - (1 << 64) if raw >> 63 else raw
        divisor = _s32(self.dvsr)
        overflow = divisor == 0
        if not overflow:
            quotient, remainder = _trunc_div(dividend, divisor)
            # HM 10.3.3: overflow when the quotient does not fit 32 bits. Mednafen lets
            # H'80000000 through for a negative divisor with no remainder.
            exact_min = quotient == 0x80000000 and divisor < 0 and remainder == 0
            overflow = not -0x7FFFFFFF <= quotient <= 0x7FFFFFFF and not exact_min
        if overflow:
            self._overflow()
            partial = partial_64(raw, self.dvsr & 0xFFFFFFFF)
            self.dvdnth = partial >> 32
            if self.dvcr & 2:
                self.dvdnt = self.dvdntl = partial & 0xFFFFFFFF
            else:
                negative = ((self.dvdnth_sign(raw)) ^ (self.dvsr >> 31)) & 1
                self.dvdnt = self.dvdntl = 0x80000000 if negative else 0x7FFFFFFF
            return
        self.busy_until = self.chip.clock + DIVIDE_STATES
        self.dvdnth = remainder & 0xFFFFFFFF
        self.dvdnt = self.dvdntl = quotient & 0xFFFFFFFF

    @staticmethod
    def dvdnth_sign(raw: int) -> int:
        """The dividend's sign bit (bit 63), which with DVSR's gives the quotient's sign."""
        return (raw >> 63) & 1

    def state(self) -> dict[str, int]:
        return {
            "DVSR": self.dvsr,
            "DVDNT": self.dvdnt,
            "DVCR": self.dvcr,
            "VCRDIV": self.vcrdiv,
            "DVDNTH": self.dvdnth,
            "DVDNTL": self.dvdntl,
            "busy_until": self.busy_until,
        }

    def load_state(self, values: dict[str, int]) -> None:
        self.dvsr, self.dvdnt, self.dvcr = values["DVSR"], values["DVDNT"], values["DVCR"]
        self.vcrdiv, self.dvdnth = values["VCRDIV"], values["DVDNTH"]
        self.dvdntl, self.busy_until = values["DVDNTL"], values["busy_until"]


__all__ = ["DIVU", "partial_64"]
