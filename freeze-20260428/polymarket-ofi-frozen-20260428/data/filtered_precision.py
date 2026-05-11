#!/usr/bin/env python3
"""
Filtered prediction precision: high-confidence predictions vs target outcome.
Configurable via RUN_DIR and horizons.
"""
import json
import numpy as np
import pandas as pd
import polars as pl
import lightgbm as lgb
from sklearn.metrics import roc_auc_score
from pathlib import Path
from glob import glob

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
RUN_DIR = Path("/data/models/run_20260326_081107")
FEATURE_DIR = Path("/data/features_v3")
TEST_AFTER = "2026-02-15"
HORIZON_A_MS = 300_000    # 5-min (model's training horizon)
HORIZON_B_MS = 900_000    # 15-min (stretch target)
HORIZON_A_LABEL = "5min"
HORIZON_B_LABEL = "15min"
THRESHOLDS = [0.52, 0.53, 0.55, 0.57, 0.60]

# Load model + feature names
model = lgb.Booster(model_file=str(RUN_DIR / "model.lgb"))
with open(RUN_DIR / "feature_names.json") as f:
    feature_names = json.load(f)

symbol_map = {s: i for i, s in enumerate(SYMBOLS)}


def build_targets(cts, mid, horizon_ms):
    """Build binary target: 1 if mid_price[t+horizon] > mid_price[t]."""
    targets = np.full(len(cts), np.nan)
    j = 0
    for i in range(len(cts)):
        t = cts[i] + horizon_ms
        while j < len(cts) and cts[j] < t:
            j += 1
        if j < len(cts):
            targets[i] = 1.0 if mid[j] > mid[i] else 0.0
        j = max(j, i + 1)
    return targets


# Process each symbol and collect test predictions
all_preds = []
all_target_a = []
all_target_b = []

for sym in SYMBOLS:
    files = sorted(glob(str(FEATURE_DIR / sym / "*.parquet")))
    test_files = [f for f in files if Path(f).stem.split("_")[0] > TEST_AFTER]

    frames = []
    for f in test_files:
        frames.append(pl.read_parquet(f).to_pandas())

    if not frames:
        continue

    df = pd.concat(frames, ignore_index=True).sort_values("cts").reset_index(drop=True)

    # Build both targets
    cts = df["cts"].values
    mid = df["mid_price"].values
    ta = build_targets(cts, mid, HORIZON_A_MS)
    tb = build_targets(cts, mid, HORIZON_B_MS)

    # Only keep rows where both targets are valid
    valid = ~np.isnan(ta) & ~np.isnan(tb)
    df = df[valid].reset_index(drop=True)
    ta = ta[valid]
    tb = tb[valid]

    df["symbol_cat"] = symbol_map[sym]
    X = df[feature_names].values.astype(np.float64)
    pred = model.predict(X)

    all_preds.append(pred)
    all_target_a.append(ta.astype(int))
    all_target_b.append(tb.astype(int))

    print(f"  {sym}: {len(df):,} rows loaded")
    del df, frames

# Concatenate
preds = np.concatenate(all_preds)
target_a = np.concatenate(all_target_a)
target_b = np.concatenate(all_target_b)
pred_label = (preds > 0.5).astype(int)

print(f"\nTotal test rows (both targets valid): {len(preds):,}")
print(f"Baseline {HORIZON_A_LABEL} accuracy: {(pred_label == target_a).mean():.4f}")
print(f"Baseline {HORIZON_B_LABEL} accuracy: {(pred_label == target_b).mean():.4f}")
print(f"AUC {HORIZON_A_LABEL}: {roc_auc_score(target_a, preds):.4f}")
print(f"AUC {HORIZON_B_LABEL}: {roc_auc_score(target_b, preds):.4f}")

print()
print("=" * 90)
print(f"{'thresh':>8s}  {'n':>8s}  {'coverage':>10s}  {f'acc_{HORIZON_A_LABEL}':>10s}  {f'acc_{HORIZON_B_LABEL}':>10s}  {'pass':>10s}")
print("=" * 90)

for thresh in THRESHOLDS:
    high_conf = (preds > thresh) | (preds < (1 - thresh))
    n = high_conf.sum()
    if n == 0:
        print(f"{thresh:8.2f}  {0:8d}  {'0.0%':>10s}  {'N/A':>10s}  {'N/A':>10s}  {'N/A':>10s}")
        continue

    coverage = n / len(preds)
    acc_a = (pred_label[high_conf] == target_a[high_conf]).mean()
    acc_b = (pred_label[high_conf] == target_b[high_conf]).mean()

    # Check go criteria: primary horizon acc >= 51.5% AND coverage >= 5%
    passes = "YES ✓" if (acc_a >= 0.515 and coverage >= 0.05) else "NO"

    print(f"{thresh:8.2f}  {n:8,d}  {coverage:9.1%}  {acc_a:10.4f}  {acc_b:10.4f}  {passes:>10s}")

    # Per-symbol breakdown at this threshold
    for sym, p_arr, ta_arr, tb_arr in zip(
        SYMBOLS,
        all_preds, all_target_a, all_target_b,
    ):
        p_sym = p_arr
        hc = (p_sym > thresh) | (p_sym < (1 - thresh))
        n_sym = hc.sum()
        if n_sym >= 50:
            pl_sym = (p_sym > 0.5).astype(int)
            aa = (pl_sym[hc] == ta_arr[hc]).mean()
            ab = (pl_sym[hc] == tb_arr[hc]).mean()
            print(f"          {sym:10s}  n={n_sym:6,d}  acc_{HORIZON_A_LABEL}={aa:.4f}  acc_{HORIZON_B_LABEL}={ab:.4f}")

print("=" * 90)
print()
print(f"Criteria: acc_{HORIZON_A_LABEL} >= 0.515 AND coverage >= 5%")
print("If any row shows 'YES ✓' — that threshold defines the trading filter.")
print("If no row passes both — hypothesis is falsified.")
print("If no row passes both — hypothesis is falsified.")
