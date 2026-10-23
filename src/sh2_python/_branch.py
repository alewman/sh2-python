"""Branches: BF, BT, BF/S, BT/S, BRA, BRAF, BSR, BSRF, JMP, JSR, RTS.

PM 6.6-6.13, 6.25-6.26, 6.51. The PC in every displacement is the branch's address + 4
(``self.pc + 2`` here; ``_core.py``). ``BT``/``BF`` are not delayed: taken, they jump at
once and cost 3 states instead of 1 (PM table A.50; PM 7.7.5: "the two instructions
fetched after the branch are discarded"). The others are delayed: they record the
target in ``delay_target`` and the next step runs the slot (``_core.py``); the target
register is read now, before the slot can change it (PM 4.1.5). ``BT/S``/``BF/S`` cost
2 states taken, 1 not taken, and make no slot when not taken (U2).

C6: HM chapter 2 prints the descriptions of ``BT`` and ``BT/S`` swapped; PM and SWM are
followed. PM table A.50 also misprints BT/S's not-taken case as "When T = 1, nop".
"""

M32 = 0xFFFFFFFF
T = 0x001


def _disp8(op: int) -> int:
    d = op & 0xFF
    return (d - 0x100 if d & 0x80 else d) << 1


def _disp12(op: int) -> int:
    d = op & 0xFFF
    return (d - 0x1000 if d & 0x800 else d) << 1


class BranchMixin:
    def _op_bf(self, op: int) -> None:
        """BF label -- if T = 0, disp x 2 + PC -> PC; else nop (PM 6.6)."""
        if not self.sr & T:
            self.pc = (self.pc + 2 + _disp8(op)) & M32
            self._ibuf_line = -1
            self._extra += 2

    def _op_bt(self, op: int) -> None:
        """BT label -- if T = 1, disp x 2 + PC -> PC; else nop (PM 6.12)."""
        if self.sr & T:
            self.pc = (self.pc + 2 + _disp8(op)) & M32
            self._ibuf_line = -1
            self._extra += 2

    def _op_bf_s(self, op: int) -> None:
        """BF/S label -- if T = 0, delayed branch to disp x 2 + PC; else nop (PM 6.7)."""
        if not self.sr & T:
            self.delay_target = (self.pc + 2 + _disp8(op)) & M32
            self._extra += 1

    def _op_bt_s(self, op: int) -> None:
        """BT/S label -- if T = 1, delayed branch to disp x 2 + PC; else nop (PM 6.13)."""
        if self.sr & T:
            self.delay_target = (self.pc + 2 + _disp8(op)) & M32
            self._extra += 1

    def _op_bra(self, op: int) -> None:
        """BRA label -- delayed branch to disp x 2 + PC (PM 6.8)."""
        self.delay_target = (self.pc + 2 + _disp12(op)) & M32

    def _op_braf(self, op: int) -> None:
        """BRAF Rm -- delayed branch to Rm + PC (PM 6.9)."""
        self.delay_target = (self.pc + 2 + self.R[(op >> 8) & 15]) & M32

    def _op_bsr(self, op: int) -> None:
        """BSR label -- PC -> PR; delayed branch to disp x 2 + PC (PM 6.10)."""
        pc = self.pc + 2
        self.pr = pc & M32
        self.delay_target = (pc + _disp12(op)) & M32

    def _op_bsrf(self, op: int) -> None:
        """BSRF Rm -- PC -> PR; delayed branch to Rm + PC (PM 6.11)."""
        pc = self.pc + 2
        target = (pc + self.R[(op >> 8) & 15]) & M32  # Rm read before PR is written
        self.pr = pc & M32
        self.delay_target = target

    def _op_jmp(self, op: int) -> None:
        """JMP @Rm -- delayed branch to Rm (PM 6.25)."""
        self.delay_target = self.R[(op >> 8) & 15]

    def _op_jsr(self, op: int) -> None:
        """JSR @Rm -- PC -> PR; delayed branch to Rm (PM 6.26)."""
        self.delay_target = self.R[(op >> 8) & 15]
        self.pr = (self.pc + 2) & M32

    def _op_rts(self, op: int) -> None:
        """RTS -- delayed branch to PR (PM 6.51)."""
        self.delay_target = self.pr


__all__ = ["BranchMixin"]
