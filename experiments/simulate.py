"""Offline harness: replays a prepared stream round by round through the exact
same EdgeLogic / VariantServer code as the live Flower pipeline (minus the
network and the SecAgg masks, which cancel exactly - see tests), and scores
every release against exact ground truth.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from fedsketch.protocol import EdgeLogic, VariantConfig, VariantServer

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
RESULTS = ROOT / "results"


def load(name: str):
    rounds = pd.read_parquet(PROC / f"{name}_rounds.parquet")
    dictionary = np.load(PROC / f"{name}_dict.npy")
    meta = json.loads((PROC / f"{name}_meta.json").read_text())
    return rounds, dictionary, meta


class Truth:
    """Exact per-round / sliding-window / cumulative counts on a dense index."""

    def __init__(self, rounds: pd.DataFrame, dictionary: np.ndarray, window: int):
        self.keys = np.union1d(rounds.key.values, dictionary)
        self.window = window
        self.per_round = {}
        for t, g in rounds.groupby("round"):
            idx, cnt = np.unique(np.searchsorted(self.keys, g.key.values), return_counts=True)
            self.per_round[t] = (idx, cnt)
        self.dict_idx = np.searchsorted(self.keys, dictionary)
        self.cum = np.zeros(len(self.keys), np.int64)
        self.win = np.zeros(len(self.keys), np.int64)
        self.now = np.zeros(len(self.keys), np.int64)

    def advance(self, t: int):
        idx, cnt = self.per_round.get(t, (np.zeros(0, int), np.zeros(0, int)))
        self.now[:] = 0
        self.now[idx] = cnt
        self.cum[idx] += cnt
        self.win[idx] += cnt
        if t - self.window in self.per_round:
            oi, oc = self.per_round[t - self.window]
            self.win[oi] -= oc

    def topk(self, vec, k):
        idx = np.argpartition(-vec, k - 1)[:k]
        return set(self.keys[idx].tolist())


def f1(pred, true) -> float:
    pred, true = set(pred), set(true)
    if not pred or not true:
        return 0.0
    tp = len(pred & true)
    if tp == 0:
        return 0.0
    p, r = tp / len(pred), tp / len(true)
    return 2 * p * r / (p + r)


def run(name: str, variants: list[VariantConfig], k: int = 50, max_rounds: int | None = None,
        linf_every: int = 1, verbose: bool = True) -> pd.DataFrame:
    rounds, dictionary, meta = load(name)
    n_edges = meta["n_edges"]
    T = meta["n_rounds"] if max_rounds is None else min(max_rounds, meta["n_rounds"])
    window = variants[0].window
    assert all(v.window == window for v in variants)
    truth = Truth(rounds[rounds["round"] < T], dictionary, window)
    by = {(r, e): (g.user.values, g.key.values) for (r, e), g in rounds[rounds["round"] < T].groupby(["round", "edge"])}
    empty = (np.zeros(0, np.int64), np.zeros(0, np.int64))

    servers = {v.name: VariantServer(v, n_edges, dictionary) for v in variants}
    edges = {v.name: [EdgeLogic(v, e, n_edges) for e in range(n_edges)] for v in variants}
    rows = []
    t_start = time.time()
    for t in range(T):
        truth.advance(t)
        top_win, top_now, top_cum = truth.topk(truth.win, k), truth.topk(truth.now, k), truth.topk(truth.cum, k)
        for v in variants:
            srv = servers[v.name]
            tic = time.perf_counter()
            plan = srv.plan(t)
            agg = None
            for e, edge in enumerate(edges[v.name]):
                p = edge.payload(plan, *by.get((t, e), empty))
                agg = p if agg is None else agg + p
            srv.ingest(plan, agg)
            proc = time.perf_counter() - tic
            info = srv.info[-1]
            row = dict(variant=v.name, round=t, width=info.width, drift=info.drift,
                       bytes_per_edge=8 * info.payload_len, n_true=int(truth.now.sum()), n_hat=info.n_hat,
                       iblt_decoded=info.iblt_decoded, iblt_complete=info.iblt_complete, iblt_thr=info.iblt_thr,
                       eps_spent=srv.epsilon_spent(t), proc_s=proc)
            row["f1_win"] = f1(srv.topk(k, "win", t)[0], top_win)
            row["f1_now"] = f1(srv.topk(k, "win", t)[0], top_now)
            row["f1_cum"] = f1(srv.topk(k, "cum", t)[0], top_cum)
            if t % linf_every == 0 or t == T - 1:
                tw, tc = truth.win[truth.dict_idx], truth.cum[truth.dict_idx]
                row["linf_win"] = float(np.abs(srv.estimate(dictionary, "win", t) - tw).max())
                row["linf_cum"] = float(np.abs(srv.estimate(srv.dictionary, "cum", t) - tc).max())
                row["n_win"], row["n_cum"] = int(truth.win.sum()), int(truth.cum.sum())
            rows.append(row)
        if verbose and (t % 25 == 0 or t == T - 1):
            last = {r["variant"]: r for r in rows[-len(variants):]}
            msg = " | ".join(f"{n}: F1w={r['f1_win']:.2f} F1c={r['f1_cum']:.2f}" for n, r in last.items())
            print(f"[{name}] round {t + 1}/{T} ({time.time() - t_start:.0f}s) {msg}", flush=True)
    return pd.DataFrame(rows)
