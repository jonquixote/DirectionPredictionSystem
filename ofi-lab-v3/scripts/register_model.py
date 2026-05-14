"""Register a trained model artifact into model_registry."""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path


def _sha256(path: Path) -> str:
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def register_model(
    *,
    conn: sqlite3.Connection,
    artifact_dir: str,
    evaluation_windows: list[int],
) -> str:
    """
    Register a trained model artifact into model_registry.

    Args:
        conn: SQLite database connection with row_factory set to sqlite3.Row
        artifact_dir: Path to directory containing model.lgb, metrics.json, feature_names.json
        evaluation_windows: List of evaluation window durations in seconds

    Returns:
        Model name (e.g., "h180_xrp_v3_330d")

    If a baseline model with the same name exists, updates only artifact pointers.
    Otherwise, inserts a new fleet model with is_baseline=0, paper_active=1, live_eligible=0.
    """
    art = Path(artifact_dir)
    metrics = json.loads((art / "metrics.json").read_text())
    horizon = metrics["horizon_seconds"]
    symbol = metrics["symbol"]
    train_days = metrics.get("train_days", 330)
    name = f"h{horizon}_{symbol.replace('USDT','').lower()}_v3_{train_days}d"

    artifact_hash = _sha256(art / "model.lgb")

    existing = conn.execute(
        "SELECT is_baseline, lifecycle_state, paper_active, live_eligible "
        "FROM model_registry WHERE name=?",
        (name,),
    ).fetchone()

    if existing and existing["is_baseline"]:
        # Don't trample baseline state — only refresh artifact pointer
        conn.execute(
            "UPDATE model_registry SET artifact_path=?, feature_names_path=?, "
            "artifact_hash=?, train_window_start=?, train_window_end=?, "
            "train_days=?, feature_version=?, evaluation_windows=? "
            "WHERE name=?",
            (
                str(art / "model.lgb"),
                str(art / "feature_names.json"),
                artifact_hash,
                metrics["train_window_start"],
                metrics["train_window_end"],
                train_days,
                metrics.get("feature_version", "v3"),
                json.dumps(evaluation_windows),
                name,
            ),
        )
    else:
        conn.execute(
            "INSERT OR REPLACE INTO model_registry "
            "(name, is_baseline, paper_active, live_eligible, lifecycle_state, "
            "symbol, training_horizon_seconds, generation, artifact_path, "
            "feature_names_path, artifact_hash, train_window_start, "
            "train_window_end, train_days, feature_version, evaluation_windows, "
            "filter_config_json, platform_active_json) "
            "VALUES (?, 0, 1, 0, 'active', ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, '{}', "
            "'{\"paper\":true,\"kalshi\":false,\"polymarket\":false}')",
            (
                name,
                symbol,
                horizon,
                str(art / "model.lgb"),
                str(art / "feature_names.json"),
                artifact_hash,
                metrics["train_window_start"],
                metrics["train_window_end"],
                train_days,
                metrics.get("feature_version", "v3"),
                json.dumps(evaluation_windows),
            ),
        )

    # Audit
    conn.execute(
        "INSERT INTO model_audit (model_name, action, by_user, detail) "
        "VALUES (?, 'register', ?, ?)",
        (name, "fleet_trainer", json.dumps(metrics)),
    )
    conn.commit()
    return name


def main():
    p = argparse.ArgumentParser(description="Register trained model artifact into registry")
    p.add_argument("--db", default="/data/v3.db", help="SQLite database path")
    p.add_argument(
        "--artifact-dir",
        required=True,
        help="Directory containing model.lgb, metrics.json, feature_names.json",
    )
    p.add_argument(
        "--evaluation-windows",
        default="300,900,1800",
        help="Comma-separated evaluation window durations in seconds",
    )
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    name = register_model(
        conn=conn,
        artifact_dir=args.artifact_dir,
        evaluation_windows=[int(x) for x in args.evaluation_windows.split(",")],
    )
    print(f"registered: {name}")


if __name__ == "__main__":
    main()
