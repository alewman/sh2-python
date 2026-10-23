"""The 16-bit free-running timer (FRT): FRC, two output compares with FTOA/FTOB, input capture.

HM chapter 11. FRC counts φ/8, φ/32 or φ/128 (TCR.CKS1-0 = 00, 01, 10) or rising
edges on FTCI (11). On each count:

- if FRC equalled OCRA, compare match A: FTCSR.OCFA = 1, the FTOA pin takes TOCR.OLVLA,
  and with FTCSR.CCLRA = 1 FRC becomes H'0000 instead of counting on (HM 11.4.2,
  11.4.3; 11.4.6: "the compare match signal is generated in the last state in which the
  values matched (at the timing for updating the count value that matched the FRC)");
- likewise OCRB and OCFB, FTOB and OLVLB (no clear);
- if FRC was H'FFFF, it overflows to H'0000 and sets OVF (HM 11.4.7).

The CPU reaches FRC, OCRA/OCRB and FICR one byte at a time through the 8-bit TEMP
register, upper byte first (HM 11.3): writing the upper byte stores it in TEMP, writing
the lower byte writes both; reading the upper byte copies the lower into TEMP.

The 32X wires FTOA to IRL0 and its software "bumps" the FRT to toggle it (U17); the
board sees FTOA through ``on_ftoa(level, clock)``, called whenever the pin changes.

Time is lazy: the module remembers the clock it last caught up to and counts the
timer's input clocks since, jumping from event to event (a compare match, an
overflow), so a long gap costs a few operations, not one per count. The chip asks
:meth:`next_event` for the clock of the next flag change and calls :meth:`run_until`.

Not modelled yet (rung 5): the external clock FTCI and input capture on FTI (the pins
exist as ``chip.ftci``/``chip.fti`` but are not sampled), the one-clock input-capture
delay of HM figure 11.9, module standby.
"""

from sh2_python._module import Module, Register

DIVIDERS = (8, 32, 128, 0)  # TCR.CKS1-0; 0 = external clock (FTCI)
NEVER = 1 << 62

ICF, OCFA, OCFB, OVF, CCLRA = 0x80, 0x08, 0x04, 0x02, 0x01


