"""Rung 0: the decode table against PM appendix A, and the pipeline classes against PM 7.

The table is generated (scripts/gen_decode.py) from tests/pm_a50.py, which is PM table
A.50 read by scripts/extract_pm_tables.py; these tests regenerate it, check that every
one of the 65,536 encodings decodes to at most one form, that every form has a handler
and a PM table 7.2 class, and that the classes say what PM table 7.2 prints.
"""

import importlib.util
from pathlib import Path

import pytest
from pm_a50 import A50

from sh2_python import SH7604
from sh2_python._decode import FORMS, decode_index
from sh2_python.pipeline import CLASSES, EXCEPTIONS, TAKEN_EXTRA

ROOT = Path(__file__).resolve().parents[1]


def test_142_forms_from_pm_table_a50():
    # PM table 5.1 ("Total: 62" mnemonics, 142 forms) and table A.1 ("Total: 142" SH-2
    # forms against 133 for the SH-1).
    assert len(A50) == 142
    assert len(FORMS) == 142
    assert [(f.mnemonic, f.operands, f.code) for f in FORMS] == [row[:3] for row in A50]


def test_generated_file_is_current():
    spec = importlib.util.spec_from_file_location("gen_decode", ROOT / "scripts" / "gen_decode.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        expected = module.formatted(module.generate())
    except SystemExit as exc:  # no ruff on this interpreter's path (the PyPy venv)
        pytest.skip(str(exc))
    assert (ROOT / "src" / "sh2_python" / "_decode.py").read_text() == expected


def test_every_encoding_decodes_to_at_most_one_form():
    index = decode_index()  # raises on any overlap
    defined = sum(form is not None for form in index)
    # Each form covers 2 ** (its variable bits) encodings; they must not overlap.
    expected = sum(2 ** sum(c not in "01" for c in form.code) for form in FORMS)
    assert defined == expected == 0x10000 - index.count(None)


def test_every_form_has_a_handler_and_a_class():
    for form in FORMS:
        assert callable(getattr(SH7604, form.handler, None)), form.handler
        assert form.pipeline in CLASSES, form


# PM table 7.2 (pp. 177-183), SH-2 column: stages and states of each category.
TABLE_7_2 = {
    "transfer": (3, 1), "load": (5, 1), "store": (4, 1), "arith": (3, 1),
    "mac_w": (7, 3), "mac_l": (9, 3), "mul_w": (6, 1), "mul_l": (9, 2),
    "logic": (3, 1), "logic_mem": (6, 3), "tas": (6, 4), "shift": (3, 1),
    "branch_cond": (3, 1), "branch_cond_delayed": (3, 1), "branch": (3, 2),
    "system": (3, 1), "ldc_l": (5, 3), "stc_l": (4, 2), "lds_l_pr": (5, 1),
    "sts_l_pr": (4, 1), "reg_to_mac": (4, 1), "mem_to_mac": (4, 1), "mac_to_reg": (5, 1),
    "mac_to_mem": (4, 1), "rte": (5, 4), "trap": (9, 8), "sleep": (3, 3),
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_pipeline_class_matches_pm_table_7_2(name):
    pclass = CLASSES[name]
    stages, states = TABLE_7_2[name]
    assert len(pclass.stages) == stages
    assert pclass.states == states
    assert pclass.stages[:3] == ("IF", "ID", "EX")


def test_multiplier_states_and_id_stalls():
    # PM 7.7.2: mm runs 2 states after MULS.W/MULU.W/MAC.W, 4 after the 32-bit
    # multiplies and MAC.L; MAC.W, MAC.L and the double-length multiplies stall the
    # next instruction's ID one slot.
    assert {n: CLASSES[n].mm for n in ("mul_w", "mac_w", "mul_l", "mac_l")} == {
        "mul_w": 2,
        "mac_w": 2,
        "mul_l": 4,
        "mac_l": 4,
    }
    assert [CLASSES[n].stages.count("mm") for n in ("mul_w", "mac_w", "mul_l", "mac_l")] == [
        2,
        2,
        4,
        4,
    ]
    assert {n: CLASSES[n].next_id_stall for n in ("mul_w", "mac_w", "mul_l", "mac_l")} == {
        "mul_w": 0,
        "mac_w": 1,
        "mul_l": 1,
        "mac_l": 1,
    }
    # PM figures 7.75, 7.76, 7.84, 7.85, 7.92: ID stalls of the next instruction.
    assert CLASSES["logic_mem"].next_id_stall == 2
    assert CLASSES["tas"].next_id_stall == 3
    assert CLASSES["ldc_l"].next_id_stall == 2
    assert CLASSES["stc_l"].next_id_stall == 1
    assert CLASSES["rte"].next_id_stall == 3


def test_exception_sequences_are_pm_7_7_7():
    assert " ".join(EXCEPTIONS["interrupt"].stages) == "IF ID EX EX MA MA EX MA EX EX"
    assert EXCEPTIONS["address_error"].stages == EXCEPTIONS["interrupt"].stages
    assert " ".join(EXCEPTIONS["illegal"].stages) == "IF ID EX EX MA MA MA EX EX"


def test_table_a50_states_agree_with_the_classes():
    """Table A.50's state cells are table 7.2's numbers (C1: TRAPA is 8)."""
    for form in FORMS:
        pclass = CLASSES[form.pipeline]
        minimum = int(form.states.split("/")[0].split("to")[0])
        if form.pipeline in TAKEN_EXTRA:
            # "3/1": taken / not taken; the class counts the not-taken state.
            taken, not_taken = (int(x) for x in form.states.split("/"))
            assert not_taken == pclass.states
            assert taken == not_taken + TAKEN_EXTRA[form.pipeline]
        else:
            assert minimum == pclass.states, form
    trapa = next(f for f in FORMS if f.mnemonic == "TRAPA")
    assert trapa.states == "8"
