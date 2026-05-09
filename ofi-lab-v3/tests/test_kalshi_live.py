"""
Tests for Kalshi live trading: auto-recalibration, maker-first ordering,
and fee calculation correctness.
"""

import json
import math
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from execution.calibration import ProbabilityCalibrator, MIN_REFIT_SAMPLES, MIN_SAMPLES_PER_BIN
from execution.kalshi_fees import fee, maker_fee, taker_fee, kelly_fraction
from execution.kalshi_live_trader import (
    GateResult,
    KalshiLiveConfig,
    KalshiLiveTrader,
    MAKER_RETRIES,
    MAKER_WAIT_SECS,
)


# ----------------------------------------------------------
# AUTO-RECALIBRATION TESTS
# ----------------------------------------------------------

class TestAutoRecalibration:

    def _make_calibrator(self, tmp_path):
        """Create a calibrator with a temp path for testing."""
        cal_path = os.path.join(tmp_path, "calibration.json")
        os.environ["KALSHI_CALIBRATION_PATH"] = cal_path
        cal = ProbabilityCalibrator(path=cal_path)
        return cal, cal_path

    def test_record_outcome_accumulates(self):
        """record_outcome accumulates data without refitting below threshold."""
        with tempfile.TemporaryDirectory() as tmp:
            cal, cal_path = self._make_calibrator(tmp)
            assert cal.outcome_count == 0

            cal.record_outcome(0.53, True)
            assert cal.outcome_count == 1

            cal.record_outcome(0.55, False)
            assert cal.outcome_count == 2

            # No refit yet (below MIN_REFIT_SAMPLES)
            assert not os.path.exists(cal_path)

    def test_record_outcome_persists_to_disk(self):
        """Outcomes are written to a JSONL file for durability."""
        with tempfile.TemporaryDirectory() as tmp:
            cal, cal_path = self._make_calibrator(tmp)
            cal.record_outcome(0.53, True)
            cal.record_outcome(0.55, False)

            outcomes_path = os.path.join(tmp, "calibration_outcomes.jsonl")
            assert os.path.exists(outcomes_path)

            with open(outcomes_path) as f:
                lines = [json.loads(l) for l in f if l.strip()]
            assert len(lines) == 2
            assert lines[0]["side_conf"] == pytest.approx(0.53, abs=1e-4)
            assert lines[0]["won"] is True
            assert lines[1]["won"] is False

    def test_outcomes_survive_restart(self):
        """Outcomes are reloaded from disk on restart."""
        with tempfile.TemporaryDirectory() as tmp:
            cal1, cal_path = self._make_calibrator(tmp)
            for _ in range(10):
                cal1.record_outcome(0.53, True)
            assert cal1.outcome_count == 10

            # "Restart" — create a new calibrator pointing at the same dir
            cal2 = ProbabilityCalibrator(path=cal_path)
            assert cal2.outcome_count == 10

    def test_refit_triggers_at_threshold(self):
        """Refit triggers when outcome_count reaches MIN_REFIT_SAMPLES."""
        with tempfile.TemporaryDirectory() as tmp:
            cal, cal_path = self._make_calibrator(tmp)

            # Add enough outcomes in a single bin to trigger refit
            for i in range(MIN_REFIT_SAMPLES - 1):
                result = cal.record_outcome(0.53, i % 2 == 0)
                assert result is False  # No refit yet

            # This one should trigger
            result = cal.record_outcome(0.53, True)
            assert result is True
            assert os.path.exists(cal_path)

            # Verify the calibration file is valid
            with open(cal_path) as f:
                data = json.load(f)
            assert data["method"] == "binmap"
            assert data["source"] == "auto_refit"
            assert len(data["bins"]) >= 1

    def test_refit_produces_valid_calibration(self):
        """Refit bins have valid calibrated values in [0, 1]."""
        with tempfile.TemporaryDirectory() as tmp:
            cal, cal_path = self._make_calibrator(tmp)

            # Generate enough outcomes to fill a bin and trigger refit
            for _ in range(MIN_REFIT_SAMPLES):
                cal.record_outcome(0.54, True)  # All wins at 0.54

            assert os.path.exists(cal_path)
            with open(cal_path) as f:
                data = json.load(f)

            for b in data["bins"]:
                assert 0.0 <= b["calibrated"] <= 1.0
                assert 0.50 <= b["raw"] <= 1.0

    def test_calibrate_after_refit(self):
        """After refit, calibrate() uses the new bins."""
        with tempfile.TemporaryDirectory() as tmp:
            cal, cal_path = self._make_calibrator(tmp)

            # Feed all wins at 0.53 — calibrated should be ~1.0
            for _ in range(MIN_REFIT_SAMPLES):
                cal.record_outcome(0.53, True)

            # Calibrated value at 0.53 should reflect the 100% win rate
            calibrated = cal.calibrate(0.53)
            assert calibrated == pytest.approx(1.0, abs=0.01)

    def test_below_050_ignored(self):
        """Outcomes with side_conf < 0.50 are not binned."""
        with tempfile.TemporaryDirectory() as tmp:
            cal, cal_path = self._make_calibrator(tmp)

            # Feed sub-0.50 outcomes
            for _ in range(MIN_REFIT_SAMPLES):
                cal.record_outcome(0.45, True)

            # Should NOT have produced a calibration file (no bins above 0.50)
            assert not os.path.exists(cal_path) or not cal.is_enabled()


