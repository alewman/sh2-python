"""The SH7604 cache: 4 KB, four ways of 64 sixteen-byte lines, write-through, pseudo-LRU.

HM chapter 8. An address in the cache area (A31-A29 = 000) splits into a 19-bit tag
(A28-A10), a 6-bit entry (A9-A4) and a byte in the line (A3-A0) (HM figure 8.2). Each
entry has four ways, each with a tag, a valid bit and 16 bytes; each entry has six LRU
bits (HM 8.4.5, figure 8.8). The same storage is reachable directly:

- the **address array** at H'60000000-H'600003FF (A31-A29 = 011), longword only, the
  way chosen by CCR.W1-W0 (HM 8.4.9, figure 8.11);
- the **data array** at H'C0000000-H'C0000FFF (A31-A29 = 110), any size, way in A11-A10
  (HM 8.4.8, figure 8.10): 4 KB of RAM with the cache off, ways 0-1 as 2 KB of RAM in
  two-way mode (CCR.TW);
- the **associative purge** space at H'40000000- (A31-A29 = 010): a write invalidates
  the line holding that address in whichever way holds it (HM 8.4.7).

This module holds the storage and the rules; the chip (``_core.py``) decides which
accesses reach it and makes the line fills on the bus (``kind`` = ``FILL``).
"""

from sh2_python._module import Module, Register

# CCR bits (HM 8.2, table 8.1).
CCR_CE = 0x01  # cache enable
CCR_ID = 0x02  # instruction replacement disable
CCR_OD = 0x04  # data replacement disable (printed "OC" in HM appendix B: C7)
CCR_TW = 0x08  # two-way mode: ways 0-1 are RAM, ways 2-3 cache
CCR_CP = 0x10  # write 1: purge every valid bit and the LRU; always reads 0
CCR_W = 0xC0  # W1-W0: the way the address array shows

# HM table 8.3, "LRU Information after Update": for each way, the bits a hit or a
# replacement clears and sets (bit 5..0 of the six LRU bits).
LRU_CLEAR = (0x38, 0x06, 0x01, 0x00)
LRU_SET = (0x00, 0x20, 0x14, 0x0B)


def lru_victim(lru: int) -> int:
    """The way HM table 8.4 ("Selection Conditions for Replaced Way") replaces.

    Way 0 when bits 5, 4, 3 are 1, 1, 1; way 1 when bits 5, 2, 1 are 0, 1, 1; way 2
    when bits 4, 2, 0 are 0, 0, 1; way 3 when bits 3, 1, 0 are 0, 0, 0. LRU = 0 after
    a purge selects way 3, then 2, 1, 0 (HM 8.4.5). The six bits can hold a value no
    row matches only if software writes one through the address array (HM: "be sure
    not to write a value other than 0"); this core then replaces way 3, a choice no
    source settles (recorded in docs/undocumented-behavior.md, U8).
    """
    if lru & 0x38 == 0x38:
        return 0
    if lru & 0x26 == 0x06:
        return 1
    if lru & 0x15 == 0x01:
        return 2
    return 3


