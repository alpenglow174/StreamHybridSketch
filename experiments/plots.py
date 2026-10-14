"""Figures for the report: python -m experiments.plots  -> results/figures/*.pdf|png"""
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from experiments.simulate import RESULTS

matplotlib.use("Agg")
FIG = RESULTS / "figures"
FIG.mkdir(parents=True, exist_ok=True)
# categorical slots in fixed order (validated reference palette), + markers for print/CVD
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
M = ["o", "s", "^", "D", "v", "P", "X", "*"]
INK, MUTED = "#0b0b0b", "#52514e"
plt.rcParams.update({"font.size": 8, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED,
                     "ytick.color": MUTED, "axes.grid": True, "grid.color": "#e6e5e1", "grid.linewidth": 0.6,
                     "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 1.6,
                     "lines.markersize": 4, "legend.frameon": False, "figure.dpi": 150, "savefig.bbox": "tight"})
DATASETS = [("synthetic", "Synthetic Zipf (drift)"), ("gowalla", "Gowalla (5 km cells)"), ("sentiment140", "Sentiment140 (words)")]


def save(fig, name):
    fig.savefig(FIG / f"{name}.pdf")
    fig.savefig(FIG / f"{name}.png", dpi=200)
    plt.close(fig)


def load(name):
    p = RESULTS / f"{name}.csv"
    return pd.read_csv(p) if p.exists() else None


def repro_paper1():
    s = load("repro_paper1_single")
    if s is not None:
        fig, axes = plt.subplots(1, 2, figsize=(7, 2.4))
        g = s[s.dataset == "gowalla"].groupby(["tau", "method"]).agg(width=("width", "median"), linf=("linf", "mean")).reset_index()
        for i, m in enumerate(["minimax", "instance_opt", "two_phase"]):
            x = g[g.method == m]
            axes[0].plot(x.tau, x.width, marker=M[i], color=C[i], label=m.replace("_", "-"))
            axes[1].plot(x.tau, x.linf, marker=M[i], color=C[i], label=m.replace("_", "-"))
        axes[1].plot(g.tau.unique(), g.tau.unique(), ls="--", color=MUTED, lw=1, label="error = tau")
        for a, yl in zip(axes, ["sketch width W", r"$\ell_\infty$ error (normalised)"]):
            a.set_xscale("log"); a.set_yscale("log"); a.set_xlabel(r"target error $\tau$"); a.set_ylabel(yl)
        axes[1].legend(fontsize=7)
        fig.suptitle("Paper 1 reproduction, single round (Gowalla, n=17.5k)", fontsize=8)
        save(fig, "repro_single")
    m = load("repro_paper1_multi")
    if m is not None:
        fig, axes = plt.subplots(1, 3, figsize=(7, 2.2), sharey=False)
        for a, (ds, title) in zip(axes, DATASETS):
            g = m[m.dataset == ds].groupby(["width", "method"]).linf.mean().unstack()
            for i, meth in enumerate(["shared", "fresh", "hybrid"]):
                a.plot(g.index, g[meth], marker=M[i], color=C[i], label=meth.capitalize() + "Sketch")
            a.set_xscale("log", base=2); a.set_yscale("log"); a.set_title(title, fontsize=8); a.set_xlabel("width W")
        axes[0].set_ylabel(r"$\ell_\infty$ error"); axes[0].legend(fontsize=7)
        save(fig, "repro_multi")
    d = load("repro_paper1_dp")
    if d is not None:
        fig, a = plt.subplots(figsize=(3.4, 2.2))
        g = d.groupby(["eps", "dataset"]).linf.mean().unstack()
        fin = g[np.isfinite(g.index)]
        for i, (ds, title) in enumerate(DATASETS):
            a.plot(fin.index, fin[ds], marker=M[i], color=C[i], label=title)
            a.axhline(g.loc[np.inf, ds], color=C[i], ls=":", lw=1)
        a.set_xscale("log", base=2); a.set_yscale("log"); a.set_xlabel(r"$\varepsilon$ (per round)")
        a.set_ylabel(r"$\ell_\infty$ error"); a.legend(fontsize=6); a.set_title("DP-HybridSketch, M=10 (dotted: non-private)", fontsize=8)
        save(fig, "repro_dp")


def triehh():
    t = load("repro_triehh")
    if t is None:
        return
    fig, axes = plt.subplots(1, 2, figsize=(7, 2.2))
    for j, k in enumerate([50, 200]):
        a = axes[j]
        for i, meth in enumerate(["TrieHH", "DP-CountSketch+dict"]):
            g = t[(t.method == meth) & (t.k == k)].groupby("eps")[["recall", "precision"]].mean()
            a.plot(g.index, g.recall, marker=M[i], color=C[i], label=f"{meth} recall")
            a.plot(g.index, g.precision, marker=M[i], color=C[i], ls="--", label=f"{meth} precision")
        a.set_xscale("log", base=2); a.set_xlabel(r"$\varepsilon$ ($\delta=1/n^2$)"); a.set_title(f"top-{k} words", fontsize=8)
        a.set_ylim(0, 1.05)
    axes[0].legend(fontsize=6)
    save(fig, "repro_triehh")


