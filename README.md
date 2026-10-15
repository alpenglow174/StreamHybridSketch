# StreamHybridSketch — Federated Sketches for Privacy-Preserving Streaming Analytics

IOE413 Team 22: Adnan Soni (2023BCD0017) · Bhaskara Akshay Sriram (2023BCD0015) · Muhammed Minhaj P S (2023BCS0225) · Hafiz Feroze (2023BCS0006)

Finds the most popular items (top-50) in live, distributed data streams without collecting raw data.
Eight edge clients each send a noisy CountSketch through secure aggregation; the server only sees the
differentially private sum. Baseline: DP-HybridSketch (Wu et al., NeurIPS 2023). Our additions: tree
continual release, peeling, adaptive sketch width, drift detection and an IBLT layer.

## Pipeline

```
dataset replay -> Kafka (1 partition per edge) -> 8 Flower edge clients (sketch + noise share + SecAgg mask)
  -> Flower server (unmask sum, decode, release top-50) -> Kafka "releases" -> evaluator -> InfluxDB -> Grafana
```

## Requirements

Docker Desktop (or Docker Engine + compose plugin) running, ~6 GB RAM for Docker, ~5 GB disk, internet.
Ports 3000, 8086 and 29092 free.

## Setup and run

Windows (PowerShell):
```powershell
.\setup.ps1              # check, download data, build, start, test, preprocess (10-15 min first time)
.\demo.ps1               # run the live demo (~2 min); Grafana opens at http://localhost:3000
.\setup.ps1 -Experiments # optional: all experiments + figures (20+ min)
.\setup.ps1 -Stop        # stop containers
```
If scripts are blocked: `powershell -ExecutionPolicy Bypass -File .\setup.ps1`

Linux / macOS: `./setup.sh`, `./demo.sh`, `./setup.sh --experiments`, `./setup.sh --stop`

Demo settings (dataset, rounds, speed, variants): `pipeline/config.json`. Results: `results/`.
Report: `report/22_StreamHybridSketch_Report.pdf`. Slides: `presentation/`.

## Tools and versions

| Tool | Version |
|---|---|
| Apache Kafka (KRaft) | 3.7.0 |
| Flower (`flwr`) | 1.11.1 |
| Python | 3.11.9 |
| NumPy / pandas / SciPy / Matplotlib | 1.26.4 / 2.2.2 / 1.13.1 / 3.8.4 |
| confluent-kafka | 2.4.0 |
| cryptography (X25519 secure aggregation) | 42.0.8 |
| dp-accounting (Google) | 0.4.4 |
| InfluxDB | 1.8.10 |
| Grafana | 10.4.2 |
| Docker Compose | v2 |
