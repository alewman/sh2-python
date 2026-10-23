"""Logic operations: AND, OR, XOR, NOT, TST, their GBR-indexed memory forms, and TAS.B.

PM 6.5, 6.44, 6.45, 6.66, 6.68, 6.69. The ``#imm`` forms take the 8-bit immediate
zero-extended (PM 6.5: "#imm ... zero-extended"). The ``.B #imm,@(R0,GBR)`` forms read
the byte at GBR + R0, operate, and (except TST.B) write it back: a read-modify-write
of two bus cycles (PM 7.7.3, figure 7.75).

``TAS.B @Rn`` reads the byte "cache-through even when the cache area is accessed",
sets T when it is 0, and writes it back with bit 7 set, updating a cache line that
holds it (HM 8.4.4, figure 8.7); on the bus it is a read then a write.
"""

from sh2_python.bus import THROUGH, WRITE

M32 = 0xFFFFFFFF
T = 0x001


class LogicMixin:
    def _op_and(self, op: int) -> None:
        """AND Rm,Rn -- Rn & Rm -> Rn (PM 6.5)."""
        self.R[(op >> 8) & 15] &= self.R[(op >> 4) & 15]

    def _op_and_imm(self, op: int) -> None:
        """AND #imm,R0 -- R0 & the zero-extended immediate -> R0 (PM 6.5)."""
        self.R[0] &= op & 0xFF

    def _op_and_b(self, op: int) -> None:
        """AND.B #imm,@(R0,GBR) -- (R0 + GBR) & imm -> (R0 + GBR) (PM 6.5)."""
        address = (self.R[0] + self.gbr) & M32
        self._write8(address, self._read8(address) & (op & 0xFF))

    def _op_or(self, op: int) -> None:
        """OR Rm,Rn -- Rn | Rm -> Rn (PM 6.45)."""
        self.R[(op >> 8) & 15] |= self.R[(op >> 4) & 15]

    def _op_or_imm(self, op: int) -> None:
        """OR #imm,R0 -- R0 | the zero-extended immediate -> R0 (PM 6.45)."""
        self.R[0] |= op & 0xFF

    def _op_or_b(self, op: int) -> None:
        """OR.B #imm,@(R0,GBR) -- (R0 + GBR) | imm -> (R0 + GBR) (PM 6.45)."""
        address = (self.R[0] + self.gbr) & M32
        self._write8(address, self._read8(address) | (op & 0xFF))

    def _op_xor(self, op: int) -> None:
        """XOR Rm,Rn -- Rn ^ Rm -> Rn (PM 6.69)."""
        self.R[(op >> 8) & 15] ^= self.R[(op >> 4) & 15]

    def _op_xor_imm(self, op: int) -> None:
        """XOR #imm,R0 -- R0 ^ the zero-extended immediate -> R0 (PM 6.69)."""
        self.R[0] ^= op & 0xFF

    def _op_xor_b(self, op: int) -> None:
        """XOR.B #imm,@(R0,GBR) -- (R0 + GBR) ^ imm -> (R0 + GBR) (PM 6.69)."""
        address = (self.R[0] + self.gbr) & M32
        self._write8(address, self._read8(address) ^ (op & 0xFF))

    def _op_not(self, op: int) -> None:
        """NOT Rm,Rn -- ~Rm -> Rn (PM 6.44)."""
        self.R[(op >> 8) & 15] = ~self.R[(op >> 4) & 15] & M32

    def _op_tst(self, op: int) -> None:
        """TST Rm,Rn -- T = 1 when Rn & Rm is 0 (PM 6.68)."""
        zero = not self.R[(op >> 8) & 15] & self.R[(op >> 4) & 15]
        self.sr = (self.sr | T) if zero else (self.sr & ~T)

    def _op_tst_imm(self, op: int) -> None:
        """TST #imm,R0 -- T = 1 when R0 & the zero-extended immediate is 0 (PM 6.68)."""
        zero = not self.R[0] & op & 0xFF
        self.sr = (self.sr | T) if zero else (self.sr & ~T)

    def _op_tst_b(self, op: int) -> None:
        """TST.B #imm,@(R0,GBR) -- T = 1 when (R0 + GBR) & imm is 0 (PM 6.68)."""
        zero = not self._read8((self.R[0] + self.gbr) & M32) & op & 0xFF
        self.sr = (self.sr | T) if zero else (self.sr & ~T)

    def _op_tas_b(self, op: int) -> None:
        """TAS.B @Rn -- T = 1 when (Rn) is 0; bit 7 of (Rn) set (PM 6.66; HM 8.4.4)."""
        address = self.R[(op >> 8) & 15]
        space = address >> 29
        if space in (0, 1):
            # HM 8.4.4: the read is cache-through whatever the area; the write goes
            # through and updates a hit line. Each is one of its MAs (PM figure 7.76).
            if self._p_ma_left:
                self._ma_next()
            if self.observe is not None:
                self.observe(address, 8, THROUGH)
            value = self._bus_read(address, 8, THROUGH)
            if self._p_ma_left:
                self._ma_next()
            if self.observe is not None:
                self.observe(address, 8, WRITE)
            if space == 0 and self.cache.enabled:
                self.cache.write_hit(address, 8, value | 0x80)
            self._bus_write(address, 8, value | 0x80, THROUGH)
        else:
            # HM table 4.6: TAS.B on the purge, address-array, data-array or I/O
            # spaces is an address error.
            self._address_error = True
            value = 1
        self.sr = (self.sr | T) if value == 0 else (self.sr & ~T)


__all__ = ["LogicMixin"]
