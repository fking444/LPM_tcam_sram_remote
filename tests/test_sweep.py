#!/usr/bin/env python3
"""
Address sweep -- differential test.

The expected value never comes from the implementation under test. A naive
reference matcher walks every prefix and returns the longest hit; the hardware
model goes through row allocation, anchor/continuation binding and SRAM offsets.
If the two disagree, the model is wrong.

Run:
    python tests/test_sweep.py
    python tests/test_sweep.py --len 127
    python tests/test_sweep.py --pattern OUTSIDE
"""

import argparse
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from lpm_table import LpmTable, v4_to_key, make_mask          # noqa: E402
from prefix_gen import boundary_cases, BOUNDARY_V4, BOUNDARY_V6, random_prefix  # noqa: E402


# ---------------------------------------------------------------- reference

def ref_lookup(prefixes, addr):
    """Naive longest prefix match: O(n), obviously correct, used as oracle."""
    best = None
    for p in prefixes:
        if (addr & p["mask"]) == p["key"]:
            if best is None or p["len"] > best["len"]:
                best = p
    return best


# ---------------------------------------------------------------- fixture

def build(count_per_len=4, cell_bits=48, capacity=16384):
    rnd = random.Random(42)
    table = LpmTable(cell_bits=cell_bits, capacity_rows=capacity)
    prefixes = []
    seen = set()

    for is_v6, lengths in ((False, BOUNDARY_V4), (True, BOUNDARY_V6)):
        for L in lengths:
            # /0 can only exist once in the unified key space
            target = 1 if L == 0 else count_per_len
            made = 0
            attempts = 0
            while made < target and attempts < 500:
                attempts += 1
                ip = random_prefix(is_v6, L, rnd)
                key = ip if is_v6 else v4_to_key(ip)
                mask = make_mask(L)
                masked = key & mask
                if (L, masked) in seen:
                    continue
                seen.add((L, masked))
                payload = "%s-%d-%d" % ("v6" if is_v6 else "v4", L, made)
                table.insert(key, L, payload)
                prefixes.append(
                    {"key": masked, "mask": mask, "len": L, "payload": payload})
                made += 1
    return table, prefixes


# ---------------------------------------------------------------- sweep

def sweep(table, prefixes, only_len=None, only_pattern=None, verbose=True):
    total = 0
    fails = []
    per_len = {}

    for is_v6, L, ip, pattern in boundary_cases():
        if only_len is not None and L != only_len:
            continue
        if only_pattern and pattern != only_pattern:
            continue

        addr = ip if is_v6 else v4_to_key(ip)
        total += 1
        key = (L, is_v6)
        stat = per_len.setdefault(key, [0, 0])
        stat[0] += 1

        exp = ref_lookup(prefixes, addr)
        exp_len = exp["len"] if exp else None
        exp_pay = exp["payload"] if exp else None

        try:
            act = table.lookup(addr)
        except RuntimeError as e:
            stat[1] += 1
            fails.append({
                "len": L, "v6": is_v6, "pattern": pattern, "addr": addr,
                "exp_len": exp_len, "exp_pay": exp_pay,
                "act_len": "EXC", "act_pay": str(e),
                "hint": "anchor/continuation rows inconsistent",
            })
            continue

        if act is None:
            act_len, act_pay = None, None
        else:
            act_len, act_pay, _ = act

        if exp_len != act_len or exp_pay != act_pay:
            stat[1] += 1
            hint = ""
            if exp_len is not None and act_len is not None and exp_len != act_len:
                hint = "length mismatch: anchor span or mask likely wrong"
            elif exp_len == act_len:
                hint = "right prefix length, wrong payload: SRAM offset mismatch"
            elif act_len is None:
                hint = "no hit at all: mask too narrow or entry lost"
            fails.append({
                "len": L, "v6": is_v6, "pattern": pattern, "addr": addr,
                "exp_len": exp_len, "exp_pay": exp_pay,
                "act_len": act_len, "act_pay": act_pay,
                "hint": hint,
            })

    if verbose:
        print("  %-6s %-4s %-8s %s" % ("len", "fam", "cases", "result"))
        for (L, is_v6) in sorted(per_len):
            tot, bad = per_len[(L, is_v6)]
            print("  %-6d %-4s %-8d %s" % (
                L, "v6" if is_v6 else "v4", tot, "ok" if bad == 0 else "FAIL %d" % bad))

    return total, fails


