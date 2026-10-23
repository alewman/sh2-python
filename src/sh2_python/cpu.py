"""The SH7604: the SH-2 CPU and its on-chip modules, composed into one chip.

The board passes two callables, ``read(address, width, kind) -> int`` and
``write(address, width, value, kind)``, one call per external bus cycle (``bus.py``;
docs/handoff-brief.md, "The bus contract"), and calls :meth:`SH7604.step`, which runs
one instruction or one exception sequence and returns its states. The chip keeps its
own clock, ``chip.clock``, in states of φ since construction.

Pins in are plain attributes the board sets, also from inside a callback: ``irl``
(0-15, the inverted IRL3-IRL0 level), ``nmi`` (the pin's level; the edge is detected),
``dreq0``/``dreq1``, ``ftci``/``fti``. Pins out are optional callables: ``on_ftoa(level,
clock)``, ``on_ftob``, ``on_dack(channel, clock)``, ``on_txd(byte, clock)``,
``on_wdtovf(clock)``. ``observe(address, width, kind)``, when given, sees every CPU
access, cache hits included (``kind`` has ``HIT``); default ``None``, no cost.

Hosts never subclass the chip. Every callable is a plain attribute that may be
replaced at any time; so are ``read_onchip(module, address, width)`` and
``write_onchip(module, address, width, value)``, through which every on-chip register
access goes (a replay host uses them to see or substitute those accesses).
"""

from collections.abc import Callable

from sh2_python._arith import ArithMixin
from sh2_python._branch import BranchMixin
from sh2_python._core import M32, SR_MASK, CoreMixin, ReadFunction, WriteFunction
from sh2_python._decode import FORMS, decode_index
from sh2_python._logic import LogicMixin
from sh2_python._multiply import MultiplyMixin
from sh2_python._shift import ShiftMixin
from sh2_python._system import SystemMixin
from sh2_python._transfer import TransferMixin
from sh2_python.bsc import BSC
from sh2_python.cache import Cache
from sh2_python.divu import DIVU
from sh2_python.dmac import DMAC
from sh2_python.frt import FRT
from sh2_python.intc import INTC
from sh2_python.pipeline import CLASSES, EXCEPTIONS, TableTiming
from sh2_python.power import Power
from sh2_python.sci import SCI
from sh2_python.state import ChipState
from sh2_python.ubc import UBC
from sh2_python.wdt import WDT

#: HM table 4.9: an instruction that changes PC in a delay slot is a slot illegal.
PC_CHANGING = frozenset(
    "JMP JSR BRA BSR RTS RTE BT BF TRAPA BF/S BT/S BSRF BRAF".split()
)  # fmt: skip
#: HM 4.6.2, table 4.10: no interrupt is accepted after these.
INTERRUPT_DISABLING = frozenset("LDC LDC.L STC STC.L LDS LDS.L STS STS.L".split())

_INDEX = decode_index()


def _build_tables() -> tuple[list[str], list[int], bytearray, bytearray]:
    """Per-opcode handler names, base states, and the two rule tables."""
    names: list[str] = []
    states: list[int] = []
    slot_illegal = bytearray(0x10000)
    blocks = bytearray(0x10000)
    for opcode, form in enumerate(_INDEX):
        if form is None:
            names.append("_illegal")
            states.append(0)
            slot_illegal[opcode] = 1
            continue
        names.append(form.handler)
        states.append(CLASSES[form.pipeline].states)
        slot_illegal[opcode] = form.mnemonic in PC_CHANGING
        blocks[opcode] = form.mnemonic in INTERRUPT_DISABLING
    return names, states, slot_illegal, blocks


_NAMES, _STATES, _SLOT_ILLEGAL, _BLOCKS = _build_tables()


