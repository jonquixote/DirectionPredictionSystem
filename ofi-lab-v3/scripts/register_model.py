"""Register a trained model artifact into model_registry."""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import date, timedelta
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
    fleet_version: str | None = None,
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

    # fleet_version: explicit arg > env var > metrics.train_window_end
    effective_fleet_version = (
        fleet_version
        or os.environ.get("V3_FLEET_VERSION")
        or metrics.get("train_window_end")
    )

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
            "train_days=?, feature_version=?, evaluation_windows=?, fleet_version=? "
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
                effective_fleet_version,
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
            "filter_config_json, platform_active_json, fleet_version) "
            "VALUES (?, 0, 1, 0, 'active', ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, '{}', "
            "'{\"paper\":true,\"kalshi\":false,\"polymarket\":false}', ?)",
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
                effective_fleet_version,
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


def demote_old_fleets(conn: sqlite3.Connection, days: int) -> int:
    """
    Demote non-baseline fleet models whose fleet_version is older than
    (today - days) by setting paper_active=0.

    Refuses to demote if doing so would leave zero active fleet models.

    Returns:
        Number of rows actually updated (may be 0 if idempotent re-run).
    """
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

    # Safety: count active fleet rows that would remain after demotion.
    remaining = conn.execute(
        "SELECT COUNT(*) FROM model_registry "
        "WHERE is_baseline = 0 "
        "  AND paper_active = 1 "
        "  AND (fleet_version IS NULL OR fleet_version >= ?)",
        (cutoff,),
    ).fetchone()[0]

    if remaining == 0:
        print(
            f"demote-fleets: REFUSED (cutoff={cutoff}, days={days}): "
            "demotion would leave zero active fleet models — skipping",
            file=sys.stderr,
        )
        return 0

    cur = conn.execute(
        "UPDATE model_registry "
        "   SET paper_active = 0 "
        " WHERE is_baseline = 0 "
        "   AND fleet_version IS NOT NULL "
        "   AND fleet_version < ? "
        "   AND paper_active = 1",
        (cutoff,),
    )
    updated = cur.rowcount or 0
    conn.commit()
    print(f"demote-fleets: cutoff={cutoff} (days={days}) rows_updated={updated}")
    return updated


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
    p.add_argument(
        "--fleet-version",
        default=None,
        help=(
            "Explicit fleet grouping label (e.g. '2026-05-16'). "
            "Falls back to V3_FLEET_VERSION env var, then train_window_end."
        ),
    )
    p.add_argument(
        "--demote-fleets-older-than",
        type=int,
        default=None,
        metavar="DAYS",
        help=(
            "After registration, set paper_active=0 on non-baseline fleet "
            "rows whose fleet_version is older than (today - DAYS). "
            "Refuses to run if it would leave zero active fleet models."
        ),
    )
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    name = register_model(
        conn=conn,
        artifact_dir=args.artifact_dir,
        evaluation_windows=[int(x) for x in args.evaluation_windows.split(",")],
        fleet_version=args.fleet_version,
    )
    print(f"registered: {name}")

    if args.demote_fleets_older_than is not None:
        demote_old_fleets(conn, args.demote_fleets_older_than)


if __name__ == "__main__":
    main()
