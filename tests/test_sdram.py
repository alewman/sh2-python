"""The BSC's synchronous DRAM cycles and auto-refresh (HM 7.5), with the 32X's settings.

The 32X's boot ROMs leave BCR1 = H'0001 (CS3 synchronous DRAM), BCR2 = H'00A8 (16-bit
areas), WCR = H'0055 (CAS latency 2 on CS3), MCR = H'0AB8 (bank active, refresh on, 2
Mbit x 16, TRAS 3 cycles, TRP = RCD = TRWL = 1 cycle) and RTCSR = H'08 / RTCOR = H'59
(CKIO / 4, every 89 counts): docs/timing.md, "Synchronous DRAM". The expected states are
HM's figures 7.15-7.25 read with those registers (bsc.py's table).
"""

from sh2_python import DMA, FILL, FIRST, REFRESH, SH7604

BCR1, BCR2, WCR, MCR = 0xFFFFFFE0, 0xFFFFFFE4, 0xFFFFFFE8, 0xFFFFFFEC
RTCSR, RTCNT, RTCOR = 0xFFFFFFF0, 0xFFFFFFF4, 0xFFFFFFF8
CCR = 0xFFFFFE92
SDRAM = 0x06000000  # CS3
THROUGH = 0x20000000  # the cache-through alias
OTHER_ROW = 0x200  # A9: the next row of the same bank (2 Mbit x 16: rows A16-A9)
OTHER_BANK = 0x20000  # A17: the bank address (HM table 7.4)
MCR_32X = 0x0AB8


def board(*, mcr=MCR_32X, wcr=0x0055, refresh=False, mode="master", cache=True):
    """A chip with the 32X's BSC settings, and a log of (op, address, width, kind,
    chip.clock, the call's cycle_states) per callback."""
    log = []
    chip = None

    def read(address, width, kind):
        log.append(("r", address, width, kind, chip.clock, chip.bsc.cycle_states[3]))
        return address & ((1 << width) - 1)

    def write(address, width, value, kind):
        log.append(("w", address, width, kind, chip.clock, chip.bsc.cycle_states[3]))

    chip = SH7604(read, write, mode=mode, area0_width=16)
    for address, value in ((BCR1, 0x0001), (BCR2, 0x00A8), (WCR, wcr), (MCR, mcr)):
        chip._write(address, 32, 0xA55A0000 | value)
    if refresh:
        chip._write(RTCOR, 32, 0xA55A0059)
        chip._write(RTCSR, 32, 0xA55A0008)
    if cache:
        chip._write(CCR, 8, 0x01)  # CE
    log.clear()
    return chip, log


def states(chip, access):
    """The states ``access`` keeps the CPU (a read: until its data is in)."""
    start = chip.clock
    access()
    return chip.clock - start


# -- reads: one burst (HM 7.5.3, 7.5.4, 7.5.6) ------------------------------------------


def test_a_line_fill_is_one_burst_11_10_12_by_row():
    chip, _ = board()
    # Bank idle: Tr Tc Tw Td1-Td8 (figure 7.19 with CAS latency 2) = 11.
    assert states(chip, lambda: chip._read(SDRAM, 32)) == 11
    # Same row, bank active: Tc Tw Td1-Td8 (figure 7.20, no Tnop with CL 2) = 10.
    assert states(chip, lambda: chip._read(SDRAM + 0x10, 32)) == 10
    # Another row of that bank: Tp Tr Tc Tw Td1-Td8 (figure 7.21) = 12.
    assert states(chip, lambda: chip._read(SDRAM + OTHER_ROW, 32)) == 12
    # The other bank has its own row: idle, 11; then back to bank 0's open row, 10.
    assert states(chip, lambda: chip._read(SDRAM + OTHER_BANK, 32)) == 11
    assert states(chip, lambda: chip._read(SDRAM + OTHER_ROW + 0x20, 32)) == 10


