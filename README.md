# Federated Sketches for Privacy-Preserving Streaming Analytics (Team 22, IOE413)

End-to-end streaming pipeline that estimates item frequencies and heavy hitters over
distributed event streams **without raw events ever leaving the edge**:

```
 dataset replay ──► Apache Kafka ──► 8 edge nodes (Flower clients) ──► Flower server ──► Kafka "releases" ──► evaluator ──► InfluxDB ──► Grafana
  (event time)     topic "events",    local one-item-per-user       SecAgg unmask,       top-k per window      exact truth,     metrics      live
                   1 partition/edge   sampling, CountSketch /       decode, DP release,  + cumulative          F1 / latency                 dashboard
                   + watermarks       tree nodes + DP noise share   drift + width control
                                      [+ IBLT], X25519 masks
```

* **Baseline (reproduced, Wu et al. NeurIPS 2023):** DP-HybridSketch released every window
  (shared bucket hash, fresh sign hash per round, Gaussian noise per round).
* **StreamHybridSketch (our enhancement)**, each part switchable for ablation:
  1. **Tree continual release** (binary-tree mechanism) of window sketches, so cumulative
     queries combine O(log t) noisy nodes instead of t noisy rounds;
  2. **Heavy-hitter peeling** estimator that removes the coherent collision error that shared-sign
     tree nodes suffer from (pure post-processing, no privacy cost);
  3. **Adaptive two-phase width** per round (power-law tail fit of Wu et al. on the previous release);
  4. **Drift detector** (top-k Jaccard) that restarts the sliding window and width search;
  5. **IBLT + threshold sampling** layer (Gascón et al. 2023) that lists heavy-hitter identities
     with no candidate dictionary (SecAgg-only, not DP).
* Also reproduced: TrieHH (Zhu et al. AISTATS 2020) on Sentiment140.

## Quick start

Requirements: Docker Desktop (or Docker Engine + compose plugin on Linux) with ~6 GB RAM, internet access,
~5 GB free disk. Optional: Python 3 with pip on the host (lets the image build install packages offline).

```powershell
# Windows (PowerShell)
.\setup.ps1            # check, download, build, start, test, preprocess (~10-15 min the first time)
.\setup.ps1 -Demo      # ... and start the live demo, opens Grafana at http://localhost:3000
```
```bash
# Linux / macOS
./setup.sh             # same steps
./setup.sh --demo
```

The scripts are safe to re-run (finished steps are skipped) and stop with a specific message and fix for the
usual problems: Docker not installed/running (starts Docker Desktop if it can), Windows-containers mode,
WSL distro without Docker integration, missing Docker permissions, Compose v1 only, low memory or disk,
ports 3000/8086/29092 taken by another program, truncated/corrupt dataset downloads, dataset server down,
DNS/network failures inside the image build (retry, then offline-wheel or PyPI fallback), Docker Hub pulls failing,
services not becoming healthy, out-of-memory during preprocessing, pipeline services crashing on start.
Other options: `-Experiments`/`--experiments` (full experiment suite, 20+ min), `-SkipTests`, `-SkipData`,
`-Stop`/`--stop`, `-Clean`/`--clean` (also deletes Kafka/Influx volumes; asks first). Logs: `.setup-logs/`.
On Windows, if scripts are blocked: `powershell -ExecutionPolicy Bypass -File .\setup.ps1`.

