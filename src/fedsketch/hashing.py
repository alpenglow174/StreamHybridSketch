"""Deterministic, vectorised 64-bit hashing (splitmix64 finaliser).

Every party (edges, server, oracle, offline harness) derives the same hash
functions from a shared integer seed, which is how the server "broadcasts"
the sketch hashes in Algorithm 1 of Wu et al. (2023).
"""
import numpy as np

_G = np.uint64(0x9E3779B97F4A7C15)
_M1 = np.uint64(0xBF58476D1CE4E5B9)
_M2 = np.uint64(0x94D049BB133111EB)
_MASK64 = (1 << 64) - 1


def mix64(x, seed: int) -> np.ndarray:
    """splitmix64(x + seed*golden). Accepts scalars or arrays of non-negative ints."""
    with np.errstate(over="ignore"):
        z = np.asarray(x).astype(np.uint64) + np.uint64(seed & _MASK64) * _G + _G
        z = (z ^ (z >> np.uint64(30))) * _M1
        z = (z ^ (z >> np.uint64(27))) * _M2
        return z ^ (z >> np.uint64(31))


def derive_seed(*parts) -> int:
    """Combine a tuple of ints / strings into one 64-bit seed."""
    h = 0x12345678
    for p in parts:
        if isinstance(p, str):
            p = int.from_bytes(p.encode()[:8].ljust(8, b"\0"), "little") ^ len(p)
        h = int(mix64(np.uint64((h ^ int(p)) & _MASK64), 0x5EED))
    return h


def bucket(keys, seed: int, width: int) -> np.ndarray:
    """Bucket index in [0, width). width must be a power of two so that a
    width-W sketch folds exactly onto width W/2 (low bits are preserved)."""
    return (mix64(keys, seed) & np.uint64(width - 1)).astype(np.int64)


def sign(keys, seed: int) -> np.ndarray:
    """Rademacher sign in {-1, +1} as int8."""
    return ((mix64(keys, seed) >> np.uint64(63)).astype(np.int8) * 2 - 1).astype(np.int8)


def uniform01(keys, seed: int) -> np.ndarray:
    """Uniform [0,1) deterministic in (key, seed)."""
    return (mix64(keys, seed) >> np.uint64(11)).astype(np.float64) / float(1 << 53)