def test_a_fill_keeps_one_call_per_port_cycle_in_wrap_order():
    # The contract: FILL on every call, FIRST on the first longword's, HM 8.4.1's order
    # (address + 4 first), each call at the state its cycle starts; the first carries
    # the command states (Tr Tc Tw), the others one data cycle each.
    chip, log = board()
    start = chip.clock
    chip._read(SDRAM + 0x8, 32)
    assert [(e[1], e[2]) for e in log] == [
        (SDRAM + 0xC, 16), (SDRAM + 0xE, 16), (SDRAM + 0x0, 16), (SDRAM + 0x2, 16),
        (SDRAM + 0x4, 16), (SDRAM + 0x6, 16), (SDRAM + 0x8, 16), (SDRAM + 0xA, 16),
    ]  # fmt: skip
    assert all(e[3] & FILL for e in log)
    assert [bool(e[3] & FIRST) for e in log] == [True, True] + [False] * 6
    assert [e[4] - start for e in log] == [0, 4, 5, 6, 7, 8, 9, 10]
    assert [e[5] for e in log] == [4, 1, 1, 1, 1, 1, 1, 1]


def test_a_cache_through_read_still_makes_the_whole_burst():
    # HM 7.5.4: "the remaining 12 bytes are wasted"; data only in Td1 (word) or Td1-Td2
    # (longword on 16 bits). One call per cycle with data; the last carries the rest.
    chip, log = board(cache=False)
    assert states(chip, lambda: chip._read(THROUGH | SDRAM, 16)) == 11
    assert [(e[1], e[2], e[5]) for e in log] == [(SDRAM, 16, 3 + 1 + 7)]
    log.clear()
    assert states(chip, lambda: chip._read(THROUGH | SDRAM + 4, 32)) == 10
    assert [(e[1], e[2], e[5]) for e in log] == [(SDRAM + 4, 16, 2 + 1), (SDRAM + 6, 16, 7)]
    assert states(chip, lambda: chip._read(THROUGH | SDRAM + OTHER_ROW + 1, 8)) == 12


def test_a_32_bit_sdram_port_bursts_4_cycles():
    # MCR.SZ = 1: the port is 32 bits whatever BCR2 says (HM 7.2.4); 4 data cycles
    # (HM 7.5.1). AMX 111 with SZ = 1: 2 x (128k x 16), rows from A10, bank A18.
    chip, log = board(mcr=MCR_32X | 0x0040)
    assert chip.bsc.widths[3] == 32
    assert states(chip, lambda: chip._read(SDRAM, 32)) == 3 + 4
    assert [(e[2], e[5]) for e in log] == [(32, 4), (32, 1), (32, 1), (32, 1)]
    assert states(chip, lambda: chip._read(SDRAM + 0x3F0, 32)) == 2 + 4  # same row
    assert states(chip, lambda: chip._read(SDRAM + 0x400, 32)) == 4 + 4  # A10: next row


def test_cas_latency_1_inserts_tnop_on_the_same_row():
    # HM 7.5.6, figure 7.20: with CAS latency 1 a Tnop comes before Tc.
    chip, _ = board(wcr=0x0015)  # W31/W30 = 00: CAS latency 1
    assert states(chip, lambda: chip._read(SDRAM, 32)) == 2 + 8  # Tr Tc
    assert states(chip, lambda: chip._read(SDRAM + 0x10, 32)) == 2 + 8  # Tnop Tc
    assert states(chip, lambda: chip._read(SDRAM + OTHER_ROW, 32)) == 3 + 8  # Tp Tr Tc


def test_auto_precharge_reads_start_with_tr_and_tap_holds_the_bank():
    # RASD = 0: every read is Tr Tc Tw Td1-Td8; Tap (TRP - 1 with CL >= 2, HM 7.5.3) only
    # delays the next command to the same bank.
    chip, _ = board(mcr=MCR_32X & ~0x0200)
    for line in (SDRAM, SDRAM + 0x10, SDRAM + 0x20):
        assert states(chip, lambda line=line: chip._read(line, 32)) == 11
    chip, _ = board(mcr=(MCR_32X & ~0x0200) | 0x8000)  # TRP 2 cycles: one Tap
    assert states(chip, lambda: chip._read(SDRAM, 32)) == 11
    assert states(chip, lambda: chip._read(SDRAM + 0x10, 32)) == 1 + 11
    assert states(chip, lambda: chip._read(SDRAM + OTHER_BANK, 32)) == 11


def test_rcd_adds_trw():
    chip, _ = board(mcr=MCR_32X | 0x4000)  # RCD 2 cycles (figure 7.16's Trw)
    assert states(chip, lambda: chip._read(SDRAM, 32)) == 4 + 8
    assert states(chip, lambda: chip._read(SDRAM + 0x10, 32)) == 2 + 8  # no Tr on the row


