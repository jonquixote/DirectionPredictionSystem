"""Per-boundary overlap / consensus writer.

After all paper-active models score a (boundary, symbol,
market_window) tuple, OverlapWriter records:
  - which models scored
  - their direction predictions
  - their calibrated confidences
  - whether all directions agree (consensus flag)
  - weighted-mean confidence (EWMA-weighted via per-model weight)

The consensus signal is logged but does NOT gate trades automatically
in Plan B. Plan C dashboard surfaces it; Plan B's trade gate is the
pairwise conflict rule (T20 — model_conflict = no trade).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class ModelScore:
    model_name: str
    direction: str
    calibrated_confidence: float
    weight: float = 1.0


class OverlapWriter:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def record_boundary(
        self,
        *,
        ts_contract_open_ms: int,
        symbol: str,
        market_window_seconds: int,
        registry_load_generation: int,
        scores: List[ModelScore],
    ) -> None:
        if not scores:
            return
        models = [s.model_name for s in scores]
        directions = {s.model_name: s.direction for s in scores}
        confidences = {s.model_name: float(s.calibrated_confidence) for s in scores}
        # Consensus: all directions agree
        directions_set = set(directions.values())
        consensus = 1 if len(directions_set) == 1 else 0
        consensus_direction: Optional[str] = (
            next(iter(directions_set)) if consensus else None
        )
        # Weighted-mean confidence
        total_w = sum(s.weight for s in scores) or 1.0
        weighted = sum(s.calibrated_confidence * s.weight for s in scores) / total_w
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO model_overlap ("
                " ts_contract_open_ms, symbol, market_window_seconds,"
                " models_scored_json, directions_json, confidences_json,"
                " consensus, consensus_direction, weighted_confidence,"
                " registry_load_generation"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    ts_contract_open_ms, symbol, market_window_seconds,
                    json.dumps(models),
                    json.dumps(directions),
                    json.dumps(confidences),
                    consensus, consensus_direction, weighted,
                    registry_load_generation,
                ),
            )
