"""
Cell allocator -- variable-length rows, best-fit placement, fragmentation.

A logical entry is not a fixed-size object: a long prefix needs several rows, a
short one needs few. Deleting a long entry leaves a hole of that exact size, and
holes of irregular size are hard to reuse. This module models the placement side
of the problem so the width trade-off can be measured instead of guessed.
"""

from math import ceil

from lpm_table import ALIGN


def rows_needed(key_len, cell_bits, payload_bits=32):
    """Rows consumed by one entry at a given cell width."""
    eff = ALIGN if key_len <= 0 else int(ceil(key_len / float(ALIGN)) * ALIGN)
    return int(ceil(eff / float(cell_bits))) + int(ceil(payload_bits / float(cell_bits)))


def bit_utilization(key_len, cell_bits, payload_bits=32):
    """Fraction of physically allocated bits that carry useful information.

    A short prefix stored in a wide row wastes the rest of the row, and because
    total capacity is rows x width, that waste is real capacity loss.
    """
    eff = ALIGN if key_len <= 0 else int(ceil(key_len / float(ALIGN)) * ALIGN)
    allocated = rows_needed(key_len, cell_bits, payload_bits) * cell_bits
    return (eff + payload_bits) / float(allocated)


class CellAllocator:
    """First-free / best-fit allocator over a flat row space."""

    def __init__(self, total_rows):
        self.total = total_rows
        self.used = [False] * total_rows

    def _runs(self):
        """Yield (start, length) of every free run."""
        i = 0
        n = self.total
        while i < n:
            if self.used[i]:
                i += 1
                continue
            start = i
            while i < n and not self.used[i]:
                i += 1
            yield start, i - start

    def alloc(self, n):
        """Best fit: the smallest free run that still satisfies n."""
        best = None
        for start, length in self._runs():
            if length >= n and (best is None or length < best[1]):
                best = (start, length)
        if best is None:
            return None
        start = best[0]
        for i in range(start, start + n):
            self.used[i] = True
        return start

    def release(self, start, n):
        for i in range(start, start + n):
            self.used[i] = False

    def free_rows(self):
        return self.used.count(False)

    def stats(self):
        free_rows = self.used.count(False)
        largest = 0
        runs = 0
        for _, length in self._runs():
            runs += 1
            if length > largest:
                largest = length
        frag = 0.0 if free_rows == 0 else 1.0 - (largest / float(free_rows))
        return {
            "used": self.total - free_rows,
            "free": free_rows,
            "free_runs": runs,
            "largest_free_run": largest,
            "fragmentation": frag,
        }


def simulate(key_lens, cell_bits, total_rows, churn=0.4, seed=11):
    """Place a workload with insert/delete churn, then report what fits.

    Pure insertion never creates holes, so fragmentation stays at zero and the
    comparison is meaningless. Real tables churn: entries are deleted while
    others are added, and the leftover holes have irregular sizes.
    """
    import random as _random

    rnd = _random.Random(seed)
    a = CellAllocator(total_rows)
    placed = []
    inserted = 0
    failed = 0
    blocked = 0
    span_sum = 0
    util_sum = 0.0

    def put(kl):
        nonlocal inserted, failed, blocked, span_sum, util_sum
        n = rows_needed(kl, cell_bits)
        start = a.alloc(n)
        if start is None:
            failed += 1
            # free rows exist but are not contiguous -- this is the real cost
            if a.free_rows() >= n:
                blocked += 1
            return
        inserted += 1
        span_sum += n
        util_sum += bit_utilization(kl, cell_bits)
        placed.append((start, n))

    # phase 1 -- fill up
    cut = int(len(key_lens) * 0.65)
    for kl in key_lens[:cut]:
        put(kl)

    # phase 2 -- churn: drop a share of what is already placed
    rnd.shuffle(placed)
    survivors = []
    released = 0
    for start, n in placed:
        if rnd.random() < churn:
            a.release(start, n)
            released += 1
        else:
            survivors.append((start, n))

    # phase 3 -- keep inserting into the holes
    for kl in key_lens[cut:]:
        put(kl)

    st = a.stats()
    return {
        "cell_bits": cell_bits,
        "inserted": inserted,
        "failed": failed,
        "blocked": blocked,
        "released": released,
        "avg_span": (span_sum / float(inserted)) if inserted else 0.0,
        "avg_bit_util": (util_sum / float(inserted)) if inserted else 0.0,
        "rows_used": st["used"],
        "fragmentation": st["fragmentation"],
        "free_runs": st["free_runs"],
        "largest_free_run": st["largest_free_run"],
    }


if __name__ == "__main__":
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from prefix_gen import mixed_workload

    TOTAL_ROWS = 4096
    N = 6000
    workload = mixed_workload(N, seed=7)

    print("workload: %d prefixes (60%% IPv4 /8-/24, 40%% IPv6 /32-/128)" % N)
    print("row budget: %d, with 40%% churn (insert / delete / insert)\n" % TOTAL_ROWS)
    header = "%-8s %-11s %-9s %-10s %-9s %-8s" % (
        "cell_bits", "inserted", "avg_span", "bit_util", "blocked", "failed")
    print(header)
    print("-" * len(header))

    for cb in (32, 48, 64):
        r = simulate(workload, cb, TOTAL_ROWS)
        print("%-8d %-11d %-9.2f %-10.3f %-9d %-8d" % (
            r["cell_bits"], r["inserted"], r["avg_span"],
            r["avg_bit_util"], r["blocked"], r["failed"]))

    print("\nread it this way:")
    print("  avg_span  -> anchor rows per entry; larger means more arbitration load")
    print("  bit_util  -> useful bits / allocated bits; larger is cheaper per entry")
    print("  blocked   -> failed although enough free rows existed, just not contiguous")
    print("  no single width wins on all three; pick by which one binds first.")
