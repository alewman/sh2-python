"""The chip's core machinery: stepping, delay slots, exceptions, and every memory access.

``step()`` runs one instruction, or one exception sequence, and returns its states.
The rules it encodes, with their sources:

- **PC.** ``self.pc`` is the address of the next instruction to run. While a handler
  runs, ``self.pc`` is its address + 2, so the PC that PC-relative forms and branch
  displacements use (the instruction's address + 4, PM 6.x) is ``self.pc + 2``.
- **Delay slots.** A delayed branch (``BRA``, ``BSR``, ``BRAF``, ``BSRF``, ``JMP``,
  ``JSR``, ``RTS``, ``RTE``, and ``BT/S``/``BF/S`` when taken) leaves ``pc`` on its slot
  and stores the target in ``delay_target``; the next ``step()`` runs the slot with
  ``self.pc`` set to the target, so a PC-relative slot instruction sees the target + 2
  (U1, PM 6.x: "the PC must point to ... the branch destination + 2"), then continues
  at the target. Branch registers are read before the slot runs (PM 4.1.5). No
  interrupt or address error is accepted between a branch and its slot (HM 4.6.1); a
  slot holding undefined code or a PC-changing instruction is the illegal slot
  exception, saving the branch target (HM 4.5.3, U3). A not-taken ``BT/S``/``BF/S``
  makes no slot (U2: the three emulators; unverified).
- **Interrupts** are accepted between instructions when their level exceeds SR.I3-I0
  (HM 4.4, 5.4), but not right after a delayed branch nor after ``LDC``, ``LDC.L``,
  ``STC``, ``STC.L``, ``LDS``, ``LDS.L``, ``STS``, ``STS.L`` (HM 4.6, table 4.10). IRL is
  sampled each boundary with no noise-canceller latency (U4's build choice).
- **Exceptions** push SR then PC on R15's stack and read the vector at VBR + 4n
  (HM 4.4-4.5; PM 7.7.7: two MAs push, the third reads the vector).
- **Memory.** Each CPU access is routed by A31-A29 (HM table 8.2): the cache area, the
  cache-through area, the associative purge space, the address array, the data array,
  and the on-chip modules at H'FFFFFE00-H'FFFFFFFF. External cycles go to the board's
  callbacks through the BSC, split into port-width cycles (HM 7.4.1).
- **Address errors** (HM table 4.6): a misaligned word or longword access, an odd
  instruction address, a fetch from on-chip module space, a byte access to
  H'FFFFFF00-, a longword access to H'FFFFFE00-H'FFFFFEFF. The faulting access is not
  made (a read gives 0: U15 leaves the access itself open) and the exception (vector 9)
  is taken after the instruction completes (HM 4.3.2), saving the next instruction's
  address; one pending after a delayed branch waits for the slot (HM 4.6.1).
"""

from collections.abc import Callable

from sh2_python.bus import DATA, FETCH, FILL, FIRST, HIT, THROUGH, VECTOR, WRITE
from sh2_python.cache import CCR_ID, CCR_OD

ReadFunction = Callable[[int, int, int], int]
WriteFunction = Callable[[int, int, int, int], None]

M32 = 0xFFFFFFFF
NEVER = 1 << 62
SR_MASK = 0x3F3  # M Q I3-I0 S T: the bits that exist (PM 2.2; "the other bits read 0")

VECTOR_ILLEGAL = 4  # general illegal instruction (HM table 4.3)
VECTOR_SLOT_ILLEGAL = 6
VECTOR_ADDRESS_ERROR = 9
VECTOR_NMI = 11
IRL_VECTOR_BASE = 64  # IRL1 -> 64, IRL2-3 -> 65, ... IRL14-15 -> 71 (HM table 5.3)

WIDTH_MASK = {8: 0xFF, 16: 0xFFFF, 32: M32}


