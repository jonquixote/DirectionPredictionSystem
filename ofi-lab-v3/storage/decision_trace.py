"""Verbose decision trace writer.

Append-only forensic log. Every prediction-with-trade-decision (whether
executed, suppressed, or gated) writes a row capturing the full filter
chain, Kelly math, fee computation, and platform gating context. Linked
to the prediction by ``prediction_id``.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class FilterEval:
    name: str
    threshold: Optional[float]
    input_value: Optional[float]
    passed: bool

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "threshold": self.threshold,
            "input_value": self.input_value,
            "passed": int(self.passed),
        }


class DecisionTraceWriter:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def write(
        self,
        *,
        prediction_id: str,
        filters: Iterable[FilterEval],
        kelly_raw: Optional[float],
        kelly_capped: Optional[float],
        bankroll_used: Optional[float],
        per_trade_cap_usdc: Optional[float],
        fee_model: str,
        fee_amount: Optional[float],
        platform_gate: Optional[dict],
        warmup: bool,
        consensus_data: Optional[dict],
        policy_config_hash: str,
        calibration_map_hash: str,
        registry_load_generation: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO decision_traces ("
                " prediction_id, ts, filters_json,"
                " kelly_raw, kelly_capped, bankroll_used, per_trade_cap_usdc,"
                " fee_model, fee_amount, platform_gate_json,"
                " warmup, consensus_data_json,"
                " policy_config_hash, calibration_map_hash,"
                " registry_load_generation"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    prediction_id, _utc_iso(),
                    json.dumps([f.to_dict() for f in filters]),
                    kelly_raw, kelly_capped, bankroll_used, per_trade_cap_usdc,
                    fee_model, fee_amount,
                    json.dumps(platform_gate) if platform_gate is not None else None,
                    int(warmup),
                    json.dumps(consensus_data) if consensus_data is not None else None,
                    policy_config_hash, calibration_map_hash,
                    registry_load_generation,
                ),
            )
