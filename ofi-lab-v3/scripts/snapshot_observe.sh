#!/usr/bin/env bash
# scripts/snapshot_observe.sh — daily observation snapshot for consensus-gated pairs.
#
# Runs --recommend-premium analysis for ETHUSDT/300s and BTCUSDT/1800s,
# writes JSON outputs to /data/observe/<YYYY-MM-DD>_<sym>_<win>.json,
# and symlinks /data/observe/latest_<sym>_<win>.json.
#
# Install as cron (08:00 UTC daily) for 7-day paper observation period:
#   echo "0 8 * * * /home/johnny/ofi-lab-v3/scripts/snapshot_observe.sh" | sudo crontab -u johnny -
#
# Manual run: sudo -u johnny /home/johnny/ofi-lab-v3/scripts/snapshot_observe.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="${REPO_DIR}/.venv/bin/python3"
OBSERVE_DIR="/data/observe"
DB_PATH="${STORAGE_DB_PATH:-/data/v3.db}"
TODAY="$(date -u +%Y-%m-%d)"

mkdir -p "$OBSERVE_DIR"

run_snapshot() {
    local SYMBOL="$1"
    local WINDOW="$2"
    local FNAME="${TODAY}_${SYMBOL}_${WINDOW}.json"
    local OUTPATH="${OBSERVE_DIR}/${FNAME}"
    local LATEST="${OBSERVE_DIR}/latest_${SYMBOL}_${WINDOW}.json"

    echo "[snapshot_observe] $(date -u +%H:%M:%S) — running ${SYMBOL} ${WINDOW}s ..."

    STORAGE_DB_PATH="$DB_PATH" "$VENV_PYTHON" \
        "$REPO_DIR/scripts/run_analysis.py" \
        --recommend-premium \
        --symbol "$SYMBOL" \
        --window "$WINDOW" \
        --json-out "$OUTPATH" 2>&1 || {
        echo "[snapshot_observe] WARNING: run_analysis failed for ${SYMBOL}/${WINDOW}" >&2
        return 0  # Don't abort on failure; next pair may still succeed
    }

    ln -sf "$OUTPATH" "$LATEST"
    echo "[snapshot_observe] wrote ${OUTPATH} (symlink: ${LATEST})"
}

run_snapshot ETHUSDT 300
run_snapshot BTCUSDT 1800

echo "[snapshot_observe] done at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