class CoreMixin:
    """Stepping, exceptions and memory; the instruction families are mixed in beside it."""

    # Set by the chip class (cpu.py).
    _table: list
    _base_states: list[int]
    _slot_illegal: bytearray
    _blocks_interrupt: bytearray

    # -- stepping -----------------------------------------------------------------

    def step(self) -> int:
        """Run one instruction or one exception sequence; return the states it took.

        The states are those the pipeline's cursor moved (pipeline.py): over a run they
        add up to PM 7.3's count, the intervals between EX stages.
        """
        start = self.clock
        if self.pc != self._p_pc:
            # The host moved the PC (a test, a replay's resync): the pipeline's fetches
            # were for the old stream.
            self._pipeline_restart()
        if start >= self._next_event:
            self._service_events()
        if self.dmac.active:
            self.dmac.run()
            if self.clock != start:
                # The DMAC's cycles held the bus: the CPU's slots wait for them.
                self._p_start += self.clock - start
        target = self.delay_target
        if target < 0:
            if self._address_error:
                self._address_error = False
                self._exception(VECTOR_ADDRESS_ERROR, self.pc, -1, "address_error")
                self._p_pc = self.pc
                return self.clock - start
            if self.nmi != self._nmi_seen:
                self._nmi_edge()
            if not self._no_interrupt and self._interrupt_due():
                self._take_interrupt()
                self._p_pc = self.pc
                return self.clock - start
            self._no_interrupt = False
            if self.sleeping:
                # SLEEP: the CPU waits for an interrupt (HM 14.3.1). Idle in short
                # slices so the board can raise IRL between them; the pipeline waits.
                idle = min(max(self._next_event - self.clock, 1), 16)
                self._p_start += idle
                self.clock = self._p_start
                return self.clock - start
            pc = self.pc
            if pc & 1 or pc >= 0xFFFFFE00:
                # An odd address, or a fetch from on-chip module space (HM table 4.6):
                # nothing is executed; the saved PC is that address (HM 4.3.2).
                self._exception(VECTOR_ADDRESS_ERROR, pc, -1, "address_error")
                self._p_pc = self.pc
                return self.clock - start
            self._advance(self._p_next_ex - 1)  # through this instruction's IF
            op = self._opcode(pc)
            self.pc = pc + 2
            plan = self._plans[op]
            self._issue(plan, self._load_dest[op], self._uses[op], pc)
            self._table[op](self, op)
            self._retire(plan, pc + 2)
            self._no_interrupt = self._blocks_interrupt[op]
        else:
            # The delay slot (HM 4.6.1: no interrupt or address error between it and
            # its branch).
            pc = self.pc
            self._advance(self._p_next_ex - 1)
            op = self._opcode(pc)
            self.delay_target = -1
            if self._slot_illegal[op]:
                # HM 4.5.3: decoded in the slot; the saved PC is the branch target.
                self._exception(VECTOR_SLOT_ILLEGAL, target, -1, "illegal")
                self._p_pc = self.pc
                return self.clock - start
            self.pc = target
            plan = self._plans[op]
            self._issue(plan, self._load_dest[op], self._uses[op], pc)
            self._table[op](self, op)
            self._retire(plan, target)
            self._no_interrupt = self._blocks_interrupt[op]
        self._p_pc = self.pc
        return self.clock - start

    def _service_events(self) -> None:
        clock = self.clock
        self.frt.run_until(clock)
        self.wdt.run_until(clock)
        self._reschedule()

    def _reschedule(self) -> None:
        self._next_event = min(self.frt.next_event(), self.wdt.next_event())

    def _stall(self, states: int) -> None:
        """A module extends the current access by ``states`` (the DIVU, HM 10.4.1).

        The access's MA lasts until the clock it leaves (pipeline.py ``_ma_close``).
        """
        self.clock += states

    # -- interrupts -----------------------------------------------------------------

    def _nmi_edge(self) -> None:
        """NMI is edge-triggered; ICR.NMIE picks the edge (HM 5.2.1, 5.3.3)."""
        level = self.nmi
        self._nmi_seen = level
        if bool(level) == self.intc.nmi_rising:
            self._nmi_pending = True

    def _interrupt_due(self) -> bool:
        if self._nmi_pending:
            return True
        if self.replay_interrupts:
            return self._injected is not None
        mask = (self.sr >> 4) & 15
        return self.irl > mask or self._onchip_level > mask

    def _take_interrupt(self) -> None:
        """Accept the highest request (HM 5.4): NMI, then IRL vs on-chip by level."""
        self.sleeping = False
        if self._nmi_pending:
            self._nmi_pending = False
            # NMI: level 16, I3-I0 set to 15 (HM 4.4.3).
            self._exception(VECTOR_NMI, self.pc, 15, "interrupt")
            return
        if self.replay_interrupts:
            level, vector = self._injected
            self._injected = None
            if vector < 0:
                self._irl_exception(level)
            else:
                self._exception(vector, self.pc, level, "interrupt")
            return
        irl = self.irl
        if irl >= self._onchip_level:
            # IRL ranks above an on-chip source at the same level (HM table 5.4).
            self._irl_exception(irl)
        else:
            self._exception(self._onchip_vector, self.pc, self._onchip_level, "interrupt")

    def _irl_exception(self, level: int) -> None:
        """An IRL interrupt: its auto-vector (HM table 5.3), or the bus's (ICR.VECMD = 1)."""
        if self.intc.vecmd:
            self._exception(0, self.pc, level, "interrupt", external=level)
        else:
            self._exception(IRL_VECTOR_BASE + (level >> 1), self.pc, level, "interrupt")

    def inject_interrupt(self, level: int, vector: int = -1) -> None:
        """Replay only: make the next boundary accept this interrupt.

        A host that replays another emulator's trace (rung 2) sets
        ``chip.replay_interrupts = True``, which stops IRL and the on-chip modules from
        raising interrupts by themselves, and calls this where the trace took one.
        ``vector`` -1 means an IRL auto-vector (or external vector) for ``level``. The
        acceptance rules of HM 4.6 still apply: the interrupt waits for a boundary
        where one may be taken.
        """
        self._injected = (level, vector)

    def _exception(
        self, vector: int, saved_pc: int, level: int, sequence: str, external: int = -1
    ) -> None:
        """An exception sequence in place of the next instruction (PM 7.7.7).

        It takes the slots of PM figures 7.95-7.97 (``pipeline.PLANS[sequence]``): its
        two pushes and the vector read are its MAs, and the handler's fetch is in the
        slot of its last stage.
        """
        plan = self._exception_plans[sequence]
        self._advance(self._p_next_ex - 1)
        self._issue(plan, 0, 0, self.pc)
        self._enter(vector, saved_pc, level, external)
        self._retire(plan, -1)

    def _enter(self, vector: int, saved_pc: int, level: int, external: int = -1) -> None:
        """Exception entry (HM 4.3.2-4.5): push SR, push PC, fetch the vector, jump.

        ``level`` >= 0 is written to SR.I3-I0 (interrupts; HM 4.4.3), -1 leaves it.
        ``external`` >= 0 is an IRL level whose vector number comes from the bus
        (ICR.VECMD = 1, HM 5.2.3): that read is made in the vector-read MA, before the
        table read. The jump is not a delayed branch.
        """
        R = self.R
        sr = self.sr
        R[15] = (R[15] - 4) & M32
        self._write32(R[15], sr)
        R[15] = (R[15] - 4) & M32
        self._write32(R[15], saved_pc)
        if level >= 0:
            self.sr = (sr & ~0xF0) | (level << 4)
        if external >= 0:
            if self._p_ma_left:
                self._ma_next()
            # External vector mode: A3-A0 carry the level, D7-D0 the vector number
            # (HM 5.2.3, figure 5.4).
            vector = self._bus_read(external, 8, VECTOR) & 0x7F
        self.pc = self._read32((self.vbr + (vector << 2)) & M32)
        self.delay_target = -1
        self._no_interrupt = False
        self._address_error = False

    def _illegal(self, op: int) -> None:
        """Undefined code outside a slot: general illegal instruction (HM 4.5.4).

        The saved PC is the start address of the undefined code. Undefined codes carry
        the illegal sequence's plan in the decode tables (PM figure 7.97), so the step
        has already given it its slots; this only enters the handler.
        """
        self._enter(VECTOR_ILLEGAL, (self.pc - 2) & M32, -1)

    # -- instruction fetch ------------------------------------------------------------

    def _read_fetch(self, address: int) -> int:
        space = address >> 29
        if space == 0:
            if self.cache.enabled:
                return self._cache_read(address, 32, FETCH)
            if self.observe is not None:
                self.observe(address, 32, FETCH | THROUGH)
            return self._bus_read(address, 32, FETCH | THROUGH)
        if space == 1:
            if self.observe is not None:
                self.observe(address, 32, FETCH | THROUGH)
            return self._bus_read(address, 32, FETCH | THROUGH)
        if space == 6:
            return self.cache.get(address & 0xFFC, 32)
        # The purge, address-array, reserved and on-chip spaces hold no code: a fetch
        # there (an overrun past the end of code, or a jump into them, which faults
        # when it executes, HM table 4.6) reads nothing and touches no module.
        return 0

    # -- CPU data accesses ------------------------------------------------------------

    def _read8(self, address: int) -> int:
        return self._read(address, 8)

    def _read16(self, address: int) -> int:
        if address & 1:
            self._address_error = True
            return 0
        return self._read(address, 16)

    def _read32(self, address: int) -> int:
        if address & 3:
            self._address_error = True
            return 0
        return self._read(address, 32)

    def _write8(self, address: int, value: int) -> None:
        self._write(address, 8, value & 0xFF)

    def _write16(self, address: int, value: int) -> None:
        if address & 1:
            self._address_error = True
            return
        self._write(address, 16, value & 0xFFFF)

    def _write32(self, address: int, value: int) -> None:
        if address & 3:
            self._address_error = True
            return
        self._write(address, 32, value & M32)

    def _read(self, address: int, width: int) -> int:
        if self._p_ma_left:
            self._ma_next()  # this access is the instruction's next MA (pipeline.py)
        space = address >> 29
        if space == 0:
            if self.cache.enabled:
                return self._cache_read(address, width, DATA)
            if self.observe is not None:
                self.observe(address, width, THROUGH)
            return self._bus_read(address, width, THROUGH)
        if space == 1:
            if self.observe is not None:
                self.observe(address, width, THROUGH)
            return self._bus_read(address, width, THROUGH)
        if space == 7:
            return self._onchip_read(address, width)
        if space == 6:
            if self.observe is not None:
                self.observe(address, width, DATA)
            return self.cache.get(address & 0xFFF, width)
        if space == 3:
            if width != 32:
                return 0  # longword only (HM 8.4.9); other sizes undefined: read 0
            return self.cache.read_address_array(address)
        if space in (2, 5):
            # U10: a read of the associative purge space (and the reserved H'A0000000
            # space) purges like a write and returns all ones (Mednafen; emulator tier).
            self.cache.purge_line(address)
            return WIDTH_MASK[width]
        return 0  # H'80000000-H'9FFFFFFF: reserved (HM table 7.3)

    def _write(self, address: int, width: int, value: int) -> None:
        if self._p_ma_left:
            self._ma_next()
        space = address >> 29
        if space == 0:
            if self.observe is not None:
                self.observe(address, width, WRITE)
            if self.cache.enabled:
                self.cache.write_hit(address, width, value)  # write-through (HM 8.4.2)
            self._bus_write(address, width, value, THROUGH if not self.cache.enabled else DATA)
        elif space == 1:
            if self.observe is not None:
                self.observe(address, width, WRITE | THROUGH)
            self._bus_write(address, width, value, THROUGH)
        elif space == 7:
            self._onchip_write(address, width, value)
        elif space == 6:
            if self.observe is not None:
                self.observe(address, width, WRITE)
            self.cache.put(address & 0xFFF, width, value)
        elif space == 3:
            if width == 32:
                self.cache.write_address_array(address, value)
        elif space in (2, 5):
            self.cache.purge_line(address)  # HM 8.4.7; U10 for H'A0000000

    def _cache_read(self, address: int, width: int, kind: int) -> int:
        """A read in the cache area with CCR.CE = 1 (HM 8.4.1)."""
        cache = self.cache
        entry = (address >> 4) & 63
        way = cache.lookup(address)
        if way >= 0:
            cache.touch(entry, way)
            if self.observe is not None:
                self.observe(address, width, kind | HIT)
            return cache.get((way << 10) | (entry << 4) | (address & 15), width)
        if self.observe is not None:
            self.observe(address, width, kind)
        if cache.ccr & (CCR_ID if kind & FETCH else CCR_OD):
            # ID/OD: "the missed address data is read and directly transferred to the
            # CPU" without replacing (HM 8.4.5).
            return self._bus_read(address, width, kind)
        way = cache.victim(entry)
        base = cache.allocate(address, way)
        line = address & 0x1FFFFFF0
        flag = FIRST
        # HM 8.4.1, figure 8.4: four longwords from address + 4, wrapping in the line,
        # so the one holding the requested data comes last.
        for step in (4, 8, 12, 0):
            offset = ((address & 0xC) + step) & 0xC
            cache.put(base | offset, 32, self._bus_read(line | offset, 32, kind | FILL | flag))
            flag = 0
        return cache.get(base | (address & 15), width)

    # -- the external bus (through the BSC) -------------------------------------------

    def _bus_read(self, address: int, width: int, kind: int) -> int:
        """One CPU or DMAC read on the external bus, split into port-width cycles."""
        address &= 0x1FFFFFFF
        area = (address >> 25) & 3
        port = self._ports[area]
        if width <= port:
            return self._cycle_read(address, width, kind, area)
        step = port >> 3
        value = 0
        for i in range(width // port):
            value = (value << port) | self._cycle_read(address + i * step, port, kind, area)
        return value

    def _cycle_read(self, address: int, width: int, kind: int, area: int) -> int:
        self.wait = 0
        value = self.read(address, width, kind) & WIDTH_MASK[width]
        # The cycle lasts the BSC's states plus the board's WAIT states; the IF or MA
        # that made it lasts as long as its cycles (PM 7.2.3).
        self.clock += self._cycle_states[area] + self.wait
        return value

    def _bus_write(self, address: int, width: int, value: int, kind: int) -> None:
        address &= 0x1FFFFFFF
        area = (address >> 25) & 3
        port = self._ports[area]
        if width <= port:
            self._cycle_write(address, width, value, kind, area)
            return
        step = port >> 3
        count = width // port
        mask = WIDTH_MASK[port]
        for i in range(count):
            part = (value >> (port * (count - 1 - i))) & mask
            self._cycle_write(address + i * step, port, part, kind, area)

    def _cycle_write(self, address: int, width: int, value: int, kind: int, area: int) -> None:
        self.wait = 0
        self.write(address, width, value, kind)
        self.clock += self._cycle_states[area] + self.wait

    # -- on-chip modules -----------------------------------------------------------------

    def _onchip_read(self, address: int, width: int) -> int:
        if address < 0xFFFFFE00:
            return 0  # reserved I/O space (HM table 7.3); the SDRAM mode space reads nothing
        if (width == 8 and address >= 0xFFFFFF00) or (width == 32 and address < 0xFFFFFF00):
            self._address_error = True  # HM table 4.6: wrong size for that half
            return 0
        if self.observe is not None:
            self.observe(address, width, DATA)
        module = self._onchip[address - 0xFFFFFE00]
        if module is None:
            return 0
        return self.read_onchip(module, address, width)

    def _onchip_write(self, address: int, width: int, value: int) -> None:
        if address < 0xFFFFFE00:
            return  # reserved; the SDRAM mode-register space is not modelled (BSC note)
        if (width == 8 and address >= 0xFFFFFF00) or (width == 32 and address < 0xFFFFFF00):
            self._address_error = True
            return
        if self.observe is not None:
            self.observe(address, width, WRITE)
        module = self._onchip[address - 0xFFFFFE00]
        if module is not None:
            self.write_onchip(module, address, width, value)

    @staticmethod
    def _module_read(module: object, address: int, width: int) -> int:
        """The default ``read_onchip``: the module answers."""
        return module.read(address, width)

    @staticmethod
    def _module_write(module: object, address: int, width: int, value: int) -> None:
        module.write(address, width, value)

    # -- DMA accesses (the DMAC's side of the internal bus) -------------------------------

    def _dma_read(self, address: int, width: int, kind: int) -> int:
        space = address >> 29
        if space in (0, 1):
            return self._bus_read(address, width, kind)  # DMA never uses the cache
        if space == 7 and address >= 0xFFFFFE00:
            module = self._onchip[address - 0xFFFFFE00]
            return 0 if module is None else self.read_onchip(module, address, width)
        if space == 6:
            return self.cache.get(address & 0xFFF, width)
        return 0

    def _dma_write(self, address: int, width: int, value: int, kind: int) -> None:
        space = address >> 29
        if space in (0, 1):
            # No snooping: DMA writes do not update the cache (HM 7.11.2, 8.5.2).
            self._bus_write(address, width, value, kind)
        elif space == 7 and address >= 0xFFFFFE00:
            module = self._onchip[address - 0xFFFFFE00]
            if module is not None:
                self.write_onchip(module, address, width, value)
        elif space == 6:
            self.cache.put(address & 0xFFF, width, value)


__all__ = ["CoreMixin", "ReadFunction", "WriteFunction"]
