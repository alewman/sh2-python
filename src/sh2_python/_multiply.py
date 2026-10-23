"""The multiplier's instructions: MUL.L, MULS.W, MULU.W, DMULS.L, DMULU.L, MAC.W, MAC.L.

PM 6.20-6.21, 6.29, 6.31, 6.38-6.40. The multiplier is a unit beside the ALU (PM 7.7.2);
its results go to MACH:MACL. Its timing (the ``mm`` states, the ID stalls) is the
pipeline's business, rung 3; here are the values.

MAC with SR.S = 1 saturates, and the manuals contradict themselves about how (C11 in
docs/undocumented-behavior.md):

- ``MAC.W``: the prose of PM 6.31 and SWM says "if an overflow occurs, the LSB of the
  MACH register is set to 1"; the code on the same pages marks the two lines that do
  it "For SH-1 CPU (these 2 lines not needed for SH-2 CPU)". This core follows the
  code's SH-2 annotation: MACL saturates, MACH is unchanged. MAME and jgenesis agree;
  Ymir's vectors (origin unstated) set the bit.
- ``MAC.L``: the prose of PM 6.29 limits the result to H'FFFF8000_00000000 ..
  H'00007FFF_FFFFFFFF using "only the lower 48 bits" of MAC; the code saturates the
  negative side to H'00008000_00000000 and adds MACH's low 16 bits unsigned. This core
  follows the prose: the accumulator is MAC's low 48 bits, sign-extended, and the sum
  is clamped to the signed 48-bit range and stored sign-extended to 64 bits.
"""

M32 = 0xFFFFFFFF
S = 0x002


def _s32(value: int) -> int:
    return value - 0x100000000 if value & 0x80000000 else value


def _s16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


class MultiplyMixin:
    def _set_mac(self, value: int) -> None:
        value &= 0xFFFFFFFFFFFFFFFF
        self.mach = value >> 32
        self.macl = value & M32

    def _op_mul_l(self, op: int) -> None:
        """MUL.L Rm,Rn -- the low 32 bits of Rn x Rm -> MACL; MACH unchanged (PM 6.38)."""
        self.macl = (self.R[(op >> 8) & 15] * self.R[(op >> 4) & 15]) & M32

    def _op_muls_w(self, op: int) -> None:
        """MULS.W Rm,Rn -- signed 16 x 16 of the low words -> MACL; MACH unchanged (PM 6.39)."""
        self.macl = (_s16(self.R[(op >> 8) & 15]) * _s16(self.R[(op >> 4) & 15])) & M32

    def _op_mulu_w(self, op: int) -> None:
        """MULU.W Rm,Rn -- unsigned 16 x 16 of the low words -> MACL; MACH unchanged (PM 6.40)."""
        self.macl = ((self.R[(op >> 8) & 15] & 0xFFFF) * (self.R[(op >> 4) & 15] & 0xFFFF)) & M32

    def _op_dmuls_l(self, op: int) -> None:
        """DMULS.L Rm,Rn -- signed 32 x 32 -> the 64-bit MACH:MACL (PM 6.20)."""
        self._set_mac(_s32(self.R[(op >> 8) & 15]) * _s32(self.R[(op >> 4) & 15]))

    def _op_dmulu_l(self, op: int) -> None:
        """DMULU.L Rm,Rn -- unsigned 32 x 32 -> the 64-bit MACH:MACL (PM 6.21)."""
        self._set_mac(self.R[(op >> 8) & 15] * self.R[(op >> 4) & 15])

    def _op_mac_w(self, op: int) -> None:
        """MAC.W @Rm+,@Rn+ -- signed (Rn) x (Rm) + MAC -> MAC; Rn, Rm + 2 (PM 6.31).

        Reads @Rn first, then @Rm (PM 6.31's code). S = 0: a 64-bit accumulate. S = 1:
        MACL + product saturates to H'80000000..H'7FFFFFFF and MACH is unchanged (the
        code's SH-2 annotation; C11).
        """
        R = self.R
        n, m = (op >> 8) & 15, (op >> 4) & 15
        tn = _s16(self._read16(R[n]))
        R[n] = (R[n] + 2) & M32
        tm = _s16(self._read16(R[m]))
        R[m] = (R[m] + 2) & M32
        product = tn * tm
        if self.sr & S:
            total = _s32(self.macl) + product
            if total > 0x7FFFFFFF:
                total = 0x7FFFFFFF
            elif total < -0x80000000:
                total = -0x80000000
            self.macl = total & M32
        else:
            self._set_mac(((self.mach << 32) | self.macl) + product)

    def _op_mac_l(self, op: int) -> None:
        """MAC.L @Rm+,@Rn+ -- signed (Rn) x (Rm) + MAC -> MAC; Rn, Rm + 4 (PM 6.29).

        Reads @Rn first, then @Rm. S = 0: a 64-bit accumulate. S = 1: MAC's low 48 bits
        plus the product, clamped to the signed 48-bit range (PM 6.29's prose; C11).
        """
        R = self.R
        n, m = (op >> 8) & 15, (op >> 4) & 15
        tn = _s32(self._read32(R[n]))
        R[n] = (R[n] + 4) & M32
        tm = _s32(self._read32(R[m]))
        R[m] = (R[m] + 4) & M32
        product = tn * tm
        mac = (self.mach << 32) | self.macl
        if self.sr & S:
            low48 = mac & 0xFFFFFFFFFFFF
            if low48 & 0x800000000000:
                low48 -= 1 << 48
            total = low48 + product
            if total > 0x7FFFFFFFFFFF:
                total = 0x7FFFFFFFFFFF
            elif total < -0x800000000000:
                total = -0x800000000000
            self._set_mac(total)
        else:
            self._set_mac(mac + product)


__all__ = ["MultiplyMixin"]
