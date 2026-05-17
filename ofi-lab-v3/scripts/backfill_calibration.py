#!/usr/bin/env python3
"""Backfill calibration_bins and calibration_summary from historical predictions.

Usage:
    python scripts/backfill_calibration.py --db /data/v3.db --lookback-days 30 --n-bins 10

This script joins predictions with paper_trades to get (proba, outcome) pairs,
bins them by calibrated probability, and computes Brier score + log loss.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple


def compute_brier_score(rows: List[Tuple[float, int]]) -> float:
    """Mean of (predicted - outcome)^2."""
    if not rows:
        return 0.0
    return sum((p - o) ** 2 for p, o in rows) / len(rows)


def compute_log_loss(rows: List[Tuple[float, int]]) -> float:
    """Mean of -[y*log(p) + (1-y)*log(1-p)]."""
    if not rows:
        return 0.0
    epsilon = 1e-15
    total = 0.0
    for p, y in rows:
        p = max(epsilon, min(1.0 - epsilon, p))
        if y == 1:
            total += -math.log(p)
        else:
            total += -math.log(1.0 - p)
    return total / len(rows)


def backfill_calibration(db_path: str, lookback_days: int, n_bins: int) -> None:
    """Backfill calibration bins and summary metrics.

    For each model in model_registry, fetch resolved predictions joined with
    outcomes from predictions table (which has the contract_result).
    Bin by pred_proba_calibrated and compute metrics.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Get distinct models from model_registry
        cursor = conn.execute("SELECT DISTINCT name FROM model_registry")
        models = [row[0] for row in cursor.fetchall()]
        if not models:
            print("WARN: No models found in model_registry")
            return

        print(f"Backfilling calibration for {len(models)} models over {lookback_days} days")

        for model_name in models:
            # Fetch resolved predictions for this model over lookback window
            cursor = conn.execute("""
                SELECT
                    prediction_id,
                    pred_proba_calibrated,
                    contract_result,
                    prediction_correct
                FROM predictions
                WHERE model_name = ?
                    AND resolved = 1
                    AND resolution_type = 'evaluation'
                    AND warmup = 0
                    AND ts_contract_open_ms >= (
                        CAST(strftime('%s', 'now') AS INTEGER) * 1000
                        - ? * 24 * 60 * 60 * 1000
                    )
                ORDER BY ts_contract_open_ms DESC
            """, (model_name, lookback_days))

            rows = cursor.fetchall()
            if len(rows) < 100:
                print(f"  {model_name}: only {len(rows)} predictions (< 100), skipping")
                continue

            # Build (proba, outcome) pairs
            pairs = []
            for row in rows:
                proba = row["pred_proba_calibrated"]
                # Outcome: 1 if prediction_correct=1, 0 otherwise
                outcome = 1 if row["prediction_correct"] == 1 else 0
                if proba is not None:
                    pairs.append((proba, outcome))

            if not pairs:
                print(f"  {model_name}: no valid proba/outcome pairs, skipping")
                continue

            # Compute overall metrics
            brier = compute_brier_score(pairs)
            log_loss = compute_log_loss(pairs)
            n_obs = len(pairs)

            # Upsert into calibration_summary
            conn.execute(
                "INSERT INTO calibration_summary "
                "(model_name, brier, log_loss, n_obs, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(model_name) DO UPDATE SET "
                "  brier = excluded.brier, "
                "  log_loss = excluded.log_loss, "
                "  n_obs = excluded.n_obs, "
                "  updated_at = excluded.updated_at",
                (
                    model_name,
                    brier,
                    log_loss,
                    n_obs,
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                )
            )

            # Bin predictions: equal-width bins
            min_proba = min(p for p, _ in pairs)
            max_proba = max(p for p, _ in pairs)
            bin_width = (max_proba - min_proba) / n_bins if max_proba > min_proba else 1.0 / n_bins

            bins: Dict[int, List[Tuple[float, int]]] = {}
            for proba, outcome in pairs:
                bin_idx = int((proba - min_proba) / bin_width) if bin_width > 0 else 0
                bin_idx = min(bin_idx, n_bins - 1)
                bins.setdefault(bin_idx, []).append((proba, outcome))

            # Clear old bins for this model
            conn.execute(
                "DELETE FROM calibration_bins WHERE model_name = ?",
                (model_name,)
            )

            # Insert new bins
            for bin_idx in sorted(bins.keys()):
                bin_members = bins[bin_idx]
                bin_lo = min_proba + bin_idx * bin_width
                bin_hi = min_proba + (bin_idx + 1) * bin_width
                observed_freq = sum(1 for _, o in bin_members if o == 1) / len(bin_members)
                n_in_bin = len(bin_members)

                conn.execute(
                    "INSERT INTO calibration_bins "
                    "(model_name, bin_lo, bin_hi, observed_freq, n, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        model_name,
                        bin_lo,
                        bin_hi,
                        observed_freq,
                        n_in_bin,
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                    )
                )

            # Log summary
            print(f"  {model_name}: n_obs={n_obs} brier={brier:.4f} log_loss={log_loss:.4f} bins={len(bins)}")

        conn.commit()
        print("Backfill complete.")

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Backfill calibration_bins and calibration_summary from historical predictions"
    )
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--lookback-days", type=int, default=30,
                        help="Lookback window in days (default: 30)")
    parser.add_argument("--n-bins", type=int, default=10,
                        help="Number of probability bins (default: 10)")

    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"ERROR: Database file not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    backfill_calibration(args.db, args.lookback_days, args.n_bins)


if __name__ == "__main__":
    main()
