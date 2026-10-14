"""Print summary tables of the streaming results: python -m experiments.summarize <group> <dataset>"""
import sys

import pandas as pd

from experiments.simulate import RESULTS


def summary(df: pd.DataFrame) -> pd.DataFrame:
    T = df["round"].max()
    last = df[df["round"] == T].set_index("variant")
    late = df[df["round"] >= T // 2]
    out = pd.DataFrame({
        "F1_win(mean)": df.groupby("variant").f1_win.mean(),
        "F1_now(mean)": df.groupby("variant").f1_now.mean(),
        "F1_cum(mean)": df.groupby("variant").f1_cum.mean(),
        "F1_cum(2nd half)": late.groupby("variant").f1_cum.mean(),
        "linf_win/n(mean)": (df.linf_win / df.n_win).groupby(df.variant).mean(),
        "linf_cum/n(final)": last.linf_cum / last.n_cum,
        "linf_cum(final)": last.linf_cum,
        "KB/edge/round": df.groupby("variant").bytes_per_edge.mean() / 1024,
        "width(mean)": df.groupby("variant").width.mean(),
        "eps(final)": last.eps_spent,
        "drifts": df.groupby("variant").drift.sum(),
    })
    return out


if __name__ == "__main__":
    g, ds = sys.argv[1], sys.argv[2]
    df = pd.read_csv(RESULTS / f"stream_{g}_{ds}.csv")
    pd.set_option("display.width", 250)
    print(summary(df).round(4).to_string())
    if df.drift.any():
        print(df[df.drift].groupby("variant")["round"].apply(list).to_string())
