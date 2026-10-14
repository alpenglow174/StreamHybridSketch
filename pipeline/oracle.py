"""Evaluator ("oracle"). Not part of the privacy protocol: it reads the raw
``events`` topic to build exact ground truth with the same one-item-per-user
sampling, joins it with the server's ``releases`` and writes quality metrics
(top-k F1 vs. sliding-window, current-round and cumulative truth) to InfluxDB
for Grafana, plus results/live_metrics.csv for offline comparison.
"""
import json
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
from confluent_kafka import Consumer

from fedsketch.hashing import derive_seed, mix64
from pipeline.common import ROOT, bootstrap, influx, load_config, meta, round_of


def f1(pred, true):
    pred, true = set(pred), set(true)
    tp = len(pred & true)
    if tp == 0:
        return 0.0
    p, r = tp / len(pred), tp / len(true)
    return 2 * p * r / (p + r)


def topk(counter: Counter, k):
    return [x for x, _ in counter.most_common(k)]


def main():
    cfg = load_config()
    m = meta(cfg)
    k, n_edges, S = cfg["top_k"], cfg["n_edges"], cfg["variants"][0]["window"]
    n_rounds = min(cfg["max_rounds"], m["n_rounds"])
    wstart = np.array(m["window_start_ts"], dtype=np.int64)
    seed = derive_seed(m["seed"], "usersample")
    db = influx()
    c = Consumer({"bootstrap.servers": bootstrap(), "group.id": "oracle", "auto.offset.reset": "earliest"})
    c.subscribe([cfg["topics"]["events"], cfg["topics"]["releases"]])

    best = defaultdict(dict)               # round -> user -> (hash, key)
    ticks = defaultdict(set)               # round -> partitions with watermark
    truth_round: dict[int, Counter] = {}
    cum = Counter()
    pending: list[dict] = []
    rows = []

    def finalize(r):
        cnt = Counter(key for _, key in best.pop(r, {}).values())
        truth_round[r] = cnt
        cum.update(cnt)
        win = Counter()
        for q in range(max(0, r - S + 1), r + 1):
            win.update(truth_round.get(q, {}))
        truth_round[r] = cnt
        truth_round.setdefault("_win", {})[r] = topk(win, k)
        truth_round.setdefault("_cum", {})[r] = topk(cum, k)
        truth_round.setdefault("_now", {})[r] = topk(cnt, k)
        db.write_points([{"measurement": "truth", "fields": {"round": r, "users": sum(cnt.values()),
                                                             "distinct_items": len(cnt)}}])

    done = set()
    while len(done) < n_rounds * len(cfg["variants"]):
        for msg in c.consume(num_messages=5000, timeout=1.0):
            if msg.error():
                continue
            if msg.topic() == cfg["topics"]["releases"]:
                pending.append(json.loads(msg.value()))
                continue
            parts = msg.value().decode().split(",")
            if parts[0] == "T":
                r = int(parts[1])
                ticks[r].add(msg.partition())
                if len(ticks[r]) == n_edges:
                    finalize(r)
                continue
            eid, ts, user, key = (int(x) for x in parts[1:])
            r = int(round_of(np.array([ts]), wstart, m["window_seconds"])[0])
            h = int(mix64(np.array([eid]), seed)[0])
            cur = best[r].get(user)
            if cur is None or h < cur[0]:
                best[r][user] = (h, key)
        still = []
        for rel in pending:
            r = rel["round"]
            if r not in truth_round:
                still.append(rel)
                continue
            q = dict(variant=rel["variant"], round=r,
                     f1_win=f1(rel["top_win"], truth_round["_win"][r]),
                     f1_now=f1(rel["top_win"], truth_round["_now"][r]),
                     f1_cum=f1(rel["top_cum"], truth_round["_cum"][r]),
                     width=rel["width"], bytes_per_edge=rel["bytes_per_edge"], eps_spent=rel["eps_spent"],
                     drift=rel["drift"], latency_s=rel["latency_s"])
            rows.append(q)
            done.add((rel["variant"], r))
            db.write_points([{"measurement": "quality", "tags": {"variant": rel["variant"]},
                              "fields": {kk: float(q[kk]) for kk in ("round", "f1_win", "f1_now", "f1_cum")}}])
            print(f"oracle: round {r} {rel['variant']}: F1 win={q['f1_win']:.2f} "
                  f"now={q['f1_now']:.2f} cum={q['f1_cum']:.2f}", flush=True)
        pending = still
    out = ROOT / "results" / "live_metrics.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"oracle: wrote {out}", flush=True)


if __name__ == "__main__":
    main()