def show_failure(f):
    print("")
    print("  FAIL len=%s pattern=%s addr=0x%032X" % (f["len"], f["pattern"], f["addr"]))
    print("       expect: key_len=%s next=%s" % (f["exp_len"], f["exp_pay"]))
    print("       actual: key_len=%s next=%s" % (f["act_len"], f["act_pay"]))
    print("       hint:   %s" % f["hint"])


# ---------------------------------------------------------------- checks

def check_accounting(table):
    st = table.stats()
    rows = [r for _, r in table.read_back()]
    anchors = [r for r in rows if r.vld]
    spans = sum(r.span for r in anchors)
    ok = (spans == len(rows))
    print("  entries=%d rows_used=%d sum(span)=%d avg_span=%.2f  %s" % (
        st["entries"], st["rows_used"], spans, st["avg_span"],
        "ok" if ok else "MISMATCH"))
    return ok


def check_delete(table, prefixes):
    """Delete half the prefixes, then re-sweep: no stale hit may survive."""
    rnd = random.Random(7)
    dropped = []
    for p in prefixes:
        if rnd.random() < 0.5 and table.delete(p["key"], p["len"]):
            dropped.append(p)
    survivors = [p for p in prefixes if p not in dropped]
    print("  deleted=%d survivors=%d" % (len(dropped), len(survivors)))

    total, fails = sweep(table, survivors, verbose=False)
    print("  re-sweep %d cases, %d failed  %s" % (
        total, len(fails), "ok" if not fails else "FAIL"))
    for f in fails[:3]:
        show_failure(f)
    return len(fails) == 0


def check_overflow(cell_bits=48):
    """A full table must raise, never silently drop."""
    table = LpmTable(cell_bits=cell_bits, capacity_rows=512)
    rnd = random.Random(3)
    inserted = 0
    try:
        for i in range(2000):
            L = rnd.choice([24, 64, 128])
            is_v6 = L > 32
            ip = random_prefix(is_v6, L, rnd)
            key = ip if is_v6 else v4_to_key(ip)
            table.insert(key, L, "p%d" % i)
            inserted += 1
        print("  inserted=%d without overflow  FAIL (capacity not enforced)" % inserted)
        return False
    except MemoryError:
        print("  inserted=%d then MemoryError  ok" % inserted)
        return True


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="differential address sweep")
    ap.add_argument("--len", type=int, default=None, help="only this prefix length")
    ap.add_argument("--pattern", default=None,
                    choices=["BASE", "PLUS1", "MINUS1", "OUTSIDE"],
                    help="only this probe pattern")
    args = ap.parse_args()

    table, prefixes = build()
    print("=== L1 encoding ===")
    print("  inserted %d prefixes, cell_bits=%d" % (len(prefixes), table.cell_bits))
    ok_accounting = check_accounting(table)

    print("\n=== L2 lookup sweep ===")
    total, fails = sweep(table, prefixes, args.len, args.pattern)
    print("\n  TOTAL %d cases, %d failed" % (total, len(fails)))
    for f in fails[:5]:
        show_failure(f)

    ok_sweep = not fails
    ok_delete = True
    ok_overflow = True
    if args.len is None and args.pattern is None:
        print("\n=== delete then re-sweep ===")
        ok_delete = check_delete(table, prefixes)
        print("\n=== overflow ===")
        ok_overflow = check_overflow()

    print("\n=== summary ===")
    for name, ok in (("encoding/accounting", ok_accounting),
                     ("lookup sweep", ok_sweep),
                     ("delete", ok_delete),
                     ("overflow", ok_overflow)):
        print("  %-22s %s" % (name, "PASS" if ok else "FAIL"))

    return 0 if all([ok_accounting, ok_sweep, ok_delete, ok_overflow]) else 1


if __name__ == "__main__":
    sys.exit(main())
