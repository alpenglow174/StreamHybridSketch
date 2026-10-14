"""System-cost table from the live run's InfluxDB metrics -> report/tables/system.tex
(uses the most recent run: the last `n_rounds` points of the "server" measurement)."""
import json

import pandas as pd

from experiments.simulate import ROOT
from pipeline.common import influx, load_config

cfg = load_config()
n = cfg["max_rounds"]
db = influx()
srv = pd.DataFrame(db.query(f'SELECT * FROM "server" ORDER BY time DESC LIMIT {n}').get_points())
ing = pd.DataFrame(db.query(f'SELECT * FROM "ingest" ORDER BY time DESC LIMIT {n}').get_points())
stats = {
    "rounds": len(srv),
    "users_per_round": srv.users.mean(),
    "events_per_round": srv.events.mean(),
    "round_latency_s_median": srv.round_latency_s.median(),
    "round_latency_s_p95": srv.round_latency_s.quantile(0.95),
    "edge_build_s_median": srv.edge_build_s_max.median(),
    "edge_wait_s_median": srv.edge_wait_s_max.median(),
    "unmask_ms_median": 1e3 * srv.unmask_s.median(),
    "upload_mib_per_round": srv.upload_bytes.mean() / 2**20,
    "producer_events_per_s_median": ing.events_per_s.median(),
}
(ROOT / "results" / "system_stats.json").write_text(json.dumps(stats, indent=2))
print(json.dumps(stats, indent=2))
rows = [("Rounds (windows) processed", f"{stats['rounds']}"),
        ("Users / events per round", f"{stats['users_per_round']:,.0f} / {stats['events_per_round']:,.0f}"),
        ("Variants per round", f"{len(cfg['variants'])}"),
        ("Round latency, median / p95", f"{stats['round_latency_s_median']:.2f} s / {stats['round_latency_s_p95']:.2f} s"),
        ("Slowest edge: payload build (median)", f"{stats['edge_build_s_median']:.2f} s"),
        ("Slowest edge: wait for watermark (median)", f"{stats['edge_wait_s_median']:.2f} s"),
        ("Server SecAgg unmask (median)", f"{stats['unmask_ms_median']:.1f} ms"),
        ("Upload per round, all 8 edges", f"{stats['upload_mib_per_round']:.2f} MiB"),
        ("Kafka produce rate (median)", f"{stats['producer_events_per_s_median'] / 1e3:,.0f}k events/s")]
tex = [r"\begin{table}[t]\centering\footnotesize",
       r"\caption{System cost of the live run (synthetic stream, 2\,s per window, 8 edge containers and all services on one 12-core host).}\label{tab:system}",
       r"\begin{tabular}{lr}\toprule Metric & Value\\\midrule"]
tex += [f"{a} & {b}" + r"\\" for a, b in rows]
tex += [r"\bottomrule\end{tabular}\end{table}"]
(ROOT / "report" / "tables" / "system.tex").write_text("\n".join(tex))
