"""The pipeline of PM section 7: its classes, and the stage scheduler that times every step.

Every decode-table form names one of the classes below (``_decode.py``). A class is PM
table 7.2's row for it: the stages one instruction occupies in the five-stage pipeline
(IF ID EX MA WB, plus ``mm`` for the multiplier's busy states), the execution states
without contention, how many slots the *next* instruction's ID stalls, and the figure
in PM 7.7 that draws it. The SH-2 numbers are used throughout (PM table 7.2 note 1:
the SH-1's multiplier stages differ, and PM figures 7.14-7.22 and 7.45-7.52 are the
SH-1's).

**The model** (PM 7.1-7.5, read off the figures; docs/timing.md "The scheduler").
The pipeline is lockstep: time is a sequence of *slots*, one stage of each kind per
slot at most, and every in-flight instruction moves on at a slot boundary. Which slot
each stage falls in is structural:

- the instruction after instruction *k* is decoded (ID) in the slot of *k*'s EX, plus
  the ID stall of *k*'s class (MAC.W 1, LDC.L 2, TAS 3, RTE 3, ...; a taken delayed
  branch stalls its slot 1); EX follows ID, and the stages after EX follow in
  consecutive slots as the class draws them (``EX MA EX MA`` for the memory logic
  operations, ``EX EX MA MA EX MA EX EX`` for an interrupt);
- instruction *k*'s fetch (IF) is in the slot of the previous instruction's ID. A
  fetch is a longword (PM 7.4.2): an instruction at 4n + 2 whose longword the previous
  fetch brought takes no bus cycle (``if``, one state); every other fetch is an ``IF``
  on the bus (or on the cache bus).

How long a slot lasts depends on what is in it (PM 7.2.3, 7.4, 7.5):

- the longest stage; IF and MA last their memory cycles, the others one state;
- an MA and an ``IF`` in the same slot split it: the MA first, then the rest, and the
  slot lasts MA + IF (PM 7.4.1); an MA with an ``if`` does not split (PM 7.4.3);
- a load's MA and the next instruction's EX that uses the loaded register split the
  slot the same way (PM 7.5; not when that instruction is a load to the same register
  or a ``MAC @Rm+,@Rn+``);
- the multiplier runs ``mm`` states after a multiply's last MA, regardless of slots;
  a later instruction's multiplier MA (the MA of MULS.W, the second MA of MAC.W,
  MAC.L, MUL.L, DMULS.L, DMULU.L, the MA of LDS/STS to and from MACH/MACL, CLRMAC)
  is extended until that ``mm`` ends, as one slot (PM 7.7.2's ``M--A``).

Branches redirect the fetch stream (PM 7.7.5): a taken ``BT``/``BF`` fetches the two
instructions after it and discards them, and the target's IF is in the slot after
the branch's EX (three states); a taken delayed branch fetches its slot, stalls the
slot's ID one slot (three for RTE, PM figure 7.92) and fetches the target in the slot
of the slot's ID; a taken ``BT/S``/``BF/S`` also fetches and discards the instruction
after the slot (PM figure 7.80; figure 7.82 draws no such fetch for the unconditional
ones: C13). TRAPA and the illegal instruction exception fetch the two instructions
after them and discard them; an interrupt or an address error replaces the
instruction in its ID, whose fetch and the next one's are discarded; the handler's IF
is in the slot of the sequence's last stage (PM 7.7.7). These discarded fetches are
the overrun fetches of U11: real bus cycles (or cache fills), always made.

**The machinery** (plain integers and lists, transcribable). The scheduler keeps a
cursor: the first slot not yet *finalized*, with its start state. A slot is finalized
(its fetch made, its length fixed) only when everything in it is known, which is when
the cursor passes it. :meth:`_issue` gives an instruction its slots before its handler
runs; each data access the handler makes opens its next MA slot (:meth:`_ma_next`),
which moves the cursor there first, so ``chip.clock`` inside a bus callback is the
state that cycle starts on; :meth:`_retire` closes the MAs, places the fetches a branch
redirects, and leaves the cursor at the instruction's last MA (or its EX). The order
of bus cycles is the order of the slots: in a slot, the MA, then the fetch.
``chip.clock`` between steps is the start of the cursor's slot, so a step returns the
states between its cursor and the last one; over a run they add up to PM's count (the
interval between EX stages, PM 7.3).

``chip.pipeline_log``, when a list (default ``None``, no cost but a test), receives
the scheduler's events: ``("if", slot, address, bus)`` for every fetch placed,
``("issue", ex_slot, class_name, address)`` for every instruction or exception
sequence, ``("ma", slot, states)`` for every MA, ``("mm", state, states)`` for every
multiplier operation, ``("slot", slot, states, split)`` for every slot finalized.
tests/test_pipeline.py rebuilds PM's figures from them.
"""

