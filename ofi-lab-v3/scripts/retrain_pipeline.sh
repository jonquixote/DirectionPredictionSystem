#!/usr/bin/env bash
# ofi-lab-v3/scripts/retrain_pipeline.sh
#
# Orchestrate the v3 retrain pipeline. Idempotent step-by-step:
# each tool independently skips work that's already done.
#
# Usage:
#   scripts/retrain_pipeline.sh \
#     --symbol BTCUSDT \
#     --horizon 900 \
#     --feature-version v3 \
#     --train-end 2026-05-08 \
#     --train-days 330 \
#     --val-days 30 \
#     --test-days 14
set -euo pipefail

SYMBOL=""
HORIZON=""
FEATURE_VERSION="v3"
TRAIN_END=""
TRAIN_DAYS=330
VAL_DAYS=30
TEST_DAYS=14
FEATURE_DIR="${FEATURE_DIR:-/data/features_v3}"
KLINE_DIR="${KLINE_DIR:-/data/klines_1m}"
ORDERBOOK_DIR="${ORDERBOOK_DIR:-/data/orderbook}"
OUTPUT_DIR="${OUTPUT_DIR:-/data/models}"
MAX_CONSECUTIVE_MISSING="${MAX_CONSECUTIVE_MISSING:-2}"

while [ $# -gt 0 ]; do
  case "$1" in
    --symbol) SYMBOL="$2"; shift 2;;
    --horizon) HORIZON="$2"; shift 2;;
    --feature-version) FEATURE_VERSION="$2"; shift 2;;
    --train-end) TRAIN_END="$2"; shift 2;;
    --train-days) TRAIN_DAYS="$2"; shift 2;;
    --val-days) VAL_DAYS="$2"; shift 2;;
    --test-days) TEST_DAYS="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 1;;
  esac
done

if [ -z "${SYMBOL}" ] || [ -z "${HORIZON}" ] || [ -z "${TRAIN_END}" ]; then
  echo "Required: --symbol, --horizon, --train-end" >&2
  exit 1
fi

# 1. Download new kline data (idempotent)
echo "[1/5] download_klines for ${SYMBOL}"
python -m data.download_klines \
  --symbol "${SYMBOL}" --output-dir "${KLINE_DIR}" --through "${TRAIN_END}" || true

# 2. Download new orderbook data (idempotent)
echo "[2/5] download_orderbook for ${SYMBOL}"
python -m data.download_orderbook \
  --symbol "${SYMBOL}" --output-dir "${ORDERBOOK_DIR}" --through "${TRAIN_END}" || true

# 3. Build features for any new days
echo "[3/5] build_features_v3"
python -m data.build_features_v3 \
  --symbol "${SYMBOL}" \
  --kline-dir "${KLINE_DIR}" \
  --orderbook-dir "${ORDERBOOK_DIR}" \
  --output-dir "${FEATURE_DIR}" \
  --through "${TRAIN_END}" || true

# 4. Validate completeness — abort on excess gaps
echo "[4/5] completeness check"
python -c "
import sys
from datetime import date, timedelta
from validation.completeness import check_feature_coverage
end = date.fromisoformat('${TRAIN_END}')
start = end - timedelta(days=${TRAIN_DAYS} - 1 + ${VAL_DAYS} + ${TEST_DAYS})
rep = check_feature_coverage(
    feature_dir='${FEATURE_DIR}', symbol='${SYMBOL}',
    start=start, end=end + timedelta(days=${VAL_DAYS} + ${TEST_DAYS}),
)
print(f'coverage: {len(rep.present_days)}/{rep.total_days} ({rep.coverage_pct}%)')
print(f'max consecutive missing: {rep.max_consecutive_missing}')
if not rep.passes(max_consecutive_missing=${MAX_CONSECUTIVE_MISSING}):
    print(f'ABORT: max consecutive missing > ${MAX_CONSECUTIVE_MISSING}', file=sys.stderr)
    sys.exit(2)
"

# 5. Retrain
echo "[5/5] retrain"
python -m validation.retrain \
  --horizon "${HORIZON}" \
  --symbol "${SYMBOL}" \
  --feature-version "${FEATURE_VERSION}" \
  --train-end "${TRAIN_END}" \
  --train-days "${TRAIN_DAYS}" \
  --val-days "${VAL_DAYS}" \
  --test-days "${TEST_DAYS}" \
  --feature-dir "${FEATURE_DIR}" \
  --output-dir "${OUTPUT_DIR}"

echo "[done] retrain_pipeline complete"
echo "Next: review artifacts in ${OUTPUT_DIR} and add to model_registry.json"
