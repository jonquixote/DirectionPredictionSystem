#!/usr/bin/env bash
# Weekly retrain — runs Thursday 02:00 UTC. Waits for daily-features sentinel
# (/data/training_ready.json) before kicking off train_fleet. Uses parallel=1
# (OOM constraint). No --train-end → train_fleet auto-derives from latest
# feature date.
set -euo pipefail

LOGDIR=/data/logs
mkdir -p "$LOGDIR"
LOG="$LOGDIR/weekly_retrain.log"
exec >> "$LOG" 2>&1

echo "===== $(date -u +%FT%TZ) weekly retrain start ====="

READY=/data/training_ready.json
MAX_WAIT_SECS=7200      # 2h max
SENTINEL_MAX_AGE=86400  # 24h freshness window

WAITED=0
while [ $WAITED -lt $MAX_WAIT_SECS ]; do
    if [ -f "$READY" ]; then
        MTIME=$(stat -c %Y "$READY" 2>/dev/null || echo 0)
        NOW=$(date +%s)
        AGE=$((NOW - MTIME))
        if [ $AGE -lt $SENTINEL_MAX_AGE ]; then
            echo "training_ready.json fresh (age=${AGE}s). proceeding."
            break
        else
            echo "training_ready.json stale (age=${AGE}s, max=${SENTINEL_MAX_AGE}s). waiting..."
        fi
    else
        echo "training_ready.json absent. waiting..."
    fi
    sleep 60
    WAITED=$((WAITED + 60))
done

if [ ! -f "$READY" ]; then
    echo "ABORT: training_ready.json never landed after ${MAX_WAIT_SECS}s"
    exit 1
fi

MTIME=$(stat -c %Y "$READY" 2>/dev/null || echo 0)
AGE=$(( $(date +%s) - MTIME ))
if [ $AGE -ge $SENTINEL_MAX_AGE ]; then
    echo "ABORT: training_ready.json still stale (age=${AGE}s) after wait"
    exit 1
fi

cd /home/johnny/ofi-lab-v3
TRAIN_LOG="/tmp/fleet_train_$(date -u +%Y%m%d_%H%M).log"
echo "running train_fleet.py --parallel 1 --buffer-days 0 (log: $TRAIN_LOG)"

.venv/bin/python -m scripts.train_fleet \
    --parallel 1 \
    --buffer-days 0 \
    > "$TRAIN_LOG" 2>&1

RC=$?
echo "train_fleet exit code: $RC"
echo "===== $(date -u +%FT%TZ) weekly retrain end (rc=$RC) ====="
exit $RC
