"""Rung 0: the on-chip modules' rules that the manuals settle (and U12-U14's choices).

Most tests talk to the chip through programs or through its own access path
(``chip._read``/``chip._write``, what a CPU access does), so the on-chip register map,
the size rules and the side effects are exercised together.
"""

import pytest
from conftest import make

from sh2_python import FETCH, FILL, FIRST, HIT, SH7604, THROUGH
from sh2_python.cache import lru_victim
from sh2_python.divu import partial_64


def bare(**options):
    """A chip whose bus logs every cycle and reads a pattern (address-derived)."""
    log = []

    def read(address, width, kind):
        log.append(("r", address, width, kind))
        return (address * 0x01010101) & ((1 << width) - 1)

    def write(address, width, value, kind):
        log.append(("w", address, width, value, kind))

    chip = SH7604(read, write, **options)
    return chip, log


# -- DIVU (HM chapter 10; U12-U14) ---------------------------------------------------


def divide32(chip, dividend, divisor, ovfie=False):
    chip._write(0xFFFFFF08, 32, 2 if ovfie else 0)  # DVCR
    chip._write(0xFFFFFF00, 32, divisor & 0xFFFFFFFF)  # DVSR
    chip._write(0xFFFFFF04, 32, dividend & 0xFFFFFFFF)  # DVDNT: starts 32 / 32
    return chip._read(0xFFFFFF14, 32), chip._read(0xFFFFFF10, 32)  # quotient, remainder


def test_divu_32_by_32_quotient_and_remainder():
    chip, _ = bare()
    assert divide32(chip, 100, 7) == (14, 2)
    assert divide32(chip, -100, 7) == ((-14) & 0xFFFFFFFF, (-2) & 0xFFFFFFFF)  # truncation


def test_divu_read_waits_for_the_39_states():
    # HM 10.4.1: an access during the division "is continuously extended until the
    # operation ends".
    chip, _ = bare()
    chip._write(0xFFFFFF00, 32, 3)
    start = chip.clock
    chip._write(0xFFFFFF04, 32, 30)
    chip._read(0xFFFFFF14, 32)
    assert chip.clock - start == 39


def test_u13_most_negative_by_minus_one():
    # U13: Mednafen's answer, H'80000000 remainder 0, no overflow.
    chip, _ = bare()
    assert divide32(chip, -0x80000000, -1) == (0x80000000, 0)
    assert chip._read(0xFFFFFF08, 32) & 1 == 0


def test_u13_divide_by_zero_saturates():
    chip, _ = bare()
    q, _ = divide32(chip, 1000, 0)
    assert q == 0x7FFFFFFF and chip._read(0xFFFFFF08, 32) & 1 == 1
    q, _ = divide32(chip, -1000, 0)
    assert q == 0x80000000


def test_u12_64_by_32_overflow_partial_result():
    # HM 10.3.3 with OVFIE = 0: DVDNTH holds the partial result, DVDNTL saturates.
    chip, _ = bare()
    chip._write(0xFFFFFF08, 32, 0)
    chip._write(0xFFFFFF00, 32, 2)
    chip._write(0xFFFFFF10, 32, 0x10000000)
    chip._write(0xFFFFFF14, 32, 0)  # starts 64 / 32: quotient 2^59 overflows
    assert chip._read(0xFFFFFF08, 32) & 1 == 1
    assert chip._read(0xFFFFFF14, 32) == 0x7FFFFFFF
    assert chip._read(0xFFFFFF10, 32) == partial_64(0x10000000_00000000, 2) >> 32


def test_u14_mirrors_and_word_access():
    # U14: DVDNTH/DVDNTL read-only mirrors at +8 (MAME's map); a word access returns
    # the matching half of the longword (a recorded choice).
    chip, _ = bare()
    divide32(chip, 0x12345678, 1)
    assert chip._read(0xFFFFFF1C, 32) == 0x12345678
    assert chip._read(0xFFFFFF16, 16) == 0x5678
    assert chip._read(0xFFFFFF14, 16) == 0x1234


# -- the cache (HM chapter 8) ------------------------------------------------------------


def test_lru_selection_table_8_4():
    assert lru_victim(0) == 3  # after a purge: way 3, then 2, 1, 0 (HM 8.4.5)
    assert lru_victim(0b111000) == 0
    assert lru_victim(0b000110) == 1
    assert lru_victim(0b000001) == 2


def test_lru_order_after_a_purge_is_3_2_1_0():
    chip, _ = bare()
    cache = chip.cache
    for expected in (3, 2, 1, 0):
        way = cache.victim(0)
        assert way == expected
        cache.allocate(0x1000 * (expected + 1), way)


