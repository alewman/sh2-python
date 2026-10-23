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

import re
from collections.abc import Callable

from sh2_python._arith import ArithMixin
from sh2_python._branch import BranchMixin
from sh2_python._core import M32, SR_MASK, CoreMixin, ReadFunction, WriteFunction
from sh2_python._decode import FORMS, Form, decode_index
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
from sh2_python.pipeline import EXCEPTIONS, PLANS, PR_BIT, PipelineMixin, Plan
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


_REGISTER = re.compile(r"\bR[mn0]\b|\bPR\b")
#: Instructions that read or write PR without naming it (PM 6.10, 6.11, 6.26, 6.51).
_IMPLICIT_PR = frozenset("RTS BSR BSRF JSR".split())


def _register_bits(form: Form, opcode: int, text: str) -> int:
    """The register bits (pipeline.PR_BIT for PR) named in an operand text."""
    bits = 0
    for token in _REGISTER.findall(text):
        if token == "PR":
            bits |= PR_BIT
        elif token == "R0":
            bits |= 1
        else:
            field = form.code.rindex(token[1])  # the n or m field's last bit position
            bits |= 1 << ((opcode >> (15 - field)) & 15)
    return bits


def _load_use(form: Form, opcode: int) -> tuple[int, int]:
    """(the register a load writes in WB, the registers the load-use rule sees used).

    PM 7.5: a load's WB comes after the next instruction's EX, so that EX splits the
    slot when it uses the loaded register as a source or a destination; not when the
    next instruction is a load to the same register (its destination is not a use)
    nor a ``MAC @Rm+,@Rn+``. Loads are table 7.2's memory loads, ``LDS.L @Rm+,PR`` and
    ``STS MACH/MACL,Rn`` (WB to Rn); LDC.L's two-slot ID stall keeps its successor's
    EX out of its MA's slot.
    """
    pipeline = form.pipeline
    operands = form.operands
    if pipeline in ("mac_w", "mac_l"):
        return 0, 0
    if pipeline in ("load", "mac_to_reg"):
        sources, _, dest = operands.rpartition(",")
        return _register_bits(form, opcode, dest), _register_bits(form, opcode, sources)
    if pipeline == "lds_l_pr":
        return PR_BIT, _register_bits(form, opcode, operands.rpartition(",")[0])
    uses = _register_bits(form, opcode, operands)
    if form.mnemonic in _IMPLICIT_PR:
        uses |= PR_BIT
    return 0, uses


def _build_tables() -> tuple[list[str], list[Plan], list[int], list[int], bytearray, bytearray]:
    """Per-opcode handler names, pipeline plans, load-use registers, and the rule tables."""
    names: list[str] = []
    plans: list[Plan] = []
    load_dest: list[int] = []
    uses: list[int] = []
    slot_illegal = bytearray(0x10000)
    blocks = bytearray(0x10000)
    illegal = PLANS["illegal"]
    for opcode, form in enumerate(_INDEX):
        if form is None:
            # Undefined code: the illegal instruction sequence replaces it in its ID
            # (PM figure 7.97).
            names.append("_illegal")
            plans.append(illegal)
            load_dest.append(0)
            uses.append(0)
            slot_illegal[opcode] = 1
            continue
        names.append(form.handler)
        plans.append(PLANS[form.pipeline])
        dest, used = _load_use(form, opcode)
        load_dest.append(dest)
        uses.append(used)
        slot_illegal[opcode] = form.mnemonic in PC_CHANGING
        blocks[opcode] = form.mnemonic in INTERRUPT_DISABLING
    return names, plans, load_dest, uses, slot_illegal, blocks


_NAMES, _PLANS, _LOAD_DEST, _USES, _SLOT_ILLEGAL, _BLOCKS = _build_tables()


class SH7604(
    TransferMixin,
    ArithMixin,
    MultiplyMixin,
    LogicMixin,
    ShiftMixin,
    BranchMixin,
    SystemMixin,
    CoreMixin,
    PipelineMixin,
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
        self._no_interrupt = False
        self._address_error = False
        self._nmi_seen = self.nmi
        self._nmi_pending = False
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
        self._plans = _PLANS
        self._load_dest = _LOAD_DEST
        self._uses = _USES
        self._exception_plans = {name: PLANS[name] for name in EXCEPTIONS}

        # The stage scheduler (pipeline.py).
        #: A list to receive the scheduler's events (tests, tools), or None.
        self.pipeline_log: list | None = None
        self._pipeline_reset()

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
        self._no_interrupt = False
        self._address_error = False
        self._nmi_pending = False
        self._nmi_seen = self.nmi
        base = 0 if power_on else 8
        self._p_ma_left = 0
        self.pc = self._read32(base)
        self.R[15] = self._read32(base + 4)
        self._pipeline_restart()
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
            pipeline=self._pipeline_state(),
            modules={module.NAME: module.state() for module in self.modules},
        )

    @property
    def state(self) -> ChipState:
        """The chip's state as a :class:`ChipState` (the brief's ``chip.state``)."""
        return self.capture_state()

    @state.setter
    def state(self, value: ChipState) -> None:
        self.restore_state(value)

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
        if state.pipeline:
            self._load_pipeline_state(state.pipeline)
        else:
            self._p_slot = 0
            self._pipeline_restart()
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
