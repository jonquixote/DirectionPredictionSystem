"""Multi-window resolution row planner.

Every prediction emits one ``resolution_type='native'`` row at the
model's native training horizon and zero or more
``resolution_type='evaluation'`` rows at the other live market windows.
Lifecycle, decay, calibration, and rolling-EV queries filter on
``resolution_type='native'`` so evaluation rows never contaminate core
model statistics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class ResolutionRow:
    market_window_seconds: int
    resolution_type: str  # 'native' | 'evaluation'
    ts_resolve_at_ms: int


def plan_resolution_rows(
    boundary_ms: int,
    training_horizon_seconds: int,
    evaluation_windows: Iterable[int],
) -> List[ResolutionRow]:
    if training_horizon_seconds <= 0:
        raise ValueError("training_horizon_seconds must be positive")
    rows: List[ResolutionRow] = [
        ResolutionRow(
            market_window_seconds=training_horizon_seconds,
            resolution_type="native",
            ts_resolve_at_ms=boundary_ms + training_horizon_seconds * 1000,
        )
    ]
    for w in evaluation_windows:
        if w == training_horizon_seconds:
            continue  # native already emitted
        rows.append(
            ResolutionRow(
                market_window_seconds=w,
                resolution_type="evaluation",
                ts_resolve_at_ms=boundary_ms + w * 1000,
            )
        )
    return rows
