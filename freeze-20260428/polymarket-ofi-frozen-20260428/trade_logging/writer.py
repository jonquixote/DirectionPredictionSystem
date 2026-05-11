from __future__ import annotations
"""
Persistent SQLite log writer with background write queue.
Spec v2.6, Section 10.

Schema loaded from schema.sql at runtime (single source of truth).
"""

import os
import queue
import sqlite3
import threading


def _load_schema() -> str:
    """Load schema SQL from schema.sql relative to this file."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r") as f:
        return f.read()


class LogWriter:
    """
    Persistent SQLite connection with background write queue.
    Eliminates per-insert open/close overhead at logging frequency.
    Thread-safe: producer calls enqueue(), background thread drains queue.
    Flush and close explicitly on shutdown.
    """

    def __init__(self, db_path: str, batch_size: int = 50):
        self.db_path = db_path
        self.batch_size = batch_size
        self._queue: queue.Queue = queue.Queue()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        # executescript() runs all statements (not just the first before ;)
        # and issues an implicit COMMIT.
        self._conn.executescript(_load_schema())
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def enqueue(self, record: dict) -> None:
        """Non-blocking. Call from execution loop."""
        self._queue.put(record)

    def _worker(self) -> None:
        batch = []
        while True:
            try:
                record = self._queue.get(timeout=1.0)
                if record is None:  # Shutdown sentinel
                    if batch:
                        self._flush(batch)
                    break
                batch.append(record)
                if len(batch) >= self.batch_size:
                    self._flush(batch)
                    batch = []
            except queue.Empty:
                if batch:
                    self._flush(batch)
                    batch = []

    def _flush(self, batch: list) -> None:
        if not batch:
            return
        keys = list(batch[0].keys())
        placeholders = ", ".join(["?"] * len(keys))
        col_str = ", ".join(keys)
        rows = [list(r.values()) for r in batch]
        self._conn.executemany(
            f"INSERT INTO candidate_trades ({col_str}) VALUES ({placeholders})",
            rows,
        )
        self._conn.commit()

    def shutdown(self) -> None:
        """Flush remaining records and close connection cleanly."""
        self._queue.put(None)
        self._thread.join()
        self._conn.close()