class Cache(Module):
    """The cache storage, its LRU, CCR, and the three direct-access spaces."""

    NAME = "cache"
    REGISTERS = (Register(0xFFFFFE92, "CCR", 8, 0x00),)

    def __init__(self, chip: object) -> None:
        super().__init__(chip)
        # Cache contents survive reset (HM 8.5.1: "Cache memory is not initialized in a
        # reset"); they start zeroed here, the "undefined" choice of U19.
        self.data = bytearray(4096)  # way * 1024 + entry * 16 + byte
        self.tags = [0] * 256  # entry * 4 + way
        self.valid = [0] * 256
        self.lru = [0] * 64
        self.ccr = 0
        #: True when CCR.CE is set: the core's hot path reads this plain attribute.
        self.enabled = False

    def reset(self) -> None:
        # HM 8.4.6: "CCR's CE bit is cleared to 0 by a power-on reset or manual reset";
        # CCR's other bits too (table 8.1, initial value H'00). Contents are kept.
        self.chip._ccr_at = 0
        self.write_ccr(0)

    # -- CCR -------------------------------------------------------------------

    def write_ccr(self, value: int) -> None:
        """CCR write (HM 8.2). CP = 1 purges and reads back 0 (HM 8.2 bit 4; C7)."""
        if value & CCR_CP:
            self.purge_all()
        # Bit 5 is reserved and reads 0; CP always reads 0.
        self.ccr = value & ~(CCR_CP | 0x20) & 0xFF
        self.enabled = bool(self.ccr & CCR_CE)

    def read8(self, address: int) -> int:
        chip = self.chip
        if chip._ccr_at and chip.clock >= chip._ccr_at:
            chip._settle_ccr()
        return self.ccr if address == 0xFFFFFE92 else 0

    def write8(self, address: int, value: int) -> None:
        """A CPU's CCR write: it waits behind a buffered external write (U9).

        DN (hardware, a CPS3; the judge for purge ordering): a cache-through store, a
        CCR purge, then a cached read returns the line from before the purge, and the
        eight values of DN's sequence fit "the CCR write waits behind the pending store
        while hits can still use the old line". HM 7.11.2 says on-chip module accesses
        do not wait for a buffered write (C20). So a CCR write made while the BSC's
        write buffer holds a write is kept, and lands when that write's cycles end
        (the core's ``_settle_ccr``); the CPU and its cache hits go on. One still
        waiting when another arrives lands first.
        """
        if address != 0xFFFFFE92:
            return
        chip = self.chip
        if chip._ccr_at:
            chip._settle_ccr()
        if chip._wb_end > chip.clock:
            chip._ccr_at = chip._wb_end
            chip._ccr_value = value
        else:
            self.write_ccr(value)

    def purge_all(self) -> None:
        """CCR.CP = 1: every valid bit and all LRU information to 0 (HM 8.4.6)."""
        self.valid = [0] * 256
        self.lru = [0] * 64

    # -- lookup, replacement ----------------------------------------------------

    def lookup(self, address: int) -> int:
        """The way holding ``address``, or -1 on a miss (HM 8.4.1).

        Tags are compared in all four ways, two-way mode included (HM 8.4.5). Two ways
        holding the same tag can only be made through the address array; the first in
        way order 0-3 wins (U8, a choice).
        """
        tag = (address >> 10) & 0x7FFFF
        base = ((address >> 4) & 63) << 2
        tags, valid = self.tags, self.valid
        for way in range(4):
            if valid[base + way] and tags[base + way] == tag:
                return way
        return -1

    def touch(self, entry: int, way: int) -> None:
        """Record an access to ``way`` in the entry's LRU (HM table 8.3).

        HM 8.4.5: the LRU is rewritten on a read hit, a write hit and a replacement.
        """
        self.lru[entry] = (self.lru[entry] & ~LRU_CLEAR[way]) | LRU_SET[way]

    def victim(self, entry: int) -> int:
        """The way a miss in ``entry`` replaces (HM table 8.4; two-way mode HM 8.5.4)."""
        lru = self.lru[entry]
        if self.ccr & CCR_TW:
            # Two-way mode replaces only ways 2 and 3; with the LRU at 0 "ways 3 and 2
            # are initially used, in that order" (HM 8.5.4). Table 8.4's bit 0 alone
            # separates them.
            return 2 if lru & 1 else 3
        return lru_victim(lru)

    def allocate(self, address: int, way: int) -> int:
        """Write the tag and valid bit for a replacement; return the line's data offset.

        HM 8.4.5: "Writing for the tag address and valid bit for cache replacement does
        not wait for the read from memory to be completed."
        """
        entry = (address >> 4) & 63
        index = (entry << 2) + way
        self.tags[index] = (address >> 10) & 0x7FFFF
        self.valid[index] = 1
        self.touch(entry, way)
        return (way << 10) | (entry << 4)

    # -- line data ---------------------------------------------------------------

    def get(self, offset: int, width: int) -> int:
        """``width`` bits of the data array at ``offset`` (big-endian)."""
        d = self.data
        if width == 32:
            return (d[offset] << 24) | (d[offset + 1] << 16) | (d[offset + 2] << 8) | d[offset + 3]
        if width == 16:
            return (d[offset] << 8) | d[offset + 1]
        return d[offset]

    def put(self, offset: int, width: int, value: int) -> None:
        d = self.data
        if width == 32:
            d[offset] = (value >> 24) & 0xFF
            d[offset + 1] = (value >> 16) & 0xFF
            d[offset + 2] = (value >> 8) & 0xFF
            d[offset + 3] = value & 0xFF
        elif width == 16:
            d[offset] = (value >> 8) & 0xFF
            d[offset + 1] = value & 0xFF
        else:
            d[offset] = value & 0xFF

    def write_hit(self, address: int, width: int, value: int) -> bool:
        """Write-through's cache half: update a hit line and its LRU (HM 8.4.2)."""
        way = self.lookup(address)
        if way < 0:
            return False
        entry = (address >> 4) & 63
        self.put((way << 10) | (entry << 4) | (address & 15), width, value)
        self.touch(entry, way)
        return True

    # -- the direct-access spaces --------------------------------------------------

    def purge_line(self, address: int) -> None:
        """Associative purge (HM 8.4.7): invalidate the line holding ``address`` in any way."""
        tag = (address >> 10) & 0x7FFFF
        base = ((address >> 4) & 63) << 2
        for way in range(4):
            if self.valid[base + way] and self.tags[base + way] == tag:
                self.valid[base + way] = 0

    def read_address_array(self, address: int) -> int:
        """Address array read (HM 8.4.9, figure 8.11): tag, LRU and V of the CCR.W way."""
        entry = (address >> 4) & 63
        index = (entry << 2) + ((self.ccr & CCR_W) >> 6)
        return (self.tags[index] << 10) | (self.lru[entry] << 4) | (self.valid[index] << 2)

    def write_address_array(self, address: int, value: int) -> None:
        """Address array write (HM figure 8.11): tag and V from the address, LRU from the data."""
        entry = (address >> 4) & 63
        index = (entry << 2) + ((self.ccr & CCR_W) >> 6)
        self.tags[index] = (address >> 10) & 0x7FFFF
        self.valid[index] = (address >> 2) & 1
        self.lru[entry] = (value >> 4) & 63

    def state(self) -> dict[str, int]:
        return {"CCR": self.ccr}

    def load_state(self, values: dict[str, int]) -> None:
        self.ccr = values["CCR"]
        self.enabled = bool(self.ccr & CCR_CE)


__all__ = ["CCR_CE", "CCR_CP", "CCR_ID", "CCR_OD", "CCR_TW", "Cache", "lru_victim"]
