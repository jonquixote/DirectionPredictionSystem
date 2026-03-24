from __future__ import annotations
"""
Multi-Level Order Flow Imbalance calculator.
Spec v2.6, Section 4.

Uses w_k = 1/k weighting with MAD normalisation (never z-score).
Source: Maitrier et al. arXiv June 2025 (confirmed).
"""

from collections import deque

import numpy as np


class MLOFICalculator:
    """
    Multi-Level Order Flow Imbalance with w_k = 1/k weighting.
    Requires MAD normalisation — never z-score.

    IMPLEMENTATION NOTE — OBI vs OFI:
    compute_mlofi() computes a weighted sum of volume imbalances
    (v_bid - v_ask) / (v_bid + v_ask) at each level — technically
    Order Book Imbalance (OBI), a snapshot of current queue state.
    Traditional OFI (Cont et al. 2014) tracks *changes* in queue volumes
    between consecutive ticks.
    """

    def __init__(self, levels: int = 10, mad_window: int = 1000):
        self.levels = levels
        self.history: deque = deque(maxlen=mad_window)

    def compute_mlofi(
        self, bids: list[tuple[float, float]], asks: list[tuple[float, float]]
    ) -> float:
        """
        bids: list of (price, volume) sorted descending, len >= levels
        asks: list of (price, volume) sorted ascending, len >= levels
        Returns: raw MLOFI value (not yet normalised)
        """
        mlofi = 0.0
        for k in range(1, self.levels + 1):
            weight = 1.0 / k
            v_bid = bids[k - 1][1] if k <= len(bids) else 0.0
            v_ask = asks[k - 1][1] if k <= len(asks) else 0.0
            denom = v_bid + v_ask
            imbalance = (v_bid - v_ask) / denom if denom > 0 else 0.0
            mlofi += weight * imbalance
        return mlofi

    def normalise_mad(self, value: float) -> float:
        """
        MAD normalisation. Use this, not z-score.
        OFI has heavy-tailed distributions with slow-decaying kurtosis.
        Standard normalisation underestimates tail risk.
        """
        if len(self.history) < 10:
            self.history.append(value)
            return 0.0
        arr = np.array(self.history)
        median = np.median(arr)
        mad = np.median(np.abs(arr - median))
        self.history.append(value)
        if mad == 0:
            return 0.0
        return (value - median) / mad
