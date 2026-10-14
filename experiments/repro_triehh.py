"""Reproduction of TrieHH (Zhu, Kairouz, McMahan, Sun, Li; AISTATS 2020) on
Sentiment140, plus a same-(eps, delta) comparison with a one-shot
DP-CountSketch over a public dictionary and (non-DP) IBLT discovery.

Each user contributes ONE word chosen uniformly among its word occurrences
(Theorem 2 of the paper turns record-level into user-level DP this way).
Words are truncated to 8 letters (our item encoding) and end with "$".
Parameters follow Corollary 1: theta = max(10, ceil(e^{W(C_delta)+1} - 1/2),
ceil(e^{eps/L} - 1)), gamma = (e^{eps/L} - 1) sqrt(n) / (theta e^{eps/L}).
"""
import time

import numpy as np
import pandas as pd
from scipy.special import lambertw

from experiments.simulate import PROC, RESULTS
from fedsketch import dp
from fedsketch.data import WORD_LEN, decode_word
from fedsketch.hashing import derive_seed, mix64
from fedsketch.iblt import iblt_decode, iblt_encode, threshold_sample
from fedsketch.sketches import cs_encode, cs_query

L = WORD_LEN + 1   # characters + end-of-sequence symbol


def triehh_params(eps, delta, n):
    c_delta = np.exp(-1) * np.log(8 / (7 * np.sqrt(2 * np.pi)) / delta)
    theta = int(max(10, np.ceil(np.exp(lambertw(c_delta).real + 1) - 0.5), np.ceil(np.exp(eps / L) - 1)))
    gamma = (np.exp(eps / L) - 1) * np.sqrt(n) / (theta * np.exp(eps / L))
    gamma = max(gamma, 1.0)
    eps_check = L * np.log(1 + 1 / (np.sqrt(n) / (gamma * theta) - 1))
    return theta, gamma, eps_check


def triehh(words, theta, gamma, rng):
    n = len(words)
    m = int(gamma * np.sqrt(n))
    trie = {""}
    for i in range(1, L + 1):
        batch = words[rng.choice(n, m, replace=False)]
        votes = {}
        for w in batch:
            if len(w) >= i and w[: i - 1] in trie:
                p = w[:i]
                votes[p] = votes.get(p, 0) + 1
        new = {p for p, v in votes.items() if v >= theta}
        if not new:
            break
        trie |= new
    return {p[:-1] for p in trie if p.endswith("$")}


def main():
    t0 = time.time()
    ev = pd.read_parquet(PROC / "sentiment140_events.parquet", columns=["eid", "user", "key"])
    h = mix64(ev.eid.values, derive_seed(1, "triehh"))
    one = ev.assign(h=h).sort_values(["user", "h"]).drop_duplicates("user")
    keys = one.key.values
    n = len(keys)
    words = np.array([decode_word(k) + "$" for k in keys], dtype=object)
    counts = pd.Series(keys).value_counts()
    print(f"users n={n:,} distinct words={len(counts):,} ({time.time() - t0:.0f}s)", flush=True)
    dictionary = np.sort(counts[counts >= 3].index.values)
    rng = np.random.default_rng(0)
    rows = []
    for eps in (1.0, 2.0, 4.0, 8.0):
        delta = 1.0 / n ** 2
        theta, gamma, eps_chk = triehh_params(eps, delta, n)
        sigma = dp.calibrate_noise_multiplier(eps, 1, delta) * np.sqrt(5)
        for rep in range(5):
            found = triehh(words, theta, gamma, rng)
            found_keys = {k for k in counts.index[:5000] if decode_word(k) in found}
            S = cs_encode(keys, None, 5, 2048, rep, rep + 1).astype(float) + rng.normal(0, sigma, (5, 2048))
            est = cs_query(S, dictionary, rep, rep + 1)
            for k in (10, 50, 100, 200):
                true_top = set(counts.index[:k])
                cs_top = set(dictionary[np.argsort(-est)[:k]])
                rows.append(dict(eps=eps, rep=rep, k=k, method="TrieHH", theta=theta, gamma=gamma,
                                 eps_check=eps_chk, discovered=len(found), recall=len(found_keys & true_top) / k,
                                 precision=len(found_keys & true_top) / max(len(found_keys), 1)))
                rows.append(dict(eps=eps, rep=rep, k=k, method="DP-CountSketch+dict", sigma=sigma,
                                 discovered=k, recall=len(cs_top & true_top) / k,
                                 precision=len(cs_top & true_top) / k))
        print(f"eps={eps}: theta={theta} gamma={gamma:.2f} (eps check {eps_chk:.2f}) "
              f"m/round={int(gamma * np.sqrt(n))} discovered={len(found)} ({time.time() - t0:.0f}s)", flush=True)
    # non-DP IBLT discovery (SecAgg only), one table, threshold sampling
    for cells in (768, 1536, 3072):
        for thr in (16, 32, 64, 128):
            sk, sv = threshold_sample(keys, np.ones(n), thr, 7, salt=one.user.values)
            out, ok = iblt_decode(iblt_encode(sk, sv, cells, 9), 9)
            got = sorted(out, key=out.get, reverse=True)
            for k in (10, 50, 100, 200):
                true_top = set(counts.index[:k])
                rows.append(dict(eps=np.inf, rep=0, k=k, method=f"IBLT(c={cells},t={thr})", discovered=len(out),
                                 complete=ok, recall=len(set(got[:k]) & true_top) / k,
                                 precision=len(set(got[:k]) & true_top) / max(min(k, len(got)), 1)))
    pd.DataFrame(rows).to_csv(RESULTS / "repro_triehh.csv", index=False)
    print(f"done ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
