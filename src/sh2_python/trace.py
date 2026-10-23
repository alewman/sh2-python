"""Deterministic boundary traces: JSON Lines of :class:`~sh2_python.debug.StepRecord`.

One record per boundary, the family schema (m68000-python's ``trace.py``) with this
chip's fields: ``kind`` (instruction, slot, interrupt, exception, sleep), ``states``,
the instruction (address, opcode, text), the chip state before and after, and, when
the session tracks them, the accesses as ``[rw, address, value, width, kind]`` --
``width`` in bits and ``kind`` the bus kind bits of ``bus.py`` (``value`` is null for
cache hits and on-chip registers, seen through ``observe``). Two cores that write equal
traces for the same program behave identically as far as software can tell;
:func:`first_trace_divergence` finds where they do not.
"""

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, fields
from itertools import zip_longest
from typing import TextIO

from sh2_python.debug import BoundaryKind, StepRecord
from sh2_python.disasm import disassemble_word
from sh2_python.state import ChipState

TRACE_SCHEMA_VERSION = 1
_STATE_KEYS = tuple(f.name for f in fields(ChipState))
_MISSING = object()


def _state_to_dict(state: ChipState) -> dict[str, object]:
    out: dict[str, object] = {}
    for key in _STATE_KEYS:
        value = getattr(state, key)
        out[key] = list(value) if isinstance(value, tuple) else value
    return out


def _state_from_dict(data: dict) -> ChipState:
    if set(data) != set(_STATE_KEYS):
        raise ValueError(f"a state must have exactly the keys {sorted(_STATE_KEYS)}")
    values = dict(data)
    values["r"] = tuple(values["r"])
    return ChipState(**values)


def step_record_to_dict(record: StepRecord) -> dict[str, object]:
    instruction = None
    if record.instruction is not None:
        instruction = {
            "address": record.instruction.address,
            "opcode": record.instruction.opcode,
            "text": record.instruction.text,
        }
    out: dict[str, object] = {
        "version": TRACE_SCHEMA_VERSION,
        "sequence": record.sequence,
        "kind": record.kind.value,
        "states": record.states,
        "instruction": instruction,
        "before": _state_to_dict(record.before),
        "after": _state_to_dict(record.after),
    }
    if record.accesses is not None:
        out["accesses"] = [list(access) for access in record.accesses]
    return out


def step_record_from_dict(data: dict) -> StepRecord:
    if data.get("version") != TRACE_SCHEMA_VERSION:
        raise ValueError(f"unsupported trace version {data.get('version')!r}")
    instruction = None
    if data["instruction"] is not None:
        spec = data["instruction"]
        instruction = disassemble_word(spec["opcode"], spec["address"])
        if "text" in spec and spec["text"] != instruction.text:
            raise ValueError("instruction text does not match its opcode")
    accesses = None
    if "accesses" in data:
        accesses = tuple(tuple(access) for access in data["accesses"])
    return StepRecord(
        sequence=data["sequence"],
        kind=BoundaryKind(data["kind"]),
        before=_state_from_dict(data["before"]),
        after=_state_from_dict(data["after"]),
        states=data["states"],
        instruction=instruction,
        accesses=accesses,
    )


def write_trace(records: Iterable[StepRecord], stream: TextIO) -> int:
    """Write records as JSON Lines with sorted keys; return how many."""
    count = 0
    for record in records:
        stream.write(json.dumps(step_record_to_dict(record), sort_keys=True, separators=(",", ":")))
        stream.write("\n")
        count += 1
    return count


def read_trace(stream: TextIO) -> Iterator[StepRecord]:
    for number, line in enumerate(stream, 1):
        if not line.strip():
            continue
        try:
            yield step_record_from_dict(json.loads(line))
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"trace line {number}: {exc}") from exc


@dataclass(frozen=True, slots=True)
class TraceDivergence:
    position: int
    left: StepRecord | None
    right: StepRecord | None
    differences: tuple[tuple[str, object, object], ...]


def compare_step_records(left: StepRecord, right: StepRecord) -> tuple:
    """Every differing field (path, left, right) of two aligned records."""
    out = []

    def check(path: str, a: object, b: object) -> None:
        if a != b:
            out.append((path, a, b))

    check("kind", left.kind.value, right.kind.value)
    check("states", left.states, right.states)
    for side in ("before", "after"):
        a, b = getattr(left, side), getattr(right, side)
        for key in _STATE_KEYS:
            check(f"{side}.{key}", getattr(a, key), getattr(b, key))
    if left.accesses is not None and right.accesses is not None:
        check("accesses", left.accesses, right.accesses)
    return tuple(out)


def first_trace_divergence(
    left: Iterable[StepRecord], right: Iterable[StepRecord]
) -> TraceDivergence | None:
    for position, (a, b) in enumerate(zip_longest(left, right, fillvalue=_MISSING)):
        if a is _MISSING or b is _MISSING:
            return TraceDivergence(
                position,
                None if a is _MISSING else a,
                None if b is _MISSING else b,
                (("record", a is not _MISSING, b is not _MISSING),),
            )
        differences = compare_step_records(a, b)
        if differences:
            return TraceDivergence(position, a, b, differences)
    return None


__all__ = [
    "TRACE_SCHEMA_VERSION",
    "TraceDivergence",
    "compare_step_records",
    "first_trace_divergence",
    "read_trace",
    "step_record_from_dict",
    "step_record_to_dict",
    "write_trace",
]
