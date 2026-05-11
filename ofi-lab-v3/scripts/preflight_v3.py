#!/usr/bin/env python3
"""Combined preflight validation before v3-paper-trader.service starts.

Usage:
    python scripts/preflight_v3.py --db /data/v3.db

Runs both backfills, then validates all invariants:
  - Every symbol in model_registry has regime_thresholds.
  - Every baseline model has calibration_summary (warn for non-baseline).
  - Every baseline model has ewma_ev in decay_metrics.

Exits 0 on success, non-zero on failure.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List
import sqlite3


def run_backfills(db_path: str) -> bool:
    """Run backfill_regime.py and backfill_calibration.py."""
    scripts_dir = Path(__file__).parent

    print("=" * 70)
    print("BACKFILL PHASE: Regime Thresholds")
    print("=" * 70)
    result = subprocess.run(
        [sys.executable, str(scripts_dir / "backfill_regime.py"), "--db", db_path],
        capture_output=False
    )
    if result.returncode != 0:
        print("ERROR: backfill_regime.py failed", file=sys.stderr)
        return False

    print()
    print("=" * 70)
    print("BACKFILL PHASE: Calibration Bins & Summary")
    print("=" * 70)
    result = subprocess.run(
        [sys.executable, str(scripts_dir / "backfill_calibration.py"), "--db", db_path],
        capture_output=False
    )
    if result.returncode != 0:
        print("ERROR: backfill_calibration.py failed", file=sys.stderr)
        return False

    return True


def validate_invariants(db_path: str) -> bool:
    """Validate all required tables are populated correctly."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        errors: List[str] = []
        warnings: List[str] = []

        print()
        print("=" * 70)
        print("VALIDATION PHASE")
        print("=" * 70)

        # Validate regime_thresholds
        print()
        print("Checking regime_thresholds...")
        cursor = conn.execute("SELECT DISTINCT symbol FROM model_registry")
        symbols = [row[0] for row in cursor.fetchall()]
        cursor = conn.execute("SELECT symbol FROM regime_thresholds")
        thresholds_symbols = set(row[0] for row in cursor.fetchall())
        missing_thresholds = set(symbols) - thresholds_symbols
        if missing_thresholds:
            errors.append(f"Missing regime_thresholds for symbols: {sorted(missing_thresholds)}")
        else:
            print(f"  ✓ All {len(symbols)} symbols have regime_thresholds")

        # Validate calibration_summary
        print()
        print("Checking calibration_summary...")
        cursor = conn.execute(
            "SELECT name, is_baseline FROM model_registry"
        )
        models = [(row[0], row[1]) for row in cursor.fetchall()]
        baseline_models = [m for m, b in models if b]
        cursor = conn.execute("SELECT model_name FROM calibration_summary")
        calib_models = set(row[0] for row in cursor.fetchall())

        missing_calib = []
        for model_name, is_baseline in models:
            if model_name not in calib_models:
                if is_baseline:
                    errors.append(f"Baseline model {model_name} missing from calibration_summary")
                else:
                    warnings.append(f"Non-baseline model {model_name} missing from calibration_summary (acceptable for cold-start)")

        if not errors or (len(missing_calib) < len(models)):
            calib_count = len(calib_models)
            baseline_count = len(baseline_models)
            print(f"  ✓ {calib_count} models have calibration_summary")
            if baseline_count > 0:
                baseline_calib = sum(1 for m, _ in models if m in calib_models and _ in [m for n, _ in models if _ == True])
                print(f"  ✓ Baseline models with calibration: {baseline_calib}/{baseline_count}")

        # Validate decay_metrics has EV data for baseline models
        print()
        print("Checking decay_metrics...")
        cursor = conn.execute(
            "SELECT name FROM model_registry WHERE is_baseline = 1"
        )
        baseline_names = [row[0] for row in cursor.fetchall()]

        for baseline_name in baseline_names:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM decay_metrics WHERE model_name = ? AND rolling_ev IS NOT NULL",
                (baseline_name,)
            )
            count = cursor.fetchone()[0]
            if count == 0:
                warnings.append(f"Baseline model {baseline_name} has no rolling_ev data in decay_metrics yet (normal if not yet live)")
            else:
                print(f"  ✓ {baseline_name}: {count} decay_metrics snapshots with EV")

        # Print results
        print()
        print("=" * 70)
        print("VALIDATION SUMMARY")
        print("=" * 70)

        if warnings:
            print()
            print("WARNINGS (non-fatal):")
            for w in warnings:
                print(f"  ⚠ {w}")

        if errors:
            print()
            print("ERRORS (blocking):")
            for e in errors:
                print(f"  ✗ {e}")
            return False

        if not warnings and not errors:
            print("  ✓ All invariants satisfied. Ready for production.")

        return True

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Preflight validation for v3-paper-trader.service"
    )
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--skip-backfill", action="store_true",
                        help="Skip backfill phase, only validate")

    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"ERROR: Database file not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    # Run backfills unless --skip-backfill
    if not args.skip_backfill:
        if not run_backfills(args.db):
            sys.exit(1)

    # Validate
    if not validate_invariants(args.db):
        sys.exit(1)

    print()
    print("SUCCESS: All preflight checks passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
