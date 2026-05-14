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


def _add_column_if_missing(conn, table: str, column: str, col_type: str) -> None:
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
