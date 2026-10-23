"""Shifts and rotates: ROTL, ROTR, ROTCL, ROTCR, SHAL, SHAR, SHLL, SHLR, SHLLn, SHLRn.

PM 6.46-6.58. The one-bit forms move the bit shifted out into T (PM 6.46-6.55, 6.57);
the multi-bit forms SHLL2/8/16 and SHLR2/8/16 leave T alone (PM 6.56, 6.58).
"""

M32 = 0xFFFFFFFF
T = 0x001


class ShiftMixin:
    def _op_rotl(self, op: int) -> None:
        """ROTL Rn -- T <- Rn <- MSB (PM 6.48)."""
        R = self.R
        n = (op >> 8) & 15
        msb = R[n] >> 31
        R[n] = ((R[n] << 1) & M32) | msb
        self.sr = (self.sr & ~T) | msb

    def _op_rotr(self, op: int) -> None:
        """ROTR Rn -- LSB -> Rn -> T (PM 6.49)."""
        R = self.R
        n = (op >> 8) & 15
        lsb = R[n] & 1
        R[n] = (R[n] >> 1) | (lsb << 31)
        self.sr = (self.sr & ~T) | lsb

    def _op_rotcl(self, op: int) -> None:
        """ROTCL Rn -- T <- Rn <- T (PM 6.46)."""
        R = self.R
        n = (op >> 8) & 15
        msb = R[n] >> 31
        R[n] = ((R[n] << 1) & M32) | (self.sr & T)
        self.sr = (self.sr & ~T) | msb

    def _op_rotcr(self, op: int) -> None:
        """ROTCR Rn -- T -> Rn -> T (PM 6.47)."""
        R = self.R
        n = (op >> 8) & 15
        lsb = R[n] & 1
        R[n] = (R[n] >> 1) | ((self.sr & T) << 31)
        self.sr = (self.sr & ~T) | lsb

    def _op_shal(self, op: int) -> None:
        """SHAL Rn -- T <- Rn <- 0 (PM 6.53)."""
        R = self.R
        n = (op >> 8) & 15
        self.sr = (self.sr & ~T) | (R[n] >> 31)
        R[n] = (R[n] << 1) & M32

    def _op_shar(self, op: int) -> None:
        """SHAR Rn -- MSB -> Rn -> T (PM 6.54)."""
        R = self.R
        n = (op >> 8) & 15
        v = R[n]
        self.sr = (self.sr & ~T) | (v & 1)
        R[n] = (v >> 1) | (v & 0x80000000)

    def _op_shll(self, op: int) -> None:
        """SHLL Rn -- T <- Rn <- 0 (PM 6.55)."""
        R = self.R
        n = (op >> 8) & 15
        self.sr = (self.sr & ~T) | (R[n] >> 31)
        R[n] = (R[n] << 1) & M32

    def _op_shlr(self, op: int) -> None:
        """SHLR Rn -- 0 -> Rn -> T (PM 6.57)."""
        R = self.R
        n = (op >> 8) & 15
        self.sr = (self.sr & ~T) | (R[n] & 1)
        R[n] >>= 1

    def _op_shll2(self, op: int) -> None:
        """SHLL2 Rn -- Rn << 2 -> Rn (PM 6.56)."""
        n = (op >> 8) & 15
        self.R[n] = (self.R[n] << 2) & M32

    def _op_shll8(self, op: int) -> None:
        """SHLL8 Rn -- Rn << 8 -> Rn (PM 6.56)."""
        n = (op >> 8) & 15
        self.R[n] = (self.R[n] << 8) & M32

    def _op_shll16(self, op: int) -> None:
        """SHLL16 Rn -- Rn << 16 -> Rn (PM 6.56)."""
        n = (op >> 8) & 15
        self.R[n] = (self.R[n] << 16) & M32

    def _op_shlr2(self, op: int) -> None:
        """SHLR2 Rn -- Rn >> 2 -> Rn (PM 6.58)."""
        self.R[(op >> 8) & 15] >>= 2

    def _op_shlr8(self, op: int) -> None:
        """SHLR8 Rn -- Rn >> 8 -> Rn (PM 6.58)."""
        self.R[(op >> 8) & 15] >>= 8

    def _op_shlr16(self, op: int) -> None:
        """SHLR16 Rn -- Rn >> 16 -> Rn (PM 6.58)."""
        self.R[(op >> 8) & 15] >>= 16


__all__ = ["ShiftMixin"]
