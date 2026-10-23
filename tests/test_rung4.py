"""Rung 4: hardware numbers (docs/validation.md "What ran, rung 4").

Three sources, all hardware-captured (the only ones this core has):

- **DN's purge-order values** (P2 tests 185 and 186, ``lab_values.csv``): a judge for the
  order of a CCR purge and a buffered store (U9, C20). Exact.
- **DN's CPU loops** (P2's 185 timed loops, ``lab_cpu.csv``): a judge for what stays
  inside the CPU and its cache (load-use, MA/IF, multiplier issue and result, branches,
  cache hits); a detector for loops that cross the CPS3's bus. Rebuilt and run by
  ``validation/rung4/dn.py``, which says how.
- **32xspd's "cached read" loop** on a real 32X (the photograph, jgenesis #493; 11
  states per iteration, the 32XDK wiki and timing.md "The cache").

And the core behaviour rung 4 added to meet them: the BSC's write buffer (HM 7.11.2).
"""

import itertools
import struct
import sys
from pathlib import Path

import pytest
from test_pipeline import DATA, Figure, _board

from sh2_python import SH7604

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "validation" / "rung4"))

import dn  # noqa: E402
import sh2asm  # noqa: E402

STORE = 0x2B82  # MOV.L R8,@R11 (R11 = DATA + 0x2C0 on the test board)
STORE2 = 0x2A82  # MOV.L R8,@R10
LOAD = 0x6A92  # MOV.L @R9,R10
NOP = 0x0009


def _mas(program: list[int], waits: dict[int, int]) -> list[tuple[int, int]]:
    """(slot, states) of every MA when ``program`` runs on PM's one-state test board."""
    fig = Figure("", "", [*program, NOP, NOP, NOP, NOP, NOP], [], waits=waits)
    chip, _ = _board(fig)
    chip.pipeline_log = log = []
    chip._pipeline_restart()
    for _ in range(len(program) + 3):
        chip.step()
    return [(e[1], e[2]) for e in log if e[0] == "ma"]


# -- the write buffer (HM 7.11.2) ---------------------------------------------------------


def test_a_store_lasts_one_state_while_its_cycle_runs_on():
    # "When the right to use the internal bus is held, the CPU is notified that the write
    # is completed without waiting for the actual writing ... to end": a store to a
    # 4-wait device still takes one state of the CPU's time.
    assert _mas([STORE], {DATA + 0x40 * 11: 4})[0][1] == 1


def test_a_second_store_waits_for_the_first_ones_cycle():
    # One level of buffer: the second store is taken when the first one's cycle (1 + 4
    # states here) has ended, then the CPU goes on one state later.
    first, second = _mas([STORE, STORE2], {DATA + 0x40 * 11: 4})[:2]
    assert first[1] == 1 and second[1] == 5


def test_a_read_waits_for_a_buffered_write():
    # "During reads, the CPU always has to wait for the end of the operation": the load
    # after the store starts its own cycle when the store's has ended.
    store, load = _mas([STORE, LOAD], {DATA + 0x40 * 11: 4})[:2]
    assert store[1] == 1 and load[1] == 5  # 4 states left of the write, then its own 1


def test_an_onchip_access_does_not_wait_for_a_buffered_write():
    # "performing a read or write with an on-chip peripheral module enables an access to
    # the on-chip peripheral module without having to wait for the completion of the
    # write to low-speed memory": MOV.B @R0,R1 with R0 = H'FFFFFE10 (FRT TIER).
    fig = Figure("", "", [STORE, 0x6104, NOP, NOP, NOP, NOP, NOP], [], waits={DATA + 0x2C0: 6})
    chip, _ = _board(fig)
    chip.R[0] = 0xFFFFFE10
    chip.pipeline_log = log = []
    chip._pipeline_restart()
    for _ in range(4):
        chip.step()
    assert [e[2] for e in log if e[0] == "ma"][:2] == [1, 1]


