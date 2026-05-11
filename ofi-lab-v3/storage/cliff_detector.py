"""Sharp performance drop detection.

A "cliff" is a sudden divergence between recent and baseline metrics:
  - recent rolling EV drops below baseline by ``cliff_drop_threshold``
  - calibration error spikes above baseline by ``spike_threshold``
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CliffResult:
    triggered: bool
    metric: str
    delta: float
    reason: str


def detect_ev_cliff(
    *, recent_ev: Iterable[float], baseline_ev: float,
    cliff_drop_threshold: float,
) -> CliffResult:
    vals = list(recent_ev)
    if not vals:
        return CliffResult(triggered=False, metric="ev",
                           delta=0.0, reason="no recent data")
    mean_recent = sum(vals) / len(vals)
    delta = baseline_ev - mean_recent
    triggered = delta >= cliff_drop_threshold
    reason = (
        f"EV drop {delta:.4f} >= cliff threshold {cliff_drop_threshold:.4f}"
        if triggered else "stable"
    )
    return CliffResult(triggered=triggered, metric="ev",
                       delta=delta, reason=reason)


def detect_calibration_cliff(
    *, recent_error: float, baseline_error: float, spike_threshold: float,
) -> CliffResult:
    delta = recent_error - baseline_error
    triggered = delta >= spike_threshold
    reason = (
        f"calibration error spike {delta:.4f} >= threshold {spike_threshold:.4f}"
        if triggered else "stable"
    )
    return CliffResult(triggered=triggered, metric="calibration_error",
                       delta=delta, reason=reason)
