"""Policy config snapshot + version counter.

A "policy" is the full set of filter thresholds, Kelly parameters,
EV thresholds, per-symbol overrides, blackout hours, and any other
runtime-mutable knob that influences a trade decision. The snapshot is
hashed and versioned so every prediction can carry both the hash (what
the policy was) and the version (when it changed relative to other
changes). The version counter only increments when the canonical
serialization actually changes.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Tuple

from storage.provenance import policy_config_hash


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def policy_canonical_form(config: dict) -> dict:
    """Return the canonical (hash-stable) form of a policy config dict.

    Currently a pass-through (``json.dumps(sort_keys=True)`` already
    handles dict ordering). Reserved as the single normalization point
    so future changes (rounding, alias unification, etc.) live in one
    place.
    """
    return dict(config)


class PolicySnapshot:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def current(self) -> Tuple[int, str]:
        row = self._conn.execute(
            "SELECT decision_policy_version, policy_config_hash FROM policy_audit"
            " ORDER BY decision_policy_version DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return (-1, "")  # signals never captured
        return (int(row["decision_policy_version"]), row["policy_config_hash"])

    def capture(self, config: dict, initiated_by: str = "system") -> Tuple[int, str]:
        canon = policy_canonical_form(config)
        h = policy_config_hash(canon)
        with self._lock:
            cur_v, cur_h = self.current()
            if cur_h == h:
                return (cur_v, cur_h)
            new_v = 0 if cur_v < 0 else cur_v + 1
            self._conn.execute(
                "INSERT INTO policy_audit"
                " (ts, decision_policy_version, policy_config_hash,"
                "  snapshot_json, initiated_by)"
                " VALUES (?, ?, ?, ?, ?)",
                (_utc_iso(), new_v, h, json.dumps(canon, sort_keys=True),
                 initiated_by),
            )
            return (new_v, h)
