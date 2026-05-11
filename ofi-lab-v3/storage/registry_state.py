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

    def get(self, model: str):
        """Get model row from registry."""
        row = self._conn.execute(
            "SELECT * FROM model_registry WHERE name=?", (model,)
        ).fetchone()
        return row

    def set_paper(self, *, model: str, enabled: bool, by: str) -> None:
        """Enable or disable paper trading for a model."""
        self._conn.execute(
            "UPDATE model_registry SET paper_active=? WHERE name=?",
            (1 if enabled else 0, model)
        )
        self._conn.commit()
        self._audit(model, "enable_paper" if enabled else "disable_paper", by, None)

    def set_live(self, *, model: str, enabled: bool, by: str) -> None:
        """Enable or disable live trading for a model."""
        self._conn.execute(
            "UPDATE model_registry SET live_eligible=? WHERE name=?",
            (1 if enabled else 0, model)
        )
        self._conn.commit()
        self._audit(model, "enable_live" if enabled else "disable_live", by, None)

    def suspend(self, *, model: str, reason: str) -> None:
        """Suspend a model (disable both paper and live, unless baseline)."""
        row = self._conn.execute(
            "SELECT is_baseline FROM model_registry WHERE name=?", (model,)
        ).fetchone()
        if row and row["is_baseline"]:
            return  # baseline never suspended
        self._conn.execute(
            "UPDATE model_registry SET paper_active=0, live_eligible=0, "
            "lifecycle_state='suspended' WHERE name=?", (model,)
        )
        self._conn.commit()
        self._audit(model, "suspend", "lifecycle_fsm", reason)

    def _audit(self, model: str, action: str, by: str, detail: Optional[str]) -> None:
        """Record an audit entry for a model action."""
        self._conn.execute(
            "INSERT INTO model_audit (model_name, action, by_user, detail, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (model, action, by, detail, _utc_iso())
        )
        self._conn.commit()
