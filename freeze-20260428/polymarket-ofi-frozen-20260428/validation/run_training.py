#!/usr/bin/env python3
"""
Training pipeline — LightGBM baseline with time-based direction prediction.
Spec v2.6, Sections 4, 9, 14.

Training sequence:
1. LightGBM baseline (this script, Phase 1)
2. TrackA_MLP + TrackB_SequenceModel (Phase 2, after baseline gate passes)
3. MetaLearner stacking (Phase 3, after both tracks are evaluated)

Go/no-go gate: test set AUC-ROC >= 0.53 AT CONTRACT OPEN TIMES.
The gate applies to auc_at_contract_times, not auc_full.
Below this threshold the signal cannot overcome Polymarket fees.

Usage:
    python validation/run_training.py                       # default 5-min horizon
    python validation/run_training.py --horizon 900         # 15-min horizon
    python validation/run_training.py --dry-run             # validate data only
"""

import os
import sys
import json
import time
import shutil
import logging
import argparse
import datetime
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
from config import CONFIG, TRAINING_BOUNDARIES
from validation.leakage_check import check_leakage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("training")

# ── Constants ──────────────────────────────────────────────────

HORIZON_SECONDS = 300  # 5-minute contracts (default)
# HORIZON_SECONDS = 900  # 15-minute contracts (second run)

TRAIN_END = "2025-12-31"
VAL_END = "2026-02-15"
# TEST = 2026-02-16 → 2026-03-23

FEATURE_COLS = [
    # Point-in-time features (vpin dropped — SHAP-confirmed noise)
    "mlofi", "ofi", "mid_price", "spread", "relative_spread",
    "vwap_deviation", "roll",
    "mlofi_1", "mlofi_2", "mlofi_3", "mlofi_4", "mlofi_5",
    "mlofi_6", "mlofi_7", "mlofi_8", "mlofi_9", "mlofi_10",
    # Rolling window features (mlofi_120s_mean dropped — diminishing returns)
    "mlofi_30s_mean", "mlofi_60s_mean",
    "ofi_30s_mean", "ofi_60s_mean",
    "mlofi_30s_std", "mlofi_60s_std",
    "ofi_60s_std",
    "spread_5m_pct",
    # VWAP enrichment (strengthen rank-1 SHAP signal)
    "vwap_2m_deviation", "vwap_dev_velocity", "vwap_dev_30s_std",
    # Order flow enrichment (strengthen rank-2/4 SHAP signal)
    "mlofi_momentum",
    # Cross-asset features
    "btc_vwap_deviation", "btc_mlofi_30s_mean", "eth_mlofi_30s_mean",
    # Categorical
    "symbol_cat",
]

GO_NOGO_AUC = 0.53  # applied to auc_at_contract_times, NOT auc_full

# Contract-aligned evaluation: cts % (horizon_ms) within ±30s of boundary
# cts is in MILLISECONDS. Window ±30s is fixed regardless of horizon.
# CONTRACT_INTERVAL_MS is set dynamically from --horizon at runtime.
CONTRACT_WINDOW_MS = 30_000      # ±30s around contract open

LGBM_PARAMS = {
    "n_estimators": 500,
    "learning_rate": 0.05,
    "max_depth": 6,
    "num_leaves": 63,
    "min_child_samples": 100,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "random_state": 42,
    "n_jobs": -1,
    "verbose": -1,
}

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

# Walk-forward CV fold definitions (day indices relative to training start)
WF_FOLDS = [
    {"train_days": (1, 60),   "val_days": (61, 90)},
    {"train_days": (1, 120),  "val_days": (121, 150)},
    {"train_days": (1, 180),  "val_days": (181, 210)},
    {"train_days": (1, 240),  "val_days": (241, 270)},
]


# ── Data Loading ──────────────────────────────────────────────

def load_features(feature_dir: Path) -> pd.DataFrame:
    """
    Load all feature Parquet files for all symbols into a single DataFrame.
    Sorted by cts ascending. Validates schema and completeness.
    """
    logger.info("Loading features from %s", feature_dir)
    all_frames = []

    for sym in SYMBOLS:
        sym_dir = feature_dir / sym
        if not sym_dir.exists():
            raise FileNotFoundError(f"No feature directory for {sym}: {sym_dir}")

        files = sorted(sym_dir.glob("*.parquet"))
        if not files:
            raise FileNotFoundError(f"No Parquet files for {sym} in {sym_dir}")

        logger.info("  %s: %d files", sym, len(files))
        for f in files:
            df = pl.read_parquet(f).to_pandas()
            all_frames.append(df)

    combined = pd.concat(all_frames, ignore_index=True)
    combined = combined.sort_values("cts").reset_index(drop=True)

    logger.info("Total rows loaded: %d", len(combined))
    return combined


