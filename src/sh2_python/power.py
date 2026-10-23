"""Power-down modes: SLEEP, standby, and the module-stop bits (SBYCR).

HM chapter 14 (U18). ``SLEEP`` stops the CPU until an interrupt or a reset; with
SBYCR.SBY = 1 it enters standby instead, which stops the clock and the modules until
NMI or a reset. SBYCR.HIZ keeps pins high-impedance in standby; MSTP4-MSTP0 halt the
clock of the DMAC, the multiplier, the DIVU, the FRT and the SCI (HM 14.2.1).

Rungs 0-2 implement sleep (the chip's ``sleeping`` state, left by an accepted
interrupt) and hold SBYCR; standby and module stop are a later rung's work and are
treated as sleep / ignored for now.
"""

from sh2_python._module import Module, Register


class Power(Module):
    NAME = "power"
    REGISTERS = (Register(0xFFFFFE91, "SBYCR", 8, 0x00),)

    def __init__(self, chip: object) -> None:
        super().__init__(chip)
        self.sbycr = 0

    def reset(self) -> None:
        self.sbycr = 0

    def read8(self, address: int) -> int:
        return self.sbycr if address == 0xFFFFFE91 else 0

    def write8(self, address: int, value: int) -> None:
        if address == 0xFFFFFE91:
            self.sbycr = value & 0xDF  # bit 5 is reserved (HM 14.2.1)

    def state(self) -> dict[str, int]:
        return {"SBYCR": self.sbycr}

    def load_state(self, values: dict[str, int]) -> None:
        self.sbycr = values["SBYCR"]


__all__ = ["Power"]