def test_miss_fills_four_longwords_requested_last():
    # HM 8.4.1, figure 8.4: from address + 4, wrapping, the requested longword last (C2).
    chip, log = bare()
    chip.cache.write_ccr(0x11)  # CP + CE
    chip._read(0x00001008, 32)
    fills = [entry for entry in log if entry[3] & FILL]
    assert [entry[1] for entry in fills] == [0x100C, 0x1000, 0x1004, 0x1008]
    assert fills[0][3] & FIRST and not any(e[3] & FIRST for e in fills[1:])


def test_hits_make_no_bus_cycle_but_reach_observe():
    seen = []
    chip, log = bare(observe=lambda a, w, k: seen.append((a, w, k)))
    chip.cache.write_ccr(0x11)
    chip._read(0x00002000, 16)
    count = len(log)
    assert chip._read(0x00002004, 32) == (0x2004 * 0x01010101) & 0xFFFFFFFF
    assert len(log) == count
    assert seen[-1] == (0x00002004, 32, HIT)


def test_write_through_updates_a_hit_line_and_never_allocates():
    chip, log = bare()
    chip.cache.write_ccr(0x11)
    chip._write(0x00003000, 32, 0xAABBCCDD)  # miss: no allocation (HM 8.4.2)
    assert chip.cache.lookup(0x00003000) < 0
    chip._read(0x00003000, 32)  # allocate
    chip._write(0x00003000, 16, 0x1122)
    assert chip._read(0x00003000, 16) == 0x1122  # from the line
    assert log[-1][0] == "w"  # the write went out


def test_cache_through_alias_never_caches():
    chip, log = bare()
    chip.cache.write_ccr(0x11)
    chip._read(0x20004000, 32)
    assert chip.cache.lookup(0x00004000) < 0
    assert log[-1][3] & THROUGH


def test_associative_purge_and_cp():
    chip, _ = bare()
    chip.cache.write_ccr(0x11)
    chip._read(0x00005000, 32)
    chip._write(0x40005000, 32, 0)  # HM 8.4.7
    assert chip.cache.lookup(0x00005000) < 0
    chip._read(0x00005000, 32)
    chip._write(0xFFFFFE92, 8, 0x11)  # CP: everything invalid, LRU 0 (HM 8.4.6)
    assert chip.cache.lookup(0x00005000) < 0
    assert chip._read(0xFFFFFE92, 8) == 0x01  # CP reads 0


def test_address_array_shows_tag_lru_and_valid():
    # HM 8.4.9, figure 8.11; CCR.W selects the way.
    chip, _ = bare()
    chip.cache.write_ccr(0x11)
    chip._read(0x0000_7430, 32)  # entry 3, way 3 after the purge
    chip.cache.write_ccr(0xC1)  # W = 3
    value = chip._read(0x60000030, 32)
    assert value >> 10 == (0x7430 >> 10) and value & 4


def test_data_array_is_ram_with_the_cache_off():
    # HM 8.4.8: H'C0000000-H'C0000FFF, any size, cache bus only.
    chip, log = bare()
    chip._write(0xC0000123, 8, 0x5A)
    assert chip._read(0xC0000120, 32) & 0xFF == 0x5A  # big-endian: byte 3 is the low byte
    assert not log


def test_od_reads_without_replacing():
    chip, log = bare()
    chip.cache.write_ccr(0x15)  # CP, OD, CE
    chip._read(0x00006000, 32)
    assert chip.cache.lookup(0x00006000) < 0
    assert not log[-1][3] & FILL


def test_fetch_misses_fill_with_fetch_kind():
    h = make([0x0009, 0x0009])
    h.chip.cache.write_ccr(0x11)
    kinds = []
    original = h.chip.read
    h.chip.read = lambda a, w, k: kinds.append(k) or original(a, w, k)
    h.chip.step()
    assert kinds and all(k & FETCH and k & FILL for k in kinds)


# -- the bus state controller (HM chapter 7) ----------------------------------------------


def test_bsc_writes_need_the_a55a_key():
    # HM 7.1.4: only a longword write with H'A55A in the upper half is performed.
    chip, _ = bare()
    chip._write(0xFFFFFFE4, 32, 0x000000A8)
    assert chip._read(0xFFFFFFE4, 32) == 0xFC
    chip._write(0xFFFFFFE4, 32, 0xA55A00A8)
    assert chip._read(0xFFFFFFE4, 32) == 0xA8
    assert chip._read(0xFFFFFFE6, 16) == 0xA8


