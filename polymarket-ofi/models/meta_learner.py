from __future__ import annotations
"""
MetaLearner: calibrated stacking of Track A and Track B.
Spec v2.6, Section 8.

CRITICAL: Calibrate both tracks BEFORE fitting meta-learner.
Calibration and meta-learner fit on validation set only.
"""

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


class MetaLearner:
    """
    Stacks Track A and Track B probability outputs.
    Calibration (isotonic) applied before meta-learner (LR), not after.
    """

    def __init__(self):
        self.calibrator_a = IsotonicRegression(out_of_bounds="clip")
        self.calibrator_b = IsotonicRegression(out_of_bounds="clip")
        self.meta = LogisticRegression()

    def fit(
        self,
        p_a_val: np.ndarray,
        p_b_val: np.ndarray,
        y_val: np.ndarray,
    ) -> None:
        """
        Fit on validation set only.
        Calibrate both tracks, then fit LR meta-learner on calibrated outputs.
        """
        p_a_cal = self.calibrator_a.fit_transform(p_a_val, y_val)
        p_b_cal = self.calibrator_b.fit_transform(p_b_val, y_val)
        X_meta = np.column_stack([p_a_cal, p_b_cal])
        self.meta.fit(X_meta, y_val)

    def predict(self, p_a: np.ndarray, p_b: np.ndarray) -> np.ndarray:
        """Returns calibrated, stacked probability predictions."""
        p_a_cal = self.calibrator_a.transform(p_a)
        p_b_cal = self.calibrator_b.transform(p_b)
        X = np.column_stack([p_a_cal, p_b_cal])
        return self.meta.predict_proba(X)[:, 1]