class SH7604(
    TransferMixin,
    ArithMixin,
    MultiplyMixin,
    LogicMixin,
    ShiftMixin,
    BranchMixin,
    SystemMixin,
    CoreMixin,
):
    """The Hitachi SH7604: an SH-2 CPU with cache, DMAC, DIVU, FRT, WDT, SCI, BSC, INTC, UBC.

    Registers are plain attributes: ``R`` (a list of sixteen 32-bit values), ``sr``,
    ``gbr``, ``vbr``, ``mach``, ``macl``, ``pr``, ``pc`` (the next instruction), and
    ``delay_target`` (-1, or the target of a delayed branch whose slot runs next).
    Modules are attributes too: ``cache``, ``bsc``, ``intc``, ``ubc``, ``dmac``,
    ``divu``, ``frt``, ``wdt``, ``sci``, ``power``.

    Keyword options:

    * ``mode``: ``"master"`` or ``"slave"``, the MD5 pin (BCR1.MASTER; HM 7.10).
    * ``area0_width``: 8, 16 or 32, the area 0 port width set by the MD3/MD4 pins
      (HM 7.2.2 note). Default 32.
    * ``undefined``: the value given at reset to the registers HM table 2.1 calls
      undefined (R0-R14, GBR, MACH, MACL, PR, SR's M Q S T). Default 0 (U19).
    * ``observe``, ``on_ftoa``, ``on_ftob``, ``on_dack``, ``on_txd``, ``on_wdtovf``: the
      optional callables of the module docstring.

    The constructor does not reset the chip: call :meth:`reset`, which reads PC and
    R15 through ``read``, when the board is ready to answer.
    """

    def __init__(
        self,
        read: ReadFunction,
        write: WriteFunction,
        *,
        mode: str = "master",
        area0_width: int = 32,
        undefined: int = 0,
        observe: Callable[[int, int, int], None] | None = None,
        on_ftoa: Callable[[int, int], None] | None = None,
        on_ftob: Callable[[int, int], None] | None = None,
        on_dack: Callable[[int, int], None] | None = None,
        on_txd: Callable[[int, int], None] | None = None,
        on_wdtovf: Callable[[int], None] | None = None,
    ) -> None:
        if not callable(read) or not callable(write):
            raise TypeError("read and write must be callable")
        if mode not in ("master", "slave"):
            raise ValueError('mode must be "master" or "slave"')
        if area0_width not in (8, 16, 32):
            raise ValueError("area0_width must be 8, 16 or 32")
        for name, value in (("observe", observe), ("on_ftoa", on_ftoa), ("on_ftob", on_ftob),
                            ("on_dack", on_dack), ("on_txd", on_txd),
                            ("on_wdtovf", on_wdtovf)):  # fmt: skip
            if value is not None and not callable(value):
                raise TypeError(f"{name} must be callable or None")
        self.read = read
        self.write = write
        self.observe = observe
        self.on_ftoa = on_ftoa
        self.on_ftob = on_ftob
        self.on_dack = on_dack
        self.on_txd = on_txd
        self.on_wdtovf = on_wdtovf
        self.mode = mode
        self.undefined = undefined & M32

        # Pins in.
        self.irl = 0
        self.nmi = 1  # the NMI pin idles high; NMIE = 0 takes the falling edge
        self.dreq0 = self.dreq1 = 0
        self.ftci = self.fti = 0
        #: Inside a bus callback: the board adds WAIT-pin states here (0 on entry).
        self.wait = 0
        self.clock = 0

        # CPU registers (HM 2.1; values before reset are the "undefined" choice).
        u = self.undefined
        self.R = [u] * 16
        self.sr = 0xF0 | (u & 0x303)
        self.gbr = self.mach = self.macl = self.pr = u
        self.vbr = 0
        self.pc = 0
        self.delay_target = -1
        self.sleeping = False

        # Internal state of the stepper.
        self._extra = 0
        self._no_interrupt = False
        self._address_error = False
        self._nmi_seen = self.nmi
        self._nmi_pending = False
        self._ibuf = 0
        self._ibuf_line = -1
        self._onchip_level = 0
        self._onchip_vector = 0
        self._next_event = 1 << 62
        #: Replay hosts only: see :meth:`inject_interrupt`.
        self.replay_interrupts = False
        self._injected: tuple[int, int] | None = None

        # The modules (HM 1.1), each beside the CPU.
        self.cache = Cache(self)
        self.bsc = BSC(self, slave=mode == "slave", area0_width=area0_width)
        self.intc = INTC(self)
        self.ubc = UBC(self)
        self.dmac = DMAC(self)
        self.divu = DIVU(self)
        self.frt = FRT(self)
        self.wdt = WDT(self)
        self.sci = SCI(self)
        self.power = Power(self)
        self.modules = (self.cache, self.bsc, self.intc, self.ubc, self.dmac, self.divu,
                        self.frt, self.wdt, self.sci, self.power)  # fmt: skip
        self._onchip = self._onchip_map()
        self._ports = self.bsc.widths  # updated in place by the BSC
        self._cycle_states = self.bsc.cycle_states
        self.read_onchip = self._module_read
        self.write_onchip = self._module_write

        # Dispatch and timing tables.
        cls = type(self)
        if "_table" not in cls.__dict__:
            # One list of plain functions per class, built once: step() calls
            # ``_table[op](self, op)``, so constructing a chip costs no table build.
            cls._table = [getattr(cls, name) for name in _NAMES]
        self._slot_illegal = _SLOT_ILLEGAL
        self._blocks_interrupt = _BLOCKS
        self.timing = TableTiming(_STATES)
        self._base_states = self.timing.base_states
        self._exception_states = {name: c.states for name, c in EXCEPTIONS.items()}

    # -- the on-chip register map ------------------------------------------------------

    def _onchip_map(self) -> list:
        """Which module answers each address of H'FFFFFE00-H'FFFFFFFF (HM appendix B)."""
        table: list = [None] * 512

        def claim(first: int, last: int, module: object) -> None:
            for address in range(first, last + 1):
                table[address - 0xFFFFFE00] = module

        claim(0xFFFFFE00, 0xFFFFFE05, self.sci)
        claim(0xFFFFFE10, 0xFFFFFE19, self.frt)
        claim(0xFFFFFE60, 0xFFFFFE69, self.intc)
        claim(0xFFFFFE71, 0xFFFFFE72, self.dmac)
        claim(0xFFFFFE80, 0xFFFFFE83, self.wdt)
        claim(0xFFFFFE91, 0xFFFFFE91, self.power)
        claim(0xFFFFFE92, 0xFFFFFE92, self.cache)
        claim(0xFFFFFEE0, 0xFFFFFEE5, self.intc)
        claim(0xFFFFFF00, 0xFFFFFF1F, self.divu)
        claim(0xFFFFFF40, 0xFFFFFF79, self.ubc)
        claim(0xFFFFFF80, 0xFFFFFFB3, self.dmac)
        claim(0xFFFFFFE0, 0xFFFFFFFB, self.bsc)
        return table

    def _onchip_requests(self) -> list[tuple[int, int]]:
        """Every asserted on-chip interrupt source, in HM table 5.4's default order."""
        intc = self.intc
        out = []
        out += self.divu.requests(intc)
        out += self.dmac.requests(intc)
        out += self.wdt.requests(intc)
        out += self.sci.requests(intc)
        out += self.frt.requests(intc)
        return out

    # -- reset -------------------------------------------------------------------------

    def reset(self, power_on: bool = True) -> None:
        """Power-on (vectors 0/1) or manual (vectors 2/3) reset (HM 4.2, table 2.1).

        PC and R15 are read through ``read`` at absolute addresses (VBR is not
        applied); VBR = 0 and SR.I3-I0 = 15. Registers HM calls undefined take the
        ``undefined`` constructor value at a power-on reset (U19). The modules return
        to their initial values; CCR.CE is cleared and the cache contents are kept
        (HM 8.5.1).
        """
        for module in self.modules:
            module.reset()
        self._onchip_level = self._onchip_vector = 0
        if power_on:
            u = self.undefined
            self.R = [u] * 16
            self.gbr = self.mach = self.macl = self.pr = u
            self.sr = 0xF0 | (u & 0x303)
        else:
            self.sr = (self.sr & SR_MASK) | 0xF0
        self.vbr = 0
        self.delay_target = -1
        self.sleeping = False
        self._extra = 0
        self._no_interrupt = False
        self._address_error = False
        self._nmi_pending = False
        self._nmi_seen = self.nmi
        self._ibuf_line = -1
        base = 0 if power_on else 8
        self.pc = self._read32(base)
        self.R[15] = self._read32(base + 4)
        self._reschedule()

    # -- state -------------------------------------------------------------------------

    def capture_state(self) -> ChipState:
        """An immutable snapshot of the chip at a step boundary (no bus reads)."""
        return ChipState(
            r=tuple(self.R),
            sr=self.sr,
            gbr=self.gbr,
            vbr=self.vbr,
            mach=self.mach,
            macl=self.macl,
            pr=self.pr,
            pc=self.pc,
            delay_target=self.delay_target,
            sleeping=self.sleeping,
            no_interrupt=self._no_interrupt,
            address_error=self._address_error,
            nmi_pending=self._nmi_pending,
            clock=self.clock,
            modules={module.NAME: module.state() for module in self.modules},
        )

    def restore_state(self, state: ChipState) -> None:
        """Load a :class:`ChipState` (registers, in-flight state, every module)."""
        self.R = list(state.r)
        self.sr = state.sr & SR_MASK
        self.gbr, self.vbr = state.gbr, state.vbr
        self.mach, self.macl, self.pr = state.mach, state.macl, state.pr
        self.pc = state.pc
        self.delay_target = state.delay_target
        self.sleeping = state.sleeping
        self._no_interrupt = state.no_interrupt
        self._address_error = state.address_error
        self._nmi_pending = state.nmi_pending
        self.clock = state.clock
        self._ibuf_line = -1
        for module in self.modules:
            values = state.modules.get(module.NAME)
            if values:
                module.load_state(values)
        self.intc.update()
        self._reschedule()

    # -- for the debugger and the disassembler ----------------------------------------

    @staticmethod
    def form(opcode: int):
        """The decode-table form of ``opcode`` (``_decode.Form``), or ``None``."""
        return _INDEX[opcode & 0xFFFF]


__all__ = ["FORMS", "SH7604"]
