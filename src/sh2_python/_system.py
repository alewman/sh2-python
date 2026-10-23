"""System control: CLRT, SETT, CLRMAC, NOP, LDC, LDS, STC, STS (and their .L forms), RTE,
SLEEP, TRAPA.

PM 6.14-6.15, 6.27-6.28, 6.43, 6.50, 6.52, 6.59-6.61, 6.67. SR keeps only its defined
bits, M Q I3-I0 S T (``SR_MASK``; PM 2.2: the others "always read 0 ... write 0").
After LDC, LDC.L, STC, STC.L, LDS, LDS.L, STS and STS.L no interrupt is accepted before
the next instruction (HM 4.6.2); ``cpu.py`` marks them in ``_blocks_interrupt`` and
``step()`` applies it, so the handlers here need not.
"""

M32 = 0xFFFFFFFF
SR_MASK = 0x3F3
T = 0x001


class SystemMixin:
    def _op_clrt(self, op: int) -> None:
        """CLRT -- 0 -> T (PM 6.15)."""
        self.sr &= ~T

    def _op_sett(self, op: int) -> None:
        """SETT -- 1 -> T (PM 6.52)."""
        self.sr |= T

    def _op_clrmac(self, op: int) -> None:
        """CLRMAC -- 0 -> MACH, MACL (PM 6.14)."""
        self.mach = self.macl = 0

    def _op_nop(self, op: int) -> None:
        """NOP -- no operation (PM 6.43)."""

    # -- LDC / STC: SR, GBR, VBR (PM 6.27, 6.60) ------------------------------------

    def _op_ldc_sr(self, op: int) -> None:
        """LDC Rm,SR -- Rm -> SR (PM 6.27)."""
        self.sr = self.R[(op >> 8) & 15] & SR_MASK

    def _op_ldc_gbr(self, op: int) -> None:
        """LDC Rm,GBR -- Rm -> GBR (PM 6.27)."""
        self.gbr = self.R[(op >> 8) & 15]

    def _op_ldc_vbr(self, op: int) -> None:
        """LDC Rm,VBR -- Rm -> VBR (PM 6.27)."""
        self.vbr = self.R[(op >> 8) & 15]

    def _op_ldc_l_sr(self, op: int) -> None:
        """LDC.L @Rm+,SR -- (Rm) -> SR; Rm + 4 -> Rm (PM 6.27)."""
        R = self.R
        m = (op >> 8) & 15
        value = self._read32(R[m])
        R[m] = (R[m] + 4) & M32
        self.sr = value & SR_MASK

    def _op_ldc_l_gbr(self, op: int) -> None:
        """LDC.L @Rm+,GBR -- (Rm) -> GBR; Rm + 4 -> Rm (PM 6.27)."""
        R = self.R
        m = (op >> 8) & 15
        value = self._read32(R[m])
        R[m] = (R[m] + 4) & M32
        self.gbr = value

    def _op_ldc_l_vbr(self, op: int) -> None:
        """LDC.L @Rm+,VBR -- (Rm) -> VBR; Rm + 4 -> Rm (PM 6.27)."""
        R = self.R
        m = (op >> 8) & 15
        value = self._read32(R[m])
        R[m] = (R[m] + 4) & M32
        self.vbr = value

    def _op_stc_sr(self, op: int) -> None:
        """STC SR,Rn -- SR -> Rn (PM 6.60)."""
        self.R[(op >> 8) & 15] = self.sr

    def _op_stc_gbr(self, op: int) -> None:
        """STC GBR,Rn -- GBR -> Rn (PM 6.60)."""
        self.R[(op >> 8) & 15] = self.gbr

    def _op_stc_vbr(self, op: int) -> None:
        """STC VBR,Rn -- VBR -> Rn (PM 6.60)."""
        self.R[(op >> 8) & 15] = self.vbr

    def _push(self, op: int, value: int) -> None:
        R = self.R
        n = (op >> 8) & 15
        R[n] = (R[n] - 4) & M32
        self._write32(R[n], value)

    def _op_stc_l_sr(self, op: int) -> None:
        """STC.L SR,@-Rn -- Rn - 4 -> Rn; SR -> (Rn) (PM 6.60)."""
        self._push(op, self.sr)

    def _op_stc_l_gbr(self, op: int) -> None:
        """STC.L GBR,@-Rn -- Rn - 4 -> Rn; GBR -> (Rn) (PM 6.60)."""
        self._push(op, self.gbr)

    def _op_stc_l_vbr(self, op: int) -> None:
        """STC.L VBR,@-Rn -- Rn - 4 -> Rn; VBR -> (Rn) (PM 6.60)."""
        self._push(op, self.vbr)

    # -- LDS / STS: MACH, MACL, PR (PM 6.28, 6.61) -------------------------------------

    def _op_lds_mach(self, op: int) -> None:
        """LDS Rm,MACH -- Rm -> MACH (PM 6.28)."""
        self.mach = self.R[(op >> 8) & 15]

    def _op_lds_macl(self, op: int) -> None:
        """LDS Rm,MACL -- Rm -> MACL (PM 6.28)."""
        self.macl = self.R[(op >> 8) & 15]

    def _op_lds_pr(self, op: int) -> None:
        """LDS Rm,PR -- Rm -> PR (PM 6.28)."""
        self.pr = self.R[(op >> 8) & 15]

    def _pop(self, op: int) -> int:
        R = self.R
        m = (op >> 8) & 15
        value = self._read32(R[m])
        R[m] = (R[m] + 4) & M32
        return value

    def _op_lds_l_mach(self, op: int) -> None:
        """LDS.L @Rm+,MACH -- (Rm) -> MACH; Rm + 4 -> Rm (PM 6.28)."""
        self.mach = self._pop(op)

    def _op_lds_l_macl(self, op: int) -> None:
        """LDS.L @Rm+,MACL -- (Rm) -> MACL; Rm + 4 -> Rm (PM 6.28)."""
        self.macl = self._pop(op)

    def _op_lds_l_pr(self, op: int) -> None:
        """LDS.L @Rm+,PR -- (Rm) -> PR; Rm + 4 -> Rm (PM 6.28)."""
        self.pr = self._pop(op)

    def _op_sts_mach(self, op: int) -> None:
        """STS MACH,Rn -- MACH -> Rn (PM 6.61)."""
        self.R[(op >> 8) & 15] = self.mach

    def _op_sts_macl(self, op: int) -> None:
        """STS MACL,Rn -- MACL -> Rn (PM 6.61)."""
        self.R[(op >> 8) & 15] = self.macl

    def _op_sts_pr(self, op: int) -> None:
        """STS PR,Rn -- PR -> Rn (PM 6.61)."""
        self.R[(op >> 8) & 15] = self.pr

    def _op_sts_l_mach(self, op: int) -> None:
        """STS.L MACH,@-Rn -- Rn - 4 -> Rn; MACH -> (Rn) (PM 6.61)."""
        self._push(op, self.mach)

    def _op_sts_l_macl(self, op: int) -> None:
        """STS.L MACL,@-Rn -- Rn - 4 -> Rn; MACL -> (Rn) (PM 6.61)."""
        self._push(op, self.macl)

    def _op_sts_l_pr(self, op: int) -> None:
        """STS.L PR,@-Rn -- Rn - 4 -> Rn; PR -> (Rn) (PM 6.61)."""
        self._push(op, self.pr)

    # -- RTE, SLEEP, TRAPA ----------------------------------------------------------------

    def _op_rte(self, op: int) -> None:
        """RTE -- delayed branch to the PC popped from the stack; then SR popped (PM 6.50)."""
        R = self.R
        target = self._read32(R[15])
        R[15] = (R[15] + 4) & M32
        sr = self._read32(R[15])
        R[15] = (R[15] + 4) & M32
        self.sr = sr & SR_MASK
        self.delay_target = target

    def _op_sleep(self, op: int) -> None:
        """SLEEP -- enter sleep (or standby) until an interrupt or reset (PM 6.59; HM 14.3).

        The saved PC of the interrupt that ends it is the instruction after SLEEP.
        """
        self.sleeping = True

    def _op_trapa(self, op: int) -> None:
        """TRAPA #imm -- push SR and PC, jump to the vector at VBR + imm x 4 (PM 6.67; HM 4.5.2).

        The saved PC is the address of the instruction after TRAPA; the jump is not a
        delayed branch.
        """
        self._enter(op & 0xFF, self.pc, -1)


__all__ = ["SystemMixin"]
