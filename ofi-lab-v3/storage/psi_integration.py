"""PSI (population stability index) computation.

Standalone helper. PaperTrader's decay refresh loop calls this against
the current vs reference prediction windows and writes a row of
eval_type='psi' to decay_evaluations.
"""
from __future__ import annotations

import math
from typing import Iterable, List


def _histogram(values: List[float], bins: int, lo: float, hi: float) -> List[float]:
    if not values or hi <= lo:
        return [0.0] * bins
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        if v < lo:
            counts[0] += 1
            continue
        if v >= hi:
            counts[-1] += 1
            continue
        idx = min(int((v - lo) / width), bins - 1)
        counts[idx] += 1
    total = sum(counts) or 1
    return [c / total for c in counts]


def compute_psi_against_reference(
    *, current: Iterable[float], reference: Iterable[float], bins: int = 10,
) -> float:
    cur = list(current)
    ref = list(reference)
    if not cur or not ref:
        return 0.0
    lo = min(min(cur), min(ref))
    hi = max(max(cur), max(ref))
    if hi == lo:
        return 0.0
    cur_hist = _histogram(cur, bins, lo, hi)
    ref_hist = _histogram(ref, bins, lo, hi)
    psi = 0.0
    for c, r in zip(cur_hist, ref_hist):
        # Smooth zeros to avoid log(0)
        c = max(c, 1e-6)
        r = max(r, 1e-6)
        psi += (c - r) * math.log(c / r)
    return psi
