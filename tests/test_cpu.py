"""Rung 0: the CPU rules the manuals settle, one test each, with the page that settles it.

U-numbers are docs/undocumented-behavior.md's items; the tests for U1, U3 and U7 are
here, U12-U14 in test_modules.py.
"""

from conftest import PROGRAM, STACK, long_at, make

NOP = 0x0009


def run(harness, steps):
    for _ in range(steps):
        harness.chip.step()
    return harness.chip


# -- reset ------------------------------------------------------------------------------


def test_reset_loads_pc_and_sp_from_vectors_0_and_1():
    # HM 4.2, table 2.1: PC and R15 from H'00000000/4; VBR = 0; SR.I3-I0 = 1111.
    chip = make([NOP]).chip
    assert chip.pc == PROGRAM and chip.R[15] == STACK
    assert chip.vbr == 0 and (chip.sr >> 4) & 15 == 15


def test_manual_reset_uses_vectors_2_and_3():
    h = make([NOP])
    h.load(8, (PROGRAM + 0x100).to_bytes(4, "big") + (0x06004000).to_bytes(4, "big"))
    h.chip.reset(power_on=False)
    assert h.chip.pc == PROGRAM + 0x100 and h.chip.R[15] == 0x06004000


def test_undefined_registers_take_the_constructor_value():
    # U19: zero by default; ``undefined=`` fills R0-R14, GBR, MACH, MACL, PR.
    h = make([NOP], undefined=0xDEADBEEF)
    assert h.chip.R[0] == 0xDEADBEEF and h.chip.gbr == 0xDEADBEEF and h.chip.pr == 0xDEADBEEF


# -- delay slots ----------------------------------------------------------------------


def test_delay_slot_runs_before_the_target():
    # PM 6.8 (BRA): the slot instruction runs, then the target.
    h = make([0xA001, 0xE105, 0xE207, NOP, 0xE309])  # BRA +2 -> PROGRAM+6; MOV #5,R1 in slot
    chip = run(h, 3)
    assert chip.R[1] == 5 and chip.R[2] == 0 and chip.R[3] == 0  # MOV #7,R2 was skipped
    assert chip.pc == PROGRAM + 8


def test_branch_register_is_read_before_the_slot_changes_it():
    # PM 4.1.5: "the branch will still be made using the value of the register prior to
    # the change".
    h = make([0xD101, 0x412B, 0xE100, NOP, 0x0600, 0x0010])  # MOV.L @(4,PC),R1; JMP @R1; MOV #0,R1
    chip = run(h, 3)
    assert chip.pc == 0x06000010


def test_u1_pc_relative_in_a_delay_slot_uses_the_target_plus_2():
    # U1, PM 6.33/6.36: "the PC must point to ... (the starting address of the branch
    # destination) + 2". MOVA in the slot of a BRA to PROGRAM + 0x20.
    words = [0xA00E, 0xC701] + [NOP] * 20  # BRA PROGRAM+0x20; MOVA @(4,PC),R0
    h = make(words)
    run(h, 2)
    # PC for the slot = target + 2 = PROGRAM + 0x22; MOVA: (PC & ~3) + 4 = PROGRAM + 0x24.
    assert h.chip.R[0] == PROGRAM + 0x24


def test_u1_mov_l_pc_relative_in_a_slot():
    words = [0xA00E, 0xD201] + [NOP] * 30
    h = make(words)
    h.load(PROGRAM + 0x24, (0x12345678).to_bytes(4, "big"))
    run(h, 2)
    assert h.chip.R[2] == 0x12345678


def test_u3_slot_illegal_saves_the_branch_target():
    # HM 4.5.3: a PC-changing instruction in the slot -> vector 6, saved PC = the
    # delayed branch's jump address.
    words = [0xA00E, 0xA000] + [NOP] * 20  # BRA +; BRA in the slot
    h = make(words)
    h.load(6 * 4, (0x06001000).to_bytes(4, "big"))  # VBR = 0
    run(h, 2)
    chip = h.chip
    assert chip.pc == 0x06001000
    assert long_at(h, chip.R[15]) == PROGRAM + 0x20  # saved PC: the target
    assert chip.R[15] == STACK - 8


def test_undefined_code_in_a_slot_is_a_slot_illegal():
    h = make([0xA00E, 0xFFFF] + [NOP] * 20)
    h.load(6 * 4, (0x06001000).to_bytes(4, "big"))
    run(h, 2)
    assert h.chip.pc == 0x06001000


def test_general_illegal_saves_the_undefined_codes_address():
    # HM 4.5.4: vector 4; "the program counter value stored is the start address of the
    # undefined code".
    h = make([NOP, 0xFFFF])
    h.load(4 * 4, (0x06001000).to_bytes(4, "big"))
    run(h, 2)
    assert h.chip.pc == 0x06001000
    assert long_at(h, h.chip.R[15]) == PROGRAM + 2


