#!/usr/bin/env python3
"""
Retrain H60 V3 — debiased OFI model.

Three changes vs V1:
  1. Replace mid_price with mid_price_dev_30d (30-day EWM z-score)
  2. Add is_unbalance=True to LightGBM
  3. 90-day rolling training window with 70/15/15 split

Usage:
    python -m validation.retrain_v3 --horizon 60 --output-dir /data/models_v3
"""
import sys
import os
import json
import time
import datetime
import logging
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import lightgbm as lgb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sklearn.metrics import roc_auc_score, log_loss, brier_score_loss
from sklearn.calibration import calibration_curve

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import CONFIG
from validation.leakage_check import check_leakage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("retrain_v3")

# ── V3 Feature list ──────────────────────────────────────────
# mid_price → replaced by mid_price_dev_30d
# spread → dropped (price-level dependent)
V3_FEATURE_COLS = [
    "mlofi", "ofi", "mid_price_dev_30d", "relative_spread",
    "vwap_deviation", "roll",
    "mlofi_1", "mlofi_2", "mlofi_3", "mlofi_4", "mlofi_5",
    "mlofi_6", "mlofi_7", "mlofi_8", "mlofi_9", "mlofi_10",
    "mlofi_30s_mean", "mlofi_60s_mean",
    "ofi_30s_mean", "ofi_60s_mean",
    "mlofi_30s_std", "mlofi_60s_std",
    "ofi_60s_std",
    "spread_5m_pct",
    "vwap_2m_deviation", "vwap_dev_velocity", "vwap_dev_30s_std",
    "mlofi_momentum",
    "btc_vwap_deviation", "btc_mlofi_30s_mean", "eth_mlofi_30s_mean",
    "symbol_cat",
]

# V3 splits: 90-day window ending 2026-03-23
# Train: Jan 1 – Mar 4 (~63 days)
# Val:   Mar 5 – Mar 16 (~12 days)
# Test:  Mar 17 – Mar 23 (7 days)
TRAIN_START = "2026-01-01"
TRAIN_END = "2026-03-04"
VAL_END = "2026-03-16"
# Test: everything after VAL_END

# V3 LightGBM params
LGBM_PARAMS = {
    "n_estimators": 500,
    "learning_rate": 0.05,
    "max_depth": 6,
    "num_leaves": 63,
    "min_child_samples": 100,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "is_unbalance": True,  # ← Change 2: balance classes
    "random_state": 42,
    "n_jobs": -1,
    "verbose": -1,
}

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
EWM_SPAN = 43200  # 30 days × 1440 minutes
EWM_WARMUP_DAYS = 30  # drop first 30 days of EWM


