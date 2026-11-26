# SPDX-License-Identifier: MIT
"""Save states that load into a fresh chip and continue identically, and DMA address errors.

The methodology's save-state gate: save at step N, load the state into a **new**
:class:`SH7604` on the same memory, run M more steps, and compare every bus cycle (address,
width, kind, the state it starts at), every step's states and the final ``ChipState``
with a run that was never interrupted. Restoring into the same chip (tests/test_cpu.py)
cannot see what ``ChipState`` leaves out, because the chip keeps it (sh2-rust's request
R1: the cache's lines, the SDRAM banks, the refresh). DMA address errors are sh2-rust's
request R2 (HM 4.3, 9.2.7, 9.3.8).
"""

import dataclasses

import pytest
from conftest import make

from benchmarks import harness_speed
from sh2_python import SH7604
from sh2_python.harness import Harness

BCR1, BCR2, WCR, MCR = 0xFFFFFFE0, 0xFFFFFFE4, 0xFFFFFFE8, 0xFFFFFFEC
RTCSR, RTCOR = 0xFFFFFFF0, 0xFFFFFFF8
CCR = 0xFFFFFE92
SAR0, DAR0, TCR0, CHCR0 = 0xFFFFFF80, 0xFFFFFF84, 0xFFFFFF88, 0xFFFFFF8C
SAR1, DAR1, TCR1, CHCR1 = 0xFFFFFF90, 0xFFFFFF94, 0xFFFFFF98, 0xFFFFFF9C
DMAOR = 0xFFFFFFB0
LOOP_END = 0x11A  # harness_speed's BRA $: the runs below jump back to its start


def _setup(chip, *, cache, bus, modules):
    if bus == "32x":
        # The 32X's boot ROMs' BSC (tests/test_sdram.py): CS3 SDRAM, 16-bit areas, CAS
        # latency 2, bank active, refresh every 89 counts of CKIO / 4.
        for address, value in ((BCR1, 0x0001), (BCR2, 0x00A8), (WCR, 0x0055), (MCR, 0x0AB8)):
            chip._write(address, 32, 0xA55A0000 | value)
        chip._write(RTCOR, 32, 0xA55A0059)
        chip._write(RTCSR, 32, 0xA55A0008)
    else:
        chip._write(WCR, 32, 0xA55A0000)  # no waits
    if modules:
        chip._write(0xFFFFFE12, 8, 0x00)  # FRT: OCRA through TEMP, H'0040, with CCLRA
        chip._write(0xFFFFFE14, 8, 0x00)
        chip._write(0xFFFFFE15, 8, 0x40)
        chip._write(0xFFFFFE11, 8, 0x01)
        chip._write(0xFFFFFE80, 16, 0xA520)  # WDT: interval timer, TME, phi / 2
        # DMA channel 0: auto request, cycle steal, longwords H'06001000 -> H'06003000.
        chip._write(SAR0, 32, 0x06001000)
        chip._write(DAR0, 32, 0x06003000)
        chip._write(TCR0, 32, 0x4000)
        chip._write(CHCR0, 32, 0x5A01)
        chip._write(DMAOR, 32, 1)
    if cache:
        chip._write(CCR, 8, 0x01)


def _new(cache, bus, modules):
    options = {"area0_width": 16} if bus == "32x" else {}
    harness = Harness(harness_speed.program(), **options)
    harness.chip.reset()
    _setup(harness.chip, cache=cache, bus=bus, modules=modules)
    return harness


def _traced(harness):
    """Log every callback of ``harness``'s chip (with the state it starts at)."""
    chip, log = harness.chip, []
    read, write = harness.read, harness.write

    def traced_read(address, width, kind):
        log.append(("r", address, width, kind, chip.clock))
        return read(address, width, kind)

    def traced_write(address, width, value, kind):
        log.append(("w", address, width, value, kind, chip.clock))
        write(address, width, value, kind)

    chip.read, chip.write = traced_read, traced_write
    return log