def test_u2_not_taken_bt_s_makes_no_slot():
    # U2 (the three emulators; unverified): a branch after a not-taken BT/S is legal.
    h = make([0x0008, 0x8D02, 0xA003, NOP, NOP, NOP, NOP, NOP])  # CLRT; BT/S (not taken); BRA
    chip = run(h, 3)
    assert chip.delay_target == PROGRAM + 4 + 4 + 6  # BRA's own target, not an exception


def test_bt_taken_and_not_taken_states():
    # PM table A.50: BT 3 taken / 1 not (one-state memory: the harness's area 3 has the
    # BSC's reset waits, so compare against a NOP's cost).
    taken = make([0x0018, 0x8900, NOP, NOP])
    taken.chip.step()
    t_states = taken.chip.step()
    untaken = make([0x0008, 0x8900, NOP, NOP])
    untaken.chip.step()
    u_states = untaken.chip.step()
    assert t_states - u_states >= 2


# -- exceptions ---------------------------------------------------------------------


def test_trapa_pushes_sr_then_the_next_address_and_jumps():
    # HM 4.5.2: SR pushed, then "the start address of the instruction to be executed
    # after the TRAPA instruction"; vector = VBR + imm x 4.
    h = make([0xC320, NOP])  # TRAPA #32
    h.load(32 * 4, (0x06002000).to_bytes(4, "big"))
    sr = h.chip.sr
    run(h, 1)
    chip = h.chip
    assert chip.pc == 0x06002000
    assert long_at(h, chip.R[15]) == PROGRAM + 2
    assert long_at(h, chip.R[15] + 4) == sr


def test_irl_interrupt_auto_vector_and_mask():
    # HM 4.4, table 5.3: IRL level n -> vector 64 + n/2; I3-I0 takes the level.
    h = make([0xE000, 0x400E, NOP, NOP, NOP])  # MOV #0,R0; LDC R0,SR (I = 0)
    h.load((64 + 5) * 4, (0x06003000).to_bytes(4, "big"))
    chip = h.chip
    chip.irl = 11
    run(h, 2)
    assert chip.pc == PROGRAM + 4  # not after LDC (HM 4.6.2): one more instruction first
    run(h, 1)  # the NOP runs
    run(h, 1)  # now the interrupt
    assert chip.pc == 0x06003000
    assert (chip.sr >> 4) & 15 == 11
    assert long_at(h, chip.R[15]) == PROGRAM + 6


def test_no_interrupt_between_a_delayed_branch_and_its_slot():
    # HM 4.6.1.
    h = make([0xE000, 0x400E, NOP, 0xA00E, NOP] + [NOP] * 20)
    h.load((64 + 7) * 4, (0x06003000).to_bytes(4, "big"))
    chip = h.chip
    run(h, 4)  # MOV, LDC, NOP (I = 0), BRA
    assert chip.delay_target == PROGRAM + 6 + 4 + 0x1C
    chip.irl = 15
    run(h, 1)  # the slot, not the interrupt
    assert chip.delay_target < 0 and chip.pc == PROGRAM + 6 + 4 + 0x1C
    run(h, 1)
    assert chip.pc == 0x06003000


def test_nmi_on_the_falling_edge_sets_i_to_15():
    # HM 5.2.1, 5.3.3: NMIE = 0 -> falling edge; NMI is vector 11 and sets I = 15.
    h = make([NOP, NOP, NOP])
    h.load(11 * 4, (0x06004000).to_bytes(4, "big"))
    chip = h.chip
    chip.nmi = 0
    run(h, 1)
    assert chip.pc == 0x06004000 and (chip.sr >> 4) & 15 == 15


def test_address_error_on_misaligned_longword_read():
    # HM table 4.6 and 4.3.2: vector 9, after the instruction, saving the next address.
    h = make([0xE101, 0x6012, NOP])  # MOV #1,R1; MOV.L @R1,R0
    h.load(9 * 4, (0x06005000).to_bytes(4, "big"))
    run(h, 3)
    assert h.chip.pc == 0x06005000
    assert long_at(h, h.chip.R[15]) == PROGRAM + 4


def test_rte_pops_pc_then_sr():
    # PM 6.50: PC from @R15, then SR from @R15+4, delayed.
    h = make([0x002B, NOP])
    h.load(STACK, (0x06006000).to_bytes(4, "big") + (0x00000301).to_bytes(4, "big"))
    run(h, 2)
    assert h.chip.pc == 0x06006000 and h.chip.sr == 0x301 and h.chip.R[15] == STACK + 8


# -- arithmetic the manual's code settles -------------------------------------------


def test_div1_unsigned_32_by_16_example_from_pm_6_19():
    # PM 6.19 example 1: R1 (32 bits) / R0 (16 bits) = R1 (16 bits), unsigned:
    # SHLL16 R0; DIV0U; DIV1 R0,R1 x 16; ROTCL R1; EXTU.W R1,R2.
    words = [0x4028, 0x0019] + [0x3104] * 16 + [0x4124, 0x621D, NOP]
    h = make(words)
    chip = h.chip
    chip.R[0], chip.R[1] = 7, 1000
    run(h, 2 + 16 + 2)
    assert chip.R[2] == 1000 // 7


