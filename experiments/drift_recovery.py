"""Recovery after the synthetic drifts (rounds 70, 140): rounds until F1 vs the
current round's top-50 returns to >= 90% of its mean over the 10 pre-drift rounds,
and mean F1_now over the 7 rounds after each drift."""
import pandas as pd

from experiments.simulate import RESULTS

df = pd.read_csv(RESULTS / "stream_main_synthetic.csv")
rows = []
for v, g in df.groupby("variant"):
    g = g.set_index("round")
    for d in (70, 140):
        pre = g.loc[d - 10:d - 1, "f1_now"].mean()
        post = g.loc[d:d + 20, "f1_now"]
        rec = next((r - d for r, x in post.items() if x >= 0.9 * pre), None)
        rows.append(dict(variant=v, drift=d, pre_f1=pre, min_post=post.min(), recovery_rounds=rec,
                         mean_f1_next7=g.loc[d:d + 6, "f1_now"].mean()))
out = pd.DataFrame(rows)
out.to_csv(RESULTS / "drift_recovery.csv", index=False)
print(out.groupby("variant")[["pre_f1", "min_post", "recovery_rounds", "mean_f1_next7"]].mean().round(3).to_string())