def _run(chip, steps):
    out = []
    for _ in range(steps):
        if chip.pc == LOOP_END:
            chip.pc = 0x100
        out.append(chip.step())
    return out


#: Attributes that only live inside one access or one callback (set before they are
#: read): the MA being closed (pipeline.py ``_ma_next``/``_ma_close``) and the states of
#: the SDRAM callback last made (``cycle_states``, a callback's own, bsc.py docstring).
_SCRATCH = {"_p_ma_t0", "_p_ma_is_mul", "cycle_states", "_cycle_states"}


def _attributes(obj, path, out):
    names = set(getattr(obj, "__dict__", {}))
    for klass in type(obj).__mro__:
        names |= set(getattr(klass, "__slots__", ()))
    for name in sorted(names - _SCRATCH):
        value = getattr(obj, name, None)
        if name == "chip" or (callable(value) and not hasattr(value, "NAME")):
            continue
        if hasattr(value, "NAME") or type(value).__name__ == "Channel":
            _attributes(value, f"{path}.{name}", out)
        elif isinstance(value, tuple) and value and type(value[0]).__name__ == "Channel":
            for i, channel in enumerate(value):
                _attributes(channel, f"{path}.{name}[{i}]", out)
        elif isinstance(value, (int, bool, list, dict, bytes, bytearray, type(None))):
            out[f"{path}.{name}"] = bytes(value) if isinstance(value, bytearray) else value


def _everything(chip):
    out = {}
    _attributes(chip, "chip", out)
    for module in chip.modules:
        _attributes(module, module.NAME, out)
    out.pop("chip._onchip", None)  # the address -> module map: the same modules
    return out


CASES = [
    ("cache-off", False, "plain", False),
    ("cache-on", True, "plain", False),
    ("32x-cache-off", False, "32x", False),
    ("32x-cache-on", True, "32x", False),
    ("32x-cache-on-modules", True, "32x", True),
    ("modules", False, "plain", True),
]


