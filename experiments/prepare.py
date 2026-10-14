"""Preprocess raw datasets into replayable streams.

Outputs (data/processed/):
  <name>_events.parquet   eid, ts, user, key, round, edge   (what the Kafka producer replays)
  <name>_rounds.parquet   round, user, key, edge            (one sampled item per user-round)
  <name>_dict.npy         public candidate dictionary (keys seen >= min_count times)
  <name>_meta.json

Usage: python -m experiments.prepare [gowalla|sentiment140|synthetic|all]
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from fedsketch import data

ROOT = Path(__file__).resolve().parents[1]
RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "processed"
SEED = 2024
N_EDGES = 8

DATASETS = {
    # one round = one day of check-ins, 2010-01-01 .. 2010-10-23 (296 rounds)
    "gowalla": dict(loader=lambda: data.load_gowalla(RAW / "loc-gowalla_totalCheckins.txt.gz"),
                    start="2010-01-01", end="2010-10-23", window=86400, min_count=3),
    # one round = 6 hours of tweets; empty 6h windows are skipped
    "sentiment140": dict(loader=lambda: data.load_sentiment140(RAW / "sentiment140.zip"),
                         start=None, end=None, window=6 * 3600, min_count=3),
    # Zipf(1.1) with abrupt drift of the top-200 identities at rounds 70 and 140
    "synthetic": dict(loader=lambda: data.synthetic_zipf(), start=None, end=None, window=3600, min_count=1),
}


def prepare(name: str):
    spec = DATASETS[name]
    t0 = time.time()
    df = spec["loader"]()
    start = int(pd.Timestamp(spec["start"], tz="UTC").timestamp()) if spec["start"] else None
    end = int(pd.Timestamp(spec["end"], tz="UTC").timestamp()) if spec["end"] else None
    ev = data.finalize_events(df, start, end)
    base = int(ev.ts.min()) if start is None else start
    win = data.window_of(ev.ts.values, base, spec["window"])
    # compact non-empty windows into consecutive round ids
    uniq = np.unique(win)
    ev["round"] = np.searchsorted(uniq, win).astype(np.int64)
    ev["edge"] = data.edge_of(ev.user.values, N_EDGES, SEED)
    rounds = data.sample_user_items(ev.rename(columns={"round": "window"}), SEED).rename(columns={"window": "round"})
    rounds = rounds[["round", "user", "key", "edge"]]
    counts = rounds.key.value_counts()
    dictionary = np.sort(counts[counts >= spec["min_count"]].index.values.astype(np.int64))

    OUT.mkdir(parents=True, exist_ok=True)
    ev.to_parquet(OUT / f"{name}_events.parquet", index=False)
    rounds.to_parquet(OUT / f"{name}_rounds.parquet", index=False)
    np.save(OUT / f"{name}_dict.npy", dictionary)
    meta = dict(name=name, window_seconds=spec["window"], base_ts=base, n_rounds=int(len(uniq)),
                window_start_ts=[int(base + w * spec["window"]) for w in uniq],
                n_events=int(len(ev)), n_user_rounds=int(len(rounds)), n_users=int(ev.user.nunique()),
                n_items=int(ev.key.nunique()), dict_size=int(len(dictionary)),
                n_edges=N_EDGES, seed=SEED)
    (OUT / f"{name}_meta.json").write_text(json.dumps(meta))
    per_round = rounds.groupby("round").size()
    print(f"[{name}] events={len(ev):,} user-rounds={len(rounds):,} rounds={len(uniq)} "
          f"users/round median={int(per_round.median()):,} items={meta['n_items']:,} "
          f"dict={len(dictionary):,} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    for n in (DATASETS if which == "all" else [which]):
        prepare(n)
