"""The watchdog timer (WDT): an 8-bit counter used as a watchdog or an interval timer.

HM chapter 12. WTCNT counts the clock WTCSR.CKS2-0 selects (φ/2 up to φ/8192) while
WTCSR.TME = 1. On overflow (H'FF -> H'00): in interval-timer mode (WT/IT = 0) WTCSR.OVF
is set and the ITI interrupt requested; in watchdog mode (WT/IT = 1) RSTCSR.WOVF is set,
the WDTOVF pin pulses, and with RSTCSR.RSTE = 1 the chip resets itself.

Registers (HM 12.2, U16): WTCSR and WTCNT read at H'FFFFFE80 and H'FFFFFE81 but are
written only by a **word** write to H'FFFFFE80 whose upper byte is H'A5 (WTCSR) or
H'5A (WTCNT); RSTCSR reads at H'FFFFFE83 and is written by a word write to H'FFFFFE82:
H'A5 to write RSTE/RSTS, H'5A with a lower byte of H'00 to clear WOVF (HM 12.2.4).

Rungs 0-2 model the counter, the flags, ITI, and ``on_wdtovf``; the self-reset in
watchdog mode is reported through ``on_wdtovf`` but does not reset the chip yet (a
later rung, with HM 12.3.2's timing).
"""

from sh2_python._module import Module, Register

DIVIDERS = (2, 64, 128, 256, 512, 1024, 4096, 8192)  # WTCSR.CKS2-0 (HM 12.2.2)
NEVER = 1 << 62


class WDT(Module):
    NAME = "wdt"
    REGISTERS = (
        Register(0xFFFFFE80, "WTCSR", 8, 0x18),
        Register(0xFFFFFE81, "WTCNT", 8, 0x00),
        Register(0xFFFFFE83, "RSTCSR", 8, 0x1F),
    )

    def __init__(self, chip: object) -> None:
        super().__init__(chip)
        self.reset()

    def reset(self) -> None:
        self.wtcsr = 0x18
        self.wtcnt = 0
        self.rstcsr = 0x1F
        self._clock = getattr(self.chip, "clock", 0)

    def run_until(self, clock: int) -> None:
        last = self._clock
        self._clock = clock
        if not self.wtcsr & 0x20 or clock <= last:  # TME
            return
        div = DIVIDERS[self.wtcsr & 7]
        ticks = clock // div - last // div
        total = self.wtcnt + ticks
        if total <= 0xFF:
            self.wtcnt = total
            return
        self.wtcnt = total & 0xFF
        when = (last // div + (0x100 - (total - ticks))) * div
        if self.wtcsr & 0x40:  # watchdog mode
            self.rstcsr |= 0x80  # WOVF
            if self.chip.on_wdtovf is not None:
                self.chip.on_wdtovf(when)
        else:
            self.wtcsr |= 0x80  # OVF: interval timer
            self.chip.intc.update()

    def next_event(self) -> int:
        if not self.wtcsr & 0x20:
            return NEVER
        div = DIVIDERS[self.wtcsr & 7]
        return (self._clock // div + (0x100 - self.wtcnt)) * div

    def requests(self, intc: object) -> list[tuple[int, int]]:
        # ITI: interval-timer overflow (HM 12.3.2), level IPRA.WDTIP, vector VCRWDT.WITV.
        level = intc.level("wdt")
        if level and self.wtcsr & 0x80 and not self.wtcsr & 0x40:
            return [(level, intc.vector(0xFFFFFEE4))]
        return []

    def read8(self, address: int) -> int:
        self.run_until(self.chip.clock)
        if address == 0xFFFFFE80:
            return self.wtcsr
        if address == 0xFFFFFE81:
            return self.wtcnt
        if address == 0xFFFFFE83:
            return self.rstcsr
        return 0xFF  # H'FFFFFE82 reads as nothing documented; Mednafen: "open bus of a sort"

    def write(self, address: int, width: int, value: int) -> None:
        # Byte and longword writes are ignored: only the keyed word writes reach the
        # registers (HM 12.2.4, "cannot be written by byte or longword transfer").
        if width != 16:
            return
        self.run_until(self.chip.clock)
        key, data = value >> 8, value & 0xFF
        if address == 0xFFFFFE80:
            if key == 0xA5:
                # OVF can only be cleared (read 1 then write 0); bits 4-3 read 1.
                self.wtcsr = (data & 0x67) | 0x18 | (self.wtcsr & data & 0x80)
                self.chip.intc.update()
            elif key == 0x5A:
                self.wtcnt = data
        elif address == 0xFFFFFE82:
            if key == 0xA5:
                self.rstcsr = (self.rstcsr & 0x80) | (data & 0x60) | 0x1F
            elif key == 0x5A and data == 0:
                self.rstcsr &= 0x7F
        self.chip._reschedule()

    def state(self) -> dict[str, int]:
        return {"WTCSR": self.wtcsr, "WTCNT": self.wtcnt, "RSTCSR": self.rstcsr}

    def load_state(self, values: dict[str, int]) -> None:
        self.wtcsr = values["WTCSR"]
        self.wtcnt = values["WTCNT"]
        self.rstcsr = values["RSTCSR"]
        self._clock = self.chip.clock


__all__ = ["WDT"]