def test_longword_on_a_16_bit_area_is_two_cycles_high_word_first():
    # HM 7.4.1; the bus contract: the BSC splits into port-width cycles.
    chip, log = bare()
    chip._write(0xFFFFFFE4, 32, 0xA55A00A8)  # areas 1-3 16-bit (the 32X's setting)
    chip._write(0x26000010, 32, 0x11223344)
    cycles = [entry[1:4] for entry in log[-2:]]
    assert cycles == [(0x06000010, 16, 0x1122), (0x06000012, 16, 0x3344)]


def test_board_wait_states_lengthen_the_cycle():
    chip, _ = bare()

    def slow(address, width, kind):
        chip.wait += 5
        return 0

    chip.read = slow
    start = chip.clock
    chip._read(0x20000000, 32)
    # Reset WCR: area 0 long wait = 6 (BCR1.A0LW = 11); 2 + 6 + 5 states, minus the
    # table's one.
    assert chip.clock - start == 2 + 6 + 5 - 1


def test_master_bit_reads_the_mode_pin():
    assert bare(mode="slave")[0]._read(0xFFFFFFE0, 32) & 0x8000
    assert not bare()[0]._read(0xFFFFFFE0, 32) & 0x8000


# -- FRT (HM chapter 11) ------------------------------------------------------------------


def test_frt_compare_match_a_drives_ftoa_and_clears():
    levels = []
    chip, _ = bare(on_ftoa=lambda level, clock: levels.append((level, clock)))
    chip._write(0xFFFFFE17, 8, 0xE2)  # TOCR: OLVLA = 1, OCRA selected
    chip._write(0xFFFFFE14, 8, 0x00)
    chip._write(0xFFFFFE15, 8, 0x03)  # OCRA = 3 (through TEMP, upper byte first)
    chip._write(0xFFFFFE11, 8, 0x01)  # CCLRA
    chip.clock = 8 * 4  # four counts of φ/8
    chip._service_events()
    assert levels == [(1, 32)]  # the count after FRC = 3
    assert chip._read(0xFFFFFE11, 8) & 0x08  # OCFA
    assert chip._read(0xFFFFFE12, 8) == 0 and chip._read(0xFFFFFE13, 8) == 0


def test_frt_16_bit_read_goes_through_temp():
    chip, _ = bare()
    chip._write(0xFFFFFE12, 8, 0x12)
    chip._write(0xFFFFFE13, 8, 0x34)
    assert chip._read(0xFFFFFE12, 16) == 0x1234


def test_frt_interrupt_through_ipr_and_vcr():
    chip, _ = bare()
    chip._write(0xFFFFFE60, 8, 0x05)  # IPRB: FRT level 5
    chip._write(0xFFFFFE67, 8, 0x50)  # VCRC.FOCV = H'50
    chip._write(0xFFFFFE10, 8, 0x08)  # OCIAE
    chip._write(0xFFFFFE14, 8, 0)
    chip._write(0xFFFFFE15, 8, 1)
    chip.clock = 16
    chip._service_events()
    assert (chip._onchip_level, chip._onchip_vector) == (5, 0x50)


# -- WDT (HM chapter 12) -------------------------------------------------------------------


def test_wdt_keyed_word_writes():
    chip, _ = bare()
    chip._write(0xFFFFFE80, 8, 0x55)  # a byte write is ignored
    chip._write(0xFFFFFE80, 16, 0x5A42)  # WTCNT
    chip._write(0xFFFFFE80, 16, 0xA525)  # WTCSR: TME, φ/128? (CKS = 5)
    assert chip._read(0xFFFFFE81, 8) == 0x42
    assert chip._read(0xFFFFFE80, 8) & 0x27 == 0x25


def test_wdt_interval_overflow_requests_iti():
    chip, _ = bare()
    chip._write(0xFFFFFEE3, 8, 0x30)  # IPRA.WDTIP = 3
    chip._write(0xFFFFFEE4, 8, 0x44)  # VCRWDT.WITV
    chip._write(0xFFFFFE80, 16, 0x5AFE)
    chip._write(0xFFFFFE80, 16, 0xA520)  # interval mode, TME, φ/2
    chip.clock += 4
    chip._service_events()
    assert chip._read(0xFFFFFE80, 8) & 0x80
    assert (chip._onchip_level, chip._onchip_vector) == (3, 0x44)


# -- address errors on on-chip sizes (HM table 4.6) ---------------------------------------


@pytest.mark.parametrize(("address", "width"), [(0xFFFFFF00, 8), (0xFFFFFE10, 32)])
def test_wrong_size_on_chip_access_is_an_address_error(address, width):
    chip, _ = bare()
    chip._read(address, width)
    assert chip._address_error
