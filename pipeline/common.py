"""Shared helpers for the live pipeline services."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np

from fedsketch.protocol import VariantConfig

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"


def load_config() -> dict:
    return json.loads(Path(os.environ.get("PIPELINE_CONFIG", ROOT / "pipeline" / "config.json")).read_text())


def variants(cfg: dict) -> list[VariantConfig]:
    return [VariantConfig(**v) for v in cfg["variants"]]


def meta(cfg: dict) -> dict:
    return json.loads((PROC / f"{cfg['dataset']}_meta.json").read_text())


def bootstrap() -> str:
    return os.environ.get("KAFKA_BOOTSTRAP", "localhost:29092")


def round_of(ts: np.ndarray, window_start_ts: np.ndarray, window_seconds: int) -> np.ndarray:
    """Event-time -> round id (compacted non-empty windows), -1 if outside."""
    i = np.searchsorted(window_start_ts, ts, side="right") - 1
    ok = (i >= 0) & (ts < window_start_ts[np.clip(i, 0, None)] + window_seconds)
    return np.where(ok, i, -1)


def influx():
    from influxdb import InfluxDBClient
    host = os.environ.get("INFLUX_HOST", "localhost")
    for _ in range(30):
        try:
            c = InfluxDBClient(host=host, port=8086, database="fedsketch")
            c.create_database("fedsketch")
            return c
        except Exception:
            time.sleep(2)
    raise RuntimeError("InfluxDB not reachable")


def wait_for_topics(admin, names, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        md = admin.list_topics(timeout=5)
        if all(n in md.topics for n in names):
            return
        time.sleep(1)
    raise RuntimeError(f"topics {names} not found")
