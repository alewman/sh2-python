"""A line-command debugger over :class:`~sh2_python.debug.DebugSession`.

``CommandDebugger(session).execute("step 3")`` returns the lines a terminal would
print; :meth:`CommandDebugger.interact` is the prompt loop ``python -m sh2_python
--console`` runs. Numbers are decimal, or hexadecimal written ``$1F`` or ``0x1F``, the
rule of every console in this family.
"""

from dataclasses import dataclass
from typing import TextIO

from sh2_python.debug import BoundaryKind, DebugSession, StepRecord
from sh2_python.disasm import disassemble

HELP = """\
registers | r            show the registers
step [n] | s [n]         run n boundaries (default 1), showing each
run [n] | c [n]          run until a breakpoint, watchpoint, masked SLEEP or n steps
break ADDR | b ADDR      stop before the instruction at ADDR
delete ADDR              remove a breakpoint
watch ADDR [r|w|rw]      stop after a step that touches ADDR (cache hits included)
unwatch ADDR             remove a watchpoint
disassemble [ADDR] [N]   N instructions from ADDR (default: PC, 8) | d
memory ADDR [N]          N bytes from ADDR (default 64) | m
set REG VALUE            set r0-r15, sr, gbr, vbr, mach, macl, pr or pc
irl LEVEL                drive the IRL pins (0-15)
history [N]              the last N boundaries (default 10)
help                     this text
quit | q                 leave"""


