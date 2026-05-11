from __future__ import annotations
"""
FeeRegimeChecker — Binance fee regime boundary detection.
Spec v2.6, Section 3.1.

Connects TRAINING_BOUNDARIES to Stage 1 structural gate.
Returns True if the current timestamp falls within a regime
that the model was trained on.
"""

import datetime


class FeeRegimeChecker:
    """
    check_structural() requires fee_regime_active — this produces it.

    Correct approach: find the last boundary at or before trained_start
    to identify which regime the training data belongs to.
    """

    def __init__(
        self, boundaries: list[str], trained_start: str, trained_end: str
    ):
        self.boundaries = [
            datetime.date.fromisoformat(b) for b in sorted(boundaries)
        ]
        self.trained_start = datetime.date.fromisoformat(trained_start)
        self.trained_end = datetime.date.fromisoformat(trained_end)

    def is_active(self, current_ts_utc: datetime.datetime) -> bool:
        """
        Returns True if current_ts_utc falls within the same fee regime
        as the training data.

        Anchors on trained_start (not trained_end) to handle retroactive
        boundary discovery correctly.
        """
        current_date = current_ts_utc.date()
        # Find the last boundary at or before trained_start
        trained_regime_start = datetime.date.min
        for b in self.boundaries:
            if b <= self.trained_start:
                trained_regime_start = b
        # Active if no boundary falls between trained_regime_start and current_date
        for b in self.boundaries:
            if trained_regime_start < b <= current_date:
                return False
        return True
