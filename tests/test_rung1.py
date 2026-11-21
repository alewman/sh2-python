"""Rung 1: the instruction corpus (validation/rung1), agreed by MAME and jgenesis or ruled.

The gate itself is validation/rung1/gate.py; here it runs as a test: one case in 25
always, every case with ``-m slow``. Tier: emulator-derived where the two referees
agree, manual where overrides.json rules a disagreement (docs/validation.md).
"""

import json
import sys
from pathlib import Path

import pytest

RUNG1 = Path(__file__).resolve().parents[1] / "validation" / "rung1"
sys.path.insert(0, str(RUNG1))

import gate  # noqa: E402


def _run(step: int) -> None:
    counts, failures = gate.check(step=step)
    assert not failures, "\n".join(failures[:20])
    assert counts["agreed"] > 0


def test_corpus_sample():
    _run(25)


@pytest.mark.slow
def test_corpus_complete():
    _run(1)


def test_every_ruling_cites_the_manual_and_names_its_tests():
    rules = json.loads((RUNG1 / "overrides.json").read_text())["rules"]
    root = RUNG1.parents[1]
    for name, rule in rules.items():
        assert rule["ruling"] in ("mame", "jgenesis", "manual"), name
        assert "HM" in rule["citation"] or "PM" in rule["citation"], name
        for test in rule["tests"]:
            path, _, function = test.partition("::")
            assert f"def {function}(" in (root / path).read_text(), test
