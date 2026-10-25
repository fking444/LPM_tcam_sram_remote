"""
TCAM + SRAM two-level LPM table -- behavioural model.

Design notes: docs/architecture.md
  * CAM side only performs prefix comparison.
  * VLD marks the anchor row of a logical entry; continuation rows carry VLD=0
    and are bound to the anchor through the `span` field.
  * SRAM side stores payload by row offset.
  * IPv4 and IPv6 share one key space, so a single table serves both stacks.
"""

from math import ceil

WIDTH = 128      # unified key width
ALIGN = 8        # prefix length is rounded up to the smallest in-row unit


def v4_to_key(ip: int) -> int:
    """Map a 32-bit IPv4 address into the unified 128-bit key space."""
    return ip << (WIDTH - 32)


def make_mask(key_len: int) -> int:
    if key_len <= 0:
        return 0
    return ((1 << key_len) - 1) << (WIDTH - key_len)


class Entry:
    __slots__ = ("key", "key_len", "payload")

    def __init__(self, key, key_len, payload):
        self.key = key
        self.key_len = key_len
        self.payload = payload

    def __repr__(self):
        return "Entry(len=%d, payload=%s)" % (self.key_len, self.payload)


class CamRow:
    __slots__ = ("masked_key", "mask", "key_len", "vld", "span", "eid", "payload")

    def __init__(self, masked_key, mask, key_len, vld, span, eid, payload):
        self.masked_key = masked_key
        self.mask = mask
        self.key_len = key_len
        self.vld = vld          # True -> anchor row (independent entry)
        self.span = span        # number of rows owned by this entry (anchor only)
        self.eid = eid
        self.payload = payload

    def __repr__(self):
        return "Row(vld=%d len=%d span=%d eid=%s)" % (
            self.vld, self.key_len, self.span, self.eid)


class LpmTable:
    """Behavioural model of a CAM + SRAM LPM table."""

    def __init__(self, cell_bits=48, payload_bits=32, capacity_rows=4096):
        self.cell_bits = cell_bits
        self.payload_bits = payload_bits
        self.capacity_rows = capacity_rows
        self.rows = [None] * capacity_rows
        self.index = {}          # (key_len, masked_key) -> (start_row, Entry)
        self._next_eid = 1

    # ---------------- geometry ----------------

    def effective_len(self, key_len):
        """Prefix length rounded up to an in-row representable unit."""
        if key_len <= 0:
            return ALIGN          # a /0 still needs one mask slot
        return int(ceil(key_len / float(ALIGN)) * ALIGN)

    def rows_needed(self, key_len):
        """Physical rows consumed by one entry (key part + payload part)."""
        key_rows = int(ceil(self.effective_len(key_len) / float(self.cell_bits)))
        pay_rows = int(ceil(self.payload_bits / float(self.cell_bits)))
        return key_rows + pay_rows

    # ---------------- allocation ----------------

    def _find_run(self, n):
        run = 0
        start = -1
        for i, r in enumerate(self.rows):
            if r is None:
                if run == 0:
                    start = i
                run += 1
                if run == n:
                    return start
            else:
                run = 0
        return -1

    def insert(self, key, key_len, payload):
        """Insert or overwrite one prefix. Returns (start_row, rows_needed)."""
        mask = make_mask(key_len)
        masked_key = key & mask
        ident = (key_len, masked_key)

        if ident in self.index:                     # overwrite: no extra rows
            start, ent = self.index[ident]
            ent.payload = payload
            self.rows[start].payload = payload
            return start, self.rows[start].span

        need = self.rows_needed(key_len)
        start = self._find_run(need)
        if start < 0:
            raise MemoryError(
                "no room for len=%d (needs %d rows)" % (key_len, need))

        eid = self._next_eid
        self._next_eid += 1
        self.rows[start] = CamRow(masked_key, mask, key_len, True, need, eid, payload)
        for i in range(1, need):
            self.rows[start + i] = CamRow(0, 0, key_len, False, 0, eid, None)

        self.index[ident] = (start, Entry(masked_key, key_len, payload))
        return start, need

    def delete(self, key, key_len):
        """Release every row owned by this prefix."""
        ident = (key_len, key & make_mask(key_len))
        if ident not in self.index:
            return False
        start, _ = self.index.pop(ident)
        span = self.rows[start].span
        for i in range(start, start + span):
            self.rows[i] = None
        return True

    # ---------------- lookup ----------------

    def lookup(self, addr):
        """Longest prefix match. Returns (key_len, payload, row_index) or None."""
        best = None
        best_at = -1
        for i, r in enumerate(self.rows):
            if r is None or not r.vld:
                continue
            if (addr & r.mask) != r.masked_key:
                continue
            if best is None or r.key_len > best.key_len:
                best = r
                best_at = i

        if best is None:
            return None

        # continuation rows must still be present and belong to this entry
        for i in range(best_at + 1, best_at + best.span):
            r = self.rows[i]
            if r is None or r.eid != best.eid:
                raise RuntimeError(
                    "corrupted entry: anchor row %d span %d, row %d missing"
                    % (best_at, best.span, i))
        return best.key_len, best.payload, best_at

    # ---------------- inspection ----------------

    def read_back(self):
        """Full table dump: list of (row_index, CamRow)."""
        return [(i, r) for i, r in enumerate(self.rows) if r is not None]

    def stats(self):
        used = sum(1 for r in self.rows if r is not None)
        anchors = sum(1 for r in self.rows if r is not None and r.vld)
        return {
            "entries": anchors,
            "rows_used": used,
            "rows_total": self.capacity_rows,
            "utilization": used / float(self.capacity_rows),
            "avg_span": (used / float(anchors)) if anchors else 0.0,
        }


if __name__ == "__main__":
    t = LpmTable(cell_bits=48)
    t.insert(v4_to_key(0x0A000000), 8, "nh-1")          # 10.0.0.0/8
    t.insert(v4_to_key(0x0A010000), 16, "nh-2")         # 10.1.0.0/16
    t.insert(v4_to_key(0x0A010200), 24, "nh-3")         # 10.1.2.0/24

    for probe in (0x0A010205, 0x0A010305, 0x0A020305, 0x0B010101):
        r = t.lookup(v4_to_key(probe))
        print("probe 0x%08X -> %s" % (probe, r))

    st = t.stats()
    print("entries=%d rows_used=%d avg_span=%.2f"
          % (st["entries"], st["rows_used"], st["avg_span"]))
