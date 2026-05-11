"""Decay metric computations.

All functions take simple Python iterables — they're pure and easy
to test. The schedule of when these run lives in PaperTrader's
periodic loop (Plan B+ wires the cadence).
"""
from __future__ import annotations

from typing import Iterable, List, Tuple


def compute_brier_score(rows: Iterable[Tuple[float, bool]]) -> float:
    """Mean of (predicted - outcome)^2.

    Lower is better. 0 = perfect; 0.25 = always 0.5; 1.0 = inverted.
    """
    rows = list(rows)
    if not rows:
        return 0.0
    return sum((p - (1.0 if o else 0.0)) ** 2 for p, o in rows) / len(rows)


def compute_calibration_error(
    rows: Iterable[Tuple[float, bool]], bin_width: float = 0.05,
) -> float:
    """Mean absolute error per probability bin.

    Bin predictions by ``bin_width`` and compare each bin's mean
    prediction to its empirical win rate; weighted average over bins.
    """
    rows = list(rows)
    if not rows:
        return 0.0
    bins: dict = {}
    for p, o in rows:
        b = round(p / bin_width) * bin_width
        bins.setdefault(b, []).append((p, o))
    total = len(rows)
    weighted_err = 0.0
    for b, members in bins.items():
        avg_p = sum(p for p, _ in members) / len(members)
        actual = sum(1 for _, o in members if o) / len(members)
        weighted_err += abs(avg_p - actual) * (len(members) / total)
    return weighted_err


def compute_rolling_ev(ev_values: Iterable[float]) -> float:
    vals = list(ev_values)
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def compute_recency_weighted_ev(
    ev_values: Iterable[float], alpha: float = 0.05,
) -> float:
    """EWMA over per-trade EV. alpha is the decay rate.

    Older trades get exponentially less weight. alpha=0.05 = standard
    finance EWMA; alpha=0.30 = aggressively recent-biased.
    """
    vals = list(ev_values)
    if not vals:
        return 0.0
    n = len(vals)
    weight_total = 0.0
    weighted_sum = 0.0
    for i, v in enumerate(vals):
        # Newest gets weight 1.0; oldest gets (1-alpha)^(n-1)
        w = (1.0 - alpha) ** (n - 1 - i)
        weighted_sum += w * v
        weight_total += w
    return weighted_sum / weight_total if weight_total > 0 else 0.0
