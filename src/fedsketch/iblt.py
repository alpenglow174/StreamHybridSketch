"""Additive Invertible Bloom Lookup Table + threshold sampling
(Goodrich & Mitzenmacher 2011; Gascon et al. 2023, Algorithms 1-2).

Cells hold four int64 fields that are *sums*, so tables built by different
edges add up under secure aggregation:

    cnt  - number of insertions (users) that hit the cell
    ksum - sum of keys            (cnt * key in a pure cell)
    vsum - sum of values          (sampled counts)
    csum - sum of checksum(key)   (cnt * chk(key) in a pure cell)

Keys must be positive and < 2**40 so that sums never overflow int64.
"""
from __future__ import annotations

import numpy as np

from .hashing import bucket, derive_seed, mix64, uniform01

FIELDS = 4
KEY_LIMIT = 1 << 40


def _chk(keys, seed):
    return (mix64(keys, derive_seed(seed, "chk")) & np.uint64((1 << 20) - 1)).astype(np.int64)


def _positions(keys, seed, cells, k):
    sub = cells // k
    return [j * sub + bucket(keys, derive_seed(seed, "iblt", j), sub) for j in range(k)]


def iblt_encode(keys, values, cells: int, seed: int, k: int = 3) -> np.ndarray:
    """keys: int64 (one entry per insertion), values: int64. -> (4, cells) int64."""
    assert cells % k == 0 and (cells // k) & (cells // k - 1) == 0, "cells/k must be a power of 2"
    keys = np.asarray(keys, dtype=np.int64)
    vals = np.asarray(values, dtype=np.int64)
    T = np.zeros((FIELDS, cells), dtype=np.int64)
    if len(keys) == 0:
        return T
    chk = _chk(keys, seed)
    for pos in _positions(keys, seed, cells, k):
        np.add.at(T[0], pos, 1)
        np.add.at(T[1], pos, keys)
        np.add.at(T[2], pos, vals)
        np.add.at(T[3], pos, chk)
    return T


def iblt_decode(T: np.ndarray, seed: int, k: int = 3) -> tuple[dict, bool]:
    """Peel the table. Returns ({key: value_sum}, complete_flag)."""
    T = T.copy()
    cells = T.shape[1]
    sub = cells // k
    out: dict[int, int] = {}

    def pure(i):
        c = int(T[0, i])
        if c <= 0:
            return None
        ks = int(T[1, i])
        if ks % c:
            return None
        key = ks // c
        if not (0 < key < KEY_LIMIT):
            return None
        if int(T[3, i]) != c * int(_chk(np.array([key]), seed)[0]):
            return None
        pos = [int(p[0]) for p in _positions(np.array([key]), seed, cells, k)]
        return (key, pos) if i in pos else None

    stack = list(range(cells))
    while stack:
        i = stack.pop()
        p = pure(i)
        if p is None:
            continue
        key, pos = p
        c, v = int(T[0, i]), int(T[2, i])
        out[key] = out.get(key, 0) + v
        chk = c * int(_chk(np.array([key]), seed)[0])
        for q in pos:
            T[0, q] -= c
            T[1, q] -= c * key
            T[2, q] -= v
            T[3, q] -= chk
            stack.append(q)
    complete = not T.any()
    return out, complete


def threshold_sample(keys, counts, t: float, seed: int, salt) -> tuple[np.ndarray, np.ndarray]:
    """Algorithm 1 of Gascon et al.: keep h(x)>=t as is, otherwise report t with
    probability h(x)/t. ``salt`` (e.g. user ids) makes the coin per-user."""
    keys = np.asarray(keys, dtype=np.int64)
    counts = np.asarray(counts, dtype=np.float64)
    if t <= 1:
        return keys, counts.astype(np.int64)
    u = uniform01(np.asarray(salt, dtype=np.int64) ^ keys, derive_seed(seed, "ts"))
    big = counts >= t
    keep = big | (u < counts / t)
    vals = np.where(big, counts, t)
    return keys[keep], np.rint(vals[keep]).astype(np.int64)
