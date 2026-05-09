"""Registry generation counter.

Every time the model registry is reloaded (Plan B), the generation
counter is incremented and a new row is appended to ``registry_audit``.
Plan A only writes the bootstrap row (generation 0). Predictions stamp
the *current* generation, which lets pending resolutions and decision
traces remain unambiguous after a hot reload.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class RegistryState:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def bootstrap_if_empty(self, reason: str = "bootstrap") -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT count(*) AS n FROM registry_audit"
            ).fetchone()
            if row["n"] == 0:
                self._conn.execute(
                    "INSERT INTO registry_audit (ts, generation, reason, detail_json)"
                    " VALUES (?, 0, ?, NULL)",
                    (_utc_iso(), reason),
                )

    def current_generation(self) -> int:
        row = self._conn.execute(
            "SELECT MAX(generation) AS g FROM registry_audit"
        ).fetchone()
        return int(row["g"]) if row["g"] is not None else 0

    def increment(self, reason: str, detail: Optional[dict] = None) -> int:
        with self._lock:
            current = self.current_generation()
            new = current + 1
            self._conn.execute(
                "INSERT INTO registry_audit (ts, generation, reason, detail_json)"
                " VALUES (?, ?, ?, ?)",
                (_utc_iso(), new, reason,
                 json.dumps(detail) if detail is not None else None),
            )
            return new
