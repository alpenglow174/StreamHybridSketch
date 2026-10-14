"""Edge node = Flower client that owns one Kafka partition (a shard of users).

Per Flower round:
  round 1          : advertise its X25519 public key (SecAgg setup)
  round 2 + t      : wait until the window-t watermark arrives on its partition,
                     sample one item per local user, build the payload of every
                     variant (sketch nodes + noise share [+ IBLT]), mask it with
                     pairwise SecAgg masks and return it.
Raw events never leave the edge.
"""
import json
import os
import time
from collections import defaultdict

import flwr as fl
import numpy as np
from confluent_kafka import Consumer, TopicPartition

from fedsketch.hashing import derive_seed, mix64
from fedsketch.protocol import EdgeLogic
from fedsketch.secagg import SecAggParty
from pipeline.common import bootstrap, load_config, meta, round_of, variants


class EdgeClient(fl.client.NumPyClient):
    def __init__(self, edge_id: int):
        self.cfg = load_config()
        self.meta = meta(self.cfg)
        self.edge = edge_id
        self.n_edges = self.cfg["n_edges"]
        self.secagg = SecAggParty(edge_id)
        self.logic = {v.name: EdgeLogic(v, edge_id, self.n_edges) for v in variants(self.cfg)}
        self.wstart = np.array(self.meta["window_start_ts"], dtype=np.int64)
        self.consumer = Consumer({"bootstrap.servers": bootstrap(), "group.id": f"edge-{edge_id}",
                                  "auto.offset.reset": "earliest", "enable.auto.commit": False})
        self.consumer.assign([TopicPartition(self.cfg["topics"]["events"], edge_id, 0)])
        self.buf = defaultdict(lambda: ([], [], []))   # round -> (eids, users, keys)
        self.closed = -1
        self.events_seen = 0

    # ------------------------------------------------------------ kafka
    def _pump_until(self, t: int):
        while self.closed < t:
            msgs = self.consumer.consume(num_messages=5000, timeout=0.5)
            for m in msgs:
                if m.error():
                    continue
                parts = m.value().decode().split(",")
                if parts[0] == "T":
                    self.closed = max(self.closed, int(parts[1]))
                    continue
                eid, ts, user, key = (int(x) for x in parts[1:])
                r = int(round_of(np.array([ts]), self.wstart, self.meta["window_seconds"])[0])
                if r < 0:
                    continue
                b = self.buf[r]
                b[0].append(eid), b[1].append(user), b[2].append(key)
                self.events_seen += 1

    def _sample(self, t: int):
        eids, users, keys = (np.array(x, dtype=np.int64) for x in self.buf.pop(t, ([], [], [])))
        if len(eids) == 0:
            return eids, eids, 0
        # one item per user: the event with the smallest hash (== uniform over its events)
        h = mix64(eids, derive_seed(self.meta["seed"], "usersample"))
        order = np.lexsort((h, users))
        first = np.ones(len(order), bool)
        first[1:] = users[order][1:] != users[order][:-1]
        sel = order[first]
        return users[sel], keys[sel], len(eids)

    # ----------------------------------------------------------- flower
    def get_parameters(self, config):
        return []

    def fit(self, parameters, config):
        if config["phase"] == "keys":
            pk = np.frombuffer(self.secagg.public_key(), dtype=np.uint8)
            return [pk], 1, {"edge": self.edge}
        if not self.secagg._shared:
            self.secagg.set_peers({int(k): bytes.fromhex(v) for k, v in json.loads(config["peers"]).items()})
        t = int(config["t"])
        tic = time.time()
        self._pump_until(t)
        waited = time.time() - tic
        users, keys, n_events = self._sample(t)
        tic = time.time()
        payload = np.concatenate([self.logic[p["variant"]].payload(p, users, keys)
                                  for p in json.loads(config["plans"])])
        masked = self.secagg.mask(payload, round_id=t)
        build = time.time() - tic
        return [masked], len(users), {"edge": self.edge, "events": n_events, "users": len(users),
                                      "wait_s": waited, "build_s": build, "bytes": int(masked.nbytes)}

    def evaluate(self, parameters, config):
        return 0.0, 0, {}


def main():
    edge_id = int(os.environ["EDGE_ID"])
    cfg = load_config()
    client = EdgeClient(edge_id)
    for attempt in range(60):
        try:
            fl.client.start_client(server_address=cfg["fl_server"], client=client.to_client())
            print(f"edge {edge_id}: finished", flush=True)
            return
        except Exception as e:    # server not up yet
            print(f"edge {edge_id}: connect retry ({e.__class__.__name__})", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
