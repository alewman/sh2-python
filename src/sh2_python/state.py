"""Immutable chip state: what :meth:`SH7604.capture_state` returns and ``restore_state`` loads."""

from dataclasses import dataclass, field


def _require_u32(name: str, value: object) -> None:
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"{name} must be an integer in range 0..0xFFFFFFFF")


@dataclass(frozen=True, slots=True)
class ChipState:
    """The SH7604 at a step boundary.

    ``r`` is R0-R15; ``pc`` the address of the next instruction; ``delay_target`` -1 or
    the target of a delayed branch whose slot (at ``pc``) runs next: that and
    ``no_interrupt`` (the last instruction blocks interrupts, HM 4.6.2),
    ``address_error`` (one is pending) and ``nmi_pending`` are the in-flight state a
    step boundary needs to resume exactly. ``modules`` maps each module's name to its
    registers. ``sr`` holds only M, Q, I3-I0, S, T (mask H'3F3).

    Rung 1's corpus uses ``r``, ``sr``, ``gbr``, ``vbr``, ``mach``, ``macl``, ``pr``
    and ``pc`` (validation/rung1).
    """

    r: tuple[int, ...] = (0,) * 16
    sr: int = 0xF0
    gbr: int = 0
    vbr: int = 0
    mach: int = 0
    macl: int = 0
    pr: int = 0
    pc: int = 0
    delay_target: int = -1
    sleeping: bool = False
    no_interrupt: bool = False
    address_error: bool = False
    nmi_pending: bool = False
    clock: int = 0
    modules: dict[str, dict[str, int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.r) != 16:
            raise ValueError("r must hold R0-R15")
        for index, value in enumerate(self.r):
            _require_u32(f"r{index}", value)
        for name in ("gbr", "vbr", "mach", "macl", "pr", "pc"):
            _require_u32(name, getattr(self, name))
        if type(self.sr) is not int or self.sr & ~0x3F3:
            raise ValueError("sr must use only M, Q, I3-I0, S and T (mask 0x3F3)")
        if type(self.delay_target) is not int or not -1 <= self.delay_target <= 0xFFFFFFFF:
            raise ValueError("delay_target must be -1 or an address")

    @property
    def t(self) -> int:
        return self.sr & 1

    @property
    def interrupt_mask(self) -> int:
        return (self.sr >> 4) & 15

    def registers(self) -> dict[str, int]:
        """The CPU registers by name, as the rung-1 corpus and MAME's trace name them."""
        out = {f"r{i}": value for i, value in enumerate(self.r)}
        out.update(
            sr=self.sr, gbr=self.gbr, vbr=self.vbr, mach=self.mach, macl=self.macl,
            pr=self.pr, pc=self.pc,
        )  # fmt: skip
        return out


__all__ = ["ChipState"]
