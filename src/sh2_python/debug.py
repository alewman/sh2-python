"""Dependency-free execution control and structured debugging records.

``DebugSession`` drives a chip -- an :class:`~sh2_python.SH7604`, or a board whose
``step()`` runs its devices around the chip and whose ``chip`` attribute is it -- one
boundary at a time, with breakpoints, bounded runs, a history ring and, optionally,
access tracking with watchpoints. It never changes what the core does: it wraps the
chip's ``read``/``write`` callables (external bus cycles, with values) and its
``observe`` hook (every CPU access, **cache hits included**), and :meth:`close` puts
the originals back.
"""

from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum

from sh2_python.bus import HIT, WRITE
from sh2_python.disasm import Instruction, WordReader, disassemble
from sh2_python.state import ChipState


class BoundaryKind(Enum):
    """What one ``step()`` did."""

    INSTRUCTION = "instruction"
    SLOT = "slot"  # the instruction in a delay slot
    INTERRUPT = "interrupt"  # an interrupt (or NMI) accepted
    EXCEPTION = "exception"  # an address error taken
    SLEEP = "sleep"  # states spent sleeping


class StopReason(Enum):
    BREAKPOINT = "breakpoint"  # before the instruction at a breakpoint
    WATCHPOINT = "watchpoint"  # after the step that touched a watched address
    SLEEP = "sleep"  # sleeping with interrupts masked (SR.I = 15)
    STEP_LIMIT = "step_limit"
    STATE_LIMIT = "state_limit"


#: One access: ("r" or "w", address, value or None, width in bits, kind bits).
#: External cycles carry their value; ``observe`` events (cache hits, on-chip
#: registers, the CPU side of every access) carry None.
Access = tuple[str, int, int | None, int, int]


@dataclass(frozen=True, slots=True)
class StepRecord:
    """Immutable before/after evidence for one boundary."""

    sequence: int
    kind: BoundaryKind
    before: ChipState
    after: ChipState
    states: int
    instruction: Instruction | None
    accesses: tuple[Access, ...] | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    reason: StopReason
    steps: int
    instructions: int
    states: int
    state: ChipState
    last_record: StepRecord | None
    hits: tuple[Access, ...] = ()


def next_boundary(chip: object) -> BoundaryKind:
    """What the chip's next ``step()`` will do, decided as ``_core.step`` decides it."""
    if chip.delay_target >= 0:
        return BoundaryKind.SLOT
    if chip._address_error:
        return BoundaryKind.EXCEPTION
    if chip.nmi != chip._nmi_seen and bool(chip.nmi) == chip.intc.nmi_rising:
        return BoundaryKind.INTERRUPT
    if not chip._no_interrupt and chip._interrupt_due():
        return BoundaryKind.INTERRUPT
    if chip.sleeping:
        return BoundaryKind.SLEEP
    return BoundaryKind.INSTRUCTION


