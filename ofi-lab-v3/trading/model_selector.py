from __future__ import annotations

import dataclasses
import sqlite3
from typing import List, Optional


@dataclasses.dataclass(frozen=True)
class SelectionResult:
    strategy: str
    selected: List[str]
    blocked: List[str]


class ModelSelector:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def select(
        self,
        symbol: str,
        market_window_seconds: int,
        candidate_models: List[str],
    ) -> SelectionResult:
        row = self._conn.execute(
            "SELECT strategy, selected_model_name, committee_config_json "
            "FROM model_selection WHERE symbol=? AND market_window_seconds=?",
            (symbol, market_window_seconds),
        ).fetchone()

        if row is None:
            return SelectionResult(strategy="all", selected=list(candidate_models), blocked=[])

        strategy = row[0]
        selected_model = row[1]

        if strategy == "disabled":
            return SelectionResult(strategy="disabled", selected=[], blocked=list(candidate_models))

        if strategy == "single_model":
            if selected_model and selected_model in candidate_models:
                blocked = [m for m in candidate_models if m != selected_model]
                return SelectionResult(strategy="single_model", selected=[selected_model], blocked=blocked)
            return SelectionResult(strategy="all", selected=list(candidate_models), blocked=[])

        if strategy == "best_ev":
            best = self._pick_best_ev(symbol, market_window_seconds, candidate_models)
            if best:
                blocked = [m for m in candidate_models if m != best]
                return SelectionResult(strategy="best_ev", selected=[best], blocked=blocked)
            return SelectionResult(strategy="all", selected=list(candidate_models), blocked=[])

        if strategy == "committee_weighted":
            return SelectionResult(strategy="committee_weighted", selected=list(candidate_models), blocked=[])

        return SelectionResult(strategy="all", selected=list(candidate_models), blocked=[])

    def _pick_best_ev(self, symbol: str, mws: int, candidates: List[str]) -> Optional[str]:
        rows = self._conn.execute(
            "SELECT model_name, recency_weighted_ev FROM decay_metrics "
            "WHERE symbol=? AND market_window_seconds=? "
            "AND model_name IN ({}) "
            "ORDER BY ts DESC".format(",".join("?" * len(candidates))),
            [symbol, mws] + list(candidates),
        ).fetchall()
        best_model = None
        best_ev = -999.0
        seen = set()
        for model_name, ev in rows:
            if model_name in seen:
                continue
            seen.add(model_name)
            if ev is not None and ev > best_ev:
                best_ev = ev
                best_model = model_name
        return best_model

    def should_block_model(self, symbol: str, mws: int, model_name: str, candidate_models: List[str]) -> bool:
        result = self.select(symbol, mws, candidate_models)
        return model_name in result.blocked
