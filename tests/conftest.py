"""Shared helpers: a chip on the minimal harness, and programs written as opcode lists."""

import struct

import pytest

from sh2_python.harness import Harness

PROGRAM = 0x06000000  # where test programs are loaded (area 3 RAM in the harness)
STACK = 0x06008000


def make(words: list[int], *, at: int = PROGRAM, data: dict[int, bytes] | None = None, **opts):
    """A reset harness whose reset vectors point at ``words`` loaded at ``at``."""
    image = struct.pack(">II", at, STACK)
    harness = Harness(image, **opts)
    harness.load(at, b"".join(struct.pack(">H", w) for w in words))
    for address, blob in (data or {}).items():
        harness.load(address, blob)
    harness.chip.reset()
    return harness


def long_at(harness: Harness, address: int) -> int:
    memory, offset = harness._region(address & 0x1FFFFFFF)
    return int.from_bytes(memory[offset : offset + 4], "big")


@pytest.fixture
def program():
    return make
