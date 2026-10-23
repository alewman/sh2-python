"""Data transfer instructions: MOV in all its addressing modes, MOVA, MOVT, SWAP, XTRCT.

PM 6.32-6.37, 6.65, 6.70. Loads of bytes and words sign-extend into the register
(PM 6.32: "the loaded data is sign-extended to 32 bits"). The ``@Rm+`` loads increment
Rm after the read and the ``@-Rn`` stores decrement Rn before the write; when the
loaded register is the address register itself (``MOV.L @Rm+,Rm``) the loaded value
wins (PM 6.32's code: ``R[n]=...; if (n!=m) R[m]+=4;``). A store of the address
register through ``@-Rn`` (``MOV.L Rn,@-Rn``) stores its value from before the
decrement (PM 6.32's code: ``Write_Long(R[n]-4,R[m]); R[n]-=4;``).

PC-relative forms use ``self.pc + 2``: the instruction's address + 4, or the branch
target + 2 in a delay slot (U1). The longword forms clear the PC's low two bits first
(PM 6.33: "(disp x 4 + PC) & H'FFFFFFFC" is written ``PC & 0xFFFFFFFC``).
"""

M32 = 0xFFFFFFFF


def _sext8(value: int) -> int:
    return (value | 0xFFFFFF00) if value & 0x80 else value


def _sext16(value: int) -> int:
    return (value | 0xFFFF0000) if value & 0x8000 else value