def eps_sweep():
    fig, axes = plt.subplots(2, 3, figsize=(7, 4.2))
    for j, (ds, title) in enumerate(DATASETS):
        df = load(f"stream_eps_{ds}")
        if df is None:
            continue
        df["mode"] = np.where(df.variant.str.startswith("tree"), "tree", "per_round")
        df["eps"] = df.variant.str.split("_e").str[-1].astype(float)
        T = df["round"].max()
        last = df[df["round"] == T]
        late = df[df["round"] >= T // 2].groupby(["mode", "eps"]).f1_cum.mean()
        for i, (mode, lab) in enumerate([("per_round", "Baseline (per-round DP-HybridSketch)"),
                                         ("tree", "Tree continual release + peeling")]):
            x = last[last["mode"] == mode].sort_values("eps")
            axes[0, j].plot(x.eps, x.linf_cum / x.n_cum, marker=M[i], color=C[i], label=lab)
            y = late.loc[mode]
            axes[1, j].plot(y.index, y.values, marker=M[i], color=C[i], label=lab)
        axes[0, j].set_title(title, fontsize=8)
        for a in (axes[0, j], axes[1, j]):
            a.set_xscale("log", base=2); a.set_xlabel(r"stream-level $\varepsilon$ per unit")
        axes[0, j].set_yscale("log")
    axes[0, 0].set_ylabel(r"final cumulative $\ell_\infty$ / n")
    axes[1, 0].set_ylabel("top-50 F1, cumulative (2nd half)")
    h, l = axes[0, 0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=2, fontsize=7, bbox_to_anchor=(0.5, -0.03))
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    save(fig, "eps_sweep")


def cum_over_time(eps="0.25"):
    fig, axes = plt.subplots(1, 3, figsize=(7, 2.2))
    for a, (ds, title) in zip(axes, DATASETS):
        df = load(f"stream_eps_{ds}")
        if df is None:
            continue
        for i, v in enumerate([f"per_round_e{eps}", f"tree_e{eps}"]):
            x = df[df.variant == v]
            a.plot(x["round"], x.linf_cum, color=C[i], lw=1.4, label="Baseline" if i == 0 else "Tree + peeling")
        a.set_title(title, fontsize=8); a.set_xlabel("round")
    axes[0].set_ylabel(rf"cumulative $\ell_\infty$ error ($\varepsilon$={eps})"); axes[0].legend(fontsize=7)
    save(fig, "cum_over_time")


def drift():
    df = load("stream_main_synthetic")
    if df is None:
        return
    fig, a = plt.subplots(figsize=(7, 2.2))
    for i, v in enumerate(["baseline", "abl_no_tree", "abl_no_adapt"]):
        x = df[df.variant == v]
        a.plot(x["round"], x.f1_now.rolling(3, min_periods=1).mean(), color=C[i], lw=1.4,
               label={"baseline": "Baseline (fixed 7-round window)",
                      "abl_no_tree": "SHS-W: per-round + adaptive width + drift",
                      "abl_no_adapt": "SHS-C: tree + peeling + drift (fixed W)"}[v])
    for r in df[(df.variant == "abl_no_tree") & df.drift]["round"]:
        a.axvline(r, color=C[1], lw=0.8, ls=":")
    for r in (70, 140):
        a.axvline(r, color=INK, lw=1.0, ls="--")
    a.set_xlabel("round (dashed: true drift, dotted: detections)"); a.set_ylabel("top-50 F1 vs current round")
    a.legend(fontsize=7, loc="lower left")
    save(fig, "drift")


def tradeoff():
    fig, axes = plt.subplots(1, 3, figsize=(7, 2.3))
    j = 0
    for ds, title in DATASETS:
        df = load(f"stream_hh_{ds}")
        if df is None:
            continue
        a = axes[j]; j += 1
        g = df.groupby("variant").agg(kb=("bytes_per_edge", "mean"), f1=("f1_win", "mean")).reset_index()
        cs = g[g.variant.str.startswith("cs")].sort_values("kb")
        ib = g[g.variant.str.startswith("iblt")].sort_values("kb")
        a.plot(cs.kb / 1024, cs.f1, marker=M[0], color=C[0], label="CountSketch + dictionary")
        a.plot(ib.kb / 1024, ib.f1, marker=M[1], color=C[1], label="IBLT + threshold sampling (no dict.)")
        a.set_xscale("log", base=2); a.set_title(title, fontsize=8); a.set_xlabel("KiB per edge per round"); a.set_ylim(0, 1.02)
    axes[0].set_ylabel("top-50 F1 (7-round window)"); axes[0].legend(fontsize=6)
    save(fig, "hh_comm")
    fig, axes = plt.subplots(1, 2, figsize=(7, 2.3))
    for a, (ds, title) in zip(axes, DATASETS[1:]):
        df = load(f"stream_comm_{ds}")
        if df is None:
            continue
        g = df.groupby("variant").agg(kb=("bytes_per_edge", "mean"), f1=("f1_win", "mean")).reset_index()
        fx = g[g.variant.str.startswith("fixed")].sort_values("kb")
        ad = g[g.variant.str.startswith("adapt")].sort_values("kb")
        a.plot(fx.kb / 1024, fx.f1, marker=M[0], color=C[0], label="fixed width (baseline)")
        a.plot(ad.kb / 1024, ad.f1, marker=M[1], color=C[1], label="adaptive two-phase width")
        a.set_xscale("log", base=2); a.set_title(title + r", $\varepsilon$=1", fontsize=8); a.set_xlabel("KiB per edge per round")
    axes[0].set_ylabel("top-50 F1 (7-round window)"); axes[0].legend(fontsize=7)
    save(fig, "comm_adaptive")


if __name__ == "__main__":
    for f in (repro_paper1, triehh, eps_sweep, cum_over_time, drift, tradeoff):
        try:
            f()
            print("ok", f.__name__)
        except Exception as e:
            print("FAILED", f.__name__, e)
