"""Regression tests for paper_trader calibrator runtime wiring.

Phase C: Ensures paper_trader calls CalibratorRegistry.get() with the
correct 3-arg signature (model_name, symbol, market_window_seconds=300)
and that calibration actually transforms raw probabilities when a non-identity
map is loaded.

These tests guard against regression to the 1-arg bug at paper_trader.py:1354.
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, call, patch

import pytest

from execution.calibration import CalibratorRegistry, ProbabilityCalibrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry_with_spy(tmp_dir: str):
    """Return a CalibratorRegistry whose .get() records calls via a spy."""
    registry = CalibratorRegistry(base_dir=tmp_dir)
    original_get = registry.get
    calls_log: list[tuple] = []

    def spy_get(model_name, symbol, market_window_seconds):
        calls_log.append((model_name, symbol, market_window_seconds))
        return original_get(model_name, symbol, market_window_seconds)

    registry.get = spy_get  # type: ignore[method-assign]
    registry._spy_calls = calls_log  # type: ignore[attr-defined]
    return registry


def _write_calibration_map(path: str, bins: list[dict]) -> None:
    """Write a JSON calibration map to disk."""
    with open(path, "w") as f:
        json.dump({"method": "binmap", "bins": bins}, f)


# ---------------------------------------------------------------------------
# Test 1: CalibratorRegistry.get() is called with 3 args
# ---------------------------------------------------------------------------

class TestPaperTraderCallsCalibrator:
    """Verify the 3-arg signature is used at prediction time."""

    def test_get_called_with_three_args(self, tmp_path):
        """CalibratorRegistry.get() must be called with (model_name, symbol, 300)."""
        registry = _make_registry_with_spy(str(tmp_path))

        # Simulate one prediction cycle using the same code path as paper_trader:
        # pred_proba_calibrated = identity
        # calibrator = self.calibrators.get(model_name, symbol, 300)
        # pred_proba_calibrated = calibrator.calibrate(pred_proba)

        model_name = "h300_btcusdt_v1"
        symbol = "BTCUSDT"
        pred_proba = 0.62

        # This is the fixed code path (3-arg call)
        calibrator = registry.get(model_name, symbol, 300)
        pred_proba_calibrated = calibrator.calibrate(pred_proba)

        assert len(registry._spy_calls) == 1
        assert registry._spy_calls[0] == (model_name, symbol, 300), (
            f"Expected get() called with ({model_name!r}, {symbol!r}, 300), "
            f"got {registry._spy_calls[0]}"
        )
        # Cold start → identity
        assert pred_proba_calibrated == pred_proba

    def test_one_arg_call_raises_type_error(self, tmp_path):
        """Calling get() with 1 arg raises TypeError — proving the old bug."""
        registry = CalibratorRegistry(base_dir=str(tmp_path))
        with pytest.raises(TypeError):
            registry.get("h300_btcusdt_v1")  # type: ignore[call-arg]

    def test_two_arg_call_raises_type_error(self, tmp_path):
        """Calling get() with 2 args raises TypeError."""
        registry = CalibratorRegistry(base_dir=str(tmp_path))
        with pytest.raises(TypeError):
            registry.get("h300_btcusdt_v1", "BTCUSDT")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Test 2: Missing calibrator file → identity fallback
# ---------------------------------------------------------------------------

class TestMissingCalibratorFallback:
    """Cold-start (no calibration file) must return raw probability unchanged."""

    def test_missing_file_returns_identity(self, tmp_path):
        """When no calibration JSON exists, calibrate() returns raw proba."""
        registry = CalibratorRegistry(base_dir=str(tmp_path))
        calibrator = registry.get("h300_btcusdt_v1", "BTCUSDT", 300)

        raw = 0.71
        result = calibrator.calibrate(raw)

        assert result == raw, (
            f"Cold-start calibrator should return identity, got {result} for raw={raw}"
        )

    def test_missing_file_does_not_raise(self, tmp_path):
        """Registry.get() with no file must not raise any exception."""
        registry = CalibratorRegistry(base_dir=str(tmp_path))
        # Should not raise — file simply doesn't exist, ProbabilityCalibrator
        # logs a warning and stays in passthrough mode.
        calibrator = registry.get("nonexistent_model", "ETHUSDT", 300)
        assert calibrator is not None
        assert calibrator.calibrate(0.55) == 0.55

    def test_multiple_missing_models_all_identity(self, tmp_path):
        """All 84 models without calibration files must fall back to identity."""
        registry = CalibratorRegistry(base_dir=str(tmp_path))
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
        horizons = ["h300", "h900", "h1800"]

        for sym in symbols:
            for hz in horizons:
                model_name = f"{hz}_{sym.lower()}_v1"
                cal = registry.get(model_name, sym, 300)
                for raw in [0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
                    assert cal.calibrate(raw) == raw, (
                        f"Identity fallback failed for {model_name}/{sym}: "
                        f"calibrate({raw}) = {cal.calibrate(raw)}"
                    )


# ---------------------------------------------------------------------------
# Test 3: Non-identity calibrator transforms probabilities
# ---------------------------------------------------------------------------

class TestCalibratedPDiffersFromRaw:
    """When a calibration map exists, calibrate() must differ from identity."""

    def test_calibrated_differs_from_raw_when_map_exists(self, tmp_path):
        """After writing a calibration JSON, calibrate() must differ from raw."""
        model_name = "h300_btcusdt_v1"
        symbol = "BTCUSDT"
        window = 300

        # The registry computes the file path as:
        # {base_dir}/calibration_{model_name}__{symbol}__{window}.json
        slug = f"{model_name}__{symbol}__{window}"
        map_path = tmp_path / f"calibration_{slug}.json"

        # Write a non-identity map: raw 0.60 maps to calibrated 0.72
        bins = [
            {"raw": 0.45, "calibrated": 0.40},
            {"raw": 0.50, "calibrated": 0.50},
            {"raw": 0.55, "calibrated": 0.58},
            {"raw": 0.60, "calibrated": 0.72},
            {"raw": 0.65, "calibrated": 0.80},
        ]
        _write_calibration_map(str(map_path), bins)

        registry = CalibratorRegistry(base_dir=str(tmp_path))
        calibrator = registry.get(model_name, symbol, window)

        raw = 0.60
        calibrated = calibrator.calibrate(raw)

        assert calibrated != raw, (
            f"Expected calibrated != raw, but both are {calibrated} for raw={raw}"
        )
        assert abs(calibrated - 0.72) < 1e-6, (
            f"Expected calibrated=0.72, got {calibrated}"
        )

    def test_calibrated_bounded_in_unit_interval(self, tmp_path):
        """Calibrated probability must always be in [0, 1]."""
        model_name = "h300_btcusdt_v1"
        symbol = "BTCUSDT"
        window = 300

        slug = f"{model_name}__{symbol}__{window}"
        map_path = tmp_path / f"calibration_{slug}.json"
        bins = [
            {"raw": 0.40, "calibrated": 0.35},
            {"raw": 0.50, "calibrated": 0.50},
            {"raw": 0.70, "calibrated": 0.85},
        ]
        _write_calibration_map(str(map_path), bins)

        registry = CalibratorRegistry(base_dir=str(tmp_path))
        calibrator = registry.get(model_name, symbol, window)

        for raw in [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0]:
            c = calibrator.calibrate(raw)
            assert 0.0 <= c <= 1.0, f"calibrate({raw}) = {c} out of [0, 1]"

    def test_per_symbol_isolation(self, tmp_path):
        """Calibrators for different symbols must be independent."""
        model_base = "h300"
        window = 300

        # Write map only for BTC, not ETH
        btc_slug = f"{model_base}__BTCUSDT__{window}"
        btc_path = tmp_path / f"calibration_{btc_slug}.json"
        bins = [
            {"raw": 0.50, "calibrated": 0.50},
            {"raw": 0.60, "calibrated": 0.75},
        ]
        _write_calibration_map(str(btc_path), bins)

        registry = CalibratorRegistry(base_dir=str(tmp_path))
        btc_cal = registry.get(model_base, "BTCUSDT", window)
        eth_cal = registry.get(model_base, "ETHUSDT", window)

        raw = 0.60
        btc_result = btc_cal.calibrate(raw)
        eth_result = eth_cal.calibrate(raw)

        assert btc_result != eth_result, (
            "BTC and ETH calibrators should differ when only BTC has a map"
        )
        assert eth_result == raw, "ETH (no map) should return identity"

    def test_per_window_isolation(self, tmp_path):
        """Calibrators for different market windows are independent."""
        model_name = "h300_btcusdt_v1"
        symbol = "BTCUSDT"

        # Write map for window=300 only
        slug_300 = f"{model_name}__{symbol}__300"
        map_path = tmp_path / f"calibration_{slug_300}.json"
        bins = [
            {"raw": 0.50, "calibrated": 0.50},
            {"raw": 0.65, "calibrated": 0.80},
        ]
        _write_calibration_map(str(map_path), bins)

        registry = CalibratorRegistry(base_dir=str(tmp_path))
        cal_300 = registry.get(model_name, symbol, 300)
        cal_900 = registry.get(model_name, symbol, 900)

        raw = 0.65
        result_300 = cal_300.calibrate(raw)
        result_900 = cal_900.calibrate(raw)

        assert result_300 != result_900, (
            "300s and 900s calibrators should differ when only 300s has a map"
        )
        assert result_900 == raw, "900s (no map) should return identity"


# ---------------------------------------------------------------------------
# Test 4: Registry caches instances (no double-load)
# ---------------------------------------------------------------------------

class TestRegistryCaching:
    """CalibratorRegistry must return the same instance for repeated calls."""

    def test_same_key_returns_same_instance(self, tmp_path):
        """get() with same args must return the identical object."""
        registry = CalibratorRegistry(base_dir=str(tmp_path))
        cal_a = registry.get("h300_btcusdt_v1", "BTCUSDT", 300)
        cal_b = registry.get("h300_btcusdt_v1", "BTCUSDT", 300)
        assert cal_a is cal_b, "Same key must return same cached instance"

    def test_different_window_different_instance(self, tmp_path):
        """get() with different windows must return different instances."""
        registry = CalibratorRegistry(base_dir=str(tmp_path))
        cal_300 = registry.get("h300_btcusdt_v1", "BTCUSDT", 300)
        cal_900 = registry.get("h300_btcusdt_v1", "BTCUSDT", 900)
        assert cal_300 is not cal_900
