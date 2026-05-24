"""Drive fleet training: iterate (symbol, horizon, train_days), shell to retrain.py.

When --train-end is omitted, it is auto-derived from the latest available
feature data so that the val + test windows are fully covered:

    train_end = latest_feature_date - val_days - test_days - buffer_days
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
DEFAULT_HORIZONS = [60, 180, 300, 600, 900, 1200, 1800]
DEFAULT_TRAIN_DAYS = [90, 180, 330]

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_")


def discover_latest_feature_date(feature_dir: str, symbols: list[str]) -> datetime.date:
    """Scan feature parquet filenames to find the latest date common to all symbols.

    Feature files follow the pattern: {YYYY-MM-DD}_{SYMBOL}_features.parquet
    Returns the latest date for which every symbol has a feature file.
    Raises SystemExit if any symbol has no feature files at all.
    """
    feature_path = Path(feature_dir)
    if not feature_path.is_dir():
        raise SystemExit(f"feature-dir not found: {feature_dir}")

    per_symbol: dict[str, set[datetime.date]] = {}
    for sym in symbols:
        sym_dir = feature_path / sym
        if not sym_dir.is_dir():
            raise SystemExit(f"no feature directory for symbol {sym}: {sym_dir}")
        dates: set[datetime.date] = set()
        for f in sym_dir.iterdir():
            m = _DATE_RE.match(f.name)
            if m:
                dates.add(datetime.strptime(m.group(1), "%Y-%m-%d").date())
        if not dates:
            raise SystemExit(f"no feature parquet files for symbol {sym} in {sym_dir}")
        per_symbol[sym] = dates

    common = set.intersection(*per_symbol.values())
    if not common:
        raise SystemExit(
            f"no date has feature files for ALL symbols: "
            f"per-symbol counts: {', '.join(f'{s}={len(d)}' for s, d in per_symbol.items())}"
        )
    latest = max(common)
    counts = {s: len(d) for s, d in per_symbol.items()}
    print(f"feature scan: latest common date = {latest}  (per-symbol files: {counts})")
    return latest


def resolve_train_end(
    *,
    train_end_arg: str | None,
    feature_dir: str,
    symbols: list[str],
    val_days: int,
    test_days: int,
    buffer_days: int,
) -> str:
    """Resolve train_end: explicit CLI arg, or auto-derived from feature data.

    Auto-derive: train_end = latest_feature_date - val_days - test_days - buffer_days

    Validates that feature data covers the full val + test window when
    --train-end is explicitly provided (warns but continues on short data).
    """
    latest = discover_latest_feature_date(feature_dir, symbols)

    if train_end_arg:
        train_end = datetime.strptime(train_end_arg, "%Y-%m-%d").date()
        needed_through = train_end + timedelta(days=val_days + test_days)
        if needed_through > latest:
            print(
                f"WARNING: --train-end {train_end_arg} requires data through "
                f"{needed_through} but latest available is {latest}. "
                f"Val/test windows may be empty, causing training failures.",
                file=sys.stderr,
            )
        return train_end_arg

    train_end = latest - timedelta(days=val_days + test_days + buffer_days)
    val_end = train_end + timedelta(days=val_days)
    test_end = val_end + timedelta(days=test_days)
    print(f"auto-derived train_end = {train_end}  (latest data: {latest})")
    print(f"  val window:   {train_end + timedelta(days=1)} → {val_end}")
    print(f"  test window:  {val_end + timedelta(days=1)} → {test_end}")
    print(f"  data through: {latest}  ✓" if test_end <= latest else f"  data through: {latest}  ✗ INSUFFICIENT")
    if test_end > latest:
        raise SystemExit(
            f"Cannot fit val({val_days}d)+test({test_days}d) windows before "
            f"latest data date {latest}. Reduce --val-days/--test-days or add "
            f"more feature data."
        )
    return train_end.isoformat()


def enumerate_fleet(symbols, horizons, train_days_list):
    """Generate list of all (symbol, horizon, train_days) combinations."""
    cells = []
    for s in symbols:
        for h in horizons:
            for d in train_days_list:
                name = f"h{h}_{s.replace('USDT','').lower()}_v3_{d}d"
                cells.append({"symbol": s, "horizon": h, "train_days": d, "name": name})
    return cells


def filter_pending(fleet, state_path: Path):
    """Filter fleet to exclude already-completed models."""
    if not state_path.exists():
        return fleet
    state = json.loads(state_path.read_text())
    done = set(state.get("completed", []))
    return [c for c in fleet if c["name"] not in done]


def train_one(cell, *, train_end, feature_dir, output_root, evaluation_windows, db_path=None, val_days=10, test_days=5, train_mode="per-symbol", cutover_delay_hours=None):
    """Train a single model cell.

    Args:
        cell: Dict with keys: symbol, horizon, train_days, name
        train_end: Training end date YYYY-MM-DD
        feature_dir: Path to features directory
        output_root: Root output directory
        evaluation_windows: List of evaluation window durations
        db_path: Path to SQLite database (optional, for register_model)

    Returns:
        Dict with keys: name, status, duration_s, err (if failed)
    """
    out_dir = Path(output_root) / cell["name"]
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "validation.retrain",
        "--horizon",
        str(cell["horizon"]),
        "--symbol",
        cell["symbol"],
        "--feature-version",
        "v3",
        "--train-days",
        str(cell["train_days"]),
        "--train-end",
        train_end,
        "--val-days",
        str(val_days),
        "--test-days",
        str(test_days),
        "--feature-dir",
        feature_dir,
        "--output-dir",
        str(out_dir),
        "--train-mode",
        train_mode,
        "--skip-wf",
    ]
    t0 = time.time()
    try:
        subprocess.run(cmd, check=True, timeout=3600, capture_output=True)
    except Exception as e:
        return {
            "name": cell["name"],
            "status": "FAILED",
            "err": str(e),
            "duration_s": time.time() - t0,
        }

    # Register the trained model — retrain.py creates {out_dir}/run_TS/ with model.lgb.
    # Find the newest run_* subdir to point register_model at.
    run_dirs = sorted(out_dir.glob("run_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not run_dirs:
        return {
            "name": cell["name"],
            "status": "TRAINED_BUT_NO_RUN_DIR",
            "err": f"no run_* subdir in {out_dir}",
            "duration_s": time.time() - t0,
        }
    artifact_dir = run_dirs[0]

    if db_path:
        reg_cmd = [
            sys.executable,
            "scripts/register_model.py",
            "--db",
            db_path,
            "--artifact-dir",
            str(artifact_dir),
            "--evaluation-windows",
            ",".join(map(str, evaluation_windows)),
        ]
        if cutover_delay_hours is not None:
            reg_cmd.extend(["--cutover-delay-hours", str(cutover_delay_hours)])
        try:
            subprocess.run(reg_cmd, check=True, timeout=60, capture_output=True)
        except subprocess.CalledProcessError as e:
            return {
                "name": cell["name"],
                "status": "TRAINED_BUT_REGISTRATION_FAILED",
                "err": str(e),
                "duration_s": time.time() - t0,
            }

    return {"name": cell["name"], "status": "DONE", "duration_s": time.time() - t0}


def main():
    p = argparse.ArgumentParser(description="Train fleet of models")
    p.add_argument(
        "--symbols", default=",".join(DEFAULT_SYMBOLS), help="Comma-separated symbols"
    )
    p.add_argument(
        "--horizons",
        default=",".join(map(str, DEFAULT_HORIZONS)),
        help="Comma-separated horizons in seconds",
    )
    p.add_argument(
        "--train-days",
        default=",".join(map(str, DEFAULT_TRAIN_DAYS)),
        help="Comma-separated train day windows",
    )
    p.add_argument(
        "--train-end",
        default=None,
        help=(
            "Training end date YYYY-MM-DD. When omitted, auto-derived from "
            "latest feature data as: latest_date - val_days - test_days - buffer_days"
        ),
    )
    p.add_argument(
        "--buffer-days",
        type=int,
        default=0,
        help="Extra days to subtract from auto-derived train_end (default: 0)",
    )
    p.add_argument(
        "--feature-dir",
        default="/data/features_v3",
        help="Features directory",
    )
    p.add_argument(
        "--output-root",
        default="/data/models/fleet",
        help="Fleet output root",
    )
    p.add_argument(
        "--evaluation-windows",
        default="300,900,1800",
        help="Comma-separated evaluation window durations",
    )
    p.add_argument(
        "--state",
        default="/data/models/fleet/state.json",
        help="State file for resuming after crash",
    )
    p.add_argument(
        "--parallel",
        type=int,
        default=4,
        help="Number of parallel training jobs",
    )
    p.add_argument(
        "--db",
        default="/data/v3.db",
        help="SQLite database path for registration",
    )
    p.add_argument("--val-days", type=int, default=10, help="Validation window in days")
    p.add_argument("--test-days", type=int, default=5, help="Test window in days")
    p.add_argument(
        "--train-mode",
        choices=["per-symbol", "joint"],
        default="per-symbol",
        help=(
            "per-symbol (default): each cell trains on its own symbol only. "
            "joint: legacy multi-symbol training with symbol_cat feature "
            "(use only for experiments)."
        ),
    )
    p.add_argument(
        "--cutover-delay-hours",
        type=float,
        default=None,
        help=(
            "Phase 5 — forwarded to scripts/register_model.py. Hours from now "
            "until each freshly-trained model is auto-promoted to "
            "paper_active=1. If omitted, register_model's default (24h) applies."
        ),
    )
    args = p.parse_args()

    symbols = args.symbols.split(",")
    horizons = [int(x) for x in args.horizons.split(",")]
    train_days_list = [int(x) for x in args.train_days.split(",")]
    eval_windows = [int(x) for x in args.evaluation_windows.split(",")]

    train_end = resolve_train_end(
        train_end_arg=args.train_end,
        feature_dir=args.feature_dir,
        symbols=symbols,
        val_days=args.val_days,
        test_days=args.test_days,
        buffer_days=args.buffer_days,
    )

    fleet = enumerate_fleet(symbols, horizons, train_days_list)
    state_path = Path(args.state)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    pending = filter_pending(fleet, state_path)
    state = (
        json.loads(state_path.read_text()) if state_path.exists() else
        {"completed": [], "failed": []}
    )

    print(f"fleet: {len(fleet)} cells, pending: {len(pending)}, parallel: {args.parallel}")

    with ProcessPoolExecutor(max_workers=args.parallel) as ex:
        futs = {
            ex.submit(
                train_one,
                c,
                train_end=train_end,
                feature_dir=args.feature_dir,
                output_root=args.output_root,
                evaluation_windows=eval_windows,
                db_path=args.db,
                val_days=args.val_days,
                test_days=args.test_days,
                train_mode=args.train_mode,
                cutover_delay_hours=args.cutover_delay_hours,
            ): c
            for c in pending
        }
        for fut in as_completed(futs):
            res = fut.result()
            print(f"  [{res['status']}] {res['name']} ({res['duration_s']:.1f}s)")
            if res["status"] == "DONE":
                state["completed"].append(res["name"])
            else:
                state["failed"].append({"name": res["name"], "err": res.get("err")})
            state_path.write_text(json.dumps(state, indent=2))

    print(
        f"done. completed={len(state['completed'])} failed={len(state['failed'])}"
    )


if __name__ == "__main__":
    main()
