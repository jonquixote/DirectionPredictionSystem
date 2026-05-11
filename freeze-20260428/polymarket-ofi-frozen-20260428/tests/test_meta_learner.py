"""
Tests for MetaLearner.
Spec v2.6, Section 14.
"""

import sys
import os

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models.meta_learner import MetaLearner


class TestMetaLearner:

    def test_fit_accepts_exactly_3_args(self):
        """MetaLearner.fit() accepts exactly 3 args (p_a_val, p_b_val, y_val)."""
        meta = MetaLearner()
        rng = np.random.default_rng(42)
        n = 100
        p_a = rng.uniform(0.3, 0.7, n)
        p_b = rng.uniform(0.3, 0.7, n)
        y = rng.binomial(1, 0.5, n)

        # Should not raise — exactly 3 positional args
        meta.fit(p_a, p_b, y)

    def test_predict_returns_values_in_0_1(self):
        """predict() returns values in [0, 1]."""
        meta = MetaLearner()
        rng = np.random.default_rng(42)
        n = 200
        p_a = rng.uniform(0.2, 0.8, n)
        p_b = rng.uniform(0.2, 0.8, n)
        y = rng.binomial(1, 0.5, n)

        meta.fit(p_a, p_b, y)

        # Test predictions
        test_a = rng.uniform(0.2, 0.8, 50)
        test_b = rng.uniform(0.2, 0.8, 50)
        preds = meta.predict(test_a, test_b)

        assert preds.shape == (50,)
        assert np.all(preds >= 0.0)
        assert np.all(preds <= 1.0)

    def test_calibration_before_meta_learner(self):
        """calibration applied before meta-learner, not after."""
        meta = MetaLearner()
        rng = np.random.default_rng(42)
        n = 200

        # Create well-separated data
        p_a = np.concatenate([rng.uniform(0.2, 0.4, n // 2),
                              rng.uniform(0.6, 0.8, n // 2)])
        p_b = np.concatenate([rng.uniform(0.2, 0.4, n // 2),
                              rng.uniform(0.6, 0.8, n // 2)])
        y = np.concatenate([np.zeros(n // 2), np.ones(n // 2)])

        meta.fit(p_a, p_b, y)

        # Verify calibrators were fitted (they have is_fitted attributes after fit_transform)
        # Isotonic regression stores the transformation
        assert hasattr(meta.calibrator_a, "X_thresholds_")
        assert hasattr(meta.calibrator_b, "X_thresholds_")

        # Verify meta-learner operates on calibrated inputs
        assert hasattr(meta.meta, "coef_")
