"""Compare the live pipeline (results/live_metrics.csv, written by the oracle)
with the offline harness run on the same config. Same seeds => the SecAgg
aggregates are identical, so per-round F1 should match (up to top-k ties)."""
import pandas as pd

from experiments.simulate import RESULTS, run
from pipeline.common import load_config, variants

cfg = load_config()
live = pd.read_csv(RESULTS / "live_metrics.csv")
sim = run(cfg["dataset"], variants(cfg), k=cfg["top_k"], max_rounds=cfg["max_rounds"], verbose=False)
m = live.merge(sim, on=["variant", "round"], suffixes=("_live", "_sim"))
rows = []
for v, g in m.groupby("variant"):
    rows.append(dict(variant=v, rounds=len(g),
                     max_abs_diff_f1_win=(g.f1_win_live - g.f1_win_sim).abs().max(),
                     max_abs_diff_f1_cum=(g.f1_cum_live - g.f1_cum_sim).abs().max(),
                     width_equal=(g.width_live == g.width_sim).all(),
                     bytes_equal=(g.bytes_per_edge_live == g.bytes_per_edge_sim).all(),
                     mean_f1_win_live=g.f1_win_live.mean(), mean_f1_win_sim=g.f1_win_sim.mean(),
                     mean_latency_s=g.latency_s.mean()))
out = pd.DataFrame(rows)
out.to_csv(RESULTS / "live_vs_sim.csv", index=False)
print(out.round(4).to_string())