from typing import NamedTuple

M32 = 0xFFFFFFFF

#: Slots in flight are at most a dozen ahead of the cursor (an interrupt's handler
#: fetch, PM figure 7.95); the ring holds the slots from the cursor on.
RING = 64
RING_MASK = RING - 1

# How an instruction's end redirects the fetch stream (``_retire``).
KIND_PLAIN = 0  # the next fetch is sequential
KIND_COND = 1  # BT, BF: taken, two fetches discarded, target IF after EX
KIND_COND_DELAYED = 2  # BT/S, BF/S: taken, a delayed branch plus one discarded fetch
KIND_BRANCH = 3  # BRA, BRAF, BSR, BSRF, JMP, JSR, RTS, RTE: delayed
KIND_TRAP = 4  # TRAPA, illegal instructions: two discarded fetches, then the handler
KIND_SLEEP = 5  # SLEEP: no further fetch
KIND_INTERRUPT = 6  # interrupts, address errors: the replaced instruction's fetches only

#: Register bits for the load-use rule: R0-R15 are bits 0-15, PR bit 16.
PR_BIT = 1 << 16


class PipelineClass(NamedTuple):
    """One row of PM table 7.2, SH-2 column."""

    name: str
    category: str  # PM table 7.2's "Category" text
    stages: tuple[str, ...]  # the instruction's own stages, as the figure draws them
    states: int  # execution states with no contention (table 7.2 "State")
    next_id_stall: int  # slots the following instruction's ID waits (the figure)
    mm: int  # multiplier states after the last MA (PM 7.7.2)
    figure: str


def _c(name, category, stages, states, stall, mm, figure) -> PipelineClass:
    return PipelineClass(name, category, tuple(stages.split()), states, stall, mm, figure)


CLASSES: dict[str, PipelineClass] = {
    c.name: c
    for c in (
        _c("transfer", "Register-register transfer", "IF ID EX", 1, 0, 0, "PM figure 7.10"),
        _c("load", "Memory load", "IF ID EX MA WB", 1, 0, 0, "PM figure 7.11"),
        _c("store", "Memory store", "IF ID EX MA", 1, 0, 0, "PM figure 7.12"),
        _c("arith", "Arithmetic between registers", "IF ID EX", 1, 0, 0, "PM figure 7.13"),
        _c("mac_w", "Multiply/accumulate", "IF ID EX MA MA mm mm", 3, 1, 2, "PM figure 7.23"),
        _c("mac_l", "Double-length multiply/accumulate", "IF ID EX MA MA mm mm mm mm", 3, 1, 4,
           "PM figure 7.34"),
        _c("mul_w", "Multiplication", "IF ID EX MA mm mm", 1, 0, 2, "PM figure 7.53"),
        _c("mul_l", "Double-length multiplication", "IF ID EX MA MA mm mm mm mm", 2, 1, 4,
           "PM figure 7.63"),
        _c("logic", "Register-register logic operation", "IF ID EX", 1, 0, 0, "PM figure 7.74"),
        _c("logic_mem", "Memory logic operation", "IF ID EX MA EX MA", 3, 2, 0, "PM figure 7.75"),
        _c("tas", "TAS", "IF ID EX MA EX MA", 4, 3, 0, "PM figure 7.76"),
        _c("shift", "Shift", "IF ID EX", 1, 0, 0, "PM figure 7.77"),
        _c("branch_cond", "Conditional branch", "IF ID EX", 1, 0, 0, "PM figures 7.78, 7.79"),
        _c("branch_cond_delayed", "Delayed conditional branch", "IF ID EX", 1, 0, 0,
           "PM figures 7.80, 7.81"),
        _c("branch", "Unconditional branch", "IF ID EX", 2, 1, 0, "PM figure 7.82"),
        _c("system", "System control ALU", "IF ID EX", 1, 0, 0, "PM figure 7.83"),
        _c("ldc_l", "LDC.L", "IF ID EX MA EX", 3, 2, 0, "PM figure 7.84"),
        _c("stc_l", "STC.L", "IF ID EX MA", 2, 1, 0, "PM figure 7.85"),
        _c("lds_l_pr", "LDS.L (PR)", "IF ID EX MA WB", 1, 0, 0, "PM figure 7.86"),
        _c("sts_l_pr", "STS.L (PR)", "IF ID EX MA", 1, 0, 0, "PM figure 7.87"),
        _c("reg_to_mac", "Register to MAC transfer", "IF ID EX MA", 1, 0, 0, "PM figure 7.88"),
        _c("mem_to_mac", "Memory to MAC transfer", "IF ID EX MA", 1, 0, 0, "PM figure 7.89"),
        _c("mac_to_reg", "MAC to register transfer", "IF ID EX MA WB", 1, 0, 0,
           "PM figure 7.90"),
        _c("mac_to_mem", "MAC to memory transfer", "IF ID EX MA", 1, 0, 0, "PM figure 7.91"),
        _c("rte", "RTE", "IF ID EX MA MA", 4, 3, 0, "PM figure 7.92"),
        _c("trap", "TRAP", "IF ID EX EX MA MA MA EX EX", 8, 0, 0, "PM figure 7.93"),
        _c("sleep", "SLEEP", "IF ID EX", 3, 0, 0, "PM figure 7.94"),
    )
}  # fmt: skip