class CommandError(Exception):
    """A command that could not be run; the message says why."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    lines: tuple[str, ...] = ()
    quit: bool = False


def parse_number(text: str, name: str = "number", *, maximum: int = 0xFFFFFFFF) -> int:
    body = text.strip()
    try:
        if body.startswith("$"):
            value = int(body[1:], 16)
        elif body[:2].lower() == "0x":
            value = int(body[2:], 16)
        else:
            value = int(body, 10)
    except ValueError:
        raise CommandError(f"{name}: {text!r} is not a number") from None
    if not 0 <= value <= maximum:
        raise CommandError(f"{name}: {text} is out of range 0..{maximum:#x}")
    return value


class CommandDebugger:
    def __init__(self, session: DebugSession) -> None:
        self.session = session

    def registers(self) -> list[str]:
        chip = self.session.chip
        r = chip.R
        rows = [" ".join(f"R{i + j:<2}={r[i + j]:08X}" for j in range(4)) for i in range(0, 16, 4)]
        sr = chip.sr
        bits = zip("MQST", (512, 256, 2, 1), strict=True)
        flags = "".join(c if sr & bit else "." for c, bit in bits)
        rows.append(
            f"PC={chip.pc:08X} SR={sr:03X} {flags} I={(sr >> 4) & 15} GBR={chip.gbr:08X} "
            f"VBR={chip.vbr:08X}"
        )
        extra = f"MACH={chip.mach:08X} MACL={chip.macl:08X} PR={chip.pr:08X} clock={chip.clock}"
        if chip.delay_target >= 0:
            extra += f" SLOT->{chip.delay_target:08X}"
        if chip.sleeping:
            extra += " SLEEPING"
        rows.append(extra)
        return rows

    def _describe(self, record: StepRecord) -> str:
        if record.kind in (BoundaryKind.INSTRUCTION, BoundaryKind.SLOT):
            text = record.instruction.text if record.instruction else "?"
            slot = " (slot)" if record.kind is BoundaryKind.SLOT else ""
            return f"{record.before.pc:08X}  {text + slot:40} {record.states:4} states"
        return f"{record.before.pc:08X}  <{record.kind.value}> {record.states} states"

    def disassemble(self, address: int | None, count: int) -> list[str]:
        peek = self.session.peek16
        if peek is None:
            raise CommandError("the session has no peek16: nothing to disassemble from")
        if address is None:
            address = self.session.chip.pc
        lines = []
        for _ in range(count):
            instruction = disassemble(peek, address)
            lines.append(f"{address:08X}  {instruction.opcode:04X}  {instruction.text}")
            address = (address + 2) & 0xFFFFFFFF
        return lines

    def memory(self, address: int, count: int) -> list[str]:
        peek = self.session.peek16
        if peek is None:
            raise CommandError("the session has no peek16: no memory to show")
        lines = []
        for row in range(address & ~1, address + count, 16):
            data = b"".join(
                peek((row + i) & 0xFFFFFFFF).to_bytes(2, "big") for i in range(0, 16, 2)
            )
            text = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
            lines.append(f"{row & 0xFFFFFFFF:08X}  {data.hex(' ')}  {text}")
        return lines

    def execute(self, line: str) -> CommandResult:
        words = line.split()
        if not words:
            return CommandResult()
        command, arguments = words[0].lower(), words[1:]
        session = self.session
        if command in ("quit", "q", "exit"):
            return CommandResult(quit=True)
        if command == "help":
            return CommandResult(tuple(HELP.splitlines()))
        if command in ("registers", "r", "regs"):
            return CommandResult(tuple(self.registers()))
        if command in ("step", "s"):
            count = parse_number(arguments[0], "count") if arguments else 1
            return CommandResult(tuple(self._describe(session.step()) for _ in range(count)))
        if command in ("run", "c", "continue"):
            count = parse_number(arguments[0], "count") if arguments else 1_000_000
            result = session.run(max_steps=max(count, 1))
            lines = [
                f"{result.reason.value} after {result.steps:,} steps, {result.states:,} states"
            ]
            lines += [
                f"  {rw} {address:08X} width {width}" for rw, address, _v, width, _k in result.hits
            ]
            return CommandResult(tuple(lines + self.registers()))
        if command in ("break", "b"):
            session.add_breakpoint(parse_number(self._one(arguments, "address"), "address"))
            return CommandResult(
                (f"breakpoints: {', '.join(f'{a:08X}' for a in sorted(session.breakpoints))}",)
            )
        if command == "delete":
            session.remove_breakpoint(parse_number(self._one(arguments, "address"), "address"))
            return CommandResult()
        if command == "watch":
            if not arguments:
                raise CommandError("watch needs an address")
            try:
                session.add_watchpoint(
                    parse_number(arguments[0], "address"),
                    arguments[1] if len(arguments) > 1 else "rw",
                )
            except ValueError as exc:
                raise CommandError(str(exc)) from None
            return CommandResult()
        if command == "unwatch":
            session.remove_watchpoint(parse_number(self._one(arguments, "address"), "address"))
            return CommandResult()
        if command in ("disassemble", "d", "dis"):
            address = parse_number(arguments[0], "address") if arguments else None
            count = parse_number(arguments[1], "count") if len(arguments) > 1 else 8
            return CommandResult(tuple(self.disassemble(address, count)))
        if command in ("memory", "m"):
            address = parse_number(self._one(arguments[:1], "address"), "address")
            count = parse_number(arguments[1], "count", maximum=4096) if len(arguments) > 1 else 64
            return CommandResult(tuple(self.memory(address, count)))
        if command == "set":
            if len(arguments) != 2:
                raise CommandError("set needs a register and a value")
            self._set(arguments[0].lower(), parse_number(arguments[1], arguments[0]))
            return CommandResult(tuple(self.registers()))
        if command == "irl":
            session.chip.irl = parse_number(self._one(arguments, "level"), "level", maximum=15)
            return CommandResult()
        if command == "history":
            count = parse_number(arguments[0], "count") if arguments else 10
            records = list(session.iter_history())[-count:] if count else []
            return CommandResult(tuple(self._describe(record) for record in records))
        raise CommandError(f"unknown command {command!r} (try help)")

    @staticmethod
    def _one(arguments: list[str], name: str) -> str:
        if len(arguments) != 1:
            raise CommandError(f"expected one {name}")
        return arguments[0]

    def _set(self, name: str, value: int) -> None:
        chip = self.session.chip
        if name.startswith("r") and name[1:].isdigit() and int(name[1:]) < 16:
            chip.R[int(name[1:])] = value
        elif name == "sr":
            chip.sr = value & 0x3F3
        elif name in ("gbr", "vbr", "mach", "macl", "pr"):
            setattr(chip, name, value)
        elif name == "pc":
            chip.pc = value
            chip.delay_target = -1
        else:
            raise CommandError(f"no register {name!r}")

    def interact(self, stdin: TextIO, stdout: TextIO) -> None:
        while True:
            stdout.write("sh2> ")
            stdout.flush()
            line = stdin.readline()
            if not line:
                return
            try:
                result = self.execute(line)
            except CommandError as exc:
                stdout.write(f"error: {exc}\n")
                continue
            for text in result.lines:
                stdout.write(text + "\n")
            if result.quit:
                return


__all__ = ["CommandDebugger", "CommandError", "CommandResult", "parse_number"]
