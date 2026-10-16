"""Larger-type version of the drift figure for the slides: python experiments/slide_figs.py
(plain csv + matplotlib only, so it runs outside the project container too)"""
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt

matplotlib.use("Agg")
RESULTS = Path(__file__).resolve().parents[1] / "results"
C = ["#2a78d6", "#eb6834", "#1baf7a"]
plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                     "grid.color": "#e6e5e1", "axes.edgecolor": "#5f6670", "xtick.color": "#5f6670",
                     "ytick.color": "#5f6670", "savefig.bbox": "tight"})

series = defaultdict(list)
with open(RESULTS / "stream_main_synthetic.csv", newline="") as f:
    for row in csv.DictReader(f):
        series[row["variant"]].append((int(row["round"]), float(row["f1_now"])))


def smooth(vals, k=3):  # trailing 3-round mean, like the report figure
    return [sum(vals[max(0, i - k + 1):i + 1]) / len(vals[max(0, i - k + 1):i + 1]) for i in range(len(vals))]


fig, a = plt.subplots(figsize=(6.4, 3.0), dpi=200)
labels = {"baseline": "Baseline", "abl_no_tree": "SHS-W", "abl_no_adapt": "SHS-C"}
for i, v in enumerate(labels):
    pts = sorted(series[v])
    a.plot([r for r, _ in pts], smooth([y for _, y in pts]), color=C[i], lw=1.8, label=labels[v])
for r in (70, 140):
    a.axvline(r, color="#1f2329", lw=1, ls="--")
a.set_xlabel("round (dashed: top-200 items swapped)")
a.set_ylabel("top-50 F1, current round")
a.set_ylim(0.2, 1.02)
a.legend(loc="lower left", frameon=False, ncol=3)
out = RESULTS / "figures" / "drift_slide.png"
fig.savefig(out)
print("wrote", out)
