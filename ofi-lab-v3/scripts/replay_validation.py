#!/usr/bin/env python3
"""
Replay Validation Script — Feature-Contract Alignment validation.
Runs on VPS against /data/v3.db and /data/features_v3 daily parquets.

Two arms:
1. BROKEN: The exact predictions logged in the database.
2. ALIGNED: Re-evaluated predictions using the dynamically resolved training contract.

Also performs a cross-check: attempts to re-derive the broken predictions
using 32-col (sidecar) and 33-col (fallback) contracts, reporting any gaps.
"""

from __future__ import annotations

import sqlite3
import json
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import lightgbm as lgb

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feature_engineering.feature_contract import (
    FEATURE_COLS,
    FEATURE_COLS_PER_SYMBOL,
    resolve_model_feature_contract,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("replay_validation")

# Parquet row cache
parquet_cache: dict[tuple[str, str], dict[int, dict]] = {}


def get_parquet_row(symbol: str, ts_ms: int) -> dict | None:
    """Load daily parquet and retrieve the row closest to ts_ms."""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    date_str = dt.strftime("%Y-%m-%d")
    cache_key = (symbol, date_str)

    if cache_key not in parquet_cache:
        path = Path(f"/data/features_v3/{symbol}/{date_str}_{symbol}_features.parquet")
        if not path.exists():
            parquet_cache[cache_key] = {}
            return None
        try:
            import polars as pl
            df = pl.read_parquet(path)
            rows = df.to_dicts()
            from trading.live_features import SYMBOL_MAP
            sym_cat = SYMBOL_MAP.get(symbol, 0)
            for row in rows:
                if "symbol_cat" not in row:
                    row["symbol_cat"] = sym_cat
            parquet_cache[cache_key] = {row["cts"]: row for row in rows}
            logger.info("Loaded %d feature rows from %s", len(rows), path.name)
        except Exception as e:
            logger.error("Failed to load parquet %s: %s", path, e)
            parquet_cache[cache_key] = {}
            return None

    cache = parquet_cache[cache_key]
    if ts_ms in cache:
        return cache[ts_ms]

    # Fallback to closest within 2000ms
    best_row = None
    best_delta = 2001
    for cts, row in cache.items():
        delta = abs(cts - ts_ms)
        if delta < best_delta:
            best_delta = delta
            best_row = row
    return best_row


def get_confidence_bin(proba: float) -> str:
    """Determine the bin for a given prediction probability."""
    conf = max(proba, 1.0 - proba)
    if conf < 0.50:
        return "invalid"
    elif conf < 0.51:
        return "0.50-0.51"
    elif conf < 0.52:
        return "0.51-0.52"
    elif conf < 0.53:
        return "0.52-0.53"
    elif conf < 0.54:
        return "0.53-0.54"
    elif conf < 0.55:
        return "0.54-0.55"
    elif conf < 0.56:
        return "0.55-0.56"
    else:
        return ">=0.56"


def main():
    db_path = "/data/v3.db"
    if not Path(db_path).exists():
        logger.error("Database not found at %s. Are you running this on the VPS?", db_path)
        sys.exit(1)

    logger.info("Connecting to database: %s", db_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # Query registry to find active model paths
    logger.info("Fetching model registry...")
    registry_rows = conn.execute(
        "SELECT name, symbol, artifact_path, feature_names_path FROM model_registry"
    ).fetchall()

    models_meta = {}
    for r in registry_rows:
        models_meta[r["name"]] = {
            "symbol": r["symbol"],
            "artifact_path": r["artifact_path"],
            "feature_names_path": r["feature_names_path"],
        }
    logger.info("Found %d models in registry", len(models_meta))

    # Fetch unique model names from predictions
    logger.info("Querying unique model names with resolved predictions...")
    pred_models = [
        row["model_name"]
        for row in conn.execute(
            "SELECT DISTINCT model_name FROM predictions WHERE resolved = 1 AND platform = 'paper'"
        ).fetchall()
    ]
    logger.info("Predictions contain %d unique models", len(pred_models))

    # We will load boosters for models that exist in registry and have predictions
    boosters = {}
    aligned_contracts = {}
    for name in pred_models:
        if name not in models_meta:
            logger.warning("Model %s has predictions but is not in registry", name)
            continue
        meta = models_meta[name]
        if not meta.get("artifact_path"):
            logger.warning("Model %s in registry has no artifact_path", name)
            continue
        art_path = Path(meta["artifact_path"])
        if not art_path.exists():
            logger.warning("Model artifact not found for %s: %s", name, art_path)
            continue

        try:
            booster = lgb.Booster(model_file=str(art_path))
            fn_path = Path(meta["feature_names_path"]) if meta["feature_names_path"] else None
            if not fn_path or not fn_path.exists():
                fn_path = art_path.parent / "feature_names.json"

            contract = resolve_model_feature_contract(booster, fn_path)
            boosters[name] = booster
            aligned_contracts[name] = contract
            logger.info("Loaded booster %s with %d features", name, len(contract))
        except Exception as e:
            logger.error("Failed to load booster or resolve contract for %s: %s", name, e)

    if not boosters:
        logger.error("No active model boosters loaded. Cannot proceed.")
        sys.exit(1)

    # We will aggregate results across all loaded models
    # Key: bin name, Value: list of (correct_bool)
    broken_bins: dict[str, list[bool]] = {
        "0.50-0.51": [], "0.51-0.52": [], "0.52-0.53": [],
        "0.53-0.54": [], "0.54-0.55": [], "0.55-0.56": [],
        ">=0.56": []
    }
    aligned_bins: dict[str, list[bool]] = {
        "0.50-0.51": [], "0.51-0.52": [], "0.52-0.53": [],
        "0.53-0.54": [], "0.54-0.55": [], "0.55-0.56": [],
        ">=0.56": []
    }

    # Cross-check stats
    total_checked = 0
    matched_32 = 0
    matched_33 = 0
    unmatched = 0
    missing_bars = 0
    aligned_missing_cols = 0

    # Process models one by one
    for model_name, booster in boosters.items():
        symbol = models_meta[model_name]["symbol"]
        aligned_cols = aligned_contracts[model_name]

        logger.info("Replaying predictions for %s (%s)...", model_name, symbol)
        predictions = conn.execute(
            "SELECT prediction_id, ts_contract_open_ms, pred_proba_raw, pred_direction, "
            "contract_result, prediction_correct FROM predictions "
            "WHERE model_name = ? AND platform = 'paper' AND resolved = 1 "
            "ORDER BY ts_contract_open_ms ASC",
            (model_name,),
        ).fetchall()

        logger.info("Found %d resolved predictions for %s", len(predictions), model_name)

        for p in predictions:
            ts_ms = p["ts_contract_open_ms"]
            p_logged = float(p["pred_proba_raw"])
            dir_logged = p["pred_direction"]
            res = p["contract_result"]
            correct_logged = bool(p["prediction_correct"])

            # Retrieve feature bar
            bar = get_parquet_row(symbol, ts_ms)
            if not bar:
                missing_bars += 1
                continue

            # 1. ALIGNED arm: enforce no silent zero-fills
            # Raise if any aligned column is missing
            for col in aligned_cols:
                if col not in bar:
                    aligned_missing_cols += 1
                    raise KeyError(
                        f"Missing column {col} in feature bar for {symbol} at {ts_ms}"
                    )

            # Build aligned feature vector
            aligned_vec = np.array([bar[col] for col in aligned_cols], dtype=np.float64).reshape(1, -1)
            p_aligned = float(booster.predict(aligned_vec)[0])
            dir_aligned = "up" if p_aligned > 0.5 else "down"
            correct_aligned = (res == dir_aligned)

            # Put into aligned bins
            abin = get_confidence_bin(p_aligned)
            if abin != "invalid":
                aligned_bins[abin].append(correct_aligned)

            # 2. BROKEN arm: use logged database values
            bbin = get_confidence_bin(p_logged)
            if bbin != "invalid":
                broken_bins[bbin].append(correct_logged)

            # 3. Cross-check re-derivation
            # Re-derive 32-col (using sidecar)
            p_32 = -1.0
            if booster.num_feature() == len(FEATURE_COLS_PER_SYMBOL):
                try:
                    vec_32 = np.array([bar.get(col, 0.0) for col in FEATURE_COLS_PER_SYMBOL], dtype=np.float64).reshape(1, -1)
                    p_32 = float(booster.predict(vec_32)[0])
                except Exception:
                    pass

            # Re-derive 33-col (using fallback V3_FEATURE_COLS equivalent)
            p_33 = -1.0
            if booster.num_feature() == len(FEATURE_COLS):
                try:
                    vec_33 = np.array([bar.get(col, 0.0) for col in FEATURE_COLS], dtype=np.float64).reshape(1, -1)
                    p_33 = float(booster.predict(vec_33)[0])
                except Exception:
                    pass

            total_checked += 1
            if abs(p_logged - p_32) < 1e-5:
                matched_32 += 1
            elif abs(p_logged - p_33) < 1e-5:
                matched_33 += 1
            else:
                unmatched += 1

    # Print validation table
    print("\n" + "=" * 60)
    print("REPLAY VALIDATION SUMMARY")
    print("=" * 60)
    print(f"Total predictions replayed: {total_checked}")
    print(f"Predictions with missing feature bars: {missing_bars}")
    print(f"ALIGNED missing columns: {aligned_missing_cols}")
    print("\nCROSS-CHECK ANALYSIS:")
    print(f"  Matched 32-col sidecar:  {matched_32} ({matched_32/total_checked*100:.1f}%)")
    print(f"  Matched 33-col fallback: {matched_33} ({matched_33/total_checked*100:.1f}%)")
    print(f"  Unmatched (Gaps):        {unmatched} ({unmatched/total_checked*100:.1f}%)")
    print("=" * 60)
    print(f"{'Bin':<12} | {'BROKEN (Logged) Win Rate':<25} | {'ALIGNED (Dynamic) Win Rate':<25}")
    print("-" * 68)

    bins_list = ["0.50-0.51", "0.51-0.52", "0.52-0.53", "0.53-0.54", "0.54-0.55", "0.55-0.56", ">=0.56"]
    for b in bins_list:
        b_trials = broken_bins[b]
        a_trials = aligned_bins[b]

        b_pct = f"{np.mean(b_trials)*100:.2f}% (N={len(b_trials)})" if b_trials else "N/A"
        a_pct = f"{np.mean(a_trials)*100:.2f}% (N={len(a_trials)})" if a_trials else "N/A"

        print(f"{b:<12} | {b_pct:<25} | {a_pct:<25}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
