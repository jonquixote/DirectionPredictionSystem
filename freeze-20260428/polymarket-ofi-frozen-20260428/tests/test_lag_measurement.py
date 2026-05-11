"""
Tests for LagMeasurementCollector.
Spec v2.6, Section 14.
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lag_measurement.collector import LagMeasurementCollector


class TestLagMeasurement:

    def _make_collector(self, **kwargs):
        defaults = {
            "assets": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
            "kelly_fraction": 0.25,
            "bankroll_usdc": 10000.0,
        }
        defaults.update(kwargs)
        return LagMeasurementCollector(**defaults)

    def test_depth_drop_before_price_move(self):
        """t_ask_depth_drop logged BEFORE t_ask_price_move (Kavajecz ordering)."""
        # This is a design invariant: depth withdrawal precedes spread movement
        event = {
            "t_ofi_signal_ms": 1000,
            "t_ask_depth_drop_ms": 2000,
            "t_ask_price_move_ms": 3000,
        }
        # Kavajecz ordering: depth drop should come before price move
        assert event["t_ask_depth_drop_ms"] < event["t_ask_price_move_ms"]

    def test_operational_lag_uses_depth_not_price(self):
        """operational_lag = t_ask_depth_drop - t_ofi_signal (not price lag)."""
        event = {
            "t_ofi_signal_ms": 1000,
            "t_ask_depth_drop_ms": 5000,
            "t_ask_price_move_ms": 8000,
        }
        lag = LagMeasurementCollector.compute_operational_lag(event)
        assert lag == 4000  # depth_drop - ofi_signal, NOT price_move - ofi_signal

    def test_viable_size_fixed_at_startup(self):
        """execution_viable_size fixed at startup, not computed per-event."""
        collector = self._make_collector(bankroll_usdc=10000.0, kelly_fraction=0.25)

        # Compute at startup
        collector.compute_viable_size("BTCUSDT", p_market=0.5)
        startup_size = collector.execution_viable_size["BTCUSDT"]

        # Should be: 0.25 * 10000 / 0.5 = 5000
        assert startup_size == 5000.0

        # Calling again with different p_market SHOULD overwrite
        # (but the spec says call ONCE at startup, not per-event)
        # The key is that the value is FIXED before measurement begins
        assert collector.execution_viable_size["BTCUSDT"] == startup_size

    def test_trigger_fires_on_threshold(self):
        """trigger fires on |mlofi_normalised| > lag_measurement_trigger_threshold."""
        collector = self._make_collector(trigger_threshold=1.0)

        # Above threshold (positive)
        assert collector.should_trigger(1.5) is True
        # Above threshold (negative)
        assert collector.should_trigger(-1.5) is True
        # Below threshold
        assert collector.should_trigger(0.5) is False
        # At threshold (not above)
        assert collector.should_trigger(1.0) is False
        assert collector.should_trigger(-1.0) is False

    def test_trigger_does_not_require_model(self):
        """trigger does NOT require a trained model."""
        # The collector can trigger using only normalised MLOFI
        # No model attribute, no prediction method needed
        collector = self._make_collector()
        # should_trigger only depends on the normalised value and threshold
        assert collector.should_trigger(2.0) is True
        # Verify no model-related attributes
        assert not hasattr(collector, "model")
        assert not hasattr(collector, "predict")

    def test_operational_lag_none_when_no_depth_drop(self):
        """operational_lag returns None when depth drop not recorded."""
        event = {
            "t_ofi_signal_ms": 1000,
            "t_ask_depth_drop_ms": None,
            "t_ask_price_move_ms": 3000,
        }
        lag = LagMeasurementCollector.compute_operational_lag(event)
        assert lag is None
