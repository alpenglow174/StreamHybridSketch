"""Flower server: one Flower round per stream window.

The custom strategy plans every variant's round (sketch nodes, widths, hash
seeds, noise scale, IBLT threshold), sends the plans to all edges, sums the
SecAgg-masked payloads (the masks cancel, so only the aggregate is learned),
decodes each variant and publishes its release (top-k heavy hitters for the
sliding window and the cumulative stream) to the Kafka ``releases`` topic.
"""
import json
import time

import flwr as fl
import numpy as np
from confluent_kafka import Producer
from flwr.common import FitIns, ndarrays_to_parameters, parameters_to_ndarrays

from fedsketch.data import decode_word
from fedsketch.protocol import VariantServer, payload_size
from fedsketch.secagg import unmask_sum
from pipeline.common import PROC, bootstrap, influx, load_config, meta, variants


class FedSketchStrategy(fl.server.strategy.Strategy):
    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.meta = meta(cfg)
        self.K = cfg["n_edges"]
        dictionary = np.load(PROC / f"{cfg['dataset']}_dict.npy")
        self.servers = {v.name: VariantServer(v, self.K, dictionary) for v in variants(cfg)}
        self.peers: dict[int, str] = {}
        self.plans: list[dict] = []
        self.dispatch = 0.0
        self.producer = Producer({"bootstrap.servers": bootstrap()})
        self.db = influx()

    def initialize_parameters(self, client_manager):
        return ndarrays_to_parameters([])

    def configure_fit(self, server_round, parameters, client_manager):
        clients = client_manager.sample(num_clients=self.K, min_num_clients=self.K)
        if server_round == 1:
            config = {"phase": "keys"}
        else:
            t = server_round - 2
            self.plans = [srv.plan(t) for srv in self.servers.values()]
            config = {"phase": "data", "t": t, "plans": json.dumps(self.plans), "peers": json.dumps(self.peers)}
        self.dispatch = time.time()
        return [(c, FitIns(parameters, config)) for c in clients]

    def aggregate_fit(self, server_round, results, failures):
        if failures:
            raise RuntimeError(f"{len(failures)} edge(s) failed; SecAgg without dropout recovery cannot proceed")
        if server_round == 1:
            for _, res in results:
                pk = parameters_to_ndarrays(res.parameters)[0]
                self.peers[int(res.metrics["edge"])] = pk.astype(np.uint8).tobytes().hex()
            print(f"server: SecAgg keys from edges {sorted(self.peers)}", flush=True)
            return ndarrays_to_parameters([]), {}

        t = server_round - 2
        tic = time.time()
        agg = unmask_sum([parameters_to_ndarrays(r.parameters)[0] for _, r in results])
        unmask_s = time.time() - tic
        off, points, k = 0, [], self.cfg["top_k"]
        wstart = self.meta["window_start_ts"][t]
        for plan in self.plans:
            srv = self.servers[plan["variant"]]
            size = payload_size(plan, srv.cfg.depth)
            srv.ingest(plan, agg[off:off + size])
            off += size
            info = srv.info[-1]
            top_w, est_w = srv.topk(k, "win", t)
            top_c, est_c = srv.topk(k, "cum", t)
            release = {"variant": srv.cfg.name, "round": t, "window_start_ts": wstart,
                       "top_win": top_w.tolist(), "est_win": np.round(est_w, 1).tolist(),
                       "top_cum": top_c.tolist(), "est_cum": np.round(est_c, 1).tolist(),
                       "width": info.width, "bytes_per_edge": 8 * size, "eps_spent": srv.epsilon_spent(t),
                       "drift": info.drift, "n_hat": info.n_hat, "iblt_decoded": info.iblt_decoded,
                       "latency_s": time.time() - self.dispatch}
            self.producer.produce(self.cfg["topics"]["releases"], value=json.dumps(release))
            eps = release["eps_spent"]
            points.append({"measurement": "release", "tags": {"variant": srv.cfg.name},
                           "fields": {"round": t, "width": info.width, "bytes_per_edge": 8 * size,
                                      "eps_spent": float(eps) if np.isfinite(eps) else -1.0,
                                      "drift": int(info.drift), "n_hat": info.n_hat,
                                      "iblt_decoded": info.iblt_decoded, "latency_s": release["latency_s"]}})
        self.producer.flush()
        metrics = [r.metrics for _, r in results]
        total_bytes = sum(m["bytes"] for m in metrics)
        points.append({"measurement": "server", "fields": {
            "round": t, "round_latency_s": time.time() - self.dispatch, "unmask_s": unmask_s,
            "edge_wait_s_max": max(m["wait_s"] for m in metrics), "edge_build_s_max": max(m["build_s"] for m in metrics),
            "upload_bytes": total_bytes, "events": sum(m["events"] for m in metrics),
            "users": sum(m["users"] for m in metrics),
            "agg_throughput_sketch_per_s": len(results) / max(unmask_s, 1e-6)}})
        self.db.write_points(points)
        if self.meta["name"] == "sentiment140":
            print(f"server: round {t} top-5 window words "
                  f"{[decode_word(x) for x in next(iter(self.servers.values())).topk(5, 'win', t)[0]]}", flush=True)
        print(f"server: round {t} aggregated {len(results)} edges, {total_bytes / 1024:.0f} KiB, "
              f"{time.time() - self.dispatch:.2f}s", flush=True)
        return ndarrays_to_parameters([]), {}

    def configure_evaluate(self, server_round, parameters, client_manager):
        return []

    def aggregate_evaluate(self, server_round, results, failures):
        return None, {}

    def evaluate(self, server_round, parameters):
        return None


def main():
    cfg = load_config()
    n_rounds = 1 + min(cfg["max_rounds"], meta(cfg)["n_rounds"])
    fl.server.start_server(server_address="0.0.0.0:8080",
                           config=fl.server.ServerConfig(num_rounds=n_rounds),
                           strategy=FedSketchStrategy(cfg))


if __name__ == "__main__":
    main()
