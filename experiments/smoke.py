"""Quick sanity run: python -m experiments.smoke <dataset> <rounds>"""
import sys

from experiments.simulate import run
from fedsketch.protocol import VariantConfig as V

name = sys.argv[1] if len(sys.argv) > 1 else "synthetic"
T = int(sys.argv[2]) if len(sys.argv) > 2 else 40
vs = [
    V("baseline", mode="per_round", width=2048, eps=1.0),
    V("tree", mode="tree", width=2048, eps=1.0),
    V("tree_adapt_drift", mode="tree", width=2048, eps=1.0, adaptive_width=True, drift=True),
    V("iblt_nodp", mode="tree", width=2048, adaptive_width=True, drift=True, iblt=True),
]
df = run(name, vs, max_rounds=T)
print(df.groupby("variant")[["f1_win", "f1_now", "f1_cum", "linf_win", "linf_cum", "bytes_per_edge", "width",
                             "eps_spent", "proc_s", "iblt_decoded", "iblt_thr"]].mean().round(3).to_string())
print(df[df.drift][["variant", "round"]].to_string())
