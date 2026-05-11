"""Conflict detection + EWMA weighting helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List


@dataclass(frozen=True)
class ConflictResult:
    has_conflict: bool
    models: List[str]
    directions: Dict[str, str]


def detect_conflict(directions: Dict[str, str]) -> ConflictResult:
    unique = set(directions.values())
    return ConflictResult(
        has_conflict=(len(unique) > 1),
        models=sorted(directions.keys()),
        directions=dict(directions),
    )


def rolling_win_rate(outcomes: Iterable[bool]) -> float:
    out = list(outcomes)
    if not out:
        return 0.0
    return sum(1 for x in out if x) / len(out)


def ewma_win_rate(outcomes: Iterable[bool], alpha: float = 0.05) -> float:
    """Exponentially weighted win rate.

    Older outcomes get exponentially less weight. alpha=0.05 puts ~25%
    of total weight on the most recent 5 outcomes (standard finance
    EWMA). alpha=0.5 makes the recent few dominate.
    """
    out = list(outcomes)
    if not out:
        return 0.0
    # Iterate oldest-to-newest with increasing weight
    n = len(out)
    weighted_sum = 0.0
    weight_total = 0.0
    for i, won in enumerate(out):
        # Newest gets weight=1.0; oldest gets weight=(1-alpha)**(n-1)
        w = (1.0 - alpha) ** (n - 1 - i)
        weighted_sum += w * (1.0 if won else 0.0)
        weight_total += w
    if weight_total == 0:
        return 0.0
    return weighted_sum / weight_total


def ewma_weight(
    outcomes: Iterable[bool], alpha: float, min_weight: float,
    min_samples: int,
) -> float:
    out = list(outcomes)
    if len(out) < min_samples:
        return min_weight
    return max(min_weight, ewma_win_rate(out, alpha=alpha))
