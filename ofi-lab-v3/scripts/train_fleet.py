"""Drive fleet training: iterate (symbol, horizon, train_days), shell to retrain.py."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
DEFAULT_HORIZONS = [60, 180, 300, 600, 900, 1200, 1800]
DEFAULT_TRAIN_DAYS = [90, 180, 330]


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


def train_one(cell, *, train_end, feature_dir, output_root, evaluation_windows, db_path=None, val_days=10, test_days=5):
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
        required=True,
        help="Training end date YYYY-MM-DD",
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
    args = p.parse_args()

    symbols = args.symbols.split(",")
    horizons = [int(x) for x in args.horizons.split(",")]
    train_days_list = [int(x) for x in args.train_days.split(",")]
    eval_windows = [int(x) for x in args.evaluation_windows.split(",")]

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
                train_end=args.train_end,
                feature_dir=args.feature_dir,
                output_root=args.output_root,
                evaluation_windows=eval_windows,
                db_path=args.db,
                val_days=args.val_days,
                test_days=args.test_days,
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
