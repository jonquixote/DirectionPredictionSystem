"""
Tests for MLOFICalculator.
Spec v2.6, Section 14.
"""

import sys
import os

import numpy as np
import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from feature_engineering.mlofi import MLOFICalculator


class TestMLOFIWeights:
    """MLOFI weight sum: sum(1/k for k in 1..10) — not off-by-one."""

    def test_weight_sum(self):
        """Verify weight sum matches harmonic number H_10."""
        calc = MLOFICalculator(levels=10)
        expected_sum = sum(1.0 / k for k in range(1, 11))

        # With equal bid/ask volumes, imbalance=0 at each level, so MLOFI=0
        # With all bid and no ask, imbalance=1 at each level, so MLOFI = sum(1/k)
        bids = [(100 - i, 1.0) for i in range(10)]
        asks = [(101 + i, 0.0) for i in range(10)]  # zero ask volume impossible
        # Use asymmetric volumes instead
        bids = [(100 - i, 10.0) for i in range(10)]
        asks = [(101 + i, 0.0001) for i in range(10)]  # near-zero ask

        mlofi = calc.compute_mlofi(bids, asks)
        # When v_ask ≈ 0, imbalance ≈ 1 for each level → MLOFI ≈ H_10
        assert mlofi == pytest.approx(expected_sum, rel=0.01)


class TestMADNormalisation:
    """MAD normalisation behaviour."""

    def test_constant_series_returns_zero(self):
        """MAD normalisation returns 0 on constant series."""
        calc = MLOFICalculator(levels=5, mad_window=100)
        # Fill history with constant value
        for _ in range(20):
            result = calc.normalise_mad(5.0)
        # MAD = 0 → returns 0.0
        assert result == 0.0

    def test_zero_mad_no_division_error(self):
        """MAD normalisation handles zero MAD without division error."""
        calc = MLOFICalculator(levels=5, mad_window=100)
        for _ in range(20):
            val = calc.normalise_mad(42.0)
        # Should not raise, should return 0.0
        assert val == 0.0

    def test_heavy_tail_mad_less_than_std(self):
        """Heavy tail: MAD < std for synthetic Cauchy-distributed series."""
        rng = np.random.default_rng(42)
        cauchy_data = rng.standard_cauchy(2000)

        calc = MLOFICalculator(levels=5, mad_window=2000)
        for v in cauchy_data:
            calc.normalise_mad(v)

        arr = np.array(calc.history)
        median = np.median(arr)
        mad = np.median(np.abs(arr - median))
        std = np.std(arr)

        assert mad < std, f"Expected MAD ({mad}) < std ({std}) for heavy-tailed data"

    def test_warmup_returns_zero(self):
        """First 10 values (warmup period) should return 0.0."""
        calc = MLOFICalculator(levels=5, mad_window=100)
        for i in range(10):
            result = calc.normalise_mad(float(i))
            assert result == 0.0, f"Warmup value {i} should return 0.0, got {result}"

    def test_post_warmup_returns_nonzero(self):
        """After warmup, non-median values should produce non-zero normalised output."""
        calc = MLOFICalculator(levels=5, mad_window=100)
        # Build diverse history
        for i in range(20):
            calc.normalise_mad(float(i))
        # An outlier should produce non-zero
        result = calc.normalise_mad(1000.0)
        assert result != 0.0
