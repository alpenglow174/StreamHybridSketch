"""Federated streaming sketch protocol: server-side planning/decoding and
edge-side payload construction. Shared verbatim by the live Flower/Kafka
pipeline and the offline experiment harness.

Variants
--------
* Baseline (Wu et al. 2023, DP-HybridSketch applied per window):
  mode="per_round", fixed width, shared bucket hash + fresh per-round sign
  hash, each round's aggregate released once with Gaussian noise.
* StreamHybridSketch (ours) adds, each switchable for ablations:
  - mode="tree"        binary-tree continual release (Chan et al. 2011) of the
                       window sketches, so cumulative queries combine
                       O(log t) noisy nodes instead of t noisy rounds;
  - adaptive_width     per-round width from the tail estimate of the previous
                       release (two-phase rule of Wu et al., Cor. 2.3);
  - drift              top-k Jaccard drift detector that restarts the sliding
                       window and the width search;
  - iblt               IBLT + threshold sampling (Gascon et al. 2023) to list
                       heavy-hitter identities without a candidate dictionary.

Payload layout (int64, one per edge per round, summed by SecAgg):
  [count scalar | node sketches (fixed point) ... | IBLT (4 x cells, exact)]
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

import numpy as np

from . import dp
from .hashing import derive_seed
from .iblt import FIELDS, iblt_decode, iblt_encode, threshold_sample
from .hashing import bucket, sign
from .sketches import cs_encode, cs_rows, peel_rows, row_seed, two_phase_width

FP = dp.FIXED_POINT


@dataclass
class VariantConfig:
    name: str
    mode: str = "per_round"          # per_round | tree
    depth: int = 5
    width: int = 1024                # fixed width / initial width
    adaptive_width: bool = False
    w_min: int = 64
    w_max: int = 8192
    rel_target: float = 0.001        # adaptive width: target error / users-per-round
    eps: float | None = None         # per-unit privacy budget over the whole stream
    delta: float = 1e-6
    horizon: int = 512               # rounds the tree must support (sets its height)
    window: int = 7                  # sliding window length (rounds)
    drift: bool = False
    drift_ratio: float = 0.5
    iblt: bool = False
    iblt_cells: int = 3 * 1024
    iblt_load: float = 0.5
    iblt_thr0: int = 16              # first-round threshold (no feedback yet)
    peel: bool = False               # heavy-hitter peeling estimator (tree mode)
    peel_top: int = 200
    seed: int = 2024

    # ---------- derived privacy parameters
    @property
    def max_level(self) -> int:
        return int(np.ceil(np.log2(self.horizon))) if self.mode == "tree" else 0

    @property
    def releases_per_unit(self) -> int:
        return self.max_level + 1

    @property
    def noise_multiplier(self) -> float:
        """Gaussian noise multiplier z such that the (releases_per_unit) sketch
        releases plus the one per-round user-count release compose to eps."""
        if self.eps is None:
            return 0.0
        return dp.calibrate_noise_multiplier(self.eps, self.releases_per_unit + 1, self.delta)

    @property
    def sigma(self) -> float:
        """Noise std per sketch cell (in counts); L2 sensitivity = sqrt(depth)."""
        return self.noise_multiplier * np.sqrt(self.depth)

    @property
    def count_sigma(self) -> float:
        return self.noise_multiplier                     # sensitivity 1

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_dict(d) -> "VariantConfig":
        return VariantConfig(**d)


def bucket_seed(cfg: VariantConfig) -> int:
    return derive_seed(cfg.seed, cfg.name, "bucket")


def sign_seed(cfg: VariantConfig, t: int) -> int:
    # per_round = HybridSketch (fresh signs); tree nodes must share signs
    return derive_seed(cfg.seed, cfg.name, "sign", t if cfg.mode == "per_round" else 0)


def payload_size(plan: dict, depth: int) -> int:
    n = 1 + sum(depth * nd["width"] for nd in plan["nodes"])
    if plan.get("iblt"):
        n += FIELDS * plan["iblt"]["cells"]
    return n


# =============================================================== edge side

class EdgeLogic:
    """State an edge keeps for one variant: per-round local histograms of
    (user, key) pairs so that it can re-encode higher tree nodes."""

    def __init__(self, cfg: VariantConfig, edge_id: int, n_edges: int):
        self.cfg, self.edge, self.n_edges = cfg, edge_id, n_edges
        self.hist: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    def payload(self, plan: dict, users: np.ndarray, keys: np.ndarray) -> np.ndarray:
        cfg, t = self.cfg, plan["t"]
        self.hist[t] = (np.asarray(users, np.int64), np.asarray(keys, np.int64))
        keep_from = t - (1 << cfg.max_level) + 1
        for old in [r for r in self.hist if r < keep_from]:
            del self.hist[old]

        parts = []
        cnt = np.array([len(keys) * FP], dtype=np.int64)
        cnt += dp.noise_share(1, plan["count_sigma"], self.n_edges,
                              dp.node_noise_seed(cfg.seed, cfg.name + "/count", (0, t), self.edge))
        parts.append(cnt)
        for nd in plan["nodes"]:
            a, b = dp.node_range(tuple(nd["node"]))
            ks = np.concatenate([self.hist[r][1] for r in range(a, b + 1) if r in self.hist] or [np.zeros(0, np.int64)])
            S = cs_encode(ks, None, cfg.depth, nd["width"], nd["bucket_seed"], nd["sign_seed"]) * FP
            S += dp.noise_share(S.shape, nd["sigma"], self.n_edges,
                                dp.node_noise_seed(cfg.seed, cfg.name, tuple(nd["node"]), self.edge))
            parts.append(S.ravel())
        if plan.get("iblt"):
            ib = plan["iblt"]
            uk, cnts = keys, np.ones(len(keys))          # one item per user-window
            sk, sv = threshold_sample(uk, cnts, ib["thr"], ib["seed"], salt=users)
            parts.append(iblt_encode(sk, sv, ib["cells"], ib["seed"]).ravel())
        return np.concatenate(parts).astype(np.int64)


# ============================================================= server side

@dataclass
class RoundInfo:
    t: int
    width: int
    n_hat: float
    payload_len: int
    drift: bool = False
    iblt_decoded: int = 0
    iblt_complete: bool = True
    iblt_thr: int = 1


class VariantServer:
    def __init__(self, cfg: VariantConfig, n_edges: int, dictionary: np.ndarray | None = None):
        self.cfg, self.n_edges = cfg, n_edges
        self.dictionary = None if dictionary is None else np.asarray(dictionary, np.int64)
        self.sigma = cfg.sigma
        self.count_sigma = cfg.count_sigma
        self.bseed = bucket_seed(cfg)
        self.widths: dict[int, int] = {}
        self.rounds: dict[int, tuple[np.ndarray, int]] = {}   # per_round: t -> (sketch, sign_seed)
        self.nodes: dict[tuple, np.ndarray] = {}              # tree: node -> sketch
        self.info: list[RoundInfo] = []
        self.next_width = cfg.width
        self._width_hist: list[int] = []
        self.window_start = 0
        self.jaccard_hist: list[float] = []
        self.decoded: dict[int, set] = {}                     # t -> keys listed by IBLT
        self.next_thr = cfg.iblt_thr0
        # running cumulative per-row reads for the dictionary (per_round mode)
        self._cum_rows = None if self.dictionary is None else np.zeros((cfg.depth, len(self.dictionary)))
        self._bcache: dict[int, np.ndarray] = {}     # width -> dictionary buckets (depth, N)
        self._scache: dict[int, np.ndarray] = {}     # sign seed -> dictionary signs (depth, N)
        self._memo: dict[tuple, np.ndarray] = {}     # (kind, t) -> dictionary estimate

    def _rows(self, S: np.ndarray, keys, ss: int) -> np.ndarray:
        """cs_rows with hash caching when querying the (large) dictionary."""
        if self.dictionary is None or keys is not self.dictionary:
            return cs_rows(S, keys, self.bseed, ss)
        depth, width = S.shape
        if width not in self._bcache:
            self._bcache[width] = np.stack([bucket(keys, row_seed(self.bseed, r), width) for r in range(depth)])
        if ss not in self._scache:
            if len(self._scache) > 2 * self.cfg.window + 4:
                self._scache.pop(next(iter(self._scache)))
            self._scache[ss] = np.stack([sign(keys, row_seed(ss, r)) for r in range(depth)])
        b, s = self._bcache[width], self._scache[ss]
        return s * np.take_along_axis(S, b, axis=1)

    # ------------------------------------------------------------ planning
    def plan(self, t: int) -> dict:
        cfg = self.cfg
        W = int(np.clip(self.next_width, cfg.w_min, cfg.w_max)) if cfg.adaptive_width else cfg.width
        self.widths[t] = W
        if cfg.mode == "per_round":
            nodes = [(0, t)]
        else:
            nodes = dp.completed_nodes(t, cfg.max_level)
        plan_nodes = []
        for nd in nodes:
            a, b = dp.node_range(nd)
            plan_nodes.append({"node": list(nd),
                               "width": max(self.widths.get(r, W) for r in range(a, b + 1)),
                               "bucket_seed": self.bseed,
                               "sign_seed": sign_seed(cfg, t),
                               "sigma": self.sigma})
        plan = {"variant": cfg.name, "t": t, "nodes": plan_nodes, "count_sigma": self.count_sigma}
        if cfg.iblt:
            plan["iblt"] = {"cells": cfg.iblt_cells, "thr": int(self.next_thr),
                            "seed": derive_seed(cfg.seed, cfg.name, "iblt", t)}
        return plan

    # ------------------------------------------------------------- ingest
    def ingest(self, plan: dict, agg: np.ndarray):
        cfg, t = self.cfg, plan["t"]
        off = 1
        n_hat = max(float(agg[0]) / FP, 1.0)
        for nd in plan["nodes"]:
            size = cfg.depth * nd["width"]
            S = agg[off:off + size].reshape(cfg.depth, nd["width"]).astype(np.float64) / FP
            off += size
            node = tuple(nd["node"])
            if cfg.mode == "per_round":
                self.rounds[t] = (S, nd["sign_seed"])
                if self._cum_rows is not None:
                    self._cum_rows += self._rows(S, self.dictionary, nd["sign_seed"])
            else:
                self.nodes[node] = S
        info = RoundInfo(t=t, width=self.widths[t], n_hat=n_hat, payload_len=len(agg), iblt_thr=self.next_thr)
        if plan.get("iblt"):
            ib = plan["iblt"]
            T = agg[off:off + FIELDS * ib["cells"]].reshape(FIELDS, ib["cells"])
            listed, complete = iblt_decode(T, ib["seed"])
            self.decoded[t] = listed
            info.iblt_decoded, info.iblt_complete = len(listed), complete
        self.info.append(info)
        self._drift_and_width(t, n_hat, info)

    def _round_rows(self, t: int, keys) -> np.ndarray:
        if self.cfg.mode == "per_round":
            S, ss = self.rounds[t]
            return self._rows(S, keys, ss)
        return self._rows(self.nodes[(0, t)], keys, sign_seed(self.cfg, 0))

    def _round_sketch(self, t: int) -> np.ndarray:
        return self.rounds[t][0] if self.cfg.mode == "per_round" else self.nodes[(0, t)]

    def _drift_and_width(self, t: int, n_hat: float, info: RoundInfo):
        cfg = self.cfg
        cand = self.candidates(t, "win")
        if cand is None or len(cand) == 0:
            return
        est_now = np.median(self._round_rows(t, cand), axis=0)
        score_now = self._iblt_sum(cand, t, t) if cfg.iblt else est_now
        k = min(50, len(cand))
        top_now = set(cand[np.argpartition(-score_now, k - 1)[:k]])

        # ---- drift detection: top-k(round t) vs top-k(window up to t-1)
        if cfg.drift and t - self.window_start >= 2:
            a, _ = self.window_range(t - 1, "win")
            est_prev = self._iblt_sum(cand, a, t - 1) if cfg.iblt else self.estimate(cand, "win", t - 1)
            top_prev = set(cand[np.argpartition(-est_prev, k - 1)[:k]])
            J = len(top_now & top_prev) / k
            hist = self.jaccard_hist[-10:]
            if len(hist) >= 3 and J < cfg.drift_ratio * np.mean(hist):
                info.drift = True
                self.window_start = t
                self.jaccard_hist = []
            else:
                self.jaccard_hist.append(J)

        # ---- adaptive width from this round's tail (Wu et al. Cor. 2.3, C=2)
        if cfg.adaptive_width:
            if info.drift:
                self.next_width = cfg.w_max
                self._width_hist = []
            else:
                # (Wu et al. phase 1: power law fitted to the top-20 estimates
                # of the previous release, which is robust to per-cell noise)
                tau = max(cfg.rel_target, 1.0 / n_hat)
                top20 = np.sort(est_now)[::-1][:20] / n_hat
                self._width_hist.append(two_phase_width(top20, tau))
                self.next_width = max(self._width_hist[-3:])   # smooth the jumpy power-law fit

        # ---- IBLT threshold: feedback on the table's own outcome. Distinct
        # survivors of threshold sampling scale ~ 1/thr in the (dominant) tail,
        # so rescale thr toward the target load; quadruple it after a failed peel.
        if cfg.iblt:
            budget = cfg.iblt_load * cfg.iblt_cells
            thr = info.iblt_thr
            if not info.iblt_complete:
                thr *= 4
            else:
                thr = max(thr / 2, thr * info.iblt_decoded / budget)
            self.next_thr = int(max(1, np.ceil(thr)))

    # ------------------------------------------------------------- queries
    def window_range(self, t: int, kind: str) -> tuple[int, int]:
        if kind == "cum":
            return 0, t
        a = max(0, t - self.cfg.window + 1)
        if self.cfg.drift:
            a = max(a, self.window_start)
        return a, t

    def candidates(self, t: int, kind: str) -> np.ndarray:
        if not self.cfg.iblt:
            return self.dictionary
        a, b = self.window_range(t, kind)
        s = set()
        for r in range(a, b + 1):
            s.update(self.decoded.get(r, {}))
        return np.fromiter(s, np.int64, len(s))

    def estimate(self, keys, kind: str, t: int) -> np.ndarray:
        keys = np.asarray(keys, np.int64)
        memo = keys is self.dictionary and self.dictionary is not None
        mkey = (kind, t, self.window_start)
        if memo and mkey in self._memo:
            return self._memo[mkey]
        est = self._estimate(keys, kind, t)
        if memo:
            if len(self._memo) > 8:
                self._memo.clear()
            self._memo[mkey] = est
        return est

    def _estimate(self, keys, kind: str, t: int) -> np.ndarray:
        a, b = self.window_range(t, kind)
        cfg = self.cfg
        if cfg.mode == "per_round":
            if kind == "cum" and self._cum_rows is not None and self.dictionary is not None \
                    and keys is self.dictionary and b == max(self.rounds):
                return np.median(self._cum_rows, axis=0)
            acc = np.zeros((cfg.depth, len(keys)))
            for r in range(a, b + 1):
                S, ss = self.rounds[r]
                acc += self._rows(S, keys, ss)
            return np.median(acc, axis=0)
        acc = np.zeros((cfg.depth, len(keys)))
        ss = sign_seed(cfg, 0)
        cover = dp.dyadic_cover(a, b, cfg.max_level)
        for nd in cover:
            acc += self._rows(self.nodes[nd], keys, ss)
        if not cfg.peel or len(keys) <= cfg.peel_top:
            return np.median(acc, axis=0)
        # heavy-hitter peeling per node (tree nodes share signs, so heavy
        # items collide coherently across rounds, like SharedSketch)
        est0 = np.median(acc, axis=0)
        heavy = keys[np.argpartition(-est0, cfg.peel_top - 1)[:cfg.peel_top]]
        acc = np.zeros((cfg.depth, len(keys)))
        for nd in cover:
            acc += peel_rows(self.nodes[nd], keys, heavy, self.bseed, ss,
                             rows_fn=lambda R: self._rows(R, keys, ss))
        return np.median(acc, axis=0)

    def _iblt_sum(self, keys, a: int, b: int) -> np.ndarray:
        """Unbiased threshold-sampled counts decoded from the IBLTs of rounds
        [a, b] (the frequency estimator of Gascon et al. 2023, Alg. 2)."""
        out = np.zeros(len(keys))
        pos = {int(x): i for i, x in enumerate(keys)}
        for r in range(a, b + 1):
            for key, v in self.decoded.get(r, {}).items():
                i = pos.get(key)
                if i is not None:
                    out[i] += v
        return out

    def topk(self, k: int, kind: str, t: int) -> tuple[np.ndarray, np.ndarray]:
        cand = self.candidates(t, kind)
        if cand is None or len(cand) == 0:
            return np.zeros(0, np.int64), np.zeros(0)
        if self.cfg.iblt:
            est = self._iblt_sum(cand, *self.window_range(t, kind))
        else:
            est = self.estimate(cand, kind, t)
        k = min(k, len(cand))
        idx = np.argpartition(-est, k - 1)[:k]
        idx = idx[np.argsort(-est[idx])]
        return cand[idx], est[idx]

    def epsilon_spent(self, t: int) -> float:
        """Per-unit epsilon actually spent by round t (sketch + count releases)."""
        if self.cfg.eps is None:
            return float("inf")
        z = round(self.sigma / np.sqrt(self.cfg.depth), 6)
        if self.cfg.mode == "per_round":
            return dp.epsilon(z, 2, self.cfg.delta)
        # the unit of round 0 has been in nodes (h, 0) with 2^h <= t+1 so far
        comps = min(int(np.floor(np.log2(t + 1))) + 1, self.cfg.releases_per_unit)
        return dp.epsilon(z, comps + 1, self.cfg.delta)
