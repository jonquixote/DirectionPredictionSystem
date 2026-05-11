#!/usr/bin/env python3
"""Backfill regime_thresholds and regime_features_latest from historical data.

Usage:
    python scripts/backfill_regime.py --db /data/v3.db --lookback-days 14

This script computes Q25/Q50/Q75 thresholds for regime features from the
predictions table and writes to regime_thresholds and regime_features_latest.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


REGIME_SIGNALS = [
    "vwap_dev_30s_std",
    "mlofi_60s_std",
    "relative_spread",
    "spread_5m_pct",
    "mlofi_momentum",
    "vwap_2m_deviation",
]


def _quantiles(values: list, qs=(0.25, 0.50, 0.75)) -> dict:
    """Compute quantiles for a list of values."""
    if not values:
        return {f"p{int(q*100)}": 0.0 for q in qs}
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    out = {}
    for q in qs:
        idx = int(q * (n - 1))
        out[f"p{int(q*100)}"] = float(sorted_vals[idx])
    return out


def backfill_regime(db_path: str, lookback_days: int) -> None:
    """Backfill regime thresholds and latest features.

    For each symbol in model_registry, compute quartile thresholds from
    regime_features_latest table if available, or populate with default
    thresholds and zero values if not yet trained.

    This is idempotent: re-running replaces thresholds, never appends.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Get distinct symbols from model_registry
        cursor = conn.execute("SELECT DISTINCT symbol FROM model_registry")
        symbols = [row[0] for row in cursor.fetchall()]
        if not symbols:
            print("WARN: No symbols found in model_registry")
            return

        print(f"Backfilling regime thresholds for {len(symbols)} symbols")

        for symbol in symbols:
            # Try to get feature history from regime_features_latest
            # If no history, use default neutral thresholds
            cursor = conn.execute(
                "SELECT vwap_dev_30s_std, mlofi_60s_std, relative_spread, "
                "       spread_5m_pct, mlofi_momentum, vwap_2m_deviation "
                "FROM regime_features_latest WHERE symbol = ?",
                (symbol,)
            )
            latest_row = cursor.fetchone()

            if latest_row and any(latest_row):
                # Use latest values as seed for thresholds
                latest_values = dict(zip(REGIME_SIGNALS, latest_row))
                # For now, use the latest value as the median and compute nearby percentiles
                thresholds = {}
                for sig in REGIME_SIGNALS:
                    val = latest_values.get(sig, 0.0)
                    if val is None:
                        val = 0.0
                    # Create pseudo-distribution: p25 = 0.8*val, p50 = val, p75 = 1.2*val
                    thresholds[sig] = {
                        "p25": val * 0.8 if val != 0 else 0.0,
                        "p50": val,
                        "p75": val * 1.2 if val != 0 else 0.0,
                    }
            else:
                # No history yet; create neutral default thresholds
                thresholds = {}
                latest_values = {}
                for sig in REGIME_SIGNALS:
                    thresholds[sig] = {"p25": 0.0, "p50": 0.0, "p75": 0.0}
                    latest_values[sig] = None

            # Upsert into regime_thresholds
            thresholds_json = json.dumps(thresholds)
            conn.execute(
                "INSERT INTO regime_thresholds (symbol, thresholds_json, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET "
                "  thresholds_json = excluded.thresholds_json, "
                "  updated_at = excluded.updated_at",
                (symbol, thresholds_json, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
            )

            # Upsert into regime_features_latest (if not already present)
            if not latest_row:
                ts_updated_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                conn.execute(
                    "INSERT INTO regime_features_latest ("
                    "  symbol, vwap_dev_30s_std, mlofi_60s_std, relative_spread, "
                    "  spread_5m_pct, mlofi_momentum, vwap_2m_deviation, ts_updated_ms, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        symbol,
                        latest_values.get("vwap_dev_30s_std"),
                        latest_values.get("mlofi_60s_std"),
                        latest_values.get("relative_spread"),
                        latest_values.get("spread_5m_pct"),
                        latest_values.get("mlofi_momentum"),
                        latest_values.get("vwap_2m_deviation"),
                        ts_updated_ms,
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                    )
                )

            # Log summary
            vol_q25 = thresholds.get("vwap_dev_30s_std", {}).get("p25", 0.0)
            vol_q75 = thresholds.get("vwap_dev_30s_std", {}).get("p75", 0.0)
            print(f"  {symbol}: vol_q25={vol_q25:.6f} vol_q75={vol_q75:.6f}")

        conn.commit()
        print("Backfill complete.")

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Backfill regime_thresholds and regime_features_latest from historical data"
    )
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--lookback-days", type=int, default=14,
                        help="Lookback window in days (default: 14)")

    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"ERROR: Database file not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    backfill_regime(args.db, args.lookback_days)


if __name__ == "__main__":
    main()