# ----------------------------------------------------------
# FEE CALCULATION TESTS
# ----------------------------------------------------------

class TestFeeCalculation:

    def test_maker_cheaper_than_taker(self):
        """Maker fees must always be lower than taker fees."""
        for price in [0.30, 0.40, 0.50, 0.60, 0.70]:
            for contracts in [1, 5, 10, 25]:
                m = maker_fee(price, contracts)
                t = taker_fee(price, contracts)
                assert m < t, f"maker ({m}) not cheaper than taker ({t}) at p={price}, n={contracts}"

    def test_fee_dispatcher(self):
        """fee() dispatches to correct function based on is_maker."""
        assert fee(0.50, 10, is_maker=True) == maker_fee(0.50, 10)
        assert fee(0.50, 10, is_maker=False) == taker_fee(0.50, 10)

    def test_fee_at_midpoint(self):
        """Known reference: taker fee at P=0.50 = 0.07 * 0.25 = 1.75¢/contract."""
        t = taker_fee(0.50, 1)
        assert t == pytest.approx(0.0175, abs=0.001)

        m = maker_fee(0.50, 1)
        # 0.0175 * 0.25 = 0.004375 → ceil to 0.0044
        assert m == pytest.approx(0.0044, abs=0.001)

    def test_fee_symmetric(self):
        """Fee at P and (1-P) should be within 1 tick (ceil rounding artifact)."""
        for p in [0.30, 0.40, 0.45]:
            assert taker_fee(p, 1) == pytest.approx(taker_fee(1 - p, 1), abs=0.0002)
            assert maker_fee(p, 1) == pytest.approx(maker_fee(1 - p, 1), abs=0.0002)


# ----------------------------------------------------------
# GATE RESULT TESTS
# ----------------------------------------------------------

class TestGateResult:

    def test_gate_result_has_is_maker(self):
        """GateResult includes is_maker field."""
        r = GateResult(placed=True, reason="PLACED", is_maker=True)
        assert r.is_maker is True

        r2 = GateResult(placed=True, reason="PLACED", is_maker=False)
        assert r2.is_maker is False

        r3 = GateResult(placed=False, reason="GATED_KILL_SWITCH")
        assert r3.is_maker is None


# ----------------------------------------------------------
# CONFIG TESTS
# ----------------------------------------------------------

class TestKalshiLiveConfig:

    def test_default_order_type_is_maker(self):
        """Default order type should be maker, not taker."""
        cfg = KalshiLiveConfig()
        assert cfg.default_order_type == "maker"


# ----------------------------------------------------------
# MAKER-FIRST STRATEGY TESTS
# ----------------------------------------------------------

