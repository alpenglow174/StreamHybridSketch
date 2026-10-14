"""Reproduction of Wu, Zhu, Kairouz, Braverman (NeurIPS 2023).

E1 single round  : l_inf error and width for minimax / instance-optimal /
                   two-phase width selection, as a function of target tau.
E2 multi round   : Fresh vs Shared vs Hybrid sketch, l_inf error vs width
                   (M = 10 rounds; rounds = consecutive windows, so they are
                   heterogeneous like real streams).
E3 DP            : DP-HybridSketch l_inf error vs epsilon.
Frequencies are normalised (sum 1) and errors are max over the dictionary,
as in the paper. Writes results/repro_paper1_{single,multi,dp}.csv
"""
import time

import numpy as np
import pandas as pd

from experiments.simulate import RESULTS, load
from fedsketch import dp
from fedsketch.hashing import derive_seed
from fedsketch.sketches import (MultiRoundCS, cs_encode, cs_query, instance_width, next_pow2,
                                two_phase_width)

REPEATS = 10
C = 2.0


def freq_vector(keys, universe):
    idx = np.searchsorted(universe, keys)
    return np.bincount(idx, minlength=len(universe)) / len(keys)


def single_round(name, n, depth, rng):
    rounds, dic, _ = load(name)
    pool = rounds.key.values
    universe = np.union1d(np.unique(pool), dic)
    rows = []
    taus = np.geomspace(max(2e-3, 25 / n), 0.1, 10)
    for rep in range(REPEATS):
        keys = rng.choice(pool, n, replace=False)
        f = freq_vector(keys, universe)
        seed = derive_seed(rep, name, "single")
        # phase 1: n/10 clients, 8 x 256 sketch, top-20 over the dictionary
        p1 = keys[: n // 10]
        S1 = cs_encode(p1, None, 8, 256, seed, seed + 1)
        est1 = cs_query(S1, dic, seed, seed + 1) / len(p1)
        top20 = np.sort(est1)[::-1][:20]
        for tau in taus:
            widths = {"minimax": next_pow2(C * min(2 / tau, n)),
                      "instance_opt": next_pow2(instance_width(f, tau, C)),
                      "two_phase": two_phase_width(top20, tau, C)}
            for meth, W in widths.items():
                S = cs_encode(keys, None, depth, W, seed + 2, seed + 3)
                est = cs_query(S, universe, seed + 2, seed + 3) / n
                err = float(np.abs(est - f).max())
                comm = W * depth * int(np.ceil(np.log2(n + 1)))  # bits per client (SecSum field)
                rows.append(dict(dataset=name, rep=rep, tau=tau, method=meth, width=W, linf=err,
                                 ok=err <= tau, bits_per_client=comm))
    return rows


def multi_round(name, n_per_round, M, depth, widths, rng):
    rounds, dic, _ = load(name)
    rows = []
    starts = rng.choice(np.arange(0, rounds["round"].max() - M), REPEATS, replace=False)
    for rep, s in enumerate(starts):
        per = []
        for m in range(M):
            pool = rounds.key.values[rounds["round"].values == s + m]
            per.append(rng.choice(pool, min(n_per_round, len(pool)), replace=False))
        allk = np.concatenate(per)
        universe = np.union1d(np.unique(allk), dic)
        # global frequency = average of per-round local frequencies (paper's definition)
        f = np.mean([freq_vector(k, universe) for k in per], axis=0)
        for W in widths:
            for kind in ("fresh", "shared", "hybrid"):
                mr = MultiRoundCS(kind, depth, derive_seed(rep, name, W, kind))
                for m, k in enumerate(per):
                    mr.add(m, mr.encode_round(m, k, np.full(len(k), 1.0 / len(k) / M * 1e6), W))
                est = mr.query(universe) / 1e6
                rows.append(dict(dataset=name, rep=rep, width=W, method=kind,
                                 linf=float(np.abs(est - f).max())))
    return rows


def dp_hybrid(name, n_per_round, M, depth, W, eps_list, delta, rng):
    rounds, dic, _ = load(name)
    rows = []
    starts = rng.choice(np.arange(0, rounds["round"].max() - M), REPEATS, replace=False)
    for rep, s in enumerate(starts):
        per = []
        for m in range(M):
            pool = rounds.key.values[rounds["round"].values == s + m]
            per.append(rng.choice(pool, min(n_per_round, len(pool)), replace=False))
        universe = np.union1d(np.unique(np.concatenate(per)), dic)
        counts = sum(np.bincount(np.searchsorted(universe, k), minlength=len(universe)) for k in per)
        N = sum(len(k) for k in per)
        for eps in [None] + list(eps_list):
            sigma = 0.0 if eps is None else dp.calibrate_noise_multiplier(eps, 1, delta) * np.sqrt(depth)
            mr = MultiRoundCS("hybrid", depth, derive_seed(rep, name, "dp"))
            for m, k in enumerate(per):
                S = mr.encode_round(m, k, None, W).astype(np.float64)
                S += rng.normal(0, sigma, S.shape) if sigma > 0 else 0
                mr.add(m, S)
            est = mr.query(universe)
            rows.append(dict(dataset=name, rep=rep, eps=eps if eps else np.inf, sigma=sigma, width=W,
                             linf=float(np.abs(est - counts).max()) / N, linf_counts=float(np.abs(est - counts).max())))
    return rows


def main():
    rng = np.random.default_rng(0)
    t0 = time.time()
    single = []
    for name, n in (("gowalla", 17500), ("sentiment140", 65000), ("synthetic", 20000)):
        single += single_round(name, n, depth=15, rng=rng)
        print(f"single-round {name} done ({time.time() - t0:.0f}s)", flush=True)
    pd.DataFrame(single).to_csv(RESULTS / "repro_paper1_single.csv", index=False)

    multi = []
    for name, n in (("gowalla", 5000), ("sentiment140", 5000), ("synthetic", 10000)):
        multi += multi_round(name, n, M=10, depth=5, widths=[64, 128, 256, 512, 1024, 2048], rng=rng)
        print(f"multi-round {name} done ({time.time() - t0:.0f}s)", flush=True)
    pd.DataFrame(multi).to_csv(RESULTS / "repro_paper1_multi.csv", index=False)

    dps = []
    for name, n in (("gowalla", 5000), ("sentiment140", 5000), ("synthetic", 10000)):
        dps += dp_hybrid(name, n, M=10, depth=5, W=1024, eps_list=[0.25, 0.5, 1, 2, 4, 8], delta=1e-6, rng=rng)
        print(f"dp {name} done ({time.time() - t0:.0f}s)", flush=True)
    pd.DataFrame(dps).to_csv(RESULTS / "repro_paper1_dp.csv", index=False)


if __name__ == "__main__":
    main()