def test_div1_signed_example():
    # PM 6.19 example 3: R1 (16 bits) / R0 (16 bits) = R1 (16 bits), signed:
    # SHLL16 R0; EXTS.W R1,R1; XOR R2,R2; MOV R1,R3; ROTCL R3; SUBC R2,R1; DIV0S R0,R1;
    # DIV1 R0,R1 x 16; EXTS.W R1,R1; ROTCL R1; ADDC R2,R1; EXTS.W R1,R1.
    words = [0x4028, 0x611F, 0x222A, 0x6313, 0x4324, 0x312A, 0x2107]
    words += [0x3104] * 16 + [0x611F, 0x4124, 0x312E, 0x611F, NOP]
    for dividend, divisor in ((100, 7), (-100, 7), (100, -7), (-100, -7), (32767, 3)):
        h = make(words)
        chip = h.chip
        chip.R[1], chip.R[0] = dividend & 0xFFFFFFFF, divisor & 0xFFFFFFFF
        run(h, 7 + 16 + 4)
        quotient = int(dividend / divisor)
        assert chip.R[1] == quotient & 0xFFFFFFFF, (dividend, divisor)


def test_u7_mac_w_saturates_macl_and_leaves_mach():
    # U7 / C11: S = 1 saturates MACL; PM 6.31's code marks the MACH |= 1 lines "For
    # SH-1 CPU (... not needed for SH-2 CPU)".
    h = make([0x0028, 0x0028, 0x412F, NOP])  # MAC.W @R2+,@R1+ at the third word
    chip = h.chip
    h.load(0x06007000, (0x7FFF).to_bytes(2, "big") + (0x7FFF).to_bytes(2, "big"))
    run(h, 2)
    chip.R[1], chip.R[2] = 0x06007000, 0x06007002
    chip.sr |= 0x2  # S
    chip.mach, chip.macl = 0x12345678, 0x7FFF0000
    run(h, 1)
    assert chip.macl == 0x7FFFFFFF and chip.mach == 0x12345678


def test_u7_mac_l_saturates_at_48_bits():
    # PM 6.29: "limited to a range of H'FFFF800000000000 (minimum) to H'00007FFFFFFFFFFF".
    h = make([0x021F, NOP])  # MAC.L @R1+,@R2+
    h.load(0x06007000, (0x7FFFFFFF).to_bytes(4, "big") * 2)
    chip = h.chip
    chip.R[1], chip.R[2] = 0x06007000, 0x06007004
    chip.sr |= 0x2
    chip.mach, chip.macl = 0, 0
    run(h, 1)
    assert (chip.mach, chip.macl) == (0x00007FFF, 0xFFFFFFFF)
    h = make([0x021F, NOP])
    h.load(0x06007000, (0x7FFFFFFF).to_bytes(4, "big") + (0x80000000).to_bytes(4, "big"))
    chip = h.chip
    chip.R[1], chip.R[2] = 0x06007000, 0x06007004
    chip.sr |= 0x2
    run(h, 1)
    assert (chip.mach, chip.macl) == (0xFFFF8000, 0x00000000)


def test_sr_keeps_only_its_defined_bits():
    # PM 2.2: bits other than M, Q, I3-I0, S, T read 0.
    h = make([0xE0FF, 0x400E, 0x0102])  # MOV #-1,R0; LDC R0,SR; STC SR,R1
    chip = run(h, 3)
    assert chip.R[1] == 0x3F3


def test_mov_l_postincrement_into_its_own_address_register():
    # PM 6.32's code: R[n]=Read_Long(R[m]); if (n!=m) R[m]+=4.
    h = make([0x6116, NOP])  # MOV.L @R1+,R1
    h.load(0x06007000, (0xCAFEF00D).to_bytes(4, "big"))
    h.chip.R[1] = 0x06007000
    run(h, 1)
    assert h.chip.R[1] == 0xCAFEF00D


def test_mov_l_predecrement_stores_the_old_value():
    # PM 6.32's code: Write_Long(R[n]-4,R[m]); R[n]-=4 -- with n == m, the old value.
    h = make([0x2116, NOP])  # MOV.L R1,@-R1
    h.chip.R[1] = 0x06007004
    run(h, 1)
    assert h.chip.R[1] == 0x06007000 and long_at(h, 0x06007000) == 0x06007004


def test_state_round_trip():
    h = make([0xE07F, 0xA000, 0x0009, 0x0009])
    chip = h.chip
    chip.step()
    chip.step()  # into the BRA's slot: in-flight state
    saved = chip.state
    assert saved.delay_target >= 0 and saved.r[0] == 0x7F
    chip.step()
    chip.state = saved
    assert chip.capture_state() == saved
