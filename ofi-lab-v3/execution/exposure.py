"""Per-model open-position cap tracker.

Plan B's exposure cap is in-memory and per-process. Plan B+ persists
to SQLite and shares across paper_trader / api_server for race-free
display.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Set


@dataclass(frozen=True)
class ExposureBlock:
    allowed: bool
    current: int
    cap: Optional[int]


class ExposureCap:
    def __init__(self, max_open_per_model: Dict[str, int]) -> None:
        self._caps = dict(max_open_per_model)
        self._open: Dict[str, Set[str]] = {}

    def open_count(self, model_name: str) -> int:
        return len(self._open.get(model_name, set()))

    def record_open(self, model_name: str, trade_id: str) -> None:
        self._open.setdefault(model_name, set()).add(trade_id)

    def record_close(self, model_name: str, trade_id: str) -> None:
        if model_name in self._open:
            self._open[model_name].discard(trade_id)

    def can_open(self, model_name: str) -> ExposureBlock:
        cap = self._caps.get(model_name)
        current = self.open_count(model_name)
        if cap is None:
            return ExposureBlock(allowed=True, current=current, cap=None)
        return ExposureBlock(allowed=(current < cap), current=current, cap=cap)