def test_a_ccr_purge_waits_behind_a_buffered_store_and_hits_use_the_old_line():
    # U9 (DN, hardware): store through the cache-through alias, CCR = H'11, cached read.
    # The read hits the line from before the purge; once the store's cycles end the
    # purge has happened and the next read misses.
    source = " mov.l r0,@r7\n mov.b r1,@r2\n mov.l @r6,r3\n" + " nop\n" * 16 + " mov.l @r6,r4\n"
    program = sh2asm.assemble(source, 0xC0000000)  # run from the data array: no fetches
    memory = bytearray(0x1000)
    chip: SH7604

    def read(address: int, width: int, kind: int) -> int:
        offset = address & 0xFFF
        return int.from_bytes(memory[offset : offset + width // 8], "big")

    def write(address: int, width: int, value: int, kind: int) -> None:
        # A slow device: with WCR's reset value area 0 has BCR1's long wait (6), so the
        # store's cycle lasts 2 + 6 + 6 = 14 states, the 16 NOPs fewer than that.
        chip.wait += 6
        offset = address & 0xFFF
        memory[offset : offset + width // 8] = value.to_bytes(width // 8, "big")

    chip = SH7604(read, write)
    chip.cache.write_ccr(0x01)
    memory[0x100:0x104] = (0x11111111).to_bytes(4, "big")
    chip.R[6] = 0x00000100
    chip._read32(0x100)  # the line is cached (outside any instruction)
    memory[0x100:0x104] = (0x22222222).to_bytes(4, "big")  # memory changes behind it
    chip.R[7], chip.R[0] = 0x20000200, 0x33333333  # an unrelated cache-through store
    chip.R[2], chip.R[1] = 0xFFFFFE92, 0x11  # CCR = CP | CE
    for i, word in enumerate(program.words):
        chip.cache.put(i * 2, 16, word)
    chip.pc = 0xC0000000
    for _ in range(20):
        chip.step()
    assert chip.R[3] == 0x11111111  # hit on the old line: the purge was still waiting
    assert chip.R[4] == 0x22222222  # the purge landed; this read missed and refilled


def test_the_write_buffer_is_in_the_saved_state():
    fig = Figure("", "", [STORE, NOP, LOAD, NOP, NOP, NOP, NOP, NOP], [], waits={DATA + 0x2C0: 5})
    chip, _ = _board(fig)
    chip._pipeline_restart()
    chip.step()
    saved = chip.state
    first = [chip.step() for _ in range(4)]
    chip.state = saved
    chip.bsc.cycle_states[:] = [1, 1, 1, 1]
    assert [chip.step() for _ in range(4)] == first


# -- DN's loops and values (P2, CPS3) ------------------------------------------------------


def test_every_p2_routine_rebuilds_where_p2_put_it():
    # The rebuilt routines' instructions and literal pools land on exactly the halfwords
    # the probe's instruction list marks (dn.build raises otherwise): 185 routines.
    for loop in dn.LOOPS.values():
        dn.build(loop)


def test_purge_order_values_are_dns_exactly():
    values = dn.run_values()
    assert values["data_alias"]["core"] == ["00001111", "00001122", "00002222", "00002222"]
    assert values["data_purge"]["core"] == ["00002222"] * 4
    for name in ("data_alias", "data_purge"):
        assert values[name]["core"] == values[name]["board"]


def test_an_immediate_purge_gives_the_emulators_pattern_instead(monkeypatch):
    # The control DN predicts for "a cache with synchronous stores, immediate purges":
    # A = 1111 and B = 1122 on every repetition. The deferred purge is what matches.
    from sh2_python.cache import Cache

    def immediate(self, address, value):
        if address == 0xFFFFFE92:
            self.write_ccr(value)

    monkeypatch.setattr(Cache, "write8", immediate)
    values = dn.run_values()
    assert values["data_alias"]["core"] == ["00001111"] * 4
    assert values["data_purge"]["core"] == ["00001122"] * 4


#: Loops whose warm board samples are not the core's because of the board's unpublished
#: working set, with the evidence: the difference is one work-RAM line fill (P1: 35.97
#: clocks = 4.5 ticks), so the board's warm sample took a miss the rebuilt one does not.
WORKING_SET = {129: "two-way, three lines in sets 0-2: the board is 4.5 ticks slower"}

#: A quick subset for the normal suite; the slow test runs every loop.
QUICK = (0, 40, 61, 1, 2, 26, 27, 30, 31, 32, 82, 85, 88, 89, 90, 151, 152, 156, 157, 158,
         161, 162, 116, 118, 177)  # fmt: skip


def _gate(ids):
    runs = dn.run_all(list(ids))
    low, high = dn.fit_runner(runs)
    assert low <= high, "no runner constant fits the three empty loops"
    rows = dn.evaluate(runs, (low + high) // 2)
    resident = [r for r in rows if r["resident"]]
    misses = [r for r in resident if not r["ok"] and r["id"] not in WORKING_SET]
    assert not misses, [(r["name"], r["model_warm"] / 8, r["board_warm"]) for r in misses]
    for r in resident:
        if r["id"] in WORKING_SET:
            assert 3.5 < -r["error_ticks"] < 5.5, r
    return rows


def test_cpu_resident_loops_fall_in_dns_warm_spread():
    rows = _gate(QUICK)
    assert all(r["resident"] for r in rows)


@pytest.mark.slow
def test_every_cpu_resident_loop_falls_in_dns_warm_spread():
    rows = _gate(dn.LOOPS)
    assert sum(r["resident"] for r in rows) == 68


# -- 32xspd's cached read (a real 32X) -------------------------------------------------------

#: The "cached read" loop of 32xspd (bakemono; reference/roms/32xspd.32x, file offset
#: H'10B0, run from the cartridge at H'020010B0): R1 and R0 from its literal pool, then
#: four longword loads, ADD, CMP/EQ, BF/S with ADD in the slot. Short and attributed;
#: the ROM itself is not in the repository.
SPD_LOOP = (0xD163, 0xD064, 0x6212, 0x5211, 0x5212, 0x5213, 0x7100, 0x8801, 0x8FF8, 0x70FF)
SPD_AT = 0x020010B0
SPD_POOL = {0x02001240: 0x06010000, 0x02001244: 0x00000200}  # R1 (SDRAM), R0 (512)


def _spd_states_per_iteration() -> list[int]:
    area1, area3 = bytearray(0x2000), bytearray(0x20000)
    area1[SPD_AT & 0x1FFF : (SPD_AT & 0x1FFF) + 20] = struct.pack(">10H", *SPD_LOOP)
    for address, value in SPD_POOL.items():
        area1[address & 0x1FFF : (address & 0x1FFF) + 4] = value.to_bytes(4, "big")

    def region(address):
        area = (address >> 25) & 3
        if area == 1:
            return area1, address & 0x1FFF
        return area3, address & 0x1FFFF

    def read(address, width, kind):
        memory, offset = region(address)
        return int.from_bytes(memory[offset : offset + width // 8], "big")

    def write(address, width, value, kind):
        memory, offset = region(address)
        memory[offset : offset + width // 8] = value.to_bytes(width // 8, "big")

    chip = SH7604(read, write)
    chip.cache.write_ccr(0x11)  # the cache on, four-way (the loop is the "cached" one)
    chip.pc = SPD_AT
    arrivals = []
    while len(arrivals) < 40:
        if chip.pc == SPD_AT + 4 and chip.delay_target < 0:
            arrivals.append(chip.clock)
        chip.step()
    return [b - a for a, b in itertools.pairwise(arrivals)]


def test_32xspd_cached_read_loop_is_11_states_per_iteration():
    steady = _spd_states_per_iteration()[4:]
    assert set(steady) == {11}


def test_32xspd_loop_is_the_roms():
    rom = ROOT / "reference" / "roms" / "32xspd.32x"
    if not rom.exists():
        pytest.skip("reference/roms/32xspd.32x not fetched (scripts/fetch_references.py)")
    data = rom.read_bytes()
    offset = SPD_AT & 0xFFFFF
    assert struct.unpack(">10H", data[offset : offset + 20]) == SPD_LOOP
    for address, value in SPD_POOL.items():
        assert int.from_bytes(data[address & 0xFFFFF : (address & 0xFFFFF) + 4], "big") == value
    # The photograph's 31,840 KB/s: 1,990 batches of 512 iterations of 16 bytes in 30
    # frames (the result is batches x 16, timing.md). At 23.01136 MHz and 59.92 frames
    # a second that is 5,789 clocks a batch: at most 11.3 states an iteration.
    clocks = 23_011_360 * 30 / 59.92 / (31_840 / 16)
    assert 11 * 512 < clocks < 12 * 512


def test_assembler_round_trips_every_dn_text():
    for loop in dn.LOOPS.values():
        program = dn.build(loop)
        sh2asm.check_round_trip(program)
    assert sh2asm.assemble(" mov.l @(4,r15),r0\n", 0).words == [0x50F1]
    assert sh2asm.assemble("a:\n bf/s a\n nop\n", 0x100).words == [0x8FFE, NOP]
