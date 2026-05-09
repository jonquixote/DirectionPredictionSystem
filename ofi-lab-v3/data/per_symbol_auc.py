#!/usr/bin/env python3
"""Per-symbol AUC breakdown using saved model."""
import json
import numpy as np
import pandas as pd
import polars as pl
import lightgbm as lgb
from sklearn.metrics import roc_auc_score
from pathlib import Path
from glob import glob

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
RUN_DIR = Path("/data/models/run_20260326_042710")
FEATURE_DIR = Path("/data/features_v3")
HORIZON_MS = 60_000
TEST_AFTER = "2026-02-15"

# Load model + feature names
model = lgb.Booster(model_file=str(RUN_DIR / "model.lgb"))
with open(RUN_DIR / "feature_names.json") as f:
    feature_names = json.load(f)

symbol_map = {s: i for i, s in enumerate(SYMBOLS)}

results = {}

for sym in SYMBOLS:
    files = sorted(glob(str(FEATURE_DIR / sym / "*.parquet")))
    # Only load test period files
    test_files = [f for f in files if Path(f).stem.split("_")[0] > TEST_AFTER]

    frames = []
    for f in test_files:
        frames.append(pl.read_parquet(f).to_pandas())

    if not frames:
        print(f"{sym}: no test files")
        continue

    df = pd.concat(frames, ignore_index=True).sort_values("cts").reset_index(drop=True)

    # Build targets
    cts = df["cts"].values
    mid = df["mid_price"].values
    targets = np.full(len(df), np.nan)
    j = 0
    for i in range(len(df)):
        t = cts[i] + HORIZON_MS
        while j < len(df) and cts[j] < t:
            j += 1
        if j < len(df):
            targets[i] = 1.0 if mid[j] > mid[i] else 0.0
        j = max(j, i + 1)

    df["target"] = targets
    df = df.dropna(subset=["target"]).reset_index(drop=True)
    df["target"] = df["target"].astype(int)
    df["symbol_cat"] = symbol_map[sym]

    X = df[feature_names].values.astype(np.float64)
    y = df["target"].values
    pred = model.predict(X)

    auc = roc_auc_score(y, pred)
    pos_rate = y.mean()

    # Recent 30 days
    df["date"] = pd.to_datetime(df["cts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    recent = df[df["date"] >= "2026-02-21"]
    if len(recent) >= 100:
        r_idx = recent.index
        auc_recent = roc_auc_score(y[r_idx], pred[r_idx])
    else:
        auc_recent = float("nan")

    results[sym] = {"auc": auc, "auc_recent": auc_recent, "n": len(df), "pos_rate": pos_rate}
    print(f"  {sym}: AUC={auc:.4f}  recent_AUC={auc_recent:.4f}  n={len(df):,}  target_rate={pos_rate:.3f}")

    del df, frames  # free memory

print()
print("=" * 60)
print("PER-SYMBOL AUC BREAKDOWN (H=60s, test > 2026-02-15)")
print("=" * 60)
for sym, r in results.items():
    print(f"  {sym:10s}  AUC={r['auc']:.4f}  recent={r['auc_recent']:.4f}  n={r['n']:,}")
# Weighted average
total_n = sum(r["n"] for r in results.values())
weighted_auc = sum(r["auc"] * r["n"] for r in results.values()) / total_n
weighted_recent = sum(r["auc_recent"] * r["n"] for r in results.values()) / total_n
print(f"  {'WEIGHTED':10s}  AUC={weighted_auc:.4f}  recent={weighted_recent:.4f}  n={total_n:,}")
print("=" * 60)
