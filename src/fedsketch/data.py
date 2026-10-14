"""Dataset loading, item encoding and window/user preprocessing.

Every stream is a table of events (eid, ts, user, key):
  eid  - unique event id (row number in replay order)
  ts   - unix seconds
  user - int64 user id (client)
  key  - int64 item id (> 0, < 2**40 so IBLT sums cannot overflow)

Per window, each user contributes ONE item, chosen uniformly among its events
in that window (== proportionally to local frequency) by the deterministic
rule argmin_e mix64(eid, seed). This is the one-item-per-client model of
Wu et al. (2023) / Zhu et al. (2020) and fixes the DP sensitivity.
"""
from __future__ import annotations

import gzip
import io
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .hashing import derive_seed, mix64

ALPHA = "abcdefghijklmnopqrstuvwxyz"
WORD_LEN = 8
_TOKEN = re.compile(r"[a-z]+")


# ------------------------------------------------------------------ words

def encode_word(w: str) -> int:
    k = 0
    for ch in w[:WORD_LEN]:
        k = k * 27 + (ord(ch) - 96)
    return k


def decode_word(k: int) -> str:
    s = []
    while k > 0:
        k, r = divmod(int(k), 27)
        s.append(ALPHA[r - 1] if r else "?")
    return "".join(reversed(s))


# ------------------------------------------------------------------ loaders

GRID_DEG = 0.05   # Gowalla items = ~5 km grid cells (Wu et al. also map Gowalla to a grid)


def gowalla_cell(lat, lon) -> np.ndarray:
    n_lon = int(round(360 / GRID_DEG))
    i = np.floor((np.asarray(lat) + 90) / GRID_DEG).astype(np.int64)
    j = np.floor((np.asarray(lon) + 180) / GRID_DEG).astype(np.int64)
    return i * n_lon + j + 1


def cell_center(key: int) -> tuple[float, float]:
    n_lon = int(round(360 / GRID_DEG))
    i, j = divmod(int(key) - 1, n_lon)
    return (i + 0.5) * GRID_DEG - 90, (j + 0.5) * GRID_DEG - 180


def load_gowalla(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep="\t", header=None, names=["user", "time", "lat", "lon", "loc"],
                     compression="gzip", dtype={"user": np.int64, "lat": np.float64, "lon": np.float64})
    df = df[df.lat.between(-90, 90) & df.lon.between(-180, 180)]
    ts = pd.to_datetime(df["time"], utc=True, format="%Y-%m-%dT%H:%M:%SZ").astype("int64") // 10**9
    return pd.DataFrame({"ts": ts.values, "user": df["user"].values,
                         "key": gowalla_cell(df.lat.values, df.lon.values)})


def load_sentiment140(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        name = [n for n in z.namelist() if n.startswith("training")][0]
        raw = z.read(name)
    df = pd.read_csv(io.BytesIO(raw), encoding="latin-1", header=None,
                     names=["pol", "id", "date", "q", "user", "text"])
    ts = pd.to_datetime(df["date"].str.replace(" PDT", "", regex=False),
                        format="%a %b %d %H:%M:%S %Y").astype("int64") // 10**9 + 7 * 3600
    users = pd.util.hash_pandas_object(df["user"], index=False).values.astype(np.uint64) >> np.uint64(2)
    rows_ts, rows_user, rows_key = [], [], []
    for t, u, text in zip(ts.values, users, df["text"].str.lower().values):
        for tok in _TOKEN.findall(text):
            rows_ts.append(t)
            rows_user.append(u)
            rows_key.append(encode_word(tok))
    return pd.DataFrame({"ts": np.array(rows_ts, dtype=np.int64),
                         "user": np.array(rows_user, dtype=np.int64),
                         "key": np.array(rows_key, dtype=np.int64)})


def synthetic_zipf(n_rounds=200, users_per_round=20000, universe=100_000, s=1.1,
                   drift_rounds=(70, 140), drift_top=200, round_seconds=3600, seed=7) -> pd.DataFrame:
    """Zipf(s) stream. At each drift round the identities of the top
    ``drift_top`` ranks are replaced by previously-cold items (abrupt drift)."""
    rng = np.random.default_rng(seed)
    p = 1.0 / np.arange(1, universe + 1) ** s
    p /= p.sum()
    perm = rng.permutation(universe) + 1
    rows = []
    user_pool = 5 * users_per_round
    for t in range(n_rounds):
        if t in drift_rounds:
            cold = rng.choice(np.arange(universe // 2, universe), size=drift_top, replace=False)
            top = perm[:drift_top].copy()
            perm[:drift_top], perm[cold] = perm[cold], top
        users = rng.choice(user_pool, size=users_per_round, replace=False)
        ranks = rng.choice(universe, size=users_per_round, p=p)
        rows.append(pd.DataFrame({"ts": t * round_seconds + rng.integers(0, round_seconds, users_per_round),
                                  "user": users, "key": perm[ranks]}))
    return pd.concat(rows, ignore_index=True)


# ------------------------------------------------------------ windowing

def finalize_events(df: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    if start is not None:
        df = df[df.ts >= start]
    if end is not None:
        df = df[df.ts < end]
    df = df.sort_values("ts", kind="stable").reset_index(drop=True)
    df.insert(0, "eid", np.arange(len(df), dtype=np.int64))
    return df


def edge_of(users, n_edges: int, seed: int) -> np.ndarray:
    return (mix64(np.asarray(users, dtype=np.int64), derive_seed(seed, "edge")) % np.uint64(n_edges)).astype(np.int64)


def window_of(ts, t0: int, window_seconds: int) -> np.ndarray:
    return (np.asarray(ts, dtype=np.int64) - t0) // window_seconds


def sample_user_items(events: pd.DataFrame, seed: int) -> pd.DataFrame:
    """events must contain eid, window, user, key. Returns one row per (window, user)."""
    h = mix64(events["eid"].values, derive_seed(seed, "usersample"))
    tmp = events.assign(_h=h).sort_values(["window", "user", "_h"], kind="stable")
    out = tmp.drop_duplicates(["window", "user"], keep="first").drop(columns="_h")
    return out.reset_index(drop=True)
