#!/usr/bin/env bash
# v3-daily-features.sh — Download orderbook data and build the full feature
# pipeline (v1 → v2 → v3), with smart backfill and training readiness report.
#
# Run daily at 00:30 UTC via cron:
#   30 0 * * * /home/johnny/ofi-lab-v3/deploy/cron/v3-daily-features.sh >> /data/logs/daily_features.log 2>&1
#
# Full pipeline per date:
#   1. download_orderbook  → /data/parquet/orderbook/{SYM}/{date}_{SYM}_ob200.parquet
#   2. build_features      → /data/features/{SYM}/{date}_{SYM}_features.parquet
#   3. add_rolling_features → /data/features_v2/{SYM}/{date}_{SYM}_features.parquet
#   4. build_features_v3   → /data/features_v3/{SYM}/{date}_{SYM}_features.parquet
#
# Each step skips dates that already have output files, so re-running is safe.
#
# Backfill logic:
#   - Finds the latest existing features_v3 file as the reference point
#   - Fills all missing dates from there to yesterday
#   - Caps at MAX_BACKFILL_DAYS when data exists (prevents runaway catch-up)
#   - Caps at MAX_COLD_START_DAYS on a fresh install with no data
#
# Training readiness:
#   - After pipeline completes, writes /data/training_ready.json with
#     date coverage, latest date, and days available per symbol.
#   - This file is read by the dashboard to show "Ready to retrain" status.
#   - Does NOT run training itself — that is a manual / on-demand operation.
#
set -euo pipefail

PYTHON="/home/johnny/ofi-lab-v3/.venv/bin/python3.12"
PROJECT="/home/johnny/ofi-lab-v3"
SYMBOLS="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT"
REF_SYMBOL="BTCUSDT"
FEATURES_V3_DIR="/data/features_v3"
READINESS_FILE="/data/training_ready.json"

# Backfill caps
MAX_BACKFILL_DAYS=7
MAX_COLD_START_DAYS=30

cd "${PROJECT}"

# ── Determine date range to process ──────────────────────────────────────────

YESTERDAY=$(date -u -d "yesterday" +%Y-%m-%d 2>/dev/null || date -u -v-1d +%Y-%m-%d)

# Find the latest existing feature file for the reference symbol
LATEST_FILE=$(ls -1 "${FEATURES_V3_DIR}/${REF_SYMBOL}/"*_${REF_SYMBOL}_features.parquet 2>/dev/null \
    | sort | tail -1 || true)

if [ -z "${LATEST_FILE}" ]; then
    # Cold start — no data at all
    MAX_DAYS=${MAX_COLD_START_DAYS}
    START_DATE=$(date -u -d "${YESTERDAY} - ${MAX_DAYS} days" +%Y-%m-%d 2>/dev/null \
        || date -u -v-${MAX_DAYS}d -j -f "%Y-%m-%d" "${YESTERDAY}" +%Y-%m-%d)
    echo "COLD START: No existing features found. Backfilling ${MAX_DAYS} days from ${START_DATE}"
else
    LATEST_DATE=$(basename "${LATEST_FILE}" | grep -oP '^\d{4}-\d{2}-\d{2}')
    START_DATE=$(date -u -d "${LATEST_DATE} + 1 day" +%Y-%m-%d 2>/dev/null \
        || date -u -v+1d -j -f "%Y-%m-%d" "${LATEST_DATE}" +%Y-%m-%d)

    # Cap at MAX_BACKFILL_DAYS to prevent runaway catch-up
    EARLIEST_ALLOWED=$(date -u -d "${YESTERDAY} - ${MAX_BACKFILL_DAYS} days" +%Y-%m-%d 2>/dev/null \
        || date -u -v-${MAX_BACKFILL_DAYS}d -j -f "%Y-%m-%d" "${YESTERDAY}" +%Y-%m-%d)

    if [[ "${START_DATE}" < "${EARLIEST_ALLOWED}" ]]; then
        echo "WARNING: Gap larger than ${MAX_BACKFILL_DAYS} days (since ${LATEST_DATE}). Capping at ${EARLIEST_ALLOWED}."
        START_DATE="${EARLIEST_ALLOWED}"
    fi

    if [[ "${START_DATE}" > "${YESTERDAY}" ]]; then
        TOTAL_FILES=$(ls "${FEATURES_V3_DIR}/${REF_SYMBOL}/"*_${REF_SYMBOL}_features.parquet 2>/dev/null | wc -l || echo 0)
        OLDEST_FILE=$(ls -1 "${FEATURES_V3_DIR}/${REF_SYMBOL}/"*_${REF_SYMBOL}_features.parquet 2>/dev/null | sort | head -1 | xargs -r basename 2>/dev/null | cut -d'_' -f1 || echo "unknown")
        cat > "${READINESS_FILE}" << EOF
{
  "ready": true,
  "generated_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "latest_feature_date": "${LATEST_DATE}",
  "oldest_feature_date": "${OLDEST_FILE}",
  "total_days_available": ${TOTAL_FILES},
  "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
  "feature_dir": "${FEATURES_V3_DIR}",
  "pipeline_note": "Run train_fleet.py with --train-end ${LATEST_DATE} --feature-dir ${FEATURES_V3_DIR} to retrain"
}
EOF
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Features up to date (${LATEST_DATE}). Total: ${TOTAL_FILES} days. Readiness: ${READINESS_FILE}"
        exit 0
    fi

    DAYS_TO_FILL=$(( ( $(date -u -d "${YESTERDAY}" +%s) - $(date -u -d "${START_DATE}" +%s) ) / 86400 + 1 ))
    echo "BACKFILL: Latest=${LATEST_DATE}. Filling ${DAYS_TO_FILL} day(s): ${START_DATE} → ${YESTERDAY}"
