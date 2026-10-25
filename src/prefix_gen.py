"""
Prefix generators for workload simulation and boundary scanning.

Two different jobs:
  * mixed_workload()  -> length distribution for capacity / width comparison
  * boundary_cases()  -> addresses on the length edges where encoding and
                         allocation errors show up
"""

import random

V4_LENGTHS = [8, 12, 16, 20, 24]
V6_LENGTHS = [32, 48, 64, 96, 122, 127, 128]

BOUNDARY_V4 = [0, 1, 8, 16, 24, 30, 31, 32]
BOUNDARY_V6 = [0, 1, 32, 48, 64, 96, 122, 127, 128]


def mixed_workload(n, seed=0, v4_ratio=0.6):
    """Prefix lengths for capacity simulation."""
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        if rnd.random() < v4_ratio:
            out.append(rnd.choice(V4_LENGTHS))
        else:
            out.append(rnd.choice(V6_LENGTHS))
    return out


def _mask(width, length):
    if length <= 0:
        return 0
    return ((1 << length) - 1) << (width - length)


def random_prefix(v6, length, rnd):
    """A random prefix of the given length, in its native width."""
    width = 128 if v6 else 32
    if length <= 0:
        return 0
    raw = rnd.getrandbits(width)
    return raw & _mask(width, length)


def boundary_cases(rnd=None):
    """(is_v6, length, ip_int, pattern) covering every edge length.

    For each length we emit four probes: the network address itself, one above,
    one below, and an address that flips a bit just outside the mask (must not
    match this prefix, so it reveals over-broad masks).
    """
    rnd = rnd or random.Random(1234)
    cases = []
    for is_v6, lengths in ((False, BOUNDARY_V4), (True, BOUNDARY_V6)):
        width = 128 if is_v6 else 32
        top = (1 << width) - 1
        for L in lengths:
            base = random_prefix(is_v6, L, rnd)
            probes = [
                (base, "BASE"),
                ((base + 1) & top, "PLUS1"),
                ((base - 1) & top if base > 0 else top, "MINUS1"),
                (base | (1 << (width - 1 - L)) if L < width else base, "OUTSIDE"),
            ]
            for p, tag in probes:
                cases.append((is_v6, L, p, tag))
    return cases


if __name__ == "__main__":
    w = mixed_workload(20, seed=1)
    print("sample workload lengths:", w)
    print("boundary cases:", len(boundary_cases()))
