"""Append-only writer for decay snapshots and evaluations."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class DecayWriter:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def write_snapshot(
        self, *, model_name: str, symbol: str,
        market_window_seconds: int, window_size: int,
        rolling_ev: Optional[float], recency_weighted_ev: Optional[float],
        rolling_win_rate: Optional[float], brier_score: Optional[float],
        calibration_error: Optional[float], sample_count: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO decay_metrics ("
                " ts, model_name, symbol, market_window_seconds,"
                " window_size, rolling_ev, recency_weighted_ev,"
                " rolling_win_rate, brier_score, calibration_error,"
                " sample_count"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_utc_iso(), model_name, symbol, market_window_seconds,
                 window_size, rolling_ev, recency_weighted_ev,
                 rolling_win_rate, brier_score, calibration_error,
                 sample_count),
            )

    def write_evaluation(
        self, *, model_name: str, symbol: str,
        market_window_seconds: int, eval_type: str,
        metric_value: Optional[float], threshold: Optional[float],
        triggered: bool, detail: Optional[dict] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO decay_evaluations ("
                " ts, model_name, symbol, market_window_seconds,"
                " eval_type, metric_value, threshold, triggered,"
                " detail_json"
                ") VALUES (?,?,?,?,?,?,?,?,?)",
                (_utc_iso(), model_name, symbol, market_window_seconds,
                 eval_type, metric_value, threshold, int(triggered),
                 json.dumps(detail) if detail is not None else None),
            )