#: The exception sequences of PM 7.7.7. ``states`` is not in PM table 7.2: it is the
#: interval from the sequence's first EX to the handler's first EX in PM figures
#: 7.95-7.97 (the handler's IF in the slot of the last stage), which the scheduler
#: reproduces (tests/test_pipeline.py).
EXCEPTIONS: dict[str, PipelineClass] = {
    "interrupt": _c("interrupt", "Interrupt exception processing",
                    "IF ID EX EX MA MA EX MA EX EX", 9, 0, 0, "PM figure 7.95"),
    "address_error": _c("address_error", "Address error exception processing",
                        "IF ID EX EX MA MA EX MA EX EX", 9, 0, 0, "PM figure 7.96"),
    "illegal": _c("illegal", "Illegal instruction exception processing",
                  "IF ID EX EX MA MA MA EX EX", 8, 0, 0, "PM figure 7.97"),
}  # fmt: skip

#: Extra states of a taken conditional branch over its not-taken 1 (PM table A.50: BT/BF
#: 3/1, BT/S/BF/S 2/1). The scheduler produces them; the table is for the rung-0 test.
TAKEN_EXTRA = {"branch_cond": 2, "branch_cond_delayed": 1}

#: The MA that waits for the multiplier (PM 7.7.2), by class: its index among the
#: class's MAs. MULS.W's only MA; the second MA of the MACs and the double-length
#: multiplies ("the second MA reads the memory and accesses the multiplier"); the MA
#: the MAC transfers add "for accessing the multiplier".
MULTIPLIER_MA = {
    "mul_w": 0, "mac_w": 1, "mac_l": 1, "mul_l": 1,
    "reg_to_mac": 0, "mem_to_mac": 0, "mac_to_reg": 0, "mac_to_mem": 0,
}  # fmt: skip

_KINDS = {
    "branch_cond": KIND_COND,
    "branch_cond_delayed": KIND_COND_DELAYED,
    "branch": KIND_BRANCH,
    "rte": KIND_BRANCH,
    "trap": KIND_TRAP,
    "illegal": KIND_TRAP,
    "sleep": KIND_SLEEP,
    "interrupt": KIND_INTERRUPT,
    "address_error": KIND_INTERRUPT,
}


class Plan(NamedTuple):
    """What the scheduler needs of one class: built once per class by :func:`plan`."""

    mas: tuple[int, ...]  # each MA's slot, counted from the (first) EX
    stall: int  # the next instruction's ID stall
    mul: int  # index in ``mas`` of the MA that waits for the multiplier, or -1
    mm: int  # multiplier states it starts after that MA
    kind: int  # KIND_*: how it redirects the fetch stream
    tail: int  # slots from EX to the last stage (the handler's IF, KIND_TRAP/INTERRUPT)
    name: str


