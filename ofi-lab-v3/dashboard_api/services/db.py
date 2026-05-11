"""SQLite connection helper for dashboard API routers.

Returns a WAL-mode connection to the v3 database (same DB the
PaperTrader writes to).  Thread-safe because each call creates
a fresh connection with ``check_same_thread=False``.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def get_db() -> sqlite3.Connection:
    """Return a ready-to-use SQLite connection."""
    db_path = os.environ.get(
        "STORAGE_DB_PATH",
        str(Path(__file__).resolve().parents[2] / "data" / "v3.db"),
    )
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
