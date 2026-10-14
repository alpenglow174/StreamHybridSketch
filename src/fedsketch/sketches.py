"""CountSketch family used by the federated protocols.

A sketch is a float/int array of shape (depth, width). Hash functions are
identified by two integer seeds:

* ``bucket_seed`` - shared by every round (HybridSketch / SharedSketch);
* ``sign_seed``   - fresh per round for HybridSketch, shared for SharedSketch.

All widths are powers of two; ``fold`` maps a width-W sketch onto width W'<W
exactly, so sketches of different widths remain linear-combinable.
"""
from __future__ import annotations

import numpy as np

from .hashing import bucket, derive_seed, sign


def row_seed(seed: int, row: int) -> int:
    return derive_seed(seed, row)


def cs_encode(keys, weights, depth: int, width: int, bucket_seed: int, sign_seed: int) -> np.ndarray:
    """CountSketch of the histogram {keys[i]: weights[i]} -> int64 (depth, width)."""
    keys = np.asarray(keys, dtype=np.int64)
    w = np.ones(len(keys), dtype=np.float64) if weights is None else np.asarray(weights, dtype=np.float64)
    out = np.zeros((depth, width), dtype=np.int64)
    if len(keys) == 0:
        return out
    for r in range(depth):
        b = bucket(keys, row_seed(bucket_seed, r), width)
        s = sign(keys, row_seed(sign_seed, r))
        out[r] = np.rint(np.bincount(b, weights=s * w, minlength=width)).astype(np.int64)
    return out


def cs_encode_float(keys, weights, depth: int, width: int, bucket_seed: int, sign_seed: int) -> np.ndarray:
    """Like cs_encode but keeps real-valued weights (used to subtract estimates)."""
    keys = np.asarray(keys, dtype=np.int64)
    out = np.zeros((depth, width))
    for r in range(depth):
        b = bucket(keys, row_seed(bucket_seed, r), width)
        s = sign(keys, row_seed(sign_seed, r))
        out[r] = np.bincount(b, weights=s * np.asarray(weights, dtype=np.float64), minlength=width)
    return out


def peel_rows(sketch: np.ndarray, keys, heavy, bucket_seed: int, sign_seed: int, rows_fn=None) -> np.ndarray:
    """Heavy-hitter peeling: estimate ``heavy`` items from the sketch, subtract
    their encoding, read ``keys`` from the residual and add back each key's own
    heavy estimate. Removes coherent collision error caused by heavy items
    (dominant for SharedSketch-style sums). Pure post-processing."""
    depth, width = sketch.shape
    heavy = np.asarray(heavy, dtype=np.int64)
    f_h = np.median(cs_rows(sketch, heavy, bucket_seed, sign_seed), axis=0)
    resid = sketch - cs_encode_float(heavy, f_h, depth, width, bucket_seed, sign_seed)
    rows = rows_fn(resid) if rows_fn is not None else cs_rows(resid, keys, bucket_seed, sign_seed)
    keys = np.asarray(keys, dtype=np.int64)
    order = np.argsort(heavy)
    pos = np.searchsorted(heavy[order], keys)
    hit = (pos < len(heavy)) & (heavy[order][np.minimum(pos, len(heavy) - 1)] == keys)
    rows[:, hit] += f_h[order][pos[hit]]
    return rows


def cs_rows(sketch: np.ndarray, keys, bucket_seed: int, sign_seed: int) -> np.ndarray:
    """Per-row signed bucket reads, shape (depth, len(keys))."""
    keys = np.asarray(keys, dtype=np.int64)
    depth, width = sketch.shape
    out = np.empty((depth, len(keys)), dtype=np.float64)
    for r in range(depth):
        b = bucket(keys, row_seed(bucket_seed, r), width)
        s = sign(keys, row_seed(sign_seed, r))
        out[r] = s * sketch[r, b]
    return out


def cs_query(sketch: np.ndarray, keys, bucket_seed: int, sign_seed: int) -> np.ndarray:
    return np.median(cs_rows(sketch, keys, bucket_seed, sign_seed), axis=0)


