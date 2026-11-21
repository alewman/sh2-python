"""Rung 0: the readability contract, enforced like correctness.

Every decode-table form names a handler; every handler must be findable by the name
a programmer greps for and must say where its rule comes from. Derived from the source
with :mod:`ast` (no execution):

1. Each ``_op_*`` method's docstring starts with the form exactly as PM table A.50
   prints it (``MOV.L @(disp,Rm),Rn``, ``CLRT``), then `` -- `` and a description,
   and its headline cites the PM section 6 entry the decode table names (``(PM 6.35``).
2. The handler lives in the module that owns its instruction family.
3. No handler name is defined twice, every form's handler exists, and every
   ``_op_`` method is some form's handler (no dead handlers).
"""

import ast
from pathlib import Path

import pytest

from sh2_python._decode import FORMS

SRC = Path(__file__).resolve().parents[1] / "src" / "sh2_python"

OWNERS = {
    "_transfer.py": {"MOV", "MOV.B", "MOV.W", "MOV.L", "MOVA", "MOVT", "SWAP.B", "SWAP.W",
                     "XTRCT"},
    "_arith.py": {"ADD", "ADDC", "ADDV", "CMP/EQ", "CMP/GE", "CMP/GT", "CMP/HI", "CMP/HS",
                  "CMP/PL", "CMP/PZ", "CMP/STR", "DIV0S", "DIV0U", "DIV1", "DT", "EXTS.B",
                  "EXTS.W", "EXTU.B", "EXTU.W", "NEG", "NEGC", "SUB", "SUBC", "SUBV"},
    "_multiply.py": {"MUL.L", "MULS.W", "MULU.W", "DMULS.L", "DMULU.L", "MAC.W", "MAC.L"},
    "_logic.py": {"AND", "AND.B", "NOT", "OR", "OR.B", "TAS.B", "TST", "TST.B", "XOR",
                  "XOR.B"},
    "_shift.py": {"ROTL", "ROTR", "ROTCL", "ROTCR", "SHAL", "SHAR", "SHLL", "SHLR", "SHLL2",
                  "SHLR2", "SHLL8", "SHLR8", "SHLL16", "SHLR16"},
    "_branch.py": {"BF", "BT", "BF/S", "BT/S", "BRA", "BRAF", "BSR", "BSRF", "JMP", "JSR",
                   "RTS"},
    "_system.py": {"CLRT", "SETT", "CLRMAC", "NOP", "LDC", "LDC.L", "LDS", "LDS.L", "STC",
                   "STC.L", "STS", "STS.L", "RTE", "SLEEP", "TRAPA"},
}  # fmt: skip


def _handlers() -> dict[str, tuple[str, str]]:
    """handler name -> (module file, docstring)."""
    found: dict[str, tuple[str, str]] = {}
    for path in sorted(SRC.glob("_*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_op_"):
                assert node.name not in found, f"{node.name} defined twice"
                found[node.name] = (path.name, ast.get_docstring(node) or "")
    return found


HANDLERS = _handlers()


@pytest.mark.parametrize("form", FORMS, ids=lambda f: f"{f.mnemonic} {f.operands}".strip())
def test_handler_docstring_names_the_form_and_cites_pm(form):
    module, doc = HANDLERS[form.handler]
    headline = doc.split("\n\n")[0].replace("\n", " ")
    name = f"{form.mnemonic} {form.operands}".strip()
    assert headline.startswith(f"{name} -- "), (form.handler, headline)
    assert f"(PM {form.section}" in headline, (form.handler, headline)
    assert form.mnemonic in OWNERS[module], (form.handler, module)


def test_no_dead_handlers():
    assert set(HANDLERS) == {form.handler for form in FORMS}