def plan(pclass: PipelineClass) -> Plan:
    """The scheduler's view of a class: the slots of its stages after ID."""
    after_id = pclass.stages[2:]
    mas = tuple(i for i, stage in enumerate(after_id) if stage == "MA")
    tail = max(i for i, stage in enumerate(after_id) if stage != "mm")
    stall = 2 if pclass.name == "sleep" else pclass.next_id_stall  # SLEEP: 3 states
    return Plan(
        mas,
        stall,
        MULTIPLIER_MA.get(pclass.name, -1),
        pclass.mm,
        _KINDS.get(pclass.name, KIND_PLAIN),
        tail,
        pclass.name,
    )


PLANS: dict[str, Plan] = {name: plan(c) for name, c in {**CLASSES, **EXCEPTIONS}.items()}


class PipelineMixin:
    """The stage scheduler (module docstring); mixed into the chip beside CoreMixin."""

    clock: int
    pipeline_log: list | None

    def _pipeline_reset(self) -> None:
        """An empty pipeline at ``self.clock``; the first fetch is at ``self.pc``."""
        self._p_if = [-1] * RING  # per slot: the line a bus fetch reads, -2 'if', -1 none
        self._p_ma = [0] * RING  # per slot: the MA's states, 0 = no MA
        self._p_lu = [0] * RING  # per slot: 1 when a load-use split falls in it
        self._p_slot = 0
        self._p_start = self.clock
        self._p_next_ex = 2
        self._p_ex = 0
        self._p_fetch_pc = self.pc
        self._p_fetch_line = -1
        self._p_mm_end = 0
        self._p_lu_dest = 0
        self._p_lu_slot = -1
        # The instruction's MAs while its handler runs.
        self._p_mas: tuple[int, ...] = ()
        self._p_ma_i = 0
        self._p_ma_left = 0
        self._p_ma_open = -1
        self._p_ma_t0 = 0
        self._p_mul = -1
        self._p_mm = 0
        self._p_ma_is_mul = False
        # The two most recently fetched longwords (line address, value).
        self._fl0 = self._fl1 = -1
        self._fv0 = self._fv1 = 0
        self._pipeline_restart()

    def _pipeline_restart(self) -> None:
        """Refetch from ``self.pc``: a reset, a loaded state without a pipeline, a host's resync.

        The fetch at ``pc`` goes in the cursor's slot, the next in the slot after (its
        ID), and the instruction's EX two slots on, as after a branch (PM figure 7.7).
        """
        for i in range(RING):
            self._p_if[i] = -1
            self._p_ma[i] = 0
            self._p_lu[i] = 0
        slot = self._p_slot
        self._p_start = self.clock
        self._p_fetch_line = -1
        self._p_lu_dest = 0
        self._fl0 = self._fl1 = -1
        target = self.delay_target
        self._p_pc = self.pc
        self._p_fetch_pc = self.pc
        self._place(slot)
        if target >= 0:
            self._p_fetch_pc = target
        self._place(slot + 1)
        self._p_next_ex = slot + 2

    def _drop_fetched(self) -> None:
        """Forget the fetched longwords, so the next opcodes are read again (replay hosts).

        Timing is unchanged; the opcode of the next instruction comes from a fresh read
        of the bus. validation/rung2/replay.py uses it because it fills its memory one
        instruction ahead of the core's fetches.
        """
        self._fl0 = self._fl1 = -1

    # -- the fetch stream ----------------------------------------------------------------

    def _place(self, slot: int) -> None:
        """Put the fetch of the next instruction of the stream in ``slot``."""
        address = self._p_fetch_pc
        line = address & 0xFFFFFFFC
        if address & 2 and line == self._p_fetch_line:
            request = -2  # 'if': the previous fetch brought this longword (PM 7.4.2)
        else:
            request = line
        self._p_fetch_line = line
        self._p_if[slot & RING_MASK] = request
        self._p_fetch_pc = (address + 2) & M32
        if self.pipeline_log is not None:
            self.pipeline_log.append(("if", slot, address, request >= 0))

    def _fetch_into(self, line: int) -> None:
        """One IF: read the longword at ``line`` and keep it beside the previous one."""
        value = self._read_fetch(line)
        self._fl1, self._fv1 = self._fl0, self._fv0
        self._fl0, self._fv0 = line, value

    def _opcode(self, pc: int) -> int:
        """The instruction at ``pc``, from the longword its IF fetched."""
        line = pc & 0xFFFFFFFC
        if line == self._fl0:
            value = self._fv0
        elif line == self._fl1:
            value = self._fv1
        else:
            # Not fetched by the pipeline (a host replaced the PC or dropped the
            # fetches): read it now, outside the slots' timing.
            clock = self.clock
            value = self._read_fetch(line)
            self.clock = clock
            self._fl1, self._fv1 = self._fl0, self._fv0
            self._fl0, self._fv0 = line, value
        return (value & 0xFFFF) if pc & 2 else (value >> 16)

    # -- slots ---------------------------------------------------------------------------

    def _advance(self, target: int) -> None:
        """Finalize every slot before ``target``; ``self.clock`` = the start of ``target``.

        Finalizing a slot makes its fetch (after its MA: the MA has priority, PM
        7.4.1) and fixes its length: MA + IF when an MA meets an ``IF`` or a load-use
        split, else the longest stage (PM 7.2.3, 7.4, 7.5).
        """
        slot = self._p_slot
        start = self._p_start
        if slot < target:
            requests, mas, splits = self._p_if, self._p_ma, self._p_lu
            log = self.pipeline_log
            while slot < target:
                i = slot & RING_MASK
                ma = mas[i]
                request = requests[i]
                if request >= 0:
                    fetch = start + ma
                    self.clock = fetch
                    self._fetch_into(request)
                    length = self.clock - fetch
                    length = ma + (length if length > 1 else 1)
                    split = ma > 0
                elif ma and splits[i]:
                    length = ma + 1
                    split = True
                else:
                    length = ma if ma > 1 else 1
                    split = False
                requests[i] = -1
                mas[i] = splits[i] = 0
                if log is not None:
                    log.append(("slot", slot, length, split))
                start += length
                slot += 1
            self._p_slot = slot
            self._p_start = start
        self.clock = start

    # -- one instruction -----------------------------------------------------------------

    def _issue(self, p: Plan, dest: int, uses: int, pc: int) -> None:
        """Give the next instruction (or sequence) its slots, before its handler runs.

        ``dest`` is the register a load writes in WB (bits as ``PR_BIT``), ``uses`` the
        registers the instruction reads or writes for the load-use rule, ``pc`` its
        address (for ``pipeline_log``; an exception sequence's is the instruction it
        replaces).
        """
        ex = self._p_next_ex
        if uses & self._p_lu_dest and self._p_lu_slot == ex:
            self._p_lu[ex & RING_MASK] = 1  # PM 7.5: the load's MA and this EX split
        self._p_lu_dest = dest
        self._p_lu_slot = ex + 1
        self._p_ex = ex
        mas = p.mas
        self._p_mas = mas
        self._p_ma_i = 0
        self._p_ma_left = len(mas)
        self._p_mul = p.mul
        self._p_mm = p.mm
        if p.kind == KIND_PLAIN:
            # The fetch two instructions on, in the next instruction's ID slot.
            self._place(ex + p.stall)
            self._p_next_ex = ex + 1 + p.stall
        if self.pipeline_log is not None:
            self.pipeline_log.append(("issue", ex, p.name, pc))

    def _ma_next(self) -> None:
        """Open the instruction's next MA: move the cursor to its slot (one data access)."""
        if self._p_ma_open >= 0:
            self._ma_close()
        i = self._p_ma_i
        slot = self._p_ex + self._p_mas[i]
        self._p_ma_i = i + 1
        self._p_ma_left -= 1
        self._advance(slot)
        self._p_ma_open = slot
        self._p_ma_t0 = self.clock
        self._p_ma_is_mul = i == self._p_mul

    def _ma_close(self) -> None:
        """Fix the open MA's states: its accesses' cycles, at least one, and the multiplier.

        A multiplier MA lasts until the running ``mm`` ends (PM 7.7.2's ``M--A``); a
        multiply then starts its own ``mm`` when its MA ends.
        """
        slot = self._p_ma_open
        t0 = self._p_ma_t0
        length = self.clock - t0
        if length < 1:
            length = 1
        if self._p_ma_is_mul:
            if self._p_mm_end - t0 > length:
                length = self._p_mm_end - t0
            if self._p_mm:
                self._p_mm_end = t0 + length + self._p_mm
                if self.pipeline_log is not None:
                    self.pipeline_log.append(("mm", t0 + length, self._p_mm))
        self._p_ma[slot & RING_MASK] = length
        self._p_ma_open = -1
        if self.pipeline_log is not None:
            self.pipeline_log.append(("ma", slot, length))

    def _retire(self, p: Plan, sequential: int) -> None:
        """After the handler: the MAs it did not use, the redirected fetches, the cursor.

        ``sequential`` is the address the next instruction would have without a
        branch (a ``BT``/``BF`` that moved ``self.pc`` elsewhere was taken).
        """
        while self._p_ma_left:
            self._ma_next()  # an MA with no data access: the multiplier's, or a fault's
        if self._p_ma_open >= 0:
            self._ma_close()
        kind = p.kind
        if kind:
            ex = self._p_ex
            if kind == KIND_BRANCH:
                # PM figures 7.82, 7.92: the slot's ID stalls; the target's IF is in it.
                self._p_fetch_pc = self.delay_target
                self._place(ex + p.stall)
                self._p_next_ex = ex + 1 + p.stall
            elif kind == KIND_COND_DELAYED:
                self._place(ex)  # PM figure 7.80: fetched and discarded when taken
                if self.delay_target >= 0:
                    self._p_fetch_pc = self.delay_target
                    self._place(ex + 1)
                    self._p_next_ex = ex + 2
                else:
                    self._p_next_ex = ex + 1
            elif kind == KIND_COND:
                self._place(ex)
                if self.pc != sequential:
                    # PM figure 7.78: two fetches discarded, the target's IF after EX.
                    self._p_fetch_pc = self.pc
                    self._place(ex + 1)
                    self._place(ex + 2)
                    self._p_next_ex = ex + 3
                else:
                    self._p_next_ex = ex + 1
            elif kind == KIND_SLEEP:
                self._p_next_ex = ex + 1 + p.stall  # PM figure 7.94: no further fetch
            else:
                if kind == KIND_TRAP:
                    self._place(ex)  # PM figures 7.93, 7.97: the third instruction's IF
                # PM 7.7.7: the handler's IF in the slot of the sequence's last stage.
                self._p_fetch_pc = self.pc
                self._place(ex + p.tail)
                self._place(ex + p.tail + 1)
                self._p_next_ex = ex + p.tail + 2
        mas = p.mas
        self._advance(self._p_ex + mas[-1] if mas else self._p_ex)

    def _pipeline_state(self) -> tuple[int, ...]:
        """The scheduler's in-flight state at a step boundary, for :class:`ChipState`."""
        base = self._p_slot
        ring = []
        for slot in range(base, base + 16):
            i = slot & RING_MASK
            ring += (self._p_if[i], self._p_ma[i], self._p_lu[i])
        return (
            base, self._p_start, self._p_next_ex, self._p_ex, self._p_fetch_pc,
            self._p_fetch_line, self._p_mm_end, self._p_lu_dest, self._p_lu_slot,
            self._fl0, self._fv0, self._fl1, self._fv1, *ring,
        )  # fmt: skip

    def _load_pipeline_state(self, values: tuple[int, ...]) -> None:
        (
            base, self._p_start, self._p_next_ex, self._p_ex, self._p_fetch_pc,
            self._p_fetch_line, self._p_mm_end, self._p_lu_dest, self._p_lu_slot,
            self._fl0, self._fv0, self._fl1, self._fv1,
        ) = values[:13]  # fmt: skip
        self._p_slot = base
        ring = values[13:]
        for i in range(RING):
            self._p_if[i], self._p_ma[i], self._p_lu[i] = -1, 0, 0
        for k in range(16):
            i = (base + k) & RING_MASK
            self._p_if[i], self._p_ma[i], self._p_lu[i] = ring[3 * k : 3 * k + 3]
        self._p_ma_left = 0
        self._p_ma_open = -1
        self._p_pc = self.pc


__all__ = [
    "CLASSES",
    "EXCEPTIONS",
    "KIND_PLAIN",
    "PLANS",
    "PR_BIT",
    "TAKEN_EXTRA",
    "PipelineClass",
    "PipelineMixin",
    "Plan",
]