class FRT(Module):
    NAME = "frt"
    REGISTERS = (
        Register(0xFFFFFE10, "TIER", 8, 0x01),
        Register(0xFFFFFE11, "FTCSR", 8, 0x00),
        Register(0xFFFFFE12, "FRC", 16, 0x0000),
        Register(0xFFFFFE14, "OCRA", 16, 0xFFFF),
        Register(0xFFFFFE14, "OCRB", 16, 0xFFFF),
        Register(0xFFFFFE16, "TCR", 8, 0x00),
        Register(0xFFFFFE17, "TOCR", 8, 0xE0),
        Register(0xFFFFFE18, "FICR", 16, 0x0000),
    )

    def __init__(self, chip: object) -> None:
        super().__init__(chip)
        self.reset()

    def reset(self) -> None:
        self.tier = 0x01
        self.ftcsr = 0
        self.frc = 0
        self.ocra = self.ocrb = 0xFFFF
        self.tcr = 0
        self.tocr = 0xE0
        self.icr = 0
        self.temp = 0
        self.ftoa = 0
        self.ftob = 0
        self._flags_read = 0  # FTCSR as last read: a flag clears only after being read as 1
        self._clock = getattr(self.chip, "clock", 0)

    # -- counting ---------------------------------------------------------------

    @property
    def divider(self) -> int:
        return DIVIDERS[self.tcr & 3]

    def run_until(self, clock: int) -> None:
        """Count every timer input clock up to ``clock`` (φ states since reset)."""
        div = self.divider
        last = self._clock
        self._clock = clock
        if not div or clock <= last:
            return
        ticks = clock // div - last // div
        while ticks > 0:
            frc = self.frc
            # Counts until FRC equals each target; the count after that is the event.
            k = min((self.ocra - frc) & 0xFFFF, (self.ocrb - frc) & 0xFFFF, 0xFFFF - frc)
            if ticks <= k:
                self.frc = (frc + ticks) & 0xFFFF
                return
            frc += k
            ticks -= k + 1
            self.frc = frc
            when = (clock // div - ticks) * div  # the clock of this event's count
            self._event_count(when)

    def _event_count(self, when: int) -> None:
        frc = self.frc
        flags = 0
        nxt = (frc + 1) & 0xFFFF
        if frc == self.ocra:
            flags |= OCFA
            self._set_ftoa((self.tocr >> 1) & 1, when)
            if self.ftcsr & CCLRA:
                nxt = 0
        if frc == self.ocrb:
            flags |= OCFB
            self._set_ftob(self.tocr & 1, when)
        if frc == 0xFFFF and nxt == 0:
            flags |= OVF
        self.frc = nxt
        if flags & ~self.ftcsr:
            self.ftcsr |= flags
            self.chip.intc.update()

    def _set_ftoa(self, level: int, when: int) -> None:
        if level != self.ftoa:
            self.ftoa = level
            callback = self.chip.on_ftoa
            if callback is not None:
                callback(level, when)

    def _set_ftob(self, level: int, when: int) -> None:
        if level != self.ftob:
            self.ftob = level
            callback = self.chip.on_ftob
            if callback is not None:
                callback(level, when)

    def next_event(self) -> int:
        """The clock of the next count that sets a flag or changes a pin, or NEVER."""
        div = self.divider
        if not div:
            return NEVER
        frc = self.frc
        k = min((self.ocra - frc) & 0xFFFF, (self.ocrb - frc) & 0xFFFF, 0xFFFF - frc)
        return (self._clock // div + k + 1) * div

    # -- interrupts (HM 11.5, table 11.3) -------------------------------------------

    def requests(self, intc: object) -> list[tuple[int, int]]:
        level = intc.level("frt")
        if not level:
            return []
        out = []
        tier, ftcsr = self.tier, self.ftcsr
        if tier & 0x80 and ftcsr & ICF:
            out.append((level, intc.vector(0xFFFFFE66)))  # ICI: VCRC.FICV
        if (tier & 0x08 and ftcsr & OCFA) or (tier & 0x04 and ftcsr & OCFB):
            out.append((level, intc.vector(0xFFFFFE67)))  # OCI: VCRC.FOCV
        if tier & 0x02 and ftcsr & OVF:
            out.append((level, intc.vector(0xFFFFFE68)))  # OVI: VCRD.FOVV
        return out

    # -- registers (HM 11.2, 11.3) ------------------------------------------------------

    def read8(self, address: int) -> int:
        self.run_until(self.chip.clock)
        if address == 0xFFFFFE10:
            return self.tier
        if address == 0xFFFFFE11:
            self._flags_read = self.ftcsr
            return self.ftcsr
        if address in (0xFFFFFE12, 0xFFFFFE14, 0xFFFFFE18):
            word = {0xFFFFFE12: self.frc, 0xFFFFFE18: self.icr}.get(
                address, self.ocrb if self.tocr & 0x10 else self.ocra
            )
            self.temp = word & 0xFF
            return word >> 8
        if address in (0xFFFFFE13, 0xFFFFFE15, 0xFFFFFE19):
            return self.temp
        if address == 0xFFFFFE16:
            return self.tcr
        if address == 0xFFFFFE17:
            return self.tocr
        return 0

    def write8(self, address: int, value: int) -> None:
        self.run_until(self.chip.clock)
        if address == 0xFFFFFE10:
            # ICIE (7), OCIAE (3), OCIBE (2), OVIE (1); bit 0 reads 1 (initial H'01).
            self.tier = (value & 0x8E) | 0x01
            self.chip.intc.update()
        elif address == 0xFFFFFE11:
            # Flags clear only where 0 is written after the flag was read as 1; CCLRA
            # is an ordinary read/write bit (table 11.2 note 1).
            cleared = ~value & self._flags_read & (ICF | OCFA | OCFB | OVF)
            self.ftcsr = (self.ftcsr & ~cleared & ~CCLRA) | (value & CCLRA)
            self.chip.intc.update()
        elif address in (0xFFFFFE12, 0xFFFFFE14):
            self.temp = value
        elif address == 0xFFFFFE13:
            self.frc = (self.temp << 8) | value
        elif address == 0xFFFFFE15:
            if self.tocr & 0x10:
                self.ocrb = (self.temp << 8) | value
            else:
                self.ocra = (self.temp << 8) | value
        elif address == 0xFFFFFE16:
            # A new clock source starts counting from the current clock.
            self.tcr = value & 0x83
        elif address == 0xFFFFFE17:
            # Bits 7-5 read 1, bits 3-2 read 0 (HM 11.2.7).
            self.tocr = (value & 0x13) | 0xE0
        self.chip._reschedule()

    def state(self) -> dict[str, int]:
        return {
            "TIER": self.tier,
            "FTCSR": self.ftcsr,
            "FRC": self.frc,
            "OCRA": self.ocra,
            "OCRB": self.ocrb,
            "TCR": self.tcr,
            "TOCR": self.tocr,
            "FICR": self.icr,
            "TEMP": self.temp,
            "FTOA": self.ftoa,
            "FTOB": self.ftob,
        }

    def load_state(self, values: dict[str, int]) -> None:
        self.tier = values["TIER"]
        self.ftcsr = values["FTCSR"]
        self.frc = values["FRC"]
        self.ocra = values["OCRA"]
        self.ocrb = values["OCRB"]
        self.tcr = values["TCR"]
        self.tocr = values["TOCR"]
        self.icr = values["FICR"]
        self.temp = values["TEMP"]
        self.ftoa = values["FTOA"]
        self.ftob = values["FTOB"]
        self._clock = self.chip.clock


__all__ = ["FRT"]
