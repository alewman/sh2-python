"""What every on-chip module shares: its register map and how sized accesses reach it.

The SH7604's modules sit on two internal buses (HM 7.11.2): the 8-bit peripheral bus
at H'FFFFFE00-H'FFFFFEFF (SCI, FRT, INTC, WDT, the DMAC's DRCRs, SBYCR, CCR) and the
16/32-bit side at H'FFFFFF00-H'FFFFFFFF (DIVU, UBC, DMAC, BSC); HM appendix B lists
every register. A module subclass declares ``REGISTERS`` (its map, for the docs, the
debugger and later rungs) and implements ``read`` and ``write`` for the sizes its
registers take. Modules are composed by :class:`~sh2_python.SH7604`; hosts never
subclass them.
"""

from typing import NamedTuple


class Register(NamedTuple):
    """One row of a module's register map (HM appendix B)."""

    address: int
    name: str
    bits: int  # register width: 8, 16 or 32
    initial: int | None  # value after a power-on reset; None when undefined


class Module:
    """Base of the on-chip modules: a name, a register map and the chip it belongs to."""

    NAME = "module"
    REGISTERS: tuple[Register, ...] = ()

    def __init__(self, chip: object) -> None:
        self.chip = chip

    def reset(self) -> None:
        """Power-on or manual reset: registers to their HM initial values."""

    def read(self, address: int, width: int) -> int:
        """A CPU (or DMAC) read of ``width`` bits at ``address``; split into bytes by default."""
        value = 0
        for offset in range(width >> 3):
            value = (value << 8) | self.read8(address + offset)
        return value

    def write(self, address: int, width: int, value: int) -> None:
        """A CPU (or DMAC) write; split into bytes, high byte first, by default."""
        count = width >> 3
        for offset in range(count):
            self.write8(address + offset, (value >> (8 * (count - 1 - offset))) & 0xFF)

    def read8(self, address: int) -> int:
        return 0

    def write8(self, address: int, value: int) -> None:
        pass

    def state(self) -> dict[str, int]:
        """The module's registers as a name -> value mapping (for state captures)."""
        return {}

    def load_state(self, values: dict[str, int]) -> None:
        """Restore what :meth:`state` returned."""


class LongwordModule(Module):
    """A module on the 16/32-bit side whose registers are longwords.

    Byte and word accesses select the matching part of the longword: reads return it,
    writes merge into it. HM says such accesses give undefined values for the DIVU
    (HM 10.4.1); this is the documented choice of U14 for every longword module.
    """

    def read(self, address: int, width: int) -> int:
        base = address & ~3
        value = self.read32(base)
        if width == 32:
            return value
        shift = (4 - (address & 3) - (width >> 3)) * 8
        return (value >> shift) & ((1 << width) - 1)

    def write(self, address: int, width: int, value: int) -> None:
        base = address & ~3
        if width == 32:
            self.write32(base, value, 0xFFFFFFFF)
            return
        shift = (4 - (address & 3) - (width >> 3)) * 8
        mask = ((1 << width) - 1) << shift
        self.write32(base, (value << shift) & mask, mask)

    def read32(self, address: int) -> int:
        return 0

    def write32(self, address: int, value: int, mask: int) -> None:
        """Write the bits of ``value`` selected by ``mask`` to the longword at ``address``."""


__all__ = ["LongwordModule", "Module", "Register"]