# -- writes: single writes (HM 7.5.5, 7.5.6) --------------------------------------------


def write_states(chip, log, address, width):
    log.clear()
    chip.clock = max(chip.clock, chip._wb_end)  # the write buffer is empty
    chip._write(THROUGH | address, width, 0)
    return [e[5] for e in log]


def test_word_writes_take_2_1_3_by_row():
    chip, log = board()
    assert write_states(chip, log, SDRAM, 16) == [2]  # Tr Tc (figure 7.22)
    assert write_states(chip, log, SDRAM + 2, 16) == [1]  # Tc (figure 7.23)
    assert write_states(chip, log, SDRAM + OTHER_ROW, 16) == [3]  # Tp Tr Tc (7.24)
    assert write_states(chip, log, SDRAM + OTHER_ROW + 4, 32) == [1, 1]  # two writes
    # A read of the row a write opened needs no Tr either.
    chip.clock = chip._wb_end
    assert states(chip, lambda: chip._read(THROUGH | SDRAM + OTHER_ROW, 16)) == 10


def test_trwl_holds_back_the_precharge_after_a_write():
    # Bank active: "the period for which the precharge command is disabled after the
    # write command" (HM 7.2.4, TRWL). A row change right after a write waits for it.
    chip, log = board()
    write_states(chip, log, SDRAM, 16)
    start = chip._wb_end
    chip.clock = start
    chip._read(THROUGH | SDRAM + OTHER_ROW, 16)
    assert log[-1][4] == start + 1  # TRWL = 1 cycle, then Tp
    assert chip.clock == start + 1 + 12


def test_auto_precharge_writes_hold_the_bank_for_trwl_and_tap():
    chip, log = board(mcr=MCR_32X & ~0x0200)
    assert write_states(chip, log, SDRAM, 16) == [2]  # Tr Tc (figure 7.18)
    end = chip._wb_end
    chip.clock = end
    chip._write(THROUGH | SDRAM + 2, 16, 0)
    assert log[-1][4] == end + 2  # Trwl, Tap
    chip.clock = chip._wb_end
    chip._write(THROUGH | SDRAM + OTHER_BANK, 16, 0)
    assert log[-1][4] == chip.clock - 1  # the other bank does not wait


def test_board_wait_still_lengthens_an_sdram_cycle():
    # Item 4 (docs/timing.md): the WAIT pin is ignored on SDRAM (HM 7.2.3), so a board
    # must not add WAIT states there; chip.wait stays the board's way to say the cycle
    # could not start yet (arbitration, HM 7.10) and still lengthens it.
    chip, _ = board(cache=False)
    chip.read = lambda address, width, kind: setattr(chip, "wait", chip.wait + 5) or 0
    assert states(chip, lambda: chip._read(THROUGH | SDRAM, 16)) == 11 + 5
    assert chip.bsc.wait_pin[3] is False and chip.bsc.waits[3] == 0


# -- DMA (HM 7.5.3, 7.5.4) --------------------------------------------------------------


def test_a_16_byte_dma_read_is_one_burst_in_address_order():
    chip, log = board()
    start = chip.clock
    words = chip._dma_read_unit(SDRAM + 0x40, DMA)
    assert [e[1] - SDRAM for e in log] == list(range(0x40, 0x50, 2))
    assert all(e[3] == DMA for e in log)
    assert chip.clock - start == 11
    assert words == [0x00400042, 0x00440046, 0x0048004A, 0x004C004E]


# -- refresh (HM 7.2.5-7.2.7, 7.5.7) ----------------------------------------------------


def test_refresh_every_rtcor_times_cks_states_through_read():
    # CKS = 001 (CKIO / 4), RTCOR = 89: a request every 356 states, each Tp Trr then
    # TRAS + 2 = 5 with no command: 7 states (figure 7.25).
    chip, log = board(refresh=True)
    assert chip.bsc.refresh_at == 356
    chip.clock = 1000
    chip._service_events()  # a step boundary with the bus idle
    refreshes = [e for e in log if e[3] == REFRESH]
    assert [(e[1], e[2], e[4], e[5]) for e in refreshes] == [
        (SDRAM, 16, 356, 7), (SDRAM, 16, 712, 7),
    ]  # fmt: skip
    assert chip.clock == 1000  # the CPU did not wait for an idle bus
    assert chip.bsc.refresh_at == 1068


