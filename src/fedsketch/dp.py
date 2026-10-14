"""Differential privacy: distributed Gaussian noise shares, the binary-tree
continual-release mechanism, and (epsilon, delta) accounting via Google's
dp-accounting RDP accountant.

Privacy unit: the contribution of one user to one window (one sampled item),
i.e. event-level DP under continual observation (Dwork et al. 2010; Chan et
al. 2011). For a CountSketch of depth d the L2 sensitivity is exactly sqrt(d).
"""
from __future__ import annotations

import functools

import numpy as np
from dp_accounting import dp_event, rdp

from .hashing import derive_seed

FIXED_POINT = 1 << 8   # payloads are int64 fixed-point with 8 fractional bits


def noise_share(shape, sigma: float, n_parties: int, seed: int) -> np.ndarray:
    """One party's share of N(0, sigma^2): N(0, sigma^2 / n_parties), fixed-point.
    (Seeded for reproducibility; a deployment would draw from OS entropy.)"""
    if sigma <= 0:
        return np.zeros(shape, dtype=np.int64)
    rng = np.random.default_rng(seed & ((1 << 63) - 1))
    z = rng.normal(0.0, sigma / np.sqrt(n_parties), size=shape)
    return np.rint(z * FIXED_POINT).astype(np.int64)


def node_noise_seed(base_seed: int, variant: str, node: tuple, edge: int) -> int:
    return derive_seed(base_seed, "noise", variant, node[0], node[1], edge)


# ---------------------------------------------------------------- accounting

@functools.lru_cache(maxsize=4096)
def epsilon(noise_multiplier: float, compositions: int, delta: float) -> float:
    if noise_multiplier <= 0:
        return float("inf")
    if compositions <= 0:
        return 0.0
    acc = rdp.RdpAccountant()
    acc.compose(dp_event.GaussianDpEvent(noise_multiplier), compositions)
    return float(acc.get_epsilon(delta))


def calibrate_noise_multiplier(target_eps: float, compositions: int, delta: float) -> float:
    lo, hi = 0.05, 2000.0
    for _ in range(60):
        mid = np.sqrt(lo * hi)
        if epsilon(round(mid, 6), compositions, delta) > target_eps:
            lo = mid
        else:
            hi = mid
    return float(hi)


# ---------------------------------------------------------- tree mechanism

def completed_nodes(t: int, max_level: int) -> list[tuple[int, int]]:
    """Dyadic nodes (level, index) whose last round is t; node (h, j) covers
    rounds [j*2^h, (j+1)*2^h - 1]."""
    out = []
    for h in range(max_level + 1):
        if (t + 1) % (1 << h) == 0:
            out.append((h, (t + 1) // (1 << h) - 1))
    return out


def node_range(node: tuple[int, int]) -> tuple[int, int]:
    h, j = node
    return j << h, ((j + 1) << h) - 1


def dyadic_cover(a: int, b: int, max_level: int) -> list[tuple[int, int]]:
    """Minimal set of tree nodes (level <= max_level) exactly covering [a, b]."""
    out, s = [], a
    while s <= b:
        h = 0
        while h < max_level and s % (1 << (h + 1)) == 0 and s + (1 << (h + 1)) - 1 <= b:
            h += 1
        out.append((h, s >> h))
        s += 1 << h
    return out
