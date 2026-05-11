"""Parameterized retrain CLI.

Wraps the legacy run_training.py with date-range params instead of
hardcoded TRAIN_END / VAL_END. Auto-derives the model name per the
v3 naming convention.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _auto_name(horizon: int, symbol: str, feature_version: str,
               train_end: str) -> str:
    sym = symbol.lower()
    cutoff = train_end.replace("-", "")
    return f"{horizon}s_{sym}_{feature_version}_{cutoff}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--horizon", type=int, required=True,
                   help="training horizon in seconds (60, 300, 900...)")
    p.add_argument("--symbol", required=True,
                   help="symbol e.g. BTCUSDT")
    p.add_argument("--feature-version", required=True,
                   help="feature schema version e.g. v3, v3d")
    p.add_argument("--train-days", type=int, default=330)
    p.add_argument("--train-end", required=True,
                   help="last full day of training data, YYYY-MM-DD")
    p.add_argument("--val-days", type=int, default=30)
    p.add_argument("--test-days", type=int, default=14)
    p.add_argument("--feature-dir", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--dry-run", action="store_true",
                   help="print resolved windows + name and exit 0")
    p.add_argument("--auto-name", action="store_true",
                   help="(default) derive model name from convention")
    p.add_argument("--skip-wf", action="store_true",
                   help="skip walk-forward CV (faster fleet training)")
    args = p.parse_args()

    train_end = datetime.strptime(args.train_end, "%Y-%m-%d").date()
    train_start = train_end - timedelta(days=args.train_days - 1)
    val_end = train_end + timedelta(days=args.val_days)
    test_end = val_end + timedelta(days=args.test_days)
    name = _auto_name(args.horizon, args.symbol, args.feature_version,
                       args.train_end)

    print(f"train: {train_start} → {train_end}")
    print(f"val: {train_end + timedelta(days=1)} → {val_end}")
    print(f"test: {val_end + timedelta(days=1)} → {test_end}")
    print(f"auto_name={name}")

    if args.dry_run:
        return 0

    # Delegate to actual training runner
    import subprocess as _sp
    runner_override = os.environ.get("V3_RUN_TRAINING_OVERRIDE")
    if runner_override:
        cmd = [sys.executable, runner_override]
    else:
        cmd = [sys.executable, "-m", "validation.run_training"]
    cmd += [
        "--symbol", args.symbol,
        "--horizon", str(args.horizon),
        "--feature-version", args.feature_version,
        "--train-start", str(train_start),
        "--train-end", str(train_end),
        "--val-end", str(val_end),
        "--test-end", str(test_end),
        "--feature-dir", args.feature_dir,
        "--output-dir", args.output_dir,
        "--model-name", name,
    ]
    if args.skip_wf:
        cmd.append("--skip-wf")
    print(f"delegating to: {' '.join(cmd)}")
    return _sp.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
