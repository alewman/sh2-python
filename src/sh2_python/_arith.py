"""Arithmetic between registers: ADD, SUB and their carry/overflow forms, CMP, DIV0/DIV1, DT,
EXTS/EXTU, NEG/NEGC.

PM 6.2-6.4, 6.16-6.19, 6.22-6.24, 6.41-6.42, 6.62-6.64 (the multiplier's instructions are
in ``_multiply.py``). T is SR bit 0; Q is bit 8 and M bit 9 (PM 2.2). Every result is a
32-bit two's-complement value; comparisons read their operands as signed or unsigned
as the mnemonic says (PM 6.16, table 6.x of CMP/cond).
"""

M32 = 0xFFFFFFFF
T, Q, M = 0x001, 0x100, 0x200


def _s32(value: int) -> int:
    return value - 0x100000000 if value & 0x80000000 else value


class ArithMixin:
    def _set_t(self, condition: bool) -> None:
        self.sr = (self.sr | T) if condition else (self.sr & ~T)

    # -- addition, subtraction -----------------------------------------------------

    def _op_add(self, op: int) -> None:
        """ADD Rm,Rn -- Rn + Rm -> Rn (PM 6.2)."""
        R = self.R
        n = (op >> 8) & 15
        R[n] = (R[n] + R[(op >> 4) & 15]) & M32

    def _op_add_imm(self, op: int) -> None:
        """ADD #imm,Rn -- Rn + the sign-extended 8-bit immediate -> Rn (PM 6.2)."""
        R = self.R
        n = (op >> 8) & 15
        imm = op & 0xFF
        R[n] = (R[n] + (imm - 0x100 if imm & 0x80 else imm)) & M32

    def _op_addc(self, op: int) -> None:
        """ADDC Rm,Rn -- Rn + Rm + T -> Rn, the carry -> T (PM 6.3)."""
        R = self.R
        n = (op >> 8) & 15
        total = R[n] + R[(op >> 4) & 15] + (self.sr & T)
        R[n] = total & M32
        self._set_t(total > M32)

    def _op_addv(self, op: int) -> None:
        """ADDV Rm,Rn -- Rn + Rm -> Rn, signed overflow -> T (PM 6.4)."""
        R = self.R
        n = (op >> 8) & 15
        total = _s32(R[n]) + _s32(R[(op >> 4) & 15])
        R[n] = total & M32
        self._set_t(not -0x80000000 <= total <= 0x7FFFFFFF)

    def _op_sub(self, op: int) -> None:
        """SUB Rm,Rn -- Rn - Rm -> Rn (PM 6.62)."""
        R = self.R
        n = (op >> 8) & 15
        R[n] = (R[n] - R[(op >> 4) & 15]) & M32

    def _op_subc(self, op: int) -> None:
        """SUBC Rm,Rn -- Rn - Rm - T -> Rn, the borrow -> T (PM 6.63)."""
        R = self.R
        n = (op >> 8) & 15
        difference = R[n] - R[(op >> 4) & 15] - (self.sr & T)
        R[n] = difference & M32
        self._set_t(difference < 0)

    def _op_subv(self, op: int) -> None:
        """SUBV Rm,Rn -- Rn - Rm -> Rn, signed underflow -> T (PM 6.64)."""
        R = self.R
        n = (op >> 8) & 15
        difference = _s32(R[n]) - _s32(R[(op >> 4) & 15])
        R[n] = difference & M32
        self._set_t(not -0x80000000 <= difference <= 0x7FFFFFFF)

    def _op_neg(self, op: int) -> None:
        """NEG Rm,Rn -- 0 - Rm -> Rn (PM 6.41)."""
        self.R[(op >> 8) & 15] = -self.R[(op >> 4) & 15] & M32

    def _op_negc(self, op: int) -> None:
        """NEGC Rm,Rn -- 0 - Rm - T -> Rn, the borrow -> T (PM 6.42)."""
        difference = -self.R[(op >> 4) & 15] - (self.sr & T)
        self.R[(op >> 8) & 15] = difference & M32
        self._set_t(difference < 0)

    def _op_dt(self, op: int) -> None:
        """DT Rn -- Rn - 1 -> Rn; T = 1 when the result is 0 (PM 6.22)."""
        R = self.R
        n = (op >> 8) & 15
        R[n] = (R[n] - 1) & M32
        self._set_t(R[n] == 0)

    # -- comparisons (PM 6.16) ----------------------------------------------------------

    def _op_cmp_eq(self, op: int) -> None:
        """CMP/EQ Rm,Rn -- T = 1 when Rn = Rm (PM 6.16)."""
        self._set_t(self.R[(op >> 8) & 15] == self.R[(op >> 4) & 15])

    def _op_cmp_eq_imm(self, op: int) -> None:
        """CMP/EQ #imm,R0 -- T = 1 when R0 = the sign-extended immediate (PM 6.16)."""
        imm = op & 0xFF
        self._set_t(self.R[0] == ((imm | 0xFFFFFF00) if imm & 0x80 else imm))

    def _op_cmp_hs(self, op: int) -> None:
        """CMP/HS Rm,Rn -- T = 1 when Rn >= Rm, unsigned (PM 6.16)."""
        self._set_t(self.R[(op >> 8) & 15] >= self.R[(op >> 4) & 15])

    def _op_cmp_ge(self, op: int) -> None:
        """CMP/GE Rm,Rn -- T = 1 when Rn >= Rm, signed (PM 6.16)."""
        self._set_t(_s32(self.R[(op >> 8) & 15]) >= _s32(self.R[(op >> 4) & 15]))

    def _op_cmp_hi(self, op: int) -> None:
        """CMP/HI Rm,Rn -- T = 1 when Rn > Rm, unsigned (PM 6.16)."""
        self._set_t(self.R[(op >> 8) & 15] > self.R[(op >> 4) & 15])

    def _op_cmp_gt(self, op: int) -> None:
        """CMP/GT Rm,Rn -- T = 1 when Rn > Rm, signed (PM 6.16)."""
        self._set_t(_s32(self.R[(op >> 8) & 15]) > _s32(self.R[(op >> 4) & 15]))

    def _op_cmp_pz(self, op: int) -> None:
        """CMP/PZ Rn -- T = 1 when Rn >= 0, signed (PM 6.16)."""
        self._set_t(not self.R[(op >> 8) & 15] & 0x80000000)

    def _op_cmp_pl(self, op: int) -> None:
        """CMP/PL Rn -- T = 1 when Rn > 0, signed (PM 6.16)."""
        value = self.R[(op >> 8) & 15]
        self._set_t(value != 0 and not value & 0x80000000)

    def _op_cmp_str(self, op: int) -> None:
        """CMP/STR Rm,Rn -- T = 1 when any byte of Rn equals the same byte of Rm (PM 6.16)."""
        x = self.R[(op >> 8) & 15] ^ self.R[(op >> 4) & 15]
        self._set_t(
            not x & 0xFF000000 or not x & 0x00FF0000 or not x & 0x0000FF00 or not x & 0x000000FF
        )

    # -- extension -----------------------------------------------------------------------

    def _op_exts_b(self, op: int) -> None:
        """EXTS.B Rm,Rn -- Rm's low byte, sign-extended -> Rn (PM 6.23)."""
        v = self.R[(op >> 4) & 15] & 0xFF
        self.R[(op >> 8) & 15] = (v | 0xFFFFFF00) if v & 0x80 else v

    def _op_exts_w(self, op: int) -> None:
        """EXTS.W Rm,Rn -- Rm's low word, sign-extended -> Rn (PM 6.23)."""
        v = self.R[(op >> 4) & 15] & 0xFFFF
        self.R[(op >> 8) & 15] = (v | 0xFFFF0000) if v & 0x8000 else v

    def _op_extu_b(self, op: int) -> None:
        """EXTU.B Rm,Rn -- Rm's low byte, zero-extended -> Rn (PM 6.24)."""
        self.R[(op >> 8) & 15] = self.R[(op >> 4) & 15] & 0xFF

    def _op_extu_w(self, op: int) -> None:
        """EXTU.W Rm,Rn -- Rm's low word, zero-extended -> Rn (PM 6.24)."""
        self.R[(op >> 8) & 15] = self.R[(op >> 4) & 15] & 0xFFFF

    # -- division steps ----------------------------------------------------------------

    def _op_div0s(self, op: int) -> None:
        """DIV0S Rm,Rn -- Rn's MSB -> Q, Rm's MSB -> M, M xor Q -> T (PM 6.17)."""
        q = self.R[(op >> 8) & 15] >> 31
        m = self.R[(op >> 4) & 15] >> 31
        self.sr = (self.sr & ~(T | Q | M)) | (q << 8) | (m << 9) | (q ^ m)

    def _op_div0u(self, op: int) -> None:
        """DIV0U -- 0 -> M, Q, T (PM 6.18)."""
        self.sr &= ~(T | Q | M)

    def _op_div1(self, op: int) -> None:
        """DIV1 Rm,Rn -- one step of the non-restoring division of Rn by Rm (PM 6.19).

        PM 6.19's code, transcribed: shift Rn left taking T in; subtract Rm when the old
        Q equals M, else add it; the new Q is the shifted-out bit, flipped by the
        carry or borrow as the four cases say; T = (Q == M).
        """
        R = self.R
        n, m = (op >> 8) & 15, (op >> 4) & 15
        sr = self.sr
        old_q = (sr >> 8) & 1
        mm = (sr >> 9) & 1
        rn = R[n]
        q = rn >> 31
        rn = ((rn << 1) & M32) | (sr & T)
        # PM's code shifts R[n] in place before reading R[m]: DIV1 Rn,Rn divides by the
        # shifted value (MAME agrees; jgenesis reads Rm first: overrides.json).
        rm = rn if n == m else R[m]
        if old_q == mm:
            # Subtract; tmp1 is the borrow (R[n] > tmp0 after the subtraction).
            result = (rn - rm) & M32
            carry = result > rn
        else:
            # Add; tmp1 is the carry (R[n] < tmp0 after the addition).
            result = (rn + rm) & M32
            carry = result < rn
        # The new Q from the shifted-out bit and the carry/borrow, in PM's four cases.
        if old_q == 0 and mm == 0:
            q = carry if q == 0 else (not carry)
        elif old_q == 0 and mm == 1:
            q = (not carry) if q == 0 else carry
        elif old_q == 1 and mm == 0:
            q = carry if q == 0 else (not carry)
        else:
            q = (not carry) if q == 0 else carry
        q = int(bool(q))
        R[n] = result
        self.sr = (sr & ~(T | Q)) | (q << 8) | int(q == mm)


__all__ = ["ArithMixin"]
