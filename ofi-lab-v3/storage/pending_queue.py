"""Pending-resolution queue.

Keyed on the same composite the idempotency index uses so hot-reload
cleanup is unambiguous. Persisted as a single JSON file (atomic
write-and-rename). Plan B replaces the JSON file with a SQLite table
if scale demands; the public API stays the same.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator, Set


@dataclass
class PendingEntry:
    prediction_id: str
    boundary_ms: int
    model_name: str
    symbol: str
    market_window_seconds: int
    registry_load_generation: int
    ts_resolve_at_ms: int
    resolution_type: str   # 'native' | 'evaluation'
    price_at_open: float
    # Note: no full features dict here — Steering 10i. Verbose features
    # live in decision_traces.

    def composite_key(self) -> tuple:
        return (
            self.model_name, self.symbol, self.market_window_seconds,
            self.boundary_ms, self.registry_load_generation,
        )


class PendingResolutionQueue:
    def __init__(self, path) -> None:
        self._path = Path(path)
        self._entries: dict[str, PendingEntry] = {}
        if self._path.exists():
            self.load()

    def load(self) -> None:
        raw = json.loads(self._path.read_text())
        self._entries = {r["prediction_id"]: PendingEntry(**r) for r in raw}

    def persist(self) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps([asdict(e) for e in self._entries.values()]))
        os.replace(tmp, self._path)

    def enqueue(self, entry: PendingEntry) -> None:
        self._entries[entry.prediction_id] = entry

    def remove(self, prediction_id: str) -> None:
        self._entries.pop(prediction_id, None)

    def iter_all(self) -> Iterator[PendingEntry]:
        return iter(self._entries.values())

    def iter_ripe(self, now_ms: int) -> Iterator[PendingEntry]:
        for e in self._entries.values():
            if e.ts_resolve_at_ms <= now_ms:
                yield e

    def prune_for_models(self, active_models: Set[str]) -> int:
        before = len(self._entries)
        self._entries = {
            pid: e for pid, e in self._entries.items()
            if e.model_name in active_models
        }
        return before - len(self._entries)