class TransferMixin:
    # -- register to register ------------------------------------------------------

    def _op_mov(self, op: int) -> None:
        """MOV Rm,Rn -- Rm -> Rn (PM 6.32)."""
        self.R[(op >> 8) & 15] = self.R[(op >> 4) & 15]

    def _op_mov_imm(self, op: int) -> None:
        """MOV #imm,Rn -- the sign-extended 8-bit immediate -> Rn (PM 6.33)."""
        self.R[(op >> 8) & 15] = _sext8(op & 0xFF)

    def _op_mova(self, op: int) -> None:
        """MOVA @(disp,PC),R0 -- disp x 4 + (PC & ~3) -> R0 (PM 6.36; U1 in a slot)."""
        self.R[0] = (((self.pc + 2) & ~3) + ((op & 0xFF) << 2)) & M32

    def _op_movt(self, op: int) -> None:
        """MOVT Rn -- T -> Rn (PM 6.37)."""
        self.R[(op >> 8) & 15] = self.sr & 1

    def _op_swap_b(self, op: int) -> None:
        """SWAP.B Rm,Rn -- Rm with the two bytes of its lower word swapped -> Rn (PM 6.65)."""
        v = self.R[(op >> 4) & 15]
        self.R[(op >> 8) & 15] = (v & 0xFFFF0000) | ((v & 0xFF) << 8) | ((v >> 8) & 0xFF)

    def _op_swap_w(self, op: int) -> None:
        """SWAP.W Rm,Rn -- Rm with its two words swapped -> Rn (PM 6.65)."""
        v = self.R[(op >> 4) & 15]
        self.R[(op >> 8) & 15] = ((v & 0xFFFF) << 16) | (v >> 16)

    def _op_xtrct(self, op: int) -> None:
        """XTRCT Rm,Rn -- the middle 32 bits of Rm:Rn -> Rn (PM 6.70)."""
        R = self.R
        n = (op >> 8) & 15
        R[n] = ((R[(op >> 4) & 15] & 0xFFFF) << 16) | (R[n] >> 16)

    # -- loads --------------------------------------------------------------------------

    def _op_mov_w_load_pc(self, op: int) -> None:
        """MOV.W @(disp,PC),Rn -- the word at disp x 2 + PC, sign-extended -> Rn (PM 6.33)."""
        address = (self.pc + 2 + ((op & 0xFF) << 1)) & M32
        self.R[(op >> 8) & 15] = _sext16(self._read16(address))

    def _op_mov_l_load_pc(self, op: int) -> None:
        """MOV.L @(disp,PC),Rn -- the longword at disp x 4 + (PC & ~3) -> Rn (PM 6.33)."""
        address = (((self.pc + 2) & ~3) + ((op & 0xFF) << 2)) & M32
        self.R[(op >> 8) & 15] = self._read32(address)

    def _op_mov_b_load(self, op: int) -> None:
        """MOV.B @Rm,Rn -- the byte at Rm, sign-extended -> Rn (PM 6.32)."""
        self.R[(op >> 8) & 15] = _sext8(self._read8(self.R[(op >> 4) & 15]))

    def _op_mov_w_load(self, op: int) -> None:
        """MOV.W @Rm,Rn -- the word at Rm, sign-extended -> Rn (PM 6.32)."""
        self.R[(op >> 8) & 15] = _sext16(self._read16(self.R[(op >> 4) & 15]))

    def _op_mov_l_load(self, op: int) -> None:
        """MOV.L @Rm,Rn -- the longword at Rm -> Rn (PM 6.32)."""
        self.R[(op >> 8) & 15] = self._read32(self.R[(op >> 4) & 15])

    def _op_mov_b_load_postinc(self, op: int) -> None:
        """MOV.B @Rm+,Rn -- the byte at Rm, sign-extended -> Rn; Rm + 1 -> Rm (PM 6.32)."""
        R = self.R
        n, m = (op >> 8) & 15, (op >> 4) & 15
        value = _sext8(self._read8(R[m]))
        if n != m:
            R[m] = (R[m] + 1) & M32
        R[n] = value

    def _op_mov_w_load_postinc(self, op: int) -> None:
        """MOV.W @Rm+,Rn -- the word at Rm, sign-extended -> Rn; Rm + 2 -> Rm (PM 6.32)."""
        R = self.R
        n, m = (op >> 8) & 15, (op >> 4) & 15
        value = _sext16(self._read16(R[m]))
        if n != m:
            R[m] = (R[m] + 2) & M32
        R[n] = value

    def _op_mov_l_load_postinc(self, op: int) -> None:
        """MOV.L @Rm+,Rn -- the longword at Rm -> Rn; Rm + 4 -> Rm (PM 6.32)."""
        R = self.R
        n, m = (op >> 8) & 15, (op >> 4) & 15
        value = self._read32(R[m])
        if n != m:
            R[m] = (R[m] + 4) & M32
        R[n] = value

    def _op_mov_b_load_disp(self, op: int) -> None:
        """MOV.B @(disp,Rm),R0 -- the byte at disp + Rm, sign-extended -> R0 (PM 6.35)."""
        address = (self.R[(op >> 4) & 15] + (op & 15)) & M32
        self.R[0] = _sext8(self._read8(address))

    def _op_mov_w_load_disp(self, op: int) -> None:
        """MOV.W @(disp,Rm),R0 -- the word at disp x 2 + Rm, sign-extended -> R0 (PM 6.35)."""
        address = (self.R[(op >> 4) & 15] + ((op & 15) << 1)) & M32
        self.R[0] = _sext16(self._read16(address))

    def _op_mov_l_load_disp(self, op: int) -> None:
        """MOV.L @(disp,Rm),Rn -- the longword at disp x 4 + Rm -> Rn (PM 6.35)."""
        address = (self.R[(op >> 4) & 15] + ((op & 15) << 2)) & M32
        self.R[(op >> 8) & 15] = self._read32(address)

    def _op_mov_b_load_indexed(self, op: int) -> None:
        """MOV.B @(R0,Rm),Rn -- the byte at R0 + Rm, sign-extended -> Rn (PM 6.32)."""
        R = self.R
        R[(op >> 8) & 15] = _sext8(self._read8((R[0] + R[(op >> 4) & 15]) & M32))

    def _op_mov_w_load_indexed(self, op: int) -> None:
        """MOV.W @(R0,Rm),Rn -- the word at R0 + Rm, sign-extended -> Rn (PM 6.32)."""
        R = self.R
        R[(op >> 8) & 15] = _sext16(self._read16((R[0] + R[(op >> 4) & 15]) & M32))

    def _op_mov_l_load_indexed(self, op: int) -> None:
        """MOV.L @(R0,Rm),Rn -- the longword at R0 + Rm -> Rn (PM 6.32)."""
        R = self.R
        R[(op >> 8) & 15] = self._read32((R[0] + R[(op >> 4) & 15]) & M32)

    def _op_mov_b_load_gbr(self, op: int) -> None:
        """MOV.B @(disp,GBR),R0 -- the byte at disp + GBR, sign-extended -> R0 (PM 6.34)."""
        self.R[0] = _sext8(self._read8((self.gbr + (op & 0xFF)) & M32))

    def _op_mov_w_load_gbr(self, op: int) -> None:
        """MOV.W @(disp,GBR),R0 -- the word at disp x 2 + GBR, sign-extended -> R0 (PM 6.34)."""
        self.R[0] = _sext16(self._read16((self.gbr + ((op & 0xFF) << 1)) & M32))

    def _op_mov_l_load_gbr(self, op: int) -> None:
        """MOV.L @(disp,GBR),R0 -- the longword at disp x 4 + GBR -> R0 (PM 6.34)."""
        self.R[0] = self._read32((self.gbr + ((op & 0xFF) << 2)) & M32)

    # -- stores ---------------------------------------------------------------------------

    def _op_mov_b_store(self, op: int) -> None:
        """MOV.B Rm,@Rn -- the low byte of Rm -> the byte at Rn (PM 6.32)."""
        self._write8(self.R[(op >> 8) & 15], self.R[(op >> 4) & 15])

    def _op_mov_w_store(self, op: int) -> None:
        """MOV.W Rm,@Rn -- the low word of Rm -> the word at Rn (PM 6.32)."""
        self._write16(self.R[(op >> 8) & 15], self.R[(op >> 4) & 15])

    def _op_mov_l_store(self, op: int) -> None:
        """MOV.L Rm,@Rn -- Rm -> the longword at Rn (PM 6.32)."""
        self._write32(self.R[(op >> 8) & 15], self.R[(op >> 4) & 15])

    def _op_mov_b_store_predec(self, op: int) -> None:
        """MOV.B Rm,@-Rn -- Rn - 1 -> Rn; the low byte of Rm -> the byte at Rn (PM 6.32)."""
        R = self.R
        n = (op >> 8) & 15
        value = R[(op >> 4) & 15]
        R[n] = (R[n] - 1) & M32
        self._write8(R[n], value)

    def _op_mov_w_store_predec(self, op: int) -> None:
        """MOV.W Rm,@-Rn -- Rn - 2 -> Rn; the low word of Rm -> the word at Rn (PM 6.32)."""
        R = self.R
        n = (op >> 8) & 15
        value = R[(op >> 4) & 15]
        R[n] = (R[n] - 2) & M32
        self._write16(R[n], value)

    def _op_mov_l_store_predec(self, op: int) -> None:
        """MOV.L Rm,@-Rn -- Rn - 4 -> Rn; Rm -> the longword at Rn (PM 6.32)."""
        R = self.R
        n = (op >> 8) & 15
        value = R[(op >> 4) & 15]
        R[n] = (R[n] - 4) & M32
        self._write32(R[n], value)

    def _op_mov_b_store_disp(self, op: int) -> None:
        """MOV.B R0,@(disp,Rn) -- the low byte of R0 -> the byte at disp + Rn (PM 6.35)."""
        self._write8((self.R[(op >> 4) & 15] + (op & 15)) & M32, self.R[0])

    def _op_mov_w_store_disp(self, op: int) -> None:
        """MOV.W R0,@(disp,Rn) -- the low word of R0 -> the word at disp x 2 + Rn (PM 6.35)."""
        self._write16((self.R[(op >> 4) & 15] + ((op & 15) << 1)) & M32, self.R[0])

    def _op_mov_l_store_disp(self, op: int) -> None:
        """MOV.L Rm,@(disp,Rn) -- Rm -> the longword at disp x 4 + Rn (PM 6.35)."""
        address = (self.R[(op >> 8) & 15] + ((op & 15) << 2)) & M32
        self._write32(address, self.R[(op >> 4) & 15])

    def _op_mov_b_store_indexed(self, op: int) -> None:
        """MOV.B Rm,@(R0,Rn) -- the low byte of Rm -> the byte at R0 + Rn (PM 6.32)."""
        R = self.R
        self._write8((R[0] + R[(op >> 8) & 15]) & M32, R[(op >> 4) & 15])

    def _op_mov_w_store_indexed(self, op: int) -> None:
        """MOV.W Rm,@(R0,Rn) -- the low word of Rm -> the word at R0 + Rn (PM 6.32)."""
        R = self.R
        self._write16((R[0] + R[(op >> 8) & 15]) & M32, R[(op >> 4) & 15])

    def _op_mov_l_store_indexed(self, op: int) -> None:
        """MOV.L Rm,@(R0,Rn) -- Rm -> the longword at R0 + Rn (PM 6.32)."""
        R = self.R
        self._write32((R[0] + R[(op >> 8) & 15]) & M32, R[(op >> 4) & 15])

    def _op_mov_b_store_gbr(self, op: int) -> None:
        """MOV.B R0,@(disp,GBR) -- the low byte of R0 -> the byte at disp + GBR (PM 6.34)."""
        self._write8((self.gbr + (op & 0xFF)) & M32, self.R[0])

    def _op_mov_w_store_gbr(self, op: int) -> None:
        """MOV.W R0,@(disp,GBR) -- the low word of R0 -> the word at disp x 2 + GBR (PM 6.34)."""
        self._write16((self.gbr + ((op & 0xFF) << 1)) & M32, self.R[0])

    def _op_mov_l_store_gbr(self, op: int) -> None:
        """MOV.L R0,@(disp,GBR) -- R0 -> the longword at disp x 4 + GBR (PM 6.34)."""
        self._write32((self.gbr + ((op & 0xFF) << 2)) & M32, self.R[0])


__all__ = ["TransferMixin"]