The manual steps the scripts automate are listed under [Setup](#setup).

## Repository layout

| Path | What |
|---|---|
| `src/fedsketch/` | library: hashing, CountSketch family (`sketches.py`), IBLT (`iblt.py`), SecAgg (`secagg.py`), DP + tree + accounting (`dp.py`), protocol shared by pipeline and harness (`protocol.py`), dataset loaders (`data.py`) |
| `pipeline/` | live services: `producer.py` (Kafka replay), `edge_client.py` (Flower client per edge), `fl_server.py` (Flower strategy), `oracle.py` (evaluator), `config.json` |
| `experiments/` | `prepare.py` (preprocessing), `simulate.py` (offline harness, same protocol code), `run_stream.py` (streaming experiments), `repro_paper1.py`, `repro_triehh.py`, `plots.py`, `summarize.py` |
| `tests/` | unit tests (SecAgg cancellation, IBLT linearity, exact folding, dyadic cover, tree == exact) |
| `grafana/` | provisioned datasource + dashboard (`make_dashboard.py` regenerates it) |
| `results/` | CSV results, `figures/`, `logs/` |
| `report/` | IEEE two-column report (LaTeX source + PDF) |

## Setup

Requirements: Docker Desktop (tested 4.x, Linux engine), ~3 GB free disk, ~6 GB RAM for Docker.
The project runs in its **own compose project (`fedsketch`)** and does not touch the course's
`hadoop-lab` container (that image ships Python 3.6, too old for Flower / dp-accounting).

```powershell
cd fedsketch-streaming
# 1) Python wheels for the image (downloaded on the host because DNS inside
#    Docker Desktop builds was unreliable; the build then installs offline)
python -m pip download -r docker/requirements.txt pytest==8.2.2 -d docker/wheels `
  --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux_2_28_x86_64 `
  --platform any --python-version 3.11 --implementation cp --only-binary=:all:
# 2) datasets
curl.exe -L -o data/raw/loc-gowalla_totalCheckins.txt.gz https://snap.stanford.edu/data/loc-gowalla_totalCheckins.txt.gz
curl.exe -L -o data/raw/sentiment140.zip https://cs.stanford.edu/people/alecmgo/trainingandtestdata.zip
# 3) build + infrastructure
docker compose build app
docker compose up -d kafka influxdb grafana app
docker compose exec app python -m pytest -q tests
docker compose exec app python -m experiments.prepare all      # ~3 min
```

## Run the live pipeline (demo)

```powershell
docker compose --profile pipeline up -d          # topics -> producer, fl-server, 8 edges, oracle
docker compose logs -f fl-server oracle           # watch rounds being aggregated / scored
```
Open **Grafana at http://localhost:3000** (anonymous admin; dashboard "Federated Sketches - live streaming analytics").
Dataset, number of rounds, replay speed and the variants run side by side are set in `pipeline/config.json`.
Stop with `docker compose --profile pipeline down` (infrastructure keeps running);
`docker compose down -v` removes everything including Kafka/Influx volumes.

## Reproduce the experiments

```powershell
docker compose exec app sh experiments/run_all.sh   # all groups in parallel (~20 min on 12 cores)
docker compose exec app python -m experiments.plots
docker compose exec app python -m experiments.summarize main gowalla
```
Individual runs: `python -m experiments.run_stream {main|eps|comm|hh} {synthetic|gowalla|sentiment140}`,
`python -m experiments.repro_paper1`, `python -m experiments.repro_triehh`.

The offline harness drives the *same* `EdgeLogic`/`VariantServer` code as the live
pipeline (only Kafka transport and the SecAgg masks are skipped; masks cancel exactly, see tests),
so it produces bit-identical aggregates for the same seeds while running ~100x faster.

## Tools and versions

| Tool | Version | Role |
|---|---|---|
| Apache Kafka (KRaft) | 3.7.0 (`apache/kafka`) | event ingestion, per-edge partitions, watermarks, release topic |
| Flower (`flwr`) | 1.11.1 | federated orchestration, one round per window, custom strategy |
| Python / NumPy / pandas | 3.11.9 / 1.26.4 / 2.2.2 | sketches, IBLT, protocol |
| cryptography | 42.0.8 | X25519 key agreement + HKDF for pairwise SecAgg masks |
| dp-accounting (Google) | 0.4.4 | RDP accountant for (epsilon, delta) of Gaussian compositions |
| confluent-kafka | 2.4.0 | Kafka producer/consumers |
| InfluxDB | 1.8.10 | metrics store |
| Grafana | 10.4.2 | live dashboard |
| Apache DataSketches (python) | 5.0.2 | installed for reference checks |
| Matplotlib / SciPy | 3.8.4 / 1.13.1 | figures, Lambert-W for TrieHH parameters |

## Datasets and stream model

| Dataset | Items | Rounds (window) | Users / round |
|---|---|---|---|
| Gowalla check-ins (SNAP), 2010-01-01..2010-10-22 | 5 km grid cells (80k) | 295 (1 day) | ~8.5k |
| Sentiment140 (1.6M tweets) | words, a-z, truncated to 8 letters | 124 (6 h, empty windows skipped) | ~9.8k |
| Synthetic Zipf(1.1), 100k items | item ids; top-200 identities swapped at rounds 70, 140 | 200 | 20k |

Each user contributes **one** item per window (uniform over its events in that window, as in
Wu et al. / Zhu et al.); users are assigned to 8 edges by hashing. The privacy unit is one user's
contribution to one window (event-level DP under continual observation); the CountSketch
L2 sensitivity is therefore exactly sqrt(depth). Noise is added in shares by the edges
(distributed Gaussian, fixed-point), so the server never sees an un-noised aggregate.

## Headline results (details: `report/main.pdf`, CSVs in `results/`)

The ablations led to two recommended configurations:
**SHS-W** = per-round release + adaptive width + drift detector (windowed / current-trend queries), and
**SHS-C** = tree release + peeling + drift detector, fixed width (long-horizon cumulative queries at small epsilon).

| Finding | Number |
|---|---|
| Drift recovery (synthetic, eps=1): rounds to regain 90% of pre-drift top-50 F1 | baseline 5.5, SHS-W/SHS-C 1.5 |
| Mean F1 over 7 post-drift rounds | 0.56 -> 0.85 |
| Upload per edge per round, SHS-W vs baseline (eps=1) | Gowalla 55 vs 80 KiB (same F1), synthetic 31 vs 80 KiB |
| Cumulative l_inf error, tree+peeling vs baseline, eps=0.125 | 2.0-2.6x lower on all 3 datasets |
| Same at eps >= 0.5, and for sliding windows | baseline better (honest negative result) |
| Peeling vs plain tree estimator (Sentiment140, eps=8) | 5.7x lower cumulative l_inf |
| IBLT (no dictionary) vs CountSketch+dictionary | 2-4x more upload for equal F1 on real data |
| Paper 1 repro: two-phase width vs minimax | 6-16x narrower, target error met in 76-96% of trials |
| Paper 1 repro: Hybrid vs Shared multi-round | 3.2-3.5x lower error (Fresh was 1.15-1.4x better than Hybrid: partial repro) |
| Live pipeline: round latency / SecAgg unmask / payload build | 2.14 s (paced at 2 s/window) / 1.6 ms / 12 ms |
| Live vs offline harness | identical widths and bytes for all 200 rounds, F1 within 0.04 |

`python -m experiments.check_live` re-runs the live-vs-harness comparison after a pipeline run;
`python -m experiments.system_stats` and `experiments.tables` regenerate the report tables.

## Report

`report/main.tex` (IEEEtran, tables in `report/tables/` generated from the CSVs, figures from `results/figures/`).
Build with MiKTeX/TeX Live: `cd report; pdflatex main.tex; pdflatex main.tex`. Author names/affiliation are placeholders.

## Known limitations

* SecAgg has no dropout recovery (all 8 edges must answer every round); edges' noise seeds are
  deterministic for reproducibility.
* The IBLT layer reveals exact (threshold-sampled) counts of decoded items: it is protected by
  secure aggregation and sampling only, not by DP.
* Distributed rounded-Gaussian shares are accounted as a continuous Gaussian (rounding at 1/256
  resolution).
* The Stack Overflow (TFF) dataset proposed for Paper 3 was replaced by the synthetic drift stream
  (TFF data is several GB); Gowalla is gridded like Wu et al. instead of using venue ids.
