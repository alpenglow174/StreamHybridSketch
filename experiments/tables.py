"""Generate LaTeX tables for the report from results/*.csv -> report/tables/*.tex"""
import numpy as np
import pandas as pd

from experiments.simulate import RESULTS, ROOT
from experiments.summarize import summary

OUT = ROOT / "report" / "tables"
OUT.mkdir(parents=True, exist_ok=True)
NAMES = {"baseline": "Baseline (DP-HybridSketch/round)", "stream_hybrid": "All components (tree+adapt+drift+peel)",
         "abl_no_tree": "\\textbf{SHS-W} (= all $-$ tree, $-$ peel)", "abl_no_adapt": "\\textbf{SHS-C} (= all $-$ adaptive width)",
         "abl_no_drift": "\\quad all $-$ drift detector", "abl_no_peel": "\\quad all $-$ peeling",
         "nodp_baseline": "Non-private reference"}
ORDER = ["baseline", "abl_no_tree", "abl_no_adapt", "stream_hybrid", "abl_no_drift", "abl_no_peel", "nodp_baseline"]
DS = [("synthetic", "Synthetic"), ("gowalla", "Gowalla"), ("sentiment140", "Sentiment140")]


def main_table():
    lines = [r"\begin{table*}[t]\centering\footnotesize",
             r"\caption{Streaming results at $\varepsilon=1$, $\delta=10^{-6}$ per unit over the whole stream "
             r"(top-50, 7-round window, 8 edges). F1$_w$/F1$_n$: mean top-50 F1 against the sliding-window / current-round truth; "
             r"F1$_c$: cumulative top-50 F1 over the second half; $\ell_\infty^c$: final cumulative $\ell_\infty$ error (counts); "
             r"KiB: upload per edge per round.}\label{tab:main}",
             r"\setlength{\tabcolsep}{3.2pt}\begin{tabular}{l" + "rrrrr" * 3 + "}\\toprule",
             " & " + " & ".join(rf"\multicolumn{{5}}{{c}}{{{t}}}" for _, t in DS) + r"\\",
             " ".join(rf"\cmidrule(lr){{{2 + 5 * i}-{6 + 5 * i}}}" for i in range(3)),
             "Variant" + " & F1$_w$ & F1$_n$ & F1$_c$ & $\\ell_\\infty^c$ & KiB" * 3 + r"\\\midrule"]
    sums = {ds: summary(pd.read_csv(RESULTS / f"stream_main_{ds}.csv")) for ds, _ in DS}
    for v in ORDER:
        cells = []
        for ds, _ in DS:
            s = sums[ds].loc[v]
            cells += [f"{s['F1_win(mean)']:.2f}", f"{s['F1_now(mean)']:.2f}", f"{s['F1_cum(2nd half)']:.2f}",
                      f"{s['linf_cum(final)']:.0f}", f"{s['KB/edge/round']:.0f}"]
        if v == "nodp_baseline":
            lines.append(r"\midrule")
        lines.append(NAMES[v] + " & " + " & ".join(cells) + r"\\")
    lines += [r"\bottomrule\end{tabular}\end{table*}"]
    (OUT / "main.tex").write_text("\n".join(lines))


def eps_table():
    lines = [r"\begin{table}[t]\centering\footnotesize",
             r"\caption{Final cumulative $\ell_\infty$ error (counts) vs.\ stream-level $\varepsilon$: per-round release (B) vs.\ tree + peeling (T). Bold: lower.}\label{tab:eps}",
             r"\setlength{\tabcolsep}{3pt}\begin{tabular}{r" + "rr" * 3 + r"}\toprule",
             " & " + " & ".join(rf"\multicolumn{{2}}{{c}}{{{t}}}" for _, t in DS) + r"\\",
             "$\\varepsilon$" + " & B & T" * 3 + r"\\\midrule"]
    d = {}
    for ds, _ in DS:
        df = pd.read_csv(RESULTS / f"stream_eps_{ds}.csv")
        last = df[df["round"] == df["round"].max()].set_index("variant")
        d[ds] = last.linf_cum
    for e in (0.125, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0):
        cells = []
        for ds, _ in DS:
            b, t = d[ds][f"per_round_e{e}"], d[ds][f"tree_e{e}"]
            cells += [f"\\textbf{{{b:.0f}}}" if b <= t else f"{b:.0f}", f"\\textbf{{{t:.0f}}}" if t < b else f"{t:.0f}"]
        lines.append(f"{e:g} & " + " & ".join(cells) + r"\\")
    lines += [r"\bottomrule\end{tabular}\end{table}"]
    (OUT / "eps.tex").write_text("\n".join(lines))


