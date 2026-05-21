"""Database connection factory.

All v3 storage flows through a single SQLite database. WAL mode is
enabled so the API server can read concurrently with the paper trader's
writes. Foreign keys are enforced for prediction → trade integrity.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = "/data/v3.db"


def open_database(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (and create if needed) the v3 SQLite database.

    Returns a connection with WAL journaling, foreign keys enforced,
    and a 5-second busy timeout to handle multi-process contention
    between the API server and the paper trader.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_schema(conn) -> None:
    """Apply the canonical schema. Idempotent.

    Reads ``storage/schema.sql`` and executes its DDL. SQLite's
    ``CREATE TABLE`` and ``CREATE INDEX`` are not natively idempotent,
    so the SQL file uses ``CREATE TABLE IF NOT EXISTS`` and
    ``CREATE INDEX IF NOT EXISTS`` for safe re-application.

    Also runs in-place ALTER TABLE migrations for new columns on
    existing tables (safe to re-run — checks column existence first).
    """
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    _run_migrations(conn)


def _run_migrations(conn) -> None:
    _add_column_if_missing(conn, "model_registry", "filter_config_json", "TEXT DEFAULT '{}'")
    _add_column_if_missing(conn, "model_registry", "platform_active_json",
        "TEXT DEFAULT '{\"paper\":true,\"kalshi\":false,\"polymarket\":false}'")
    # T3.2 — explicit fleet grouping column. Backfilled from train_window_end for legacy rows.
    _add_column_if_missing(conn, "model_registry", "fleet_version", "TEXT")
    conn.execute(
        "UPDATE model_registry SET fleet_version = train_window_end WHERE fleet_version IS NULL"
    )
    # Phase 5 — scheduled cutover lifecycle. Newly-trained non-baseline models
    # land as cutover_state='scheduled' + paper_active=0 with cutover_scheduled_at
    # set to NOW+24h (configurable). A background loop in dashboard_api flips
    # them to paper_active=1 / cutover_state='cutover' when the time arrives.
    # Existing rows are backfilled with cutover_state='cutover' (already live).
    _add_column_if_missing(conn, "model_registry", "cutover_scheduled_at", "TEXT")
    _add_column_if_missing(
        conn, "model_registry", "cutover_state", "TEXT DEFAULT 'cutover'"
    )
    _add_column_if_missing(conn, "model_registry", "cutover_decided_by", "TEXT")
    _add_column_if_missing(conn, "model_registry", "cutover_decided_at", "TEXT")
    # Backfill legacy rows: they are already live, so state='cutover',
    # decided_by='auto' (assume past auto-promotion), decided_at=created_at.
    conn.execute(
        "UPDATE model_registry SET cutover_state = 'cutover' "
        "WHERE cutover_state IS NULL"
    )
    conn.execute(
        "UPDATE model_registry SET cutover_decided_by = 'auto' "
        "WHERE cutover_decided_by IS NULL AND cutover_state = 'cutover'"
    )
    conn.execute(
        "UPDATE model_registry SET cutover_decided_at = created_at "
        "WHERE cutover_decided_at IS NULL AND cutover_state = 'cutover'"
    )
    # H2 — decay-join leakage hardening. Denormalized ms timestamp + window
    # cutoff sentinel for strict-< joins from analysis service.
    _add_column_if_missing(conn, "decay_metrics", "ts_ms", "INTEGER")
    _add_column_if_missing(conn, "decay_metrics", "computed_for_max_ts_ms", "INTEGER")
    _migrate_native_to_eval_indexes(conn)


def _migrate_native_to_eval_indexes(conn) -> None:
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()}
    if "idx_pred_native_for_decay" in existing:
        conn.execute("DROP INDEX idx_pred_native_for_decay")
        conn.execute(
            "CREATE INDEX idx_pred_eval_for_decay"
            " ON predictions(model_name, symbol, resolution_type, ts_contract_open_ms)"
            " WHERE resolution_type = 'evaluation' AND resolved = 1"
        )
    if "idx_trade_native_for_decay" in existing:
        conn.execute("DROP INDEX idx_trade_native_for_decay")
        conn.execute(
            "CREATE INDEX idx_trade_eval_for_decay"
            " ON paper_trades(model_name, symbol, resolution_type, ts_contract_open_ms)"
            " WHERE resolution_type = 'evaluation' AND resolved = 1"
        )
    if "idx_cal_native" in existing:
        conn.execute("DROP INDEX idx_cal_native")
        conn.execute(
            "CREATE INDEX idx_cal_eval"
            " ON calibration_outcomes(model_name, resolution_type)"
            " WHERE resolution_type = 'evaluation'"
        )


def _add_column_if_missing(conn, table: str, column: str, col_type: str) -> None:
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
