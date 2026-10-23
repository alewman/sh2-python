"""Run a program on the minimal harness: ``python -m sh2_python``.

    python -m sh2_python ROM.bin                         # run to a stop; print R0
    python -m sh2_python ROM.bin --max-states 1000000 --trace out.jsonl
    python -m sh2_python ROM.bin --console -c "break $6000100" -c "run"

The ROM image is loaded at H'00000000 and supplies PC and SP from its first two
longwords (``sh2_python.harness``). A run stops on a branch to itself, on SLEEP with
interrupts masked, or at the state budget, and prints the reason, R0 and the counts.
``--trace`` writes every boundary as JSON Lines (``sh2_python.trace``, accesses
included). ``--console`` opens the line debugger (``help`` lists its commands) after
the ``-c`` commands. Numbers are decimal unless written with ``$`` or ``0x``.
"""

import argparse
import sys
from pathlib import Path

from sh2_python.console import CommandDebugger, CommandError, parse_number
from sh2_python.debug import DebugSession
from sh2_python.harness import Harness
from sh2_python.trace import write_trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sh2_python",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("rom", type=Path, help="flat image loaded at H'00000000")
    parser.add_argument("--max-states", default="10000000", help="state budget")
    parser.add_argument("--wait", default="0", help="board wait states added to every bus cycle")
    parser.add_argument("--trace", type=Path, help="write a JSON Lines trace here")
    parser.add_argument("--console", action="store_true", help="open the line debugger")
    parser.add_argument("-c", "--command", action="append", default=[], help="debugger command")
    args = parser.parse_args(argv)
    try:
        max_states = parse_number(args.max_states, "--max-states", maximum=1 << 62)
        wait = parse_number(args.wait, "--wait", maximum=1000)
    except CommandError as exc:
        parser.error(str(exc))
    harness = Harness(args.rom.read_bytes(), wait=wait)
    chip = harness.chip
    chip.reset()

    if args.console or args.command:
        session = DebugSession(harness.chip, peek16=harness.peek16, track_accesses=True)
        debugger = CommandDebugger(session)
        for command in ["registers", "disassemble", *args.command]:
            print(f"sh2> {command}")
            try:
                result = debugger.execute(command)
            except CommandError as exc:
                print(f"error: {exc}")
                continue
            print("\n".join(result.lines))
            if result.quit:
                return 0
        if args.console:
            debugger.interact(sys.stdin, sys.stdout)
        return 0

    if args.trace:
        session = DebugSession(chip, peek16=harness.peek16, history_limit=0, track_accesses=True)

        def records():
            start = chip.clock
            while harness.stopped() is None and chip.clock - start < max_states:
                yield session.step()

        with args.trace.open("w") as stream:
            count = write_trace(records(), stream)
        reason = harness.stopped() or "state-budget"
        print(f"{reason}: R0={chip.R[0]:08X} PC={chip.pc:08X} {count:,} records -> {args.trace}")
        return 0

    result = harness.run(max_states=max_states)
    print(
        f"{result.reason}: R0={result.r0:08X} PC={result.pc:08X} "
        f"{result.steps:,} steps, {result.states:,} states"
    )
    for clock, level in result.ftoa[:16]:
        print(f"  FTOA -> {level} at state {clock}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