class TestMakerFirstStrategy:

    def test_maker_retries_constant(self):
        """MAKER_RETRIES is 5."""
        assert MAKER_RETRIES == 5

    def test_maker_wait_secs(self):
        """MAKER_WAIT_SECS is a positive float."""
        assert MAKER_WAIT_SECS > 0

    def test_kelly_fraction_maker_vs_taker(self):
        """Kelly fraction should be higher with maker fees (cheaper)."""
        # With maker (lower fees), the edge is bigger → larger Kelly fraction
        k_maker = kelly_fraction(0.55, 0.50, is_maker=True)
        k_taker = kelly_fraction(0.55, 0.50, is_maker=False)
        assert k_maker > k_taker, "Maker Kelly should be larger due to lower fees"

    def test_kelly_fraction_zero_when_no_edge(self):
        """Kelly returns 0 when model has no edge."""
        k = kelly_fraction(0.50, 0.50, is_maker=False)
        assert k == 0.0

    def test_kelly_fraction_positive_with_edge(self):
        """Kelly returns positive when model has edge over market."""
        k = kelly_fraction(0.55, 0.50, is_maker=True)
        assert k > 0


# ----------------------------------------------------------
# KALSHI LIVE TRADER UNIT TESTS
# ----------------------------------------------------------

class TestKalshiLiveTraderUnit:

    def _make_trader(self, tmp_path, **env_overrides):
        """Create a trader with a temp env for testing."""
        env = {
            "KALSHI_LIVE_ENABLED": "true",
            "KALSHI_LIVE_ALLOW_LIST": "BTCUSDT:900",
            "KALSHI_DEFAULT_ORDER_TYPE": "maker",
            "KALSHI_CALIBRATION_PATH": os.path.join(tmp_path, "calibration.json"),
        }
        env.update(env_overrides)
        return KalshiLiveTrader(
            env=env,
            ledger_path=os.path.join(tmp_path, "kalshi_orders.jsonl"),
        )

    def test_default_config_is_maker(self):
        """Trader defaults to maker order type."""
        with tempfile.TemporaryDirectory() as tmp:
            trader = self._make_trader(tmp)
            assert trader.config.default_order_type == "maker"

    def test_config_override_to_taker(self):
        """Trader can be overridden to taker via env."""
        with tempfile.TemporaryDirectory() as tmp:
            trader = self._make_trader(tmp, KALSHI_DEFAULT_ORDER_TYPE="taker")
            assert trader.config.default_order_type == "taker"

    def test_compute_contracts_uses_maker_fees(self):
        """compute_contracts uses the configured order type's fee model."""
        with tempfile.TemporaryDirectory() as tmp:
            trader = self._make_trader(tmp)
            # Maker default → should use maker fees
            n_maker, k_maker = trader.compute_contracts(
                bankroll_usd=100.0,
                model_p=0.55,
                market_yes_price=0.50,
                side="yes",
            )

            trader2 = self._make_trader(tmp, KALSHI_DEFAULT_ORDER_TYPE="taker")
            n_taker, k_taker = trader2.compute_contracts(
                bankroll_usd=100.0,
                model_p=0.55,
                market_yes_price=0.50,
                side="yes",
            )

            # Maker fees are lower → more contracts possible
            assert n_maker >= n_taker

    def test_ledger_records_is_maker(self):
        """Ledger rows include is_maker field."""
        with tempfile.TemporaryDirectory() as tmp:
            trader = self._make_trader(tmp)
            result = GateResult(
                placed=True, reason="PLACED",
                order_id="test-123", fill_status="executed",
                final_yes_price_cents=50, final_contracts=10,
                fee_estimate_usd=0.044, is_maker=True,
            )
            trader._record(
                symbol="BTCUSDT", duration_sec=900, boundary_ts=1234567890,
                ticker="KXBTC15M-TEST", side="yes", model_p=0.55,
                market_yes_price=0.50, result=result,
            )

            ledger_path = os.path.join(tmp, "kalshi_orders.jsonl")
            with open(ledger_path) as f:
                row = json.loads(f.readline())
            assert row["is_maker"] is True
            assert row["gate_result"] == "PLACED"