class DebugSession:
    """Control a chip (or a board around one) without modifying its execution."""

    def __init__(
        self,
        target: object,
        *,
        peek16: WordReader | None = None,
        history_limit: int = 256,
        track_accesses: bool = False,
    ) -> None:
        if not callable(getattr(target, "step", None)):
            raise TypeError("target must provide step()")
        self.target = target
        #: The chip: the target itself, or the ``chip`` a board target carries.
        self.chip = getattr(target, "chip", target)
        self.peek16 = peek16
        self.history_limit = history_limit
        self.breakpoints: set[int] = set()
        self.watchpoints: dict[int, str] = {}  # address -> "r", "w" or "rw"
        self.total_steps = self.total_instructions = self.total_states = 0
        self._history: deque[StepRecord] = deque(maxlen=history_limit or 1)
        self._accesses: list[Access] | None = None
        self._originals: tuple[Callable, Callable, Callable | None] | None = None
        if track_accesses:
            self._wrap()

    # -- access tracking -------------------------------------------------------------

    @property
    def tracking(self) -> bool:
        return self._originals is not None

    def _wrap(self) -> None:
        chip = self.chip
        read, write, observe = chip.read, chip.write, chip.observe
        self._originals = (read, write, observe)
        log: list[Access] = []
        self._accesses = log

        def tracked_read(address: int, width: int, kind: int) -> int:
            value = read(address, width, kind)
            log.append(("r", address, value, width, kind))
            return value

        def tracked_write(address: int, width: int, value: int, kind: int) -> None:
            log.append(("w", address, value, width, kind))
            write(address, width, value, kind)

        def tracked_observe(address: int, width: int, kind: int) -> None:
            if kind & HIT or address >> 29 in (6, 7):
                # Hits and on-chip/data-array accesses make no bus cycle: record them here.
                log.append(("w" if kind & WRITE else "r", address, None, width, kind))
            if observe is not None:
                observe(address, width, kind)

        chip.read, chip.write, chip.observe = tracked_read, tracked_write, tracked_observe

    def close(self) -> None:
        """Stop tracking and give the chip its own callables back."""
        if self._originals is not None:
            self.chip.read, self.chip.write, self.chip.observe = self._originals
            self._originals = None
            self._accesses = None

    # -- breakpoints and watchpoints --------------------------------------------------

    def add_breakpoint(self, address: int) -> None:
        self.breakpoints.add(address & 0xFFFFFFFF)

    def remove_breakpoint(self, address: int) -> None:
        self.breakpoints.discard(address & 0xFFFFFFFF)

    def add_watchpoint(self, address: int, kind: str = "rw") -> None:
        """Stop after a step that reads/writes ``address`` -- as the CPU addresses it.

        External cycles carry 29-bit addresses; the watch matches the address with or
        without the space bits, so a watch on H'26001000 sees the cache-through cycle
        to H'06001000 and a cache hit on H'06001000.
        """
        if not self.tracking:
            raise ValueError("watchpoints need a session created with track_accesses=True")
        if kind not in ("r", "w", "rw"):
            raise ValueError('kind must be "r", "w" or "rw"')
        self.watchpoints[address & 0xFFFFFFFF] = kind

    def remove_watchpoint(self, address: int) -> None:
        self.watchpoints.pop(address & 0xFFFFFFFF, None)

    # -- history ------------------------------------------------------------------------

    @property
    def history(self) -> tuple[StepRecord, ...]:
        return tuple(self._history)

    def iter_history(self, *, newest_first: bool = False) -> Iterator[StepRecord]:
        return reversed(self._history) if newest_first else iter(self._history)

    # -- execution --------------------------------------------------------------------

    def step(self) -> StepRecord:
        """Advance exactly one boundary, ignoring breakpoints."""
        chip = self.chip
        before = chip.capture_state()
        kind = next_boundary(chip)
        instruction = None
        if kind in (BoundaryKind.INSTRUCTION, BoundaryKind.SLOT) and self.peek16 is not None:
            instruction = disassemble(self.peek16, before.pc)
        if self._accesses is not None:
            self._accesses.clear()
        states = self.target.step()
        record = StepRecord(
            sequence=self.total_steps,
            kind=kind,
            before=before,
            after=chip.capture_state(),
            states=states,
            instruction=instruction,
            accesses=None if self._accesses is None else tuple(self._accesses),
        )
        self.total_steps += 1
        self.total_states += states
        if kind in (BoundaryKind.INSTRUCTION, BoundaryKind.SLOT):
            self.total_instructions += 1
        if self.history_limit:
            self._history.append(record)
        return record

    def run(self, *, max_steps: int, max_states: int | None = None) -> RunResult:
        """Run until a breakpoint, watchpoint, masked sleep, or a budget."""
        steps = instructions = states = 0
        last = None
        chip = self.chip

        def result(reason: StopReason, **extra) -> RunResult:
            return RunResult(
                reason, steps, instructions, states, chip.capture_state(), last, **extra
            )

        while steps < max_steps:
            kind = next_boundary(chip)
            if steps and kind is BoundaryKind.INSTRUCTION and chip.pc in self.breakpoints:
                return result(StopReason.BREAKPOINT)
            if kind is BoundaryKind.SLEEP and (chip.sr >> 4) & 15 == 15:
                return result(StopReason.SLEEP)
            last = self.step()
            steps += 1
            states += last.states
            if last.kind in (BoundaryKind.INSTRUCTION, BoundaryKind.SLOT):
                instructions += 1
            hits = self._watch_hits(last)
            if hits:
                return result(StopReason.WATCHPOINT, hits=hits)
            if max_states is not None and states >= max_states:
                return result(StopReason.STATE_LIMIT)
        return result(StopReason.STEP_LIMIT)

    def _watch_hits(self, record: StepRecord) -> tuple[Access, ...]:
        if not self.watchpoints or not record.accesses:
            return ()
        watched = self.watchpoints
        out = []
        for access in record.accesses:
            rw, address, _value, width, _kind = access
            for offset in range(width >> 3):
                a = address + offset
                for candidate in (a, a & 0x1FFFFFFF, (a & 0x1FFFFFFF) | 0x20000000):
                    mode = watched.get(candidate)
                    if mode is not None and rw in mode:
                        out.append(access)
                        break
                else:
                    continue
                break
        return tuple(out)


__all__ = [
    "Access",
    "BoundaryKind",
    "DebugSession",
    "RunResult",
    "StepRecord",
    "StopReason",
    "next_boundary",
]
