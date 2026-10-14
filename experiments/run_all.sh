#!/bin/sh
# Launch every experiment in parallel inside the app container:
#   docker compose exec app sh experiments/run_all.sh
mkdir -p results/logs
for ds in synthetic gowalla sentiment140; do
  for g in main eps hh; do
    nohup python -m experiments.run_stream $g $ds > results/logs/stream_${g}_${ds}.log 2>&1 &
  done
done
for ds in gowalla sentiment140; do
  nohup python -m experiments.run_stream comm $ds > results/logs/stream_comm_${ds}.log 2>&1 &
done
nohup python -m experiments.repro_paper1 > results/logs/repro_paper1.log 2>&1 &
nohup python -m experiments.repro_triehh > results/logs/repro_triehh.log 2>&1 &
wait
