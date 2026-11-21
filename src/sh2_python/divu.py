# SPDX-License-Identifier: MIT
"""The division unit (DIVU): signed 64 ÷ 32 and 32 ÷ 32 division, HM section 10.

Rewritten 2026-11-19 from the manuals alone (docs/provenance.md). Each rule's source:

- DOC (HM 10.2.2): a write to DVDNT also writes DVDNTL, extends DVDNT's sign into all
  of DVDNTH, and starts a 32 ÷ 32 division. So 32 ÷ 32 is the 64 ÷ 32 operation on a
  sign-extended dividend, and both kinds share one path here.
- DOC (HM 10.3.1): a write to DVDNTL starts a 64 ÷ 32 division of DVDNTH:DVDNTL.
- DOC (HM 10.3.1, 10.3.2): the quotient goes to DVDNTL (and DVDNT for 32 ÷ 32), the
  remainder to DVDNTH, 39 states after the start write; the operation is signed.
- OBSERVED (MAME 0.285 ``sh7604.cpp``, BSD-3-Clause, read; jgenesis ``d41e2d9`` run as a
  closed box): the quotient rounds toward zero and the remainder takes the dividend's
  sign. HM does not say which signed convention the unit uses.
- DOC (HM 10.3.3): an overflow is a quotient outside the signed 32-bit range, a divisor
  of 0, or "in division between two negative numbers, the quotient is the maximum
  value and a remainder (negative number) is generated". The last is read literally:
  dividend < 0, divisor < 0, quotient H'7FFFFFFF with a non-zero remainder.
- DOC (HM 10.3.3, table 10.2): on an overflow OVF is set and the operation ends after
  6 states, 3 of flag setting and 3 of division. DVDNTH holds the result at that point;
  DVDNTL (and DVDNT) hold it too when OVFIE = 1, and otherwise H'7FFFFFFF for a positive
  quotient or H'80000000 for a negative one.
- HYPOTHESIS (provisional): what "the result after 6 cycles" is. Hitachi does not
  publish the unit's algorithm. ``early_state`` models it as the first three steps of
  the PM's own signed division routine (PM 6.19, example 4: a negative dividend
  decremented, DIV0S, then ROTCL low / DIV1 per step). No closed box available here
  models these values (docs/undocumented-behavior.md U12), and no hardware was run.
- HYPOTHESIS: the quotient's sign when the divisor is 0 is the dividend's (a divisor of
  0 counts as positive, as DIV0S's M bit would take it); jgenesis ``d41e2d9`` (closed
  box) saturates the same way for every case tried (U13).
- HYPOTHESIS: DVDNT holds the same quotient as DVDNTL after a 64 ÷ 32 division too
  (table 10.2 gives DVDNT DVDNTL's values in both columns).
- DOC (HM 10.4.1): an access while the unit divides is extended until it finishes.
"""

from sh2_python._arith import div1_step
from sh2_python._module import LongwordModule, Register

DIVIDE_STATES = 39  # HM 10.1.1, 10.3.1
OVERFLOW_STATES = 6  # HM 10.3.3: "3 to set flags, 3 of division"
M32 = 0xFFFFFFFF
M64 = (1 << 64) - 1


def _signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value >> (bits - 1) else value


def _trunc_div(a: int, b: int) -> tuple[int, int]:
    """Signed division toward zero; the remainder takes a's sign."""
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    return q, a - q * b


def early_state(high: int, low: int, divisor: int, steps: int = 3) -> tuple[int, int]:
    """DVDNTH and DVDNTL after an overflowing division's three division states.

    HYPOTHESIS (see the module docstring): the unit runs the PM 6.19 example-4 routine.
    A negative dividend is decremented (that routine's one's-complement step), DIV0S
    takes Q from the dividend's sign and M from the divisor's, then each step rotates
    the next dividend bit out of the low word (ROTCL) into a DIV1 on the high word.
    """
    if high >> 31:
        dividend = (((high << 32) | low) - 1) & M64
        high, low = dividend >> 32, dividend & M32
    q, m = high >> 31, divisor >> 31
    t = q ^ m  # DIV0S
    for _ in range(steps):
        t, low = low >> 31, ((low << 1) & M32) | t  # ROTCL low
        high, q, t = div1_step(high, divisor, t, q, m)
    return high, low


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
        # HM 10.2.2: DVDNT's value is also written to DVDNTL and its sign fills DVDNTH.
        self.dvdntl = self.dvdnt
        self.dvdnth = M32 if self.dvdnt >> 31 else 0
        self._divide_64()

    def _divide_64(self) -> None:
        high, low, divisor = self.dvdnth, self.dvdntl, _signed(self.dvsr, 32)
        dividend = _signed((high << 32) | low, 64)
        if divisor:
            quotient, remainder = _trunc_div(dividend, divisor)
            overflow = not -0x80000000 <= quotient <= 0x7FFFFFFF or (
                dividend < 0 and divisor < 0 and quotient == 0x7FFFFFFF and remainder != 0
            )
        else:
            overflow = True
        if not overflow:
            self.busy_until = self.chip.clock + DIVIDE_STATES
            self.dvdnth = remainder & M32
            self.dvdnt = self.dvdntl = quotient & M32
            return
        self.dvdnth, partial_low = early_state(high, low, self.dvsr & M32)
        if self.dvcr & 2:  # OVFIE: both halves hold the early result (table 10.2)
            self.dvdntl = partial_low
        else:  # the quotient's sign picks the saturated value (HM 10.3.3)
            self.dvdntl = 0x80000000 if (dividend < 0) != (divisor < 0) else 0x7FFFFFFF
        self.dvdnt = self.dvdntl
        self._overflow()

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


__all__ = ["DIVU", "early_state"]