def fold(sketch: np.ndarray, width: int) -> np.ndarray:
    depth, w0 = sketch.shape
    if w0 == width:
        return sketch
    assert w0 % width == 0, (w0, width)
    return sketch.reshape(depth, w0 // width, width).sum(axis=1)


def f2_estimate(sketch: np.ndarray, noise_var: float = 0.0) -> float:
    """AMS estimate of ||f||_2^2 from a CountSketch (median over rows),
    debiased for i.i.d. additive noise of variance ``noise_var`` per cell."""
    depth, width = sketch.shape
    per_row = (sketch.astype(np.float64) ** 2).sum(axis=1) - width * noise_var
    return float(max(np.median(per_row), 0.0))


def next_pow2(x: float) -> int:
    return 1 << max(0, int(np.ceil(np.log2(max(x, 1.0)))))


def fit_power_law(top_freqs) -> tuple[float, float]:
    """Least-squares fit of f_i ~ beta * i^-alpha (log space) to the top
    estimated normalised frequencies (phase 1 of Wu et al. 2023)."""
    f = np.asarray(top_freqs, dtype=np.float64)
    f = f[f > 0]
    if len(f) < 3:
        return 1.0, max(float(f[0]) if len(f) else 1e-3, 1e-6)
    i = np.arange(1, len(f) + 1)
    A = np.vstack([np.ones(len(f)), -np.log(i)]).T
    (logb, alpha), *_ = np.linalg.lstsq(A, np.log(f), rcond=None)
    return float(max(alpha, 0.05)), float(np.exp(logb))


def instance_width(freqs, tau: float, C: float = 2.0) -> float:
    """Wu et al. Cor. 2.3: W = C * (#{f_i >= tau} + tau^-2 * sum_{f_i < tau} f_i^2)
    with normalised frequencies f (sum 1)."""
    f = np.asarray(freqs, dtype=np.float64)
    return C * (np.count_nonzero(f >= tau) + (f[f < tau] ** 2).sum() / tau ** 2)


def two_phase_width(top_freqs, tau: float, C: float = 2.0, max_items: int = 2 * 10**6) -> int:
    """Phase 2: plug the fitted power law (truncated where its mass reaches 1)
    into the instance-optimal width rule; returns a power of two."""
    alpha, beta = fit_power_law(top_freqs)
    i = np.arange(1, max_items + 1, dtype=np.float64)
    f = beta * i ** (-alpha)
    cut = np.searchsorted(np.cumsum(f), 1.0)
    f = f[:max(cut, 1)]
    return next_pow2(instance_width(f, tau, C))


# --------------------------------------------------------------------------
# Multi-round estimators from Wu et al. (2023), Section 3
# --------------------------------------------------------------------------

class MultiRoundCS:
    """Holds one released sketch per round and answers frequency queries with
    the Fresh / Shared / Hybrid estimators.

    fresh : independent bucket+sign hashes each round; estimate = sum_m median_l
    shared: same hashes; estimate = median_l sum_m  (== one sketch of the union)
    hybrid: shared bucket hash, fresh sign hash; estimate = median_l sum_m
    """

    def __init__(self, kind: str, depth: int, seed: int):
        assert kind in ("fresh", "shared", "hybrid")
        self.kind, self.depth, self.seed = kind, depth, seed
        self.rounds: list[tuple[np.ndarray, int, int]] = []

    def seeds(self, m: int) -> tuple[int, int]:
        b = derive_seed(self.seed, "bucket", m if self.kind == "fresh" else 0)
        s = derive_seed(self.seed, "sign", 0 if self.kind == "shared" else m)
        return b, s

    def encode_round(self, m: int, keys, weights, width: int) -> np.ndarray:
        b, s = self.seeds(m)
        return cs_encode(keys, weights, self.depth, width, b, s)

    def add(self, m: int, sketch: np.ndarray):
        b, s = self.seeds(m)
        self.rounds.append((sketch, b, s))

    def query(self, keys, rounds=None) -> np.ndarray:
        sel = self.rounds if rounds is None else [self.rounds[i] for i in rounds]
        keys = np.asarray(keys, dtype=np.int64)
        if self.kind == "fresh":
            return sum(np.median(cs_rows(S, keys, b, s), axis=0) for S, b, s in sel)
        acc = np.zeros((self.depth, len(keys)))
        for S, b, s in sel:
            acc += cs_rows(S, keys, b, s)
        return np.median(acc, axis=0)