@pytest.mark.parametrize(
    ("cache", "bus", "modules"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_a_state_loaded_into_a_fresh_chip_continues_identically(cache, bus, modules):
    # Save at N, restore into a new chip on a copy of the memory, run M more; the
    # uninterrupted run is the judge.
    first, second = 1000, 400
    original = _new(cache, bus, modules)
    _run(original.chip, first)
    saved = original.chip.capture_state()
    fresh = Harness(harness_speed.program(), **({"area0_width": 16} if bus == "32x" else {}))
    fresh.low[:], fresh.high[:] = original.low, original.high
    fresh.chip.restore_state(saved)
    assert _everything(fresh.chip) == _everything(original.chip)
    log_a, log_b = _traced(original), _traced(fresh)
    assert _run(fresh.chip, second) == _run(original.chip, second)
    assert log_b == log_a
    assert log_a or (cache and not modules)  # the cached loop runs from its lines alone
    assert fresh.chip.capture_state() == original.chip.capture_state()
    assert fresh.high == original.high


def test_the_cache_contents_are_in_the_state():
    # sh2-rust R1's repro: with the cache on, 200 steps took 267 states continuously and
    # 295 after a fresh-chip restore (every hit missed).
    original = _new(True, "plain", False)
    _run(original.chip, 1000)
    saved = original.chip.capture_state()
    assert len(saved.internal["cache"]) == 4096 + 256 + 256 + 64
    fresh = Harness(harness_speed.program())
    fresh.low[:], fresh.high[:] = original.low, original.high
    fresh.chip.restore_state(saved)
    assert sum(_run(fresh.chip, 200)) == sum(_run(original.chip, 200)) == 267


def test_a_state_built_by_hand_keeps_what_the_chip_holds():
    # No ``internal`` (a state built by hand): the cache keeps its lines.
    original = _new(True, "plain", False)
    _run(original.chip, 1000)
    saved = original.chip.capture_state()
    lines = list(original.chip.cache.valid)
    original.chip.restore_state(type(saved)(r=saved.r, pc=saved.pc, clock=saved.clock))
    assert original.chip.cache.valid == lines


# -- DMA address errors (HM table 4.6, 9.2.7, 9.3.8; sh2-rust R2) ---------------------------------


def _dma_chip(sar, dar, tcr, chcr, *, sar1=None):
    vectors = {}

    def read(address, width, kind):
        return vectors.get(address, 0)

    log = []

    def write(address, width, value, kind):
        log.append((address, width, value, kind))

    chip = SH7604(read, write)
    chip.pc = 0x06000000
    chip.R[15] = 0x06008000
    vectors[0x28] = 0x06000400  # vector 10, VBR = 0
    chip._write(SAR0, 32, sar)
    chip._write(DAR0, 32, dar)
    chip._write(TCR0, 32, tcr)
    chip._write(CHCR0, 32, chcr)
    if sar1 is not None:
        chip._write(SAR1, 32, sar1)
        chip._write(DAR1, 32, 0x06001100)
        chip._write(TCR1, 32, 4)
        chip._write(CHCR1, 32, 0x5A01)
    chip._write(DMAOR, 32, 1)
    return chip, log


def test_an_unaligned_longword_from_the_data_array_is_a_dma_address_error():
    # R2's repro raised IndexError from step(). The transfer ends (it was the last, so
    # TE = 1), DMAOR.AE is set, and the CPU takes vector 10 with the next PC saved.
    chip, log = _dma_chip(0xC0000FFE, 0x06001000, 1, 0x0A01)
    chip.step()
    assert chip.dmac.dmaor == 0x5  # AE, DME
    channel = chip.dmac.channels[0]
    assert channel.chcr & 2 and channel.tcr == 0
    assert channel.sar == 0xC0000FFE and channel.dar == 0x06001000  # SM = DM = fixed
    assert chip.pc == 0x06000400
    # HYPOTHESIS: the faulting read makes no cycle and gives 0 to the write.
    assert log[0] == (0x06001000, 32, 0, log[0][3])
    assert log[1:] == [
        (0x06007FFC, 32, chip.capture_state().sr, log[1][3]),
        (0x06007FF8, 32, 0x06000000, log[2][3]),
    ]  # the exception's pushes: SR, then the saved PC (HM 4.3.2)


def test_a_dma_address_error_stops_both_channels_and_counts_its_transfer():
    # Burst, four longwords from a misaligned source: one transfer, then the halt (HM
    # 9.3.8): SAR, DAR and TCR updated by it, TE = 0 (not the last), channel 1 stopped.
    chip, log = _dma_chip(0x06001002, 0x06001200, 4, 0x5A11, sar1=0x06001000)
    chip.step()
    ch0, ch1 = chip.dmac.channels
    assert (ch0.sar, ch0.dar, ch0.tcr, ch0.chcr & 2) == (0x06001006, 0x06001204, 3, 0)
    assert ch1.tcr == 4 and not ch1.chcr & 2  # never started
    assert chip.dmac.dmaor & 4 and not chip.dmac.active
    assert log[0][:3] == (0x06001200, 32, 0)
    assert chip.pc == 0x06000400


def test_a_word_at_3_mod_4_in_a_longword_module_and_a_misaligned_destination():
    # R2's second path (LongwordModule.read's negative shift) is an odd word address:
    # an address error, no access. A misaligned destination is not written either.
    chip, log = _dma_chip(0xFFFFFF03, 0x06001000, 1, 0x0601)  # word, auto request
    chip.step()
    assert chip.dmac.dmaor & 4 and chip.pc == 0x06000400
    chip, log = _dma_chip(0x06001000, 0x06001002, 1, 0x0A01)
    chip.step()
    assert chip.dmac.dmaor & 4 and all(entry[0] != 0x06001002 for entry in log)


def test_ae_blocks_transfers_until_read_as_1_and_written_0():
    chip, _ = _dma_chip(0x06001002, 0x06001200, 4, 0x5A01)
    chip.step()
    chip._write(DMAOR, 32, 1)  # not read yet: AE stays
    assert chip.dmac.dmaor & 4 and not chip.dmac.active
    assert chip._read(DMAOR, 32) & 4
    chip._write(DMAOR, 32, 1)
    assert not chip.dmac.dmaor & 4 and chip.dmac.active  # DE still 1: it restarts


def test_a_dma_address_error_waits_for_the_delay_slot_and_ends_sleep():
    # HM 4.6.1: no address error between a delayed branch and its slot.
    h = make([0xA002, 0x0009, 0x0009, 0x0009, 0x0009])  # BRA +4; NOP (slot)
    chip = h.chip
    h.load(0x28, (0x06000100).to_bytes(4, "big"))
    chip.step()  # the BRA: its slot runs next
    for address, value in ((SAR0, 0x06001002), (DAR0, 0x06001200), (TCR0, 2), (CHCR0, 0x5A01)):
        chip._write(address, 32, value)
    chip._write(DMAOR, 32, 1)
    chip.step()  # the transfer faults; the slot still runs
    assert chip.dmac.dmaor & 4 and chip.pc == 0x06000008
    chip.step()
    assert chip.pc == 0x06000100 and chip.R[15] == 0x06008000 - 8
    assert int.from_bytes(h.high[0x7FFC - 4 : 0x7FFC], "big") == 0x06000008  # saved PC
    # SLEEP (HM 14.3.2): a DMA address error ends it.
    h = make([0x001B, 0x0009])  # SLEEP
    chip = h.chip
    h.load(0x28, (0x06000100).to_bytes(4, "big"))
    chip.step()
    chip.step()
    assert chip.sleeping
    for address, value in ((SAR0, 0x06001002), (DAR0, 0x06001200), (TCR0, 2), (CHCR0, 0x5A01)):
        chip._write(address, 32, value)
    chip._write(DMAOR, 32, 1)
    chip.step()
    assert not chip.sleeping and chip.pc == 0x06000100


def test_a_pending_dma_address_error_is_in_the_state():
    chip, _ = _dma_chip(0x06001002, 0x06001200, 4, 0x5A01)
    chip._write(0xFFFFFFE8, 32, 0xA55A0000)
    chip.delay_target = 0x06000010  # as if the next step were a delay slot
    chip._pipeline_restart()
    chip.step()
    saved = chip.capture_state()
    assert saved.dma_address_error
    other, _ = _dma_chip(0, 0, 0, 0)
    other.restore_state(saved)
    assert other._dma_address_error


# -- the per-step capture (the debugger's and the trace's) ----------------------------------------


def test_the_per_step_capture_is_the_save_state_without_internal():
    original = _new(True, "32x", True)
    _run(original.chip, 1000)
    full = original.chip.capture_state()
    light = original.chip.capture_state(internal=False)
    assert light.internal == {} and full.internal["cache"] and full.internal["bsc"]
    assert light == dataclasses.replace(full, internal={})
    assert original.chip.state == full  # ``chip.state`` is the save state


def test_the_save_state_still_round_trips_the_cache():
    # The full capture after a fresh-chip load equals the one saved, cache lines included,
    # and the light capture would not carry them (a fresh chip's lines are invalid).
    original = _new(True, "plain", False)
    _run(original.chip, 1000)
    saved = original.chip.capture_state()
    fresh = Harness(harness_speed.program())
    fresh.low[:], fresh.high[:] = original.low, original.high
    fresh.chip.restore_state(saved)
    assert fresh.chip.capture_state() == saved
    assert fresh.chip.cache.valid == original.chip.cache.valid
    assert sum(_run(fresh.chip, 200)) == sum(_run(original.chip, 200)) == 267
    blank = Harness(harness_speed.program())
    blank.chip.restore_state(original.chip.capture_state(internal=False))
    assert blank.chip.cache.valid != original.chip.cache.valid