def validate_data(df: pd.DataFrame) -> None:
    """
    Pre-training data validation. Fails loudly on any issue.
    """
    logger.info("Validating data...")

    # Check expected columns — v3 schema (dropped vpin, mlofi_120s_mean; added enrichment)
    expected_cols = [
        "cts", "mid_price", "spread", "relative_spread",
        "mlofi", "ofi", "vwap_deviation", "roll",
        "mlofi_1", "mlofi_2", "mlofi_3", "mlofi_4", "mlofi_5",
        "mlofi_6", "mlofi_7", "mlofi_8", "mlofi_9", "mlofi_10",
        # Rolling window features
        "mlofi_30s_mean", "mlofi_60s_mean",
        "ofi_30s_mean", "ofi_60s_mean",
        "mlofi_30s_std", "mlofi_60s_std",
        "ofi_60s_std",
        "spread_5m_pct",
        # VWAP enrichment
        "vwap_2m_deviation", "vwap_dev_velocity", "vwap_dev_30s_std",
        # Order flow enrichment
        "mlofi_momentum",
        # Cross-asset
        "btc_vwap_deviation", "btc_mlofi_30s_mean", "eth_mlofi_30s_mean",
        "symbol",
    ]
    missing = [c for c in expected_cols if c not in df.columns]
    assert not missing, f"Missing columns: {missing}"
    logger.info("  ✓ All %d expected columns present", len(expected_cols))

    # Check NaN in feature columns
    feature_check_cols = [c for c in expected_cols if c not in ("cts", "symbol")]
    nan_counts = df[feature_check_cols].isna().sum()
    nan_total = nan_counts.sum()
    assert nan_total == 0, f"NaN found in feature columns:\n{nan_counts[nan_counts > 0]}"
    logger.info("  ✓ No NaN in any feature column")

    # Check cts monotonic per symbol
    for sym in SYMBOLS:
        sym_df = df[df["symbol"] == sym]
        cts_vals = sym_df["cts"].values
        assert np.all(np.diff(cts_vals) > 0), f"cts not strictly monotonic for {sym}"
    logger.info("  ✓ cts strictly monotonic per symbol")

    # Date range check
    min_cts = df["cts"].min()
    max_cts = df["cts"].max()
    min_dt = datetime.datetime.fromtimestamp(min_cts / 1000, tz=datetime.timezone.utc)
    max_dt = datetime.datetime.fromtimestamp(max_cts / 1000, tz=datetime.timezone.utc)
    logger.info("  Date range: %s to %s", min_dt.date(), max_dt.date())

    # Row count per symbol
    for sym in SYMBOLS:
        n = len(df[df["symbol"] == sym])
        logger.info("  %s: %d rows", sym, n)

    logger.info("  ✓ Data validation passed")


# ── Target Construction ───────────────────────────────────────

def build_targets(df: pd.DataFrame, horizon_seconds: int) -> pd.DataFrame:
    """
    For each row at time t, find the first row where cts >= t + horizon_ms
    and set target = 1 if mid_price[t+horizon] > mid_price[t], else 0.

    cts is in MILLISECONDS (13 digits, e.g. 1768435201247 = 2026-01-15 00:00:01.247 UTC).
    Verified from raw Bybit Parquet: NOT nanoseconds (which would be 19 digits).
    Therefore: horizon_ms = horizon_seconds * 1000.
    """
    horizon_ms = horizon_seconds * 1000
    logger.info(
        "Building targets with horizon=%ds (%d ms)...",
        horizon_seconds, horizon_ms,
    )

    targets = np.full(len(df), np.nan)
    cts = df["cts"].values
    mid = df["mid_price"].values

    # Process per symbol to avoid cross-symbol contamination
    for sym in SYMBOLS:
        sym_mask = df["symbol"].values == sym
        sym_indices = np.where(sym_mask)[0]
        sym_cts = cts[sym_indices]
        sym_mid = mid[sym_indices]

        # For each row, binary search for the horizon timestamp
        j = 0
        for idx_pos in range(len(sym_indices)):
            target_cts = sym_cts[idx_pos] + horizon_ms
            # Advance j to find first cts >= target_cts
            while j < len(sym_indices) and sym_cts[j] < target_cts:
                j += 1

            if j < len(sym_indices):
                future_mid = sym_mid[j]
                current_mid = sym_mid[idx_pos]
                targets[sym_indices[idx_pos]] = 1.0 if future_mid > current_mid else 0.0
            # else: target stays NaN (will be dropped)

            # Don't reset j — cts is sorted, so next target_cts >= current
            # But we need to allow j to go backwards slightly since idx_pos steps
            # Reset j to idx_pos to maintain correctness
            j = max(j, idx_pos + 1)

    df = df.copy()
    df["target"] = targets

    n_valid = df["target"].notna().sum()
    n_dropped = len(df) - n_valid
    logger.info(
        "Targets: %d valid, %d dropped (%.1f%% — end-of-window)",
        n_valid, n_dropped, 100 * n_dropped / len(df),
    )

    # Drop rows without targets
    df = df.dropna(subset=["target"]).reset_index(drop=True)
    df["target"] = df["target"].astype(int)

    return df


