"""Replays a prepared dataset into Kafka in event-time order.

Topic ``events`` has one partition per edge; an event goes to the partition
of the edge that owns its user. After the last event of each window the
producer appends a watermark ("T,<round>") to every partition so edges know
the window is closed. Replay speed: ``seconds_per_round`` wall seconds per
window.
"""
import sys
import time

import pandas as pd
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic

from pipeline.common import PROC, bootstrap, influx, load_config


def recreate_topics(admin: AdminClient, cfg: dict):
    names = [cfg["topics"]["events"], cfg["topics"]["releases"]]
    existing = admin.list_topics(timeout=10).topics
    old = [n for n in names if n in existing]
    if old:
        for f in admin.delete_topics(old).values():
            f.result()
        while any(n in admin.list_topics(timeout=10).topics for n in old):
            time.sleep(0.5)
        time.sleep(2)
    fs = admin.create_topics([NewTopic(cfg["topics"]["events"], num_partitions=cfg["n_edges"], replication_factor=1),
                              NewTopic(cfg["topics"]["releases"], num_partitions=1, replication_factor=1)])
    for f in fs.values():
        f.result()


def main():
    cfg = load_config()
    if "--create-topics" in sys.argv:
        recreate_topics(AdminClient({"bootstrap.servers": bootstrap()}), cfg)
        print("topics recreated", flush=True)
        return
    ev = pd.read_parquet(PROC / f"{cfg['dataset']}_events.parquet",
                         columns=["eid", "ts", "user", "key", "round", "edge"])
    ev = ev[ev["round"] < cfg["max_rounds"]]
    prod = Producer({"bootstrap.servers": bootstrap(), "linger.ms": 20, "batch.num.messages": 10000,
                     "queue.buffering.max.messages": 2_000_000})
    db = influx()
    topic, n_edges = cfg["topics"]["events"], cfg["n_edges"]
    print(f"producer: {len(ev):,} events, {cfg['max_rounds']} rounds -> {topic}", flush=True)
    for r, g in ev.groupby("round", sort=True):
        t0 = time.time()
        for eid, ts, user, key, edge in zip(g.eid.values, g.ts.values, g.user.values, g.key.values, g.edge.values):
            while True:
                try:
                    prod.produce(topic, value=f"E,{eid},{ts},{user},{key}", partition=int(edge))
                    break
                except BufferError:
                    prod.poll(0.05)
        for p in range(n_edges):
            prod.produce(topic, value=f"T,{r}", partition=p)
        prod.flush()
        dt = time.time() - t0
        db.write_points([{"measurement": "ingest", "fields": {"round": int(r), "events": len(g),
                                                              "events_per_s": len(g) / max(dt, 1e-3)}}])
        print(f"producer: round {r} {len(g):,} events in {dt:.2f}s", flush=True)
        time.sleep(max(0.0, cfg["seconds_per_round"] - dt))
    print("producer: done", flush=True)


if __name__ == "__main__":
    main()
