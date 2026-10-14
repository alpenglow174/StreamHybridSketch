"""Pairwise-mask secure summation (Bonawitz et al. 2017, without dropout recovery).

Each edge holds an X25519 key pair. Every pair (i, j) derives a shared secret,
expands it per round with HKDF into a PRG seed, and adds (+mask if i<j, -mask
if i>j) to its uint64 payload. Masks cancel in the modular sum, so the server
only learns sum_i payload_i (mod 2**64), which equals the true int64 sum.
"""
from __future__ import annotations

import numpy as np
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class SecAggParty:
    def __init__(self, party_id: int):
        self.id = int(party_id)
        self._sk = X25519PrivateKey.generate()
        self._shared: dict[int, bytes] = {}

    def public_key(self) -> bytes:
        return self._sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def set_peers(self, peers: dict[int, bytes]):
        self._shared = {
            int(j): self._sk.exchange(X25519PublicKey.from_public_bytes(pk))
            for j, pk in peers.items() if int(j) != self.id
        }

    def _mask(self, peer: int, round_id: int, n: int) -> np.ndarray:
        seed = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                    info=f"fedsketch-round-{round_id}".encode()).derive(self._shared[peer])
        rng = np.random.Generator(np.random.PCG64(int.from_bytes(seed, "little")))
        return rng.integers(0, np.iinfo(np.uint64).max, size=n, dtype=np.uint64, endpoint=True)

    def mask(self, payload: np.ndarray, round_id: int) -> np.ndarray:
        out = payload.astype(np.int64).view(np.uint64).copy()
        with np.errstate(over="ignore"):
            for j in sorted(self._shared):
                m = self._mask(j, round_id, out.size)
                out = out + m if self.id < j else out - m
        return out


def unmask_sum(masked: list[np.ndarray]) -> np.ndarray:
    """Server side: modular sum of the masked payloads, reinterpreted as int64."""
    with np.errstate(over="ignore"):
        tot = np.zeros_like(masked[0], dtype=np.uint64)
        for m in masked:
            tot = tot + m.astype(np.uint64)
    return tot.view(np.int64)
