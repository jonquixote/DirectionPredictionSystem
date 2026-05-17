"""Multi-window resolution row planner.

Every prediction emits one or more ``resolution_type='evaluation'``
rows at market-window boundaries aligned to each window's duration.
A 300s row is emitted at every 5-min boundary; 900s rows only at
:00/:15/:30/:45; 1800s rows only at :00/:30.  No native rows exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class ResolutionRow:
    market_window_seconds: int
    resolution_type: str
    ts_resolve_at_ms: int


def plan_resolution_rows(
    boundary_ms: int,
    training_horizon_seconds: int,
    evaluation_windows: Iterable[int],
) -> List[ResolutionRow]:
    if training_horizon_seconds <= 0:
        raise ValueError("training_horizon_seconds must be positive")
    boundary_sec = boundary_ms // 1000
    rows: List[ResolutionRow] = []
    for w in evaluation_windows:
        if boundary_sec % w != 0:
            continue
        rows.append(
            ResolutionRow(
                market_window_seconds=w,
                resolution_type="evaluation",
                ts_resolve_at_ms=boundary_ms + w * 1000,
            )
        )
    return rows
