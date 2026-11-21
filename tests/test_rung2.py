"""Rung 2: MAME traces of 32X cartridges replayed register-exact (validation/rung2).

The traces are not in the repository (gigabytes, and made from Aubrey's romset:
``scripts/mame_trace.py CART --only master --bus --frames 120``); the slow test
replays every ``tests/mame_traces/bus-*/master.log`` it finds and skips when there is
none. The fast test checks the replay machinery itself on a log written in MAME's
format from a program this core runs.
"""

import sys
from pathlib import Path

import pytest
from conftest import PROGRAM, make

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "validation" / "rung2"))

import replay  # noqa: E402

TRACES = sorted((ROOT / "tests" / "mame_traces").glob("bus-*/master.log"))


def _log_line(chip) -> str:
    values = [chip.pc, chip.sr, chip.pr, chip.gbr, chip.vbr, chip.mach, chip.macl, *chip.R]
    return "S " + " ".join(f"{v:X}" for v in values) + f" {chip.clock}"


def test_replay_of_a_log_in_mames_format(tmp_path):
    # A loop with loads, stores, a multiply and a delayed branch; the "MAME" log is
    # written from the core's own run: fetches as 16-bit reads, data as their sizes.
    words = [0xD104, 0xE205, 0x6316, 0x2132, 0x0337, 0x4210, 0x8FFB, 0x7301, 0xAFFE, 0x0009]
    data = {PROGRAM + 0x14: (0x06001000).to_bytes(4, "big")}
    h = make(words, data=data)
    h.load(0x06001000, bytes(range(64)))
    chip = h.chip
    lines = [f"R 0 20 {PROGRAM:X}", f"R 4 20 {0x06008000:X}"]
    accesses: list[str] = []

    def observe(address, width, kind):
        # Data reads, as MAME logs them (fetches are logged separately below).
        if address >> 29 in (0, 1) and not kind & 0x201:
            offset = address & 0x3FFFFF
            value = int.from_bytes(h.high[offset : offset + width // 8], "big")
            accesses.append(f"R {address & 0x7FFFFFF:X} {width:X} {value:X}")

    def write(address, width, value, kind):
        accesses.append(f"W {address & 0x7FFFFFF:X} {width:X} {value:X}")
        bus_write(address, width, value, kind)

    bus_write = chip.write
    chip.observe, chip.write = observe, write
    for _ in range(40):
        lines.append(_log_line(chip))
        op = h.peek16(chip.pc)
        lines.append(f"R {chip.pc & 0x7FFFFFF:X} 10 {op:X}")
        accesses.clear()
        chip.step()
        lines += accesses
    lines.append(_log_line(chip))
    log = tmp_path / "master.log"
    log.write_text("\n".join(lines) + "\n")
    report = replay.Replay(cache=False).run(log, None)
    assert report["stopped_at"] is None, report
    assert report["replayed"] == 40


@pytest.mark.slow
@pytest.mark.skipif(not TRACES, reason="no MAME bus traces in tests/mame_traces")
@pytest.mark.parametrize("log", TRACES, ids=lambda p: p.parent.name)
def test_mame_trace_replays_exactly(log):
    report = replay.Replay(cache=False).run(log, None)
    assert report["stopped_at"] is None, report["stopped_at"]
