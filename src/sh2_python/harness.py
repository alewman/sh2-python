"""The minimal harness: a host that is not a 32X, for tests, the referee corpus and the CLI.

docs/handoff-brief.md, "The minimal harness". Big-endian RAM at H'00000000 and
H'06000000 (4 MB each, mirrored through their area; the cache-through alias at
+H'20000000 reaches the same bytes because the core passes the board 29-bit
addresses), a ROM image loaded at H'00000000 that supplies PC and SP from its first
two longwords at reset, and a fixed-latency bus: each external cycle lasts what the
BSC makes it (2 states plus WCR's waits, HM 7.4.1) plus ``wait`` states from the
board's WAIT pin. FTOA changes are logged; IRL can be driven from a script of
``(clock, level)`` pairs.

A run stops when the next instruction branches to itself (``BRA`` with displacement
-2, or a ``BT``/``BF``/``BT/S``/``BF/S`` to itself whose condition holds), on ``SLEEP``
with SR.I3-I0 = 15 (no interrupt can wake it), or at a state budget. R0 is the
program's result.
"""

from dataclasses import dataclass, field

from sh2_python.cpu import SH7604

AREA_SIZE = 4 << 20
MASK = AREA_SIZE - 1


@dataclass
class RunResult:
    """How a harness run ended."""

    reason: str  # "branch-to-self", "sleep", "state-budget", "step-budget"
    r0: int
    pc: int
    states: int
    steps: int
    ftoa: list[tuple[int, int]] = field(default_factory=list)


class Harness:
    """A flat two-region memory around an :class:`SH7604`."""

    def __init__(
        self,
        image: bytes = b"",
        *,
        wait: int = 0,
        irl_script: list[tuple[int, int]] | None = None,
        **chip_options: object,
    ) -> None:
        self.low = bytearray(AREA_SIZE)  # area 0, H'00000000-
        self.high = bytearray(AREA_SIZE)  # area 3, H'06000000-
        self.low[: len(image)] = image
        self.wait_states = wait
        self.irl_script = sorted(irl_script or [])
        self.ftoa_log: list[tuple[int, int]] = []
        self.chip = SH7604(self.read, self.write, on_ftoa=self._ftoa, **chip_options)  # type: ignore[arg-type]

    # -- the bus -------------------------------------------------------------------

    def _region(self, address: int) -> tuple[bytearray | None, int]:
        area = (address >> 25) & 3
        if area == 0:
            return self.low, address & MASK
        if area == 3:
            return self.high, address & MASK
        return None, 0

    def read(self, address: int, width: int, kind: int) -> int:
        memory, offset = self._region(address)
        self.chip.wait += self.wait_states
        if memory is None:
            return 0
        if width == 32:
            return int.from_bytes(memory[offset : offset + 4], "big")
        if width == 16:
            return (memory[offset] << 8) | memory[offset + 1]
        return memory[offset]

    def write(self, address: int, width: int, value: int, kind: int) -> None:
        memory, offset = self._region(address)
        self.chip.wait += self.wait_states
        if memory is None:
            return
        memory[offset : offset + (width >> 3)] = value.to_bytes(width >> 3, "big")

    def peek16(self, address: int) -> int:
        """The word at ``address`` (cache-area or cache-through address), side-effect free."""
        memory, offset = self._region(address & 0x1FFFFFFF)
        if memory is None:
            return 0
        return (memory[offset] << 8) | memory[(offset + 1) & MASK]

    def load(self, address: int, data: bytes) -> None:
        memory, offset = self._region(address & 0x1FFFFFFF)
        if memory is None:
            raise ValueError(f"no memory at {address:#010x}")
        memory[offset : offset + len(data)] = data

    def _ftoa(self, level: int, clock: int) -> None:
        self.ftoa_log.append((clock, level))

    # -- running ---------------------------------------------------------------------

    def stopped(self) -> str | None:
        """Why the next step would never leave this instruction, or ``None``."""
        chip = self.chip
        if chip.delay_target >= 0:
            return None
        op = self.peek16(chip.pc)
        if op == 0x001B and (chip.sr >> 4) & 15 == 15:
            return "sleep"
        if chip.sleeping and (chip.sr >> 4) & 15 == 15:
            return "sleep"
        if op & 0xFF == 0xFE:
            high = op >> 8
            t = chip.sr & 1
            if (
                ((op >> 12) == 0xA and op & 0xFFF == 0xFFE)  # BRA $
                or (high == 0x89 and t)  # BT $
                or (high == 0x8B and not t)  # BF $
                or (high == 0x8D and t)  # BT/S $
                or (high == 0x8F and not t)  # BF/S $
            ):
                return "branch-to-self"
        return None

    def run(self, *, max_states: int = 10_000_000, max_steps: int | None = None) -> RunResult:
        """Reset-free run from the current state until a stop condition or a budget."""
        chip = self.chip
        start = chip.clock
        steps = 0
        script = self.irl_script
        while True:
            while script and script[0][0] <= chip.clock:
                chip.irl = script.pop(0)[1]
            reason = self.stopped()
            if reason is not None:
                break
            if chip.clock - start >= max_states:
                reason = "state-budget"
                break
            if max_steps is not None and steps >= max_steps:
                reason = "step-budget"
                break
            chip.step()
            steps += 1
        return RunResult(reason, chip.R[0], chip.pc, chip.clock - start, steps, self.ftoa_log)


__all__ = ["AREA_SIZE", "Harness", "RunResult"]