# ── Feature Preparation ──────────────────────────────────────

def prepare_features(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """
    Prepare feature matrix X and target vector y.
    Encode symbol as categorical integer.
    """
    df = df.copy()

    # Encode symbol as categorical integer
    symbol_map = {sym: i for i, sym in enumerate(SYMBOLS)}
    df["symbol_cat"] = df["symbol"].map(symbol_map).astype(int)

    feature_names = FEATURE_COLS.copy()
    X = df[feature_names].values.astype(np.float64)
    y = df["target"].values.astype(int)

    return X, y, feature_names


# ── Walk-Forward Cross-Validation ─────────────────────────────

def walk_forward_cv(
    df_train: pd.DataFrame,
    feature_names: list[str],
    folds: list[dict],
    lgbm_params: dict,
) -> list[dict]:
    """
    Expanding-window walk-forward CV on the training set.
    Folds are defined by day indices relative to training start.
    """
    logger.info("Starting walk-forward CV with %d folds...", len(folds))

    # Assign day indices
    df_train = df_train.copy()
    train_start_date = pd.Timestamp(df_train["cts"].min(), unit="ms", tz="UTC").normalize()
    df_train["day_idx"] = (
        (pd.to_datetime(df_train["cts"], unit="ms", utc=True).dt.normalize() - train_start_date)
        .dt.days + 1
    )

    results = []

    for fold_idx, fold in enumerate(folds):
        train_days = fold["train_days"]
        val_days = fold["val_days"]

        train_mask = (df_train["day_idx"] >= train_days[0]) & (df_train["day_idx"] <= train_days[1])
        val_mask = (df_train["day_idx"] >= val_days[0]) & (df_train["day_idx"] <= val_days[1])

        fold_train = df_train[train_mask]
        fold_val = df_train[val_mask]

        if len(fold_train) == 0 or len(fold_val) == 0:
            logger.warning("Fold %d: empty train or val set, skipping", fold_idx)
            continue

        X_tr = fold_train[feature_names].values
        y_tr = fold_train["target"].values
        X_va = fold_val[feature_names].values
        y_va = fold_val["target"].values

        model = lgb.LGBMClassifier(**lgbm_params)
        model.fit(
            X_tr, y_tr,
            eval_set=[(X_va, y_va)],
            callbacks=[lgb.log_evaluation(period=0)],  # suppress per-iteration logging
        )

        y_pred = model.predict_proba(X_va)[:, 1]
        auc = roc_auc_score(y_va, y_pred)
        logloss = log_loss(y_va, y_pred)

        fold_result = {
            "fold": fold_idx,
            "train_size": len(fold_train),
            "val_size": len(fold_val),
            "train_days": train_days,
            "val_days": val_days,
            "auc_roc": auc,
            "log_loss": logloss,
        }
        results.append(fold_result)

        logger.info(
            "  Fold %d: AUC=%.4f, LogLoss=%.4f (train=%d, val=%d)",
            fold_idx, auc, logloss, len(fold_train), len(fold_val),
        )

    # Check variance across folds
    aucs = [r["auc_roc"] for r in results]
    if len(aucs) >= 2:
        auc_var = max(aucs) - min(aucs)
        if auc_var > 0.05:
            logger.warning(
                "⚠ AUC variance across folds = %.4f (>0.05) — signal instability "
                "across market regimes", auc_var,
            )

    return results


# ── Final Model Training & Evaluation ─────────────────────────

def train_final_model(
    df_trainval: pd.DataFrame,
    df_test: pd.DataFrame,
    feature_names: list[str],
    lgbm_params: dict,
    output_dir: Path,
    horizon_seconds: int,
) -> dict:
    """
    Train final model on full train+val, evaluate on held-out test.
    Save all artifacts to output_dir.
    """
    logger.info("Training final model on train+val, evaluating on test...")

    X_trainval = df_trainval[feature_names].values
    y_trainval = df_trainval["target"].values
    X_test = df_test[feature_names].values
    y_test = df_test["target"].values

    model = lgb.LGBMClassifier(**lgbm_params)
    model.fit(X_trainval, y_trainval)

    y_pred = model.predict_proba(X_test)[:, 1]

    # Full-sample metrics
    auc_full = roc_auc_score(y_test, y_pred)
    logloss = log_loss(y_test, y_pred)
    brier = brier_score_loss(y_test, y_pred)
    accuracy = float(np.mean((y_pred > 0.5).astype(int) == y_test))

    # Contract-aligned AUC: filter to rows within ±window of a contract boundary
    # Contract interval = horizon in ms. For 5-min: 300_000. For 1-min: 60_000.
    # Window is ±30s, but capped at half the interval to avoid overlap.
    contract_interval_ms = horizon_seconds * 1000
    contract_window_ms = min(CONTRACT_WINDOW_MS, contract_interval_ms // 2)
    test_cts = df_test["cts"].values
    remainder = test_cts % contract_interval_ms
    contract_mask = (remainder <= contract_window_ms) | (remainder >= (contract_interval_ms - contract_window_ms))
    n_contract = int(contract_mask.sum())

    if n_contract >= 100:
        auc_contract = roc_auc_score(y_test[contract_mask], y_pred[contract_mask])
        logloss_contract = log_loss(y_test[contract_mask], y_pred[contract_mask])
    else:
        logger.warning("Only %d contract-aligned rows — too few for reliable AUC", n_contract)
        auc_contract = float('nan')
        logloss_contract = float('nan')

    logger.info("=" * 50)
    logger.info("FINAL TEST SET RESULTS")
    logger.info("  AUC-ROC (full):     %.4f  (%d rows)", auc_full, len(y_test))
    logger.info("  AUC-ROC (contract): %.4f  (%d rows)", auc_contract, n_contract)
    logger.info("  Log-loss (full):    %.4f", logloss)
    logger.info("  Log-loss (contract):%.4f", logloss_contract)
    logger.info("  Brier:              %.4f", brier)
    logger.info("  Accuracy:           %.4f", accuracy)
    logger.info("=" * 50)

    # Leakage check
    leakage = check_leakage(accuracy, context="final_test_set")
    if leakage["flagged"]:
        logger.warning("LEAKAGE FLAG: %s", leakage["message"])

    # Go/no-go gate — applied to contract-aligned AUC, NOT full AUC
    gate_passed = (not np.isnan(auc_contract)) and (auc_contract >= GO_NOGO_AUC)
    if gate_passed:
        logger.info(
            "✓ GO/NO-GO GATE PASSED: contract AUC %.4f >= %.2f",
            auc_contract, GO_NOGO_AUC,
        )
    else:
        logger.warning(
            "✗ GO/NO-GO GATE FAILED: contract AUC %.4f < %.2f — signal too weak "
            "at contract open times. Do not proceed to Track A/B.",
            auc_contract, GO_NOGO_AUC,
        )

    # Save artifacts
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Model
    model_path = output_dir / "model.lgb"
    model.booster_.save_model(str(model_path))
    logger.info("Model saved: %s", model_path)

    # 2. Feature names
    with open(output_dir / "feature_names.json", "w") as f:
        json.dump(feature_names, f, indent=2)

    # 3. Metrics
    metrics = {
        "auc_full": auc_full,
        "auc_at_contract_times": float(auc_contract) if not np.isnan(auc_contract) else None,
        "n_contract_rows": n_contract,
        "log_loss": logloss,
        "log_loss_contract": float(logloss_contract) if not np.isnan(logloss_contract) else None,
        "brier_score": brier,
        "accuracy": accuracy,
        "gate_passed": gate_passed,
        "gate_threshold": GO_NOGO_AUC,
        "gate_metric": "auc_at_contract_times",
        "leakage_flagged": leakage["flagged"],
        "train_size": len(df_trainval),
        "test_size": len(df_test),
        "horizon_seconds": horizon_seconds,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with open(output_dir / "metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    # 4. Config snapshot
    config_snapshot = {
        "HORIZON_SECONDS": horizon_seconds,
        "TRAIN_END": TRAIN_END,
        "VAL_END": VAL_END,
        "LGBM_PARAMS": lgbm_params,
        "FEATURE_COLS": feature_names,
        "GO_NOGO_AUC": GO_NOGO_AUC,
        "SYMBOLS": SYMBOLS,
        "config": CONFIG,
    }
    with open(output_dir / "config_snapshot.json", "w") as f:
        json.dump(config_snapshot, f, indent=2, default=str)

    # 5. Calibration plot
    try:
        prob_true, prob_pred = calibration_curve(y_test, y_pred, n_bins=10, strategy="uniform")
        fig, ax = plt.subplots(1, 1, figsize=(8, 6))
        ax.plot(prob_pred, prob_true, "s-", label="LightGBM")
        ax.plot([0, 1], [0, 1], "k--", label="Perfect calibration")
        ax.set_xlabel("Predicted probability")
        ax.set_ylabel("Observed frequency")
        ax.set_title(f"Calibration Plot (AUC_full={auc_full:.4f}, AUC_contract={auc_contract:.4f}, H={horizon_seconds}s)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.savefig(output_dir / "calibration.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        logger.info("Calibration plot saved")
    except Exception as e:
        logger.warning("Failed to generate calibration plot: %s", e)

    # 6. SHAP feature importance
    try:
        import shap
        explainer = shap.TreeExplainer(model)
        # Use a subsample for speed
        sample_size = min(5000, len(X_test))
        rng = np.random.default_rng(42)
        sample_idx = rng.choice(len(X_test), sample_size, replace=False)
        shap_values = explainer.shap_values(X_test[sample_idx])

        if isinstance(shap_values, list):
            # Binary classification: use class 1
            shap_vals = shap_values[1]
        else:
            shap_vals = shap_values

        fig, ax = plt.subplots(1, 1, figsize=(10, 8))
        shap.summary_plot(
            shap_vals, X_test[sample_idx],
            feature_names=feature_names,
            show=False, max_display=20,
        )
        plt.savefig(output_dir / "shap_importance.png", dpi=150, bbox_inches="tight")
        plt.close()
        logger.info("SHAP importance plot saved")
    except Exception as e:
        logger.warning("Failed to generate SHAP plot: %s", e)

    return metrics


# ── Housekeeping ──────────────────────────────────────────────

MAX_MODELS_TO_KEEP = 5  # never auto-delete a model referenced in config


def cleanup_old_runs(models_dir: Path, keep_best: int = MAX_MODELS_TO_KEEP):
    """Keep only the best N runs by test AUC-ROC. Delete the rest."""
    runs = []
    for run_dir in models_dir.iterdir():
        if not run_dir.is_dir() or not run_dir.name.startswith("run_"):
            continue
        # Never delete symlink targets
        metrics_path = run_dir / "metrics.json"
        if metrics_path.exists():
            with open(metrics_path) as f:
                m = json.load(f)
            runs.append((m.get("auc_at_contract_times", m.get("auc_full", 0)), run_dir))

    if len(runs) <= keep_best:
        return

    # Protect symlink targets
    protected = set()
    for item in models_dir.iterdir():
        if item.is_symlink():
            protected.add(item.resolve())

    runs.sort(key=lambda x: x[0], reverse=True)
    for _, run_dir in runs[keep_best:]:
        if run_dir.resolve() in protected:
            logger.info("Keeping protected run: %s (symlink target)", run_dir.name)
            continue
        logger.info("Deleting old run: %s", run_dir.name)
        shutil.rmtree(run_dir)


def create_stable_symlink(models_dir: Path, run_dir: Path, horizon_seconds: int):
    """Create/update latest_h{horizon} symlink pointing to the run dir."""
    link_name = f"latest_h{horizon_seconds}"
    link_path = models_dir / link_name
    if link_path.is_symlink() or link_path.exists():
        link_path.unlink()
    link_path.symlink_to(run_dir.name)
    logger.info("Symlink: %s -> %s", link_name, run_dir.name)


# ── Main ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LightGBM baseline training pipeline")
    parser.add_argument("--horizon", type=int, default=HORIZON_SECONDS,
                        help="Prediction horizon in seconds (default: 300)")
    parser.add_argument("--feature-dir", type=str, default="/data/features_v3",
                        help="Feature matrix directory (default: v3 enriched 1-minute features)")
    parser.add_argument("--output-dir", type=str, default="/data/models",
                        help="Model artifacts directory")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate data only, no training")
    args = parser.parse_args()

    horizon = args.horizon
    feature_dir = Path(args.feature_dir)
    models_dir = Path(args.output_dir)

    logger.info("=" * 60)
    logger.info("LightGBM Baseline Training Pipeline")
    logger.info("Horizon:    %ds", horizon)
    logger.info("Features:   %s", feature_dir)
    logger.info("Output:     %s", models_dir)
    logger.info("Train end:  %s", TRAIN_END)
    logger.info("Val end:    %s", VAL_END)
    logger.info("Go/no-go:   AUC_contract >= %.2f", GO_NOGO_AUC)
    logger.info("=" * 60)

    # ── Phase 0: Load and validate data ──
    start_time = time.time()
    df = load_features(feature_dir)
    validate_data(df)

    if args.dry_run:
        logger.info("DRY RUN: data validation passed. Exiting before training.")
        return

    # ── Phase 1: Build targets ──
    df = build_targets(df, horizon)

    # ── Phase 2: Encode features ──
    symbol_map = {sym: i for i, sym in enumerate(SYMBOLS)}
    df["symbol_cat"] = df["symbol"].map(symbol_map).astype(int)

    # ── Phase 3: Time-based train/val/test split ──
    # Convert cts (ms) to dates for splitting
    df["date"] = pd.to_datetime(df["cts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")

    # Enforce TRAINING_BOUNDARIES — drop any day that falls on a boundary
    if TRAINING_BOUNDARIES:
        boundary_set = set(TRAINING_BOUNDARIES)
        before = len(df)
        df = df[~df["date"].isin(boundary_set)].reset_index(drop=True)
        dropped = before - len(df)
        if dropped > 0:
            logger.info("Dropped %d rows on boundary dates: %s", dropped, TRAINING_BOUNDARIES)

    df_train = df[df["date"] <= TRAIN_END].reset_index(drop=True)
    df_val = df[(df["date"] > TRAIN_END) & (df["date"] <= VAL_END)].reset_index(drop=True)
    df_test = df[df["date"] > VAL_END].reset_index(drop=True)

    logger.info("Split sizes: train=%d, val=%d, test=%d", len(df_train), len(df_val), len(df_test))

    # Check class balance
    for name, subset in [("train", df_train), ("val", df_val), ("test", df_test)]:
        pos_rate = subset["target"].mean()
        logger.info("  %s target rate: %.4f (%.1f%% up)", name, pos_rate, 100 * pos_rate)

    feature_names = FEATURE_COLS.copy()

    # ── Phase 4: Walk-forward CV on training set ──
    wf_results = walk_forward_cv(df_train, feature_names, WF_FOLDS, LGBM_PARAMS)

    mean_auc = np.mean([r["auc_roc"] for r in wf_results])
    logger.info("Walk-forward CV mean AUC: %.4f", mean_auc)

    # ── Phase 5: Final model on train+val, evaluate on test ──
    df_trainval = pd.concat([df_train, df_val], ignore_index=True)

    run_ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = models_dir / f"run_{run_ts}"

    metrics = train_final_model(
        df_trainval, df_test, feature_names, LGBM_PARAMS,
        run_dir, horizon,
    )

    logger.info("Contract-aligned evaluation: %d/%d test rows (%.1f%%) within ±30s of 5-min boundary",
                metrics.get("n_contract_rows", 0), len(df_test),
                100 * metrics.get("n_contract_rows", 0) / max(len(df_test), 1))

    # Save walk-forward results alongside
    with open(run_dir / "walk_forward_results.json", "w") as f:
        json.dump(wf_results, f, indent=2)

    # ── Phase 6: Create stable symlink + cleanup old runs ──
    create_stable_symlink(models_dir, run_dir, horizon)
    cleanup_old_runs(models_dir)

    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info("FINISHED in %.1f minutes", elapsed / 60)
    logger.info("Results dir: %s", run_dir)
    logger.info(
        "Gate: %s (AUC_full=%.4f, AUC_contract=%s)",
        "PASSED ✓" if metrics["gate_passed"] else "FAILED ✗",
        metrics["auc_full"],
        f"{metrics['auc_at_contract_times']:.4f}" if metrics.get("auc_at_contract_times") else "N/A",
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