def test_a_refresh_closes_the_banks():
    chip, log = board(refresh=True)
    chip._read(SDRAM, 32)
    assert states(chip, lambda: chip._read(SDRAM + 0x10, 32)) == 10
    chip.clock = 400
    assert states(chip, lambda: chip._read(SDRAM + 0x20, 32)) == 11  # the row closed
    assert [e[4] for e in log if e[3] == REFRESH] == [356]


def test_a_refresh_waits_for_the_access_under_way_and_holds_the_next():
    # "When a refresh request occurs while a bus cycle is executing, the refresh will
    # not be executed until the bus cycle is completed" (HM 7.5.7): not inside a burst.
    chip, log = board(refresh=True)
    chip.clock = 350
    chip._read(SDRAM, 32)  # 350-361: the request at 356 waits for it
    assert chip.clock == 361
    assert [e[4] for e in log if e[3] == REFRESH] == [361]
    assert states(chip, lambda: chip._read(SDRAM + 0x10, 32)) == 7 + 11  # waits, row closed


def test_refresh_needs_rfsh_master_mode_and_an_sdram_area():
    assert board(refresh=True, mode="slave")[0].bsc.refresh_at > 1 << 60  # HM 7.2.4
    assert board(refresh=True, mcr=MCR_32X & ~0x0008)[0].bsc.refresh_at > 1 << 60
    assert board(refresh=True, mcr=MCR_32X | 0x0004)[0].bsc.refresh_at > 1 << 60  # RMD
    chip, log = board(refresh=True, mode="slave")
    chip.clock = 1000
    chip._service_events()
    assert not log


def test_rtcnt_counts_and_cmf_is_set_at_the_match():
    chip, _ = board(refresh=True)
    chip.clock = 4 * 10 + 3
    assert chip._read(RTCNT, 32) == 10
    assert not chip._read(RTCSR, 32) & 0x80
    chip.clock = 356 + 4 * 5
    assert chip._read(RTCNT, 32) == 5  # cleared at the match
    assert chip._read(RTCSR, 32) & 0x80  # CMF
    chip._write(RTCSR, 32, 0xA55A0008)  # 0 written after reading 1: cleared
    assert chip._read(RTCSR, 32) == 0x08
    chip._write(RTCSR, 32, 0xA55A0088)  # CMF cannot be set by software
    assert chip._read(RTCSR, 32) == 0x08


def test_cmi_requests_an_interrupt_at_the_wdt_level_with_bcmv():
    chip, _ = board(refresh=True)
    chip._write(0xFFFFFEE3, 8, 0x50)  # IPRA.WDTIP = 5
    chip._write(0xFFFFFEE5, 8, 0x47)  # VCRWDT.BCMV
    chip._write(RTCSR, 32, 0xA55A0048)  # CMIE
    chip.clock = 400
    chip._service_events()
    assert (chip._onchip_level, chip._onchip_vector) == (5, 0x47)


# -- what does not change ---------------------------------------------------------------


def test_reset_values_select_no_sdram_and_no_refresh():
    # Hosts that do not select synchronous DRAM (the rung-1/2 hosts, the CPS3 harness)
    # keep the ordinary cycle: 2 + waits per port cycle, four cycles a fill.
    log = []
    chip = SH7604(lambda a, w, k: log.append((a, w, k)) or 0, lambda *a: None)
    assert chip.bsc.sdram == [False] * 4
    assert chip.bsc.refresh_at > 1 << 60
    chip._write(CCR, 8, 0x01)
    assert states(chip, lambda: chip._read(SDRAM, 32)) == 4 * (2 + 6)  # WCR's long wait
    assert len(log) == 4


def test_a_refresh_longer_than_its_period_does_not_run_away():
    # RTCOR = 1 at CKIO / 4: a request every 4 states, each refresh 7. Requests made
    # while one waits replace it (HM 7.2.7); the bus is refreshing back to back.
    chip, log = board(refresh=True)
    chip._write(RTCOR, 32, 0xA55A0001)
    chip.clock = 100
    chip._read(SDRAM, 32)
    starts = [e[4] for e in log if e[3] == REFRESH]
    before = [start for start in starts if start < 100]
    assert before == list(range(4, 100, 7))  # back to back from the first request
    assert chip.clock < 100 + 7 + 11 + 7  # the read ran, then one more refresh at most
