"""Model registry — JSON-backed source of truth for active models.

Each model entry carries identity (path, hashes, feature version,
training horizon), runtime flags (paper_trading_enabled,
kalshi_live_enabled), and lifecycle state. The baseline model is
protected against removal: any reload that drops the baseline raises
``BaselineRemovalError``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Optional


VALID_LIFECYCLE_STATES = {
    "prediction_only",
    "live_eligible",
    "live_active",
    "live_suspended",
    "requalification",
    "retired",
}


class BaselineRemovalError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelEntry:
    name: str
    path: str
    feature_names_path: str
    horizon_seconds: int
    symbol: str
    feature_version: str
    train_window_start: Optional[str]
    train_window_end: Optional[str]
    train_cutoff: Optional[str]
    is_baseline: bool
    paper_trading_enabled: bool
    kalshi_live_enabled: bool
    lifecycle_state: str
    min_markets_for_kalshi: int
    min_days_for_kalshi: int

    def __post_init__(self) -> None:
        if self.lifecycle_state not in VALID_LIFECYCLE_STATES:
            raise ValueError(
                f"lifecycle_state {self.lifecycle_state!r} not in {VALID_LIFECYCLE_STATES}"
            )

    def to_dict(self) -> dict:
        return asdict(self)


class ModelRegistry:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._entries: Dict[str, ModelEntry] = {}
        self._baseline_name: Optional[str] = None

    def _parse_entries(self, raw: dict) -> tuple[Dict[str, ModelEntry], Optional[str]]:
        """Parse raw JSON into entries dict and baseline name."""
        new_entries: Dict[str, ModelEntry] = {}
        baseline: Optional[str] = None
        for name, body in raw.get("models", {}).items():
            entry = ModelEntry(
                name=name,
                path=body["path"],
                feature_names_path=body["feature_names_path"],
                horizon_seconds=int(body["horizon_seconds"]),
                symbol=body["symbol"],
                feature_version=body["feature_version"],
                train_window_start=body.get("train_window_start"),
                train_window_end=body.get("train_window_end"),
                train_cutoff=body.get("train_cutoff"),
                is_baseline=bool(body.get("is_baseline", False)),
                paper_trading_enabled=bool(body.get("paper_trading_enabled", False)),
                kalshi_live_enabled=bool(body.get("kalshi_live_enabled", False)),
                lifecycle_state=body.get("lifecycle_state", "prediction_only"),
                min_markets_for_kalshi=int(body.get("min_markets_for_kalshi", 200)),
                min_days_for_kalshi=int(body.get("min_days_for_kalshi", 14)),
            )
            new_entries[name] = entry
            if entry.is_baseline:
                baseline = name
        return new_entries, baseline

    def load(self) -> None:
        raw = json.loads(self._path.read_text())
        new_entries, baseline = self._parse_entries(raw)
        if baseline is None:
            raise BaselineRemovalError(
                f"No model in {self._path} has is_baseline=true"
            )
        self._entries = new_entries
        self._baseline_name = baseline

    def reload(self, registry_state, reason: str,
               detail: Optional[dict] = None) -> int:
        """Re-read the registry file and increment generation atomically.

        On any error (including missing baseline), in-memory state is
        unchanged and the generation is NOT incremented.
        """
        raw = json.loads(self._path.read_text())
        new_entries, baseline = self._parse_entries(raw)
        if baseline is None:
            raise BaselineRemovalError(
                f"reload rejected: no baseline in {self._path}"
            )
        # Atomic swap + audit row
        self._entries = new_entries
        self._baseline_name = baseline
        return registry_state.increment(reason=reason, detail=detail)

    def reload_with_queue_cleanup(
        self, registry_state, pending_queue,
        reason: str, detail: Optional[dict] = None,
    ):
        """Reload + prune pending queue for any model removed by the reload.

        Returns (pruned_count, new_generation).
        """
        new_gen = self.reload(registry_state, reason=reason, detail=detail)
        active_names = set(self._entries.keys())
        pruned = pending_queue.prune_for_models(active_names)
        pending_queue.persist()
        return pruned, new_gen

    def entries(self) -> Dict[str, ModelEntry]:
        return dict(self._entries)

    def active_paper_models(self) -> Dict[str, ModelEntry]:
        return {n: e for n, e in self._entries.items() if e.paper_trading_enabled}

    def baseline_name(self) -> str:
        if self._baseline_name is None:
            raise BaselineRemovalError("registry not loaded or baseline missing")
        return self._baseline_name
