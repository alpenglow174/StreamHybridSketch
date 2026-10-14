"""Streaming experiments (baseline vs. StreamHybridSketch, ablations, sweeps).

python -m experiments.run_stream <group> <dataset>
  main   : baseline vs full enhancement vs ablations (eps=1)
  eps    : epsilon sweep, baseline vs tree
  comm   : communication/accuracy sweep (fixed widths vs adaptive targets), eps=1
  hh     : no-dictionary heavy hitters, SecAgg only: IBLT vs CountSketch widths
Writes results/stream_<group>_<dataset>.csv
"""
import sys
import time

from experiments.simulate import RESULTS, load, run
from fedsketch.protocol import VariantConfig as V

W0 = 2048


def horizon(dataset):
    return 1 << int(load(dataset)[2]["n_rounds"] - 1).bit_length()


def group(name, dataset):
    H = horizon(dataset)
    common = dict(horizon=H, window=7)
    if name == "main":
        return [
            V("baseline", mode="per_round", width=W0, eps=1.0, **common),
            V("stream_hybrid", mode="tree", width=W0, eps=1.0, adaptive_width=True, drift=True, peel=True, **common),
            V("abl_no_tree", mode="per_round", width=W0, eps=1.0, adaptive_width=True, drift=True, **common),
            V("abl_no_adapt", mode="tree", width=W0, eps=1.0, drift=True, peel=True, **common),
            V("abl_no_drift", mode="tree", width=W0, eps=1.0, adaptive_width=True, peel=True, **common),
            V("abl_no_peel", mode="tree", width=W0, eps=1.0, adaptive_width=True, drift=True, **common),
            V("nodp_baseline", mode="per_round", width=W0, **common),
        ]
    if name == "eps":
        out = []
        for e in (0.125, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0):
            out.append(V(f"per_round_e{e}", mode="per_round", width=W0, eps=e, **common))
            out.append(V(f"tree_e{e}", mode="tree", width=W0, eps=e, peel=True, **common))
        return out
    if name == "comm":
        out = [V(f"fixed_w{w}", mode="per_round", width=w, eps=1.0, **common) for w in (64, 128, 256, 512, 1024, 2048, 4096)]
        out += [V(f"adapt_r{r}", mode="per_round", width=W0, eps=1.0, adaptive_width=True, rel_target=r, **common)
                for r in (0.0005, 0.001, 0.002, 0.005, 0.01)]
        return out
    if name == "hh":
        out = [V(f"cs_dict_w{w}", mode="per_round", width=w, **common) for w in (32, 64, 128, 256, 512, 1024, 2048)]
        out += [V(f"iblt_c{c}", mode="per_round", width=64, iblt=True, iblt_cells=3 * c, **common)
                for c in (64, 128, 256, 512, 1024)]
        return out
    raise ValueError(name)


if __name__ == "__main__":
    g, ds = sys.argv[1], sys.argv[2]
    t0 = time.time()
    df = run(ds, group(g, ds), linf_every=1 if g in ("main", "eps") else 5)
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"stream_{g}_{ds}.csv"
    df.to_csv(out, index=False)
    print(f"wrote {out} ({time.time() - t0:.0f}s)")