def load_and_add_mid_price_dev(feature_dir: Path) -> pd.DataFrame:
    """
    Load all v3 features, compute mid_price_dev_30d per symbol.

    mid_price_dev_30d = (mid_price - ewm_mean) / ewm_std

    The EWM is computed over the full history (from 2025-04-29) to ensure
    stable estimates, but only rows from TRAIN_START onward are kept.
    """
    all_dfs = []

    for sym in SYMBOLS:
        sym_dir = feature_dir / sym
        if not sym_dir.exists():
            raise FileNotFoundError(f"No feature directory for {sym}: {sym_dir}")

        files = sorted(sym_dir.glob("*_features.parquet"))
        logger.info("  %s: %d files", sym, len(files))

        # Load all files (need full history for EWM warmup)
        sym_df = pl.concat([pl.read_parquet(f) for f in files]).sort("cts")

        # Convert to pandas for EWM (polars EWM API is limited)
        pdf = sym_df.to_pandas()
        pdf["symbol"] = sym

        # Compute 30-day EWM on mid_price
        ewm_mean = pdf["mid_price"].ewm(span=EWM_SPAN, adjust=False).mean()
        ewm_std = pdf["mid_price"].ewm(span=EWM_SPAN, adjust=False).std()

        # Z-score
        pdf["mid_price_dev_30d"] = np.where(
            ewm_std > 0,
            (pdf["mid_price"] - ewm_mean) / ewm_std,
            0.0,
        )

        # Log EWM stats
        logger.info(
            "    EWM stats: mean range [%.0f, %.0f], std range [%.1f, %.1f]",
            ewm_mean.min(), ewm_mean.max(),
            ewm_std.dropna().min(), ewm_std.dropna().max(),
        )
        logger.info(
            "    mid_price_dev_30d range: [%.3f, %.3f], mean=%.3f",
            pdf["mid_price_dev_30d"].min(),
            pdf["mid_price_dev_30d"].max(),
            pdf["mid_price_dev_30d"].mean(),
        )

        all_dfs.append(pdf)

    df = pd.concat(all_dfs, ignore_index=True)

    # Drop rows where EWM hasn't warmed up (first 30 days per symbol)
    df["date"] = pd.to_datetime(df["cts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")

    # Get the date 30 days after the first date per symbol
    min_dates = df.groupby("symbol")["date"].min().to_dict()
    for sym, min_date in min_dates.items():
        warmup_end = (pd.Timestamp(min_date) + pd.Timedelta(days=EWM_WARMUP_DAYS)).strftime("%Y-%m-%d")
        before = len(df[df["symbol"] == sym])
        df = df[~((df["symbol"] == sym) & (df["date"] < warmup_end))]
        after = len(df[df["symbol"] == sym])
        logger.info("  %s: dropped %d EWM warmup rows (before %s)", sym, before - after, warmup_end)

    logger.info("Total rows after EWM warmup drop: %d", len(df))
    return df


def correlation_check(df: pd.DataFrame) -> dict:
    """
    Step 2: Check pairwise correlations between mid_price_dev_30d and OFI features.
    Flag anything above 0.6.
    """
    check_cols = ["mid_price_dev_30d", "mlofi", "ofi", "relative_spread"]
    corr = df[check_cols].corr()

    logger.info("=" * 50)
    logger.info("CORRELATION CHECK (mid_price_dev_30d vs OFI features)")
    logger.info("=" * 50)

    flagged = {}
    for col in ["mlofi", "ofi", "relative_spread"]:
        r = corr.loc["mid_price_dev_30d", col]
        flag = "⚠️  FLAGGED" if abs(r) > 0.6 else "OK"
        logger.info("  mid_price_dev_30d × %s: r=%.4f  %s", col, r, flag)
        if abs(r) > 0.6:
            flagged[col] = r

    logger.info("=" * 50)
    return flagged


def build_targets(df: pd.DataFrame, horizon_seconds: int) -> pd.DataFrame:
    """Build binary target: 1 if price goes up within horizon, 0 otherwise."""
    horizon_ms = horizon_seconds * 1000
    df = df.sort_values(["symbol", "cts"]).reset_index(drop=True)

    targets = []
    for sym in df["symbol"].unique():
        mask = df["symbol"] == sym
        sym_df = df[mask].copy()
        mid = sym_df["mid_price"].values
        cts = sym_df["cts"].values

        target = np.full(len(sym_df), np.nan)
        j = 0
        for i in range(len(sym_df)):
            while j < len(sym_df) and cts[j] - cts[i] < horizon_ms:
                j += 1
            if j < len(sym_df):
                target[i] = 1.0 if mid[j] > mid[i] else 0.0

        sym_df["target"] = target
        targets.append(sym_df)

    df = pd.concat(targets, ignore_index=True)
    valid = df["target"].notna()
    dropped = (~valid).sum()
    df = df[valid].reset_index(drop=True)
    df["target"] = df["target"].astype(int)

    logger.info("Targets: %d valid, %d dropped (%.1f%% — end-of-window)",
                len(df), dropped, 100 * dropped / max(len(df) + dropped, 1))
    return df


def main():
    parser = argparse.ArgumentParser(description="V3 retrain — debiased OFI model")
    parser.add_argument("--horizon", type=int, default=60)
    parser.add_argument("--feature-dir", type=str, default="/data/features_v3")
    parser.add_argument("--output-dir", type=str, default="/data/models_v3")
    parser.add_argument("--correlation-only", action="store_true",
                        help="Run correlation check only, no training")
    args = parser.parse_args()

    feature_dir = Path(args.feature_dir)
    models_dir = Path(args.output_dir)
    horizon = args.horizon

    logger.info("=" * 60)
    logger.info("V3 Retrain — Debiased OFI Model")
    logger.info("  Horizon:     %ds", horizon)
    logger.info("  Features:    %s", feature_dir)
    logger.info("  Output:      %s", models_dir)
    logger.info("  Train:       %s to %s", TRAIN_START, TRAIN_END)
    logger.info("  Val:         %s to %s", TRAIN_END, VAL_END)
    logger.info("  Test:        after %s", VAL_END)
    logger.info("  is_unbalance: True")
    logger.info("  EWM span:    %d (30 days)", EWM_SPAN)
    logger.info("=" * 60)

    # ── Phase 1: Load data and compute mid_price_dev_30d ──
    t0 = time.time()
    df = load_and_add_mid_price_dev(feature_dir)

    # ── Phase 2: Correlation check ──
    # Filter to 90-day window for correlation check
    df_90d = df[df["date"] >= TRAIN_START].copy()
    logger.info("90-day window rows: %d", len(df_90d))

    flagged = correlation_check(df_90d)
    if flagged:
        logger.warning("Correlations above 0.6 detected: %s", flagged)
        logger.warning("Proceeding anyway — review before deployment")

    if args.correlation_only:
        logger.info("Correlation check complete. Exiting (--correlation-only).")
        return

    # ── Phase 3: Build targets ──
    df_90d = build_targets(df_90d, horizon)

    # ── Phase 4: Encode features ──
    symbol_map = {sym: i for i, sym in enumerate(SYMBOLS)}
    df_90d["symbol_cat"] = df_90d["symbol"].map(symbol_map).astype(int)

    # ── Phase 5: Split ──
    df_train = df_90d[df_90d["date"] <= TRAIN_END].reset_index(drop=True)
    df_val = df_90d[(df_90d["date"] > TRAIN_END) & (df_90d["date"] <= VAL_END)].reset_index(drop=True)
    df_test = df_90d[df_90d["date"] > VAL_END].reset_index(drop=True)

    logger.info("Split: train=%d, val=%d, test=%d", len(df_train), len(df_val), len(df_test))

    for name, subset in [("train", df_train), ("val", df_val), ("test", df_test)]:
        pos = subset["target"].mean()
        logger.info("  %s target rate: %.4f (%.1f%% up)", name, pos, 100 * pos)

    feature_names = V3_FEATURE_COLS.copy()

    # Verify all features exist
    for col in feature_names:
        if col not in df_train.columns:
            raise ValueError(f"Feature {col} not found in training data. Available: {list(df_train.columns)}")

    # ── Phase 6: Train final model on train+val ──
    df_trainval = pd.concat([df_train, df_val], ignore_index=True)

    X_trainval = df_trainval[feature_names].values
    y_trainval = df_trainval["target"].values
    X_test = df_test[feature_names].values
    y_test = df_test["target"].values

    logger.info("Training final model...")
    model = lgb.LGBMClassifier(**LGBM_PARAMS)
    model.fit(X_trainval, y_trainval)

    y_pred = model.predict_proba(X_test)[:, 1]

    # Metrics
    auc_full = roc_auc_score(y_test, y_pred)
    logloss = log_loss(y_test, y_pred)
    brier = brier_score_loss(y_test, y_pred)
    accuracy = float(np.mean((y_pred > 0.5).astype(int) == y_test))

    # Direction split on test set
    pred_up = (y_pred > 0.5).sum()
    pred_down = (y_pred <= 0.5).sum()
    up_pct = pred_up / len(y_pred) * 100

    logger.info("=" * 50)
    logger.info("V3 FINAL TEST SET RESULTS")
    logger.info("  AUC-ROC:      %.4f  (%d rows)", auc_full, len(y_test))
    logger.info("  Log-loss:     %.4f", logloss)
    logger.info("  Brier:        %.4f", brier)
    logger.info("  Accuracy:     %.4f", accuracy)
    logger.info("  Predictions:  Up=%d (%.1f%%)  Down=%d (%.1f%%)",
                pred_up, up_pct, pred_down, 100 - up_pct)
    logger.info("  V2 baseline:  AUC=0.5287 (must beat)")
    logger.info("  Target:       AUC > 0.535")
    logger.info("=" * 50)

    # Leakage check
    leakage = check_leakage(accuracy, context="v3_final_test")
    if leakage["flagged"]:
        logger.warning("LEAKAGE FLAG: %s", leakage["message"])

    # Gate check
    gate_passed = auc_full >= 0.535
    if gate_passed:
        logger.info("✓ V3 GATE PASSED: AUC %.4f >= 0.535", auc_full)
    else:
        logger.warning("✗ V3 GATE FAILED: AUC %.4f < 0.535", auc_full)

    # ── Phase 7: Save artifacts ──
    run_ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = models_dir / f"run_{run_ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Model
    model_path = run_dir / "model.lgb"
    model.booster_.save_model(str(model_path))
    logger.info("Model saved: %s", model_path)

    # Feature names
    with open(run_dir / "feature_names.json", "w") as f:
        json.dump(feature_names, f, indent=2)

    # Metrics
    metrics = {
        "auc_full": auc_full,
        "log_loss": logloss,
        "brier_score": brier,
        "accuracy": accuracy,
        "pred_up_count": int(pred_up),
        "pred_down_count": int(pred_down),
        "pred_up_pct": round(up_pct, 2),
        "gate_passed": gate_passed,
        "gate_threshold": 0.535,
        "train_size": len(df_trainval),
        "test_size": len(df_test),
        "horizon_seconds": horizon,
        "is_unbalance": True,
        "train_window_days": 90,
        "train_start": TRAIN_START,
        "train_end": TRAIN_END,
        "val_end": VAL_END,
        "ewm_span": EWM_SPAN,
        "features_removed": ["mid_price", "spread"],
        "features_added": ["mid_price_dev_30d"],
        "correlation_flagged": flagged,
        "v2_baseline_auc": 0.5287,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with open(run_dir / "metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    # Config
    with open(run_dir / "config_snapshot.json", "w") as f:
        json.dump({
            "FEATURE_COLS": feature_names,
            "LGBM_PARAMS": LGBM_PARAMS,
            "TRAIN_START": TRAIN_START,
            "TRAIN_END": TRAIN_END,
            "VAL_END": VAL_END,
            "EWM_SPAN": EWM_SPAN,
        }, f, indent=2)

    # Calibration plot
    try:
        prob_true, prob_pred = calibration_curve(y_test, y_pred, n_bins=10, strategy="uniform")
        fig, ax = plt.subplots(1, 1, figsize=(8, 6))
        ax.plot(prob_pred, prob_true, "s-", label="V3 LightGBM")
        ax.plot([0, 1], [0, 1], "k--", label="Perfect")
        ax.set_xlabel("Predicted probability")
        ax.set_ylabel("Observed frequency")
        ax.set_title(f"V3 Calibration (AUC={auc_full:.4f}, H={horizon}s)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.savefig(run_dir / "calibration.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
    except Exception as e:
        logger.warning("Failed to generate calibration plot: %s", e)

    # SHAP
    try:
        import shap
        explainer = shap.TreeExplainer(model)
        sample_size = min(5000, len(X_test))
        rng = np.random.default_rng(42)
        sample_idx = rng.choice(len(X_test), sample_size, replace=False)
        shap_values = explainer.shap_values(X_test[sample_idx])
        if isinstance(shap_values, list):
            shap_vals = shap_values[1]
        else:
            shap_vals = shap_values

        fig, ax = plt.subplots(figsize=(10, 8))
        shap.summary_plot(
            shap_vals, X_test[sample_idx],
            feature_names=feature_names, show=False,
        )
        fig = plt.gcf()
        fig.savefig(run_dir / "shap_importance.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        logger.info("SHAP plot saved")
    except Exception as e:
        logger.warning("Failed to generate SHAP plot: %s", e)

    # Symlink
    link_path = models_dir / "latest_h60_v3"
    if link_path.is_symlink() or link_path.exists():
        link_path.unlink()
    link_path.symlink_to(run_dir.name)
    logger.info("Symlink: latest_h60_v3 -> %s", run_dir.name)

    elapsed = time.time() - t0
    logger.info("=" * 60)
    logger.info("V3 FINISHED in %.1f minutes", elapsed / 60)
    logger.info("Results dir: %s", run_dir)
    logger.info("AUC=%.4f  Accuracy=%.4f  Up/Down=%d/%d (%.1f%%/%.1f%%)",
                auc_full, accuracy, pred_up, pred_down, up_pct, 100 - up_pct)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
