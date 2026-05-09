"""Fit a probability calibration map from prediction resolutions.

Uses the predictions ledger (ALL predictions, not just trades) for maximum
sample count. Joins prediction records to resolution records by prediction_id.
Bins resolved predictions by side-confidence and computes empirical win rate.

Side-confidence:
  - if pred_direction='up'   → conf = pred_proba
  - if pred_direction='down' → conf = 1 - pred_proba

Output bins above 0.50 only — calibrator clips below the lowest bin's
calibrated value rather than extrapolating into unfit territory.

Container-aware: reads from LOG_DIR (env KALSHI_LOG_DIR or default).
Symbol/model specific: only fits h300 BTCUSDT by default.
"""
import collections
import json
import os
import sys
from datetime import datetime, timezone

# ── Configuration ──────────────────────────────────────────────
LOG_DIR = os.environ.get("KALSHI_LOG_DIR", "/data/logs_model_a_clone")
PREDICTIONS_FILE = os.path.join(LOG_DIR, "predictions_h300.jsonl")
OUT = os.environ.get("KALSHI_CALIBRATION_PATH", "/data/calibration.json")

SYMBOL = "BTCUSDT"
MIN_SAMPLES_PER_BIN = 10   # trust threshold per bin (lowered from 15 for finer bins)
BIN_WIDTH = 0.01            # 1-hundredth bins for precise calibration

# When True, only use predictions on 15-min boundaries (Kalshi-aligned)
KALSHI_ONLY = "--kalshi-only" in sys.argv

if not os.path.exists(PREDICTIONS_FILE):
    print(f"ERROR: predictions file not found: {PREDICTIONS_FILE}")
    sys.exit(1)

# ── Pass 1: collect predictions and resolutions ────────────────
predictions = {}   # prediction_id → {pred_proba, pred_direction, ts_contract_open_ms}
resolutions = {}   # prediction_id → prediction_correct (bool)
n_lines = 0

with open(PREDICTIONS_FILE) as f:
    for line in f:
        n_lines += 1
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue

        rt = d.get("record_type")
        if rt == "prediction" and d.get("symbol") == SYMBOL:
            pid = d.get("prediction_id")
            if not pid:
                continue
            proba = d.get("pred_proba")
            direction = d.get("pred_direction")
            if proba is None or direction not in ("up", "down"):
                continue
            predictions[pid] = {
                "pred_proba": float(proba),
                "pred_direction": direction,
                "ts_contract_open_ms": d.get("ts_contract_open_ms", 0),
            }
        elif rt == "resolution":
            pid = d.get("prediction_id")
            if pid:
                resolutions[pid] = d.get("prediction_correct", False)

print(f"file: {PREDICTIONS_FILE}")
print(f"lines: {n_lines}")
print(f"{SYMBOL} predictions: {len(predictions)}")
print(f"resolutions: {len(resolutions)}")

# ── Join: prediction → resolution ──────────────────────────────
joined = []  # list of (side_conf, won_bool)
for pid, pred in predictions.items():
    if pid not in resolutions:
        continue

    # Optional: filter to 15-min boundaries only
    if KALSHI_ONLY:
        ts_open_s = pred["ts_contract_open_ms"] // 1000
        if ts_open_s % 900 != 0:
            continue

    proba = pred["pred_proba"]
    direction = pred["pred_direction"]
    # side_conf: model's confidence on the side it chose
    side_conf = proba if direction == "up" else 1.0 - proba
    won = 1 if resolutions[pid] else 0
    joined.append((side_conf, won))

boundary_label = "15-min boundaries only" if KALSHI_ONLY else "all 5-min boundaries"
print(f"\nresolved {SYMBOL} predictions ({boundary_label}): {len(joined)}")

if not joined:
    print("no samples to fit, exiting")
    sys.exit(1)

# ── Bin by side_conf ───────────────────────────────────────────
bin_data = collections.defaultdict(lambda: [0, 0])  # bin_center → [wins, total]
for conf, won in joined:
    if conf < 0.50:
        continue  # only fit the side we actually trade
    # Snap to nearest bin center
    bin_center = round(round(conf / BIN_WIDTH) * BIN_WIDTH, 4)
    bin_data[bin_center][0] += won
    bin_data[bin_center][1] += 1

# ── Build calibration map ─────────────────────────────────────
fitted = []
for bin_center in sorted(bin_data.keys()):
    wins, total = bin_data[bin_center]
    if total < MIN_SAMPLES_PER_BIN:
        continue
    cal = round(wins / total, 4)
    fitted.append({"raw": bin_center, "calibrated": cal, "n": total, "wins": wins})

print(f"\nfitted bins (raw → calibrated, n samples, wins):")
for b in fitted:
    print(f"  raw={b['raw']:.3f}  →  cal={b['calibrated']:.4f}   n={b['n']}, wins={b['wins']}")

if not fitted:
    print(f"no bins with >= {MIN_SAMPLES_PER_BIN} samples, exiting")
    sys.exit(1)

out_bins = [{"raw": b["raw"], "calibrated": b["calibrated"]} for b in fitted]
out = {
    "method": "binmap",
    "fit_at": datetime.now(timezone.utc).isoformat(),
    "source": PREDICTIONS_FILE,
    "symbol": SYMBOL,
    "model": "h300",
    "boundary_filter": "900s" if KALSHI_ONLY else "all",
    "min_samples_per_bin": MIN_SAMPLES_PER_BIN,
    "bin_width": BIN_WIDTH,
    "n_total_samples": len(joined),
    "bins": out_bins,
    "_diagnostics": {
        "fitted_bins_with_counts": fitted,
    },
}

with open(OUT, "w") as f:
    json.dump(out, f, indent=2)

print(f"\nwrote {OUT} ({len(out_bins)} bins)")