def drift_table():
    r = pd.read_csv(RESULTS / "drift_recovery.csv").groupby("variant")[["pre_f1", "min_post", "recovery_rounds", "mean_f1_next7"]].mean()
    lines = [r"\begin{table}[t]\centering\footnotesize",
             r"\caption{Recovery after the two abrupt drifts of the synthetic stream (mean of both; F1 vs.\ current-round top-50, $\varepsilon=1$).}\label{tab:drift}",
             r"\begin{tabular}{lrrrr}\toprule Variant & pre & min & rec.\ (rounds) & next 7\\\midrule"]
    short = {"baseline": "Baseline", "abl_no_tree": "SHS-W", "abl_no_adapt": "SHS-C",
             "stream_hybrid": "All components", "abl_no_drift": "All $-$ drift det."}
    for v in short:
        s = r.loc[v]
        lines.append(f"{short[v]} & {s.pre_f1:.2f} & {s.min_post:.2f} & {s.recovery_rounds:.1f} & {s.mean_f1_next7:.2f}" + r"\\")
    lines += [r"\bottomrule\end{tabular}\end{table}"]
    (OUT / "drift.tex").write_text("\n".join(lines))


def repro_table():
    s = pd.read_csv(RESULTS / "repro_paper1_single.csv")
    g = s.groupby(["dataset", "method"]).agg(w=("width", "median"), err=("linf", "mean"), ok=("ok", "mean"))
    m = pd.read_csv(RESULTS / "repro_paper1_multi.csv")
    mm = m[m.width == 512].groupby(["dataset", "method"]).linf.mean()
    lines = [r"\begin{table}[t]\centering\footnotesize",
             r"\caption{Reproduction of Wu et al.~\cite{wu2023}. Single round: median width and fraction of trials with $\ell_\infty\le\tau$ over 10 targets $\tau$. Multi-round ($M{=}10$, $W{=}512$): normalised $\ell_\infty$ ($\times10^{-3}$).}\label{tab:repro}",
             r"\setlength{\tabcolsep}{3pt}\begin{tabular}{lrrrrrr}\toprule",
             r" & \multicolumn{3}{c}{Median width (ok)} & \multicolumn{3}{c}{Multi-round $\ell_\infty$}\\\cmidrule(lr){2-4}\cmidrule(lr){5-7}",
             r"Dataset & minimax & inst.-opt & 2-phase & Shared & Fresh & Hybrid\\\midrule"]
    for ds, t in DS:
        c = [f"{g.loc[(ds, k)].w:.0f} ({g.loc[(ds, k)].ok:.2f})" for k in ("minimax", "instance_opt", "two_phase")]
        c += [f"{1e3 * mm.loc[(ds, k)]:.2f}" for k in ("shared", "fresh", "hybrid")]
        lines.append(t + " & " + " & ".join(c) + r"\\")
    lines += [r"\bottomrule\end{tabular}\end{table}"]
    (OUT / "repro.tex").write_text("\n".join(lines))


def triehh_table():
    t = pd.read_csv(RESULTS / "repro_triehh.csv")
    lines = [r"\begin{table}[t]\centering\footnotesize",
             r"\caption{TrieHH~\cite{zhu2020} on Sentiment140 ($n{=}659{,}775$ users, $\delta{=}1/n^2$): words discovered, recall of the true top-$k$ and precision of the discovered set w.r.t.\ the true top-200; DP-CountSketch with a public dictionary at the same $(\varepsilon,\delta)$ for reference.}\label{tab:triehh}",
             r"\setlength{\tabcolsep}{3pt}\begin{tabular}{rrrrrrr}\toprule",
             r"$\varepsilon$ & $\theta$ & found & R@50 & R@200 & P@200 & CS R@200\\\midrule"]
    for e in (1.0, 2.0, 4.0, 8.0):
        x = t[(t.method == "TrieHH") & (t.eps == e)]
        cs = t[(t.method == "DP-CountSketch+dict") & (t.eps == e) & (t.k == 200)].recall.mean()
        lines.append(f"{e:g} & {x.theta.iloc[0]:.0f} & {x.discovered.mean():.0f} & {x[x.k == 50].recall.mean():.2f} & "
                     f"{x[x.k == 200].recall.mean():.2f} & {x[x.k == 200].precision.mean():.2f} & {cs:.2f}" + r"\\")
    lines += [r"\bottomrule\end{tabular}\end{table}"]
    (OUT / "triehh.tex").write_text("\n".join(lines))


if __name__ == "__main__":
    for f in (main_table, eps_table, drift_table, repro_table, triehh_table):
        f()
        print("ok", f.__name__)
