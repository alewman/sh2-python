"""What a bus callback is told about the cycle it makes: the ``kind`` bits.

The board passes ``read(address, width, kind) -> int`` and ``write(address, width,
value, kind)`` to :class:`~sh2_python.SH7604`. Each call is **one external bus
cycle** the bus state controller (BSC) starts: the core has already removed the
cache-space bits (A31-A29), split the access into port-width cycles (HM 7.4.1, a
longword on a 16-bit area is two calls, high word first) and decided the cycle is
external. Cache hits, on-chip module accesses, and the address- and data-array spaces
never reach the callbacks (HM 7.11.2: they use the cache bus or the peripheral bus).

``kind`` is a sum of these bits (docs/handoff-brief.md, "The bus contract"; decided by
Aubrey 2026-10-23: misses appear as ``FILL`` cycles, hits only on ``observe``).
"""

#: An ordinary data access (no bit set).
DATA = 0
#: An instruction fetch: always a longword at the CPU (HM 7.11.2), split by the port.
FETCH = 1
#: One longword of a cache line fill after a miss: four per line, in HM's wrap order
#: (address + 4, + 8, + 12, then the missed longword; HM 8.4.1, figure 8.4).
FILL = 2
#: Set on the first cycle of a line fill.
FIRST = 4
#: A cache-through access (A31-A29 = 001), or a cache-area access with the cache off.
THROUGH = 8
#: A DMAC cycle; ``DMA1`` is also set for channel 1.
DMA = 16
DMA1 = 32
#: An external vector fetch (ICR.VECMD = 1; HM table 4.3 note 2).
VECTOR = 64
#: A BSC refresh cycle (no data; made through ``read``, value ignored).
REFRESH = 128
#: ``observe`` only: the access hit the cache and made no bus cycle.
HIT = 256
#: ``observe`` only: the access is a write.
WRITE = 512

KIND_NAMES = {
    FETCH: "FETCH",
    FILL: "FILL",
    FIRST: "FIRST",
    THROUGH: "THROUGH",
    DMA: "DMA",
    DMA1: "DMA1",
    VECTOR: "VECTOR",
    REFRESH: "REFRESH",
    HIT: "HIT",
    WRITE: "WRITE",
}


def kind_text(kind: int) -> str:
    """``FETCH|FILL|FIRST`` style text for a kind value (``DATA`` when no bit is set)."""
    names = [name for bit, name in KIND_NAMES.items() if kind & bit]
    return "|".join(names) if names else "DATA"


__all__ = [
    "DATA",
    "DMA",
    "DMA1",
    "FETCH",
    "FILL",
    "FIRST",
    "HIT",
    "KIND_NAMES",
    "REFRESH",
    "THROUGH",
    "VECTOR",
    "WRITE",
    "kind_text",
]
