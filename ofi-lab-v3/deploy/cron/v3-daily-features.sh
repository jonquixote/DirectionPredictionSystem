#!/usr/bin/env bash
# v3-daily-features.sh — Download yesterday's orderbook data and build features.
#
# Run daily at 00:30 UTC via cron:
#   30 0 * * * /home/johnny/ofi-lab-v3/deploy/cron/v3-daily-features.sh >> /data/logs/daily_features.log 2>&1
#
# Pipeline: download_orderbook → build_features → add_rolling_features → build_features_v3
#
set -euo pipefail

PYTHON="/home/johnny/ofi-lab-v3/.venv/bin/python3.12"
PROJECT="/home/johnny/ofi-lab-v3"
SYMBOLS="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT"

# Yesterday's date (UTC)
YESTERDAY=$(date -u -d "yesterday" +%Y-%m-%d 2>/dev/null || date -u -v-1d +%Y-%m-%d)

echo "============================================================"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Daily feature pipeline: ${YESTERDAY}"
echo "============================================================"

cd "${PROJECT}"

# Step 1: Download L2 orderbook data
echo "[1/4] Downloading orderbook data for ${YESTERDAY}..."
${PYTHON} -m data.download_orderbook \
    --symbols "${SYMBOLS}" \
    --start-date "${YESTERDAY}" \
    --end-date "${YESTERDAY}" \
    --output-dir /data/parquet/orderbook

# Step 2: Build v1 features from orderbook
echo "[2/4] Building v1 features..."
${PYTHON} -m data.build_features \
    --start-date "${YESTERDAY}" \
    --end-date "${YESTERDAY}" \
    --l2-dir /data/parquet/orderbook \
    --output-dir /data/features

# Step 3: Add rolling features (v1 → v2)
echo "[3/4] Adding rolling features (v2)..."
${PYTHON} -m data.add_rolling_features \
    --input-dir /data/features \
    --output-dir /data/features_v2

# Step 4: Build enriched v3 features (v2 → v3)
echo "[4/4] Building enriched v3 features..."
${PYTHON} -m data.build_features_v3 \
    --input-dir /data/features_v2 \
    --output-dir /data/features_v3 \
    --start-date "${YESTERDAY}" \
    --end-date "${YESTERDAY}"

# Verify output
for SYM in BTCUSDT ETHUSDT SOLUSDT XRPUSDT; do
    FILE="/data/features_v3/${SYM}/${YESTERDAY}_${SYM}_features.parquet"
    if [ -f "${FILE}" ]; then
        SIZE=$(stat -c%s "${FILE}" 2>/dev/null || stat -f%z "${FILE}")
        echo "  ✓ ${SYM}: ${FILE} (${SIZE} bytes)"
    else
        echo "  ✗ ${SYM}: MISSING ${FILE}"
    fi
done

echo "============================================================"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Daily feature pipeline complete"
echo "============================================================"