fi

# ── Process one date through the full 4-step pipeline ────────────────────────

process_date() {
    local DATE="$1"
    echo ""
    echo "============================================================"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Processing: ${DATE}"
    echo "============================================================"

    # Step 1: Download L2 orderbook data (skips if output exists)
    echo "[1/4] Downloading orderbook for ${DATE}..."
    ${PYTHON} -m data.download_orderbook \
        --symbols "${SYMBOLS}" \
        --start-date "${DATE}" \
        --end-date "${DATE}" \
        --output-dir /data/parquet/orderbook

    # Step 2: Build v1 base features from L2 data (skips if output exists)
    echo "[2/4] Building v1 features (OFI/MLOFI from L2)..."
    ${PYTHON} -m data.build_features \
        --start-date "${DATE}" \
        --end-date "${DATE}" \
        --l2-dir /data/parquet/orderbook \
        --output-dir /data/features

    # Step 3: Add rolling window features v1 → v2 (skips if output exists)
    echo "[3/4] Adding rolling features (v1 → v2)..."
    ${PYTHON} -m data.add_rolling_features \
        --input-dir /data/features \
        --output-dir /data/features_v2 \
        --symbols "${SYMBOLS}"

    # Step 4: Build enriched v3 features from v2 (skips if all 4 symbols exist)
    echo "[4/4] Building enriched v3 features (v2 → v3, cross-asset joins)..."
    ${PYTHON} -m data.build_features_v3 \
        --input-dir /data/features_v2 \
        --output-dir /data/features_v3 \
        --start-date "${DATE}" \
        --end-date "${DATE}"

    # Verify all 4 symbols produced output
    local ALL_OK=true
    for SYM in BTCUSDT ETHUSDT SOLUSDT XRPUSDT; do
        FILE="${FEATURES_V3_DIR}/${SYM}/${DATE}_${SYM}_features.parquet"
        if [ -f "${FILE}" ]; then
            SIZE=$(stat -c%s "${FILE}" 2>/dev/null || stat -f%z "${FILE}")
            echo "  ✓ ${SYM}: ${FILE} (${SIZE} bytes)"
        else
            echo "  ✗ ${SYM}: MISSING ${FILE}"
            ALL_OK=false
        fi
    done

    if [ "${ALL_OK}" = true ]; then
        echo "  ✅ ${DATE} complete"
    else
        echo "  ⚠️  ${DATE} had missing outputs — check logs above"
    fi
}

# ── Iterate from START_DATE to YESTERDAY ─────────────────────────────────────

CURRENT="${START_DATE}"
PROCESSED=0

while [[ "${CURRENT}" < "${YESTERDAY}" ]] || [[ "${CURRENT}" == "${YESTERDAY}" ]]; do
    process_date "${CURRENT}"
    PROCESSED=$((PROCESSED + 1))
    CURRENT=$(date -u -d "${CURRENT} + 1 day" +%Y-%m-%d 2>/dev/null \
        || date -u -v+1d -j -f "%Y-%m-%d" "${CURRENT}" +%Y-%m-%d)
done

# ── Write training readiness report ──────────────────────────────────────────

LATEST_BUILT="${YESTERDAY}"
TOTAL_FILES=$(ls "${FEATURES_V3_DIR}/${REF_SYMBOL}/"*_${REF_SYMBOL}_features.parquet 2>/dev/null | wc -l || echo 0)
OLDEST_FILE=$(ls -1 "${FEATURES_V3_DIR}/${REF_SYMBOL}/"*_${REF_SYMBOL}_features.parquet 2>/dev/null | sort | head -1 | xargs basename | grep -oP '^\d{4}-\d{2}-\d{2}' || echo "unknown")

cat > "${READINESS_FILE}" << EOF
{
  "ready": true,
  "generated_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "latest_feature_date": "${LATEST_BUILT}",
  "oldest_feature_date": "${OLDEST_FILE}",
  "total_days_available": ${TOTAL_FILES},
  "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
  "feature_dir": "${FEATURES_V3_DIR}",
  "pipeline_note": "Run train_fleet.py with --train-end ${LATEST_BUILT} --feature-dir ${FEATURES_V3_DIR} to retrain"
}
EOF

echo ""
echo "============================================================"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Daily feature pipeline complete"
echo "  Processed ${PROCESSED} date(s): ${START_DATE} → ${YESTERDAY}"
echo "  Total feature days available: ${TOTAL_FILES} (${OLDEST_FILE} → ${LATEST_BUILT})"
echo "  Training readiness: ${READINESS_FILE}"
echo "  Free disk: $(df -h / | tail -1 | awk '{print $4}')"
echo "============================================================"
