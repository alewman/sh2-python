"""The user break controller (UBC): two break channels (A and B).

HM chapter 6. Each channel compares the CPU's or DMAC's address (and, for channel B,
data) against BARx/BAMRx (BDRB/BDMRB) under BBRx's cycle conditions, and raises the
user break interrupt (level 15, vector 12) on a match; BRCR sets sequential mode and
the PC-break timing.

**Stub for rungs 0-2**: the register map below, readable and writable as HM appendix B
gives it, with no break detection. 32X software does not use the UBC; the debugger in
``debug.py`` gives breakpoints and watchpoints without it. Break detection is a later
rung's work, extending this module (its registers already hold what it needs).
"""

from sh2_python._module import Module, Register

# (address, name, initial): 16-bit registers, HM appendix B and table 6.2.
_MAP = (
    (0xFFFFFF40, "BARAH", 0x0000),
    (0xFFFFFF42, "BARAL", 0x0000),
    (0xFFFFFF44, "BAMRAH", 0x0000),
    (0xFFFFFF46, "BAMRAL", 0x0000),
    (0xFFFFFF48, "BBRA", 0x0000),
    (0xFFFFFF60, "BARBH", 0x0000),
    (0xFFFFFF62, "BARBL", 0x0000),
    (0xFFFFFF64, "BAMRBH", 0x0000),
    (0xFFFFFF66, "BAMRBL", 0x0000),
    (0xFFFFFF68, "BBRB", 0x0000),
    (0xFFFFFF70, "BDRBH", 0x0000),
    (0xFFFFFF72, "BDRBL", 0x0000),
    (0xFFFFFF74, "BDMRBH", 0x0000),
    (0xFFFFFF76, "BDMRBL", 0x0000),
    (0xFFFFFF78, "BRCR", 0x0000),
)


class UBC(Module):
    NAME = "ubc"
    REGISTERS = tuple(Register(address, name, 16, initial) for address, name, initial in _MAP)

    def __init__(self, chip: object) -> None:
        super().__init__(chip)
        self.bytes = {}
        self.reset()

    def reset(self) -> None:
        for address, _name, initial in _MAP:
            self.bytes[address] = initial >> 8
            self.bytes[address + 1] = initial & 0xFF

    def read8(self, address: int) -> int:
        return self.bytes.get(address, 0)

    def write8(self, address: int, value: int) -> None:
        if address in self.bytes:
            self.bytes[address] = value

    def state(self) -> dict[str, int]:
        return {
            name: (self.bytes[address] << 8) | self.bytes[address + 1]
            for address, name, _initial in _MAP
        }

    def load_state(self, values: dict[str, int]) -> None:
        for address, name, _initial in _MAP:
            self.bytes[address] = values[name] >> 8
            self.bytes[address + 1] = values[name] & 0xFF


__all__ = ["UBC"]
