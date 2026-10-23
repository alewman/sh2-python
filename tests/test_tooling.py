"""The debugger, the trace format, the console and the command line."""

import io
import struct

from conftest import PROGRAM, make

from sh2_python.__main__ import main
from sh2_python.console import CommandDebugger
from sh2_python.debug import BoundaryKind, DebugSession, StopReason
from sh2_python.trace import first_trace_divergence, read_trace, write_trace

NOP = 0x0009


def test_breakpoint_stops_before_the_instruction():
    h = make([NOP, NOP, NOP, 0xAFFE, NOP])
    session = DebugSession(h.chip, peek16=h.peek16)
    session.add_breakpoint(PROGRAM + 4)
    result = session.run(max_steps=100)
    assert result.reason is StopReason.BREAKPOINT and h.chip.pc == PROGRAM + 4


def test_slot_boundaries_are_marked():
    h = make([0xA000, NOP, NOP])  # BRA +0; NOP in the slot
    session = DebugSession(h.chip, peek16=h.peek16)
    assert session.step().kind is BoundaryKind.INSTRUCTION
    assert session.step().kind is BoundaryKind.SLOT


def test_watchpoint_sees_a_cache_hit():
    # The watch reaches hits through ``observe`` (the brief's bus-watchpoint rule).
    words = [0xD101, 0x6212, 0x6212, NOP, 0x0600, 0x7000]  # MOV.L @(4,PC),R1; MOV.L @R1,R2 x2
    h = make(words)
    h.chip.cache.write_ccr(0x11)
    session = DebugSession(h.chip, peek16=h.peek16, track_accesses=True)
    session.step()
    session.step()  # the miss fills the line
    session.add_watchpoint(0x06007000, "r")
    result = session.run(max_steps=1)
    assert result.reason is StopReason.WATCHPOINT
    assert result.hits[0][2] is None  # a hit: no bus value
    session.close()


def test_trace_round_trip(tmp_path):
    h = make([0xE00A, 0xE105, 0x301C, 0xAFFE, NOP])
    session = DebugSession(h.chip, peek16=h.peek16, track_accesses=True)
    records = [session.step() for _ in range(4)]
    path = tmp_path / "t.jsonl"
    with path.open("w") as stream:
        assert write_trace(records, stream) == 4
    with path.open() as stream:
        back = list(read_trace(stream))
    assert first_trace_divergence(records, back) is None
    assert back[2].instruction.text.startswith("ADD")


def test_console_commands():
    h = make([0xE00A, 0xAFFE, NOP])
    debugger = CommandDebugger(DebugSession(h.chip, peek16=h.peek16))
    assert "MOV" in debugger.execute("d $06000000 1").lines[0]
    debugger.execute("step")
    assert h.chip.R[0] == 10
    debugger.execute("set r3 $1234")
    assert h.chip.R[3] == 0x1234
    out = io.StringIO()
    debugger.interact(io.StringIO("r\nquit\n"), out)
    assert "R0 =0000000A" in out.getvalue()


def test_command_line_runs_to_a_branch_to_self(tmp_path, capsys):
    words = [0xE00A, 0xE105, 0x301C, 0xAFFE, NOP]
    image = struct.pack(">II", 0x100, 0x06008000) + bytes(0xF8)
    image += b"".join(struct.pack(">H", w) for w in words)
    rom = tmp_path / "t.bin"
    rom.write_bytes(image)
    assert main([str(rom)]) == 0
    assert "branch-to-self: R0=0000000F" in capsys.readouterr().out
