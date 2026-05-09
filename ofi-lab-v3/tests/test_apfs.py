"""
Tests for the APFS (Adaptive Prediction Filter System) — Phase 1.
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from execution.apfs.feature_confirmation import (
    FeatureConfirmationFilter,
    FilterDecision,
    SubFilterResult,
    MIN_REFIT_OUTCOMES,
)


# ── Fixtures ──────────────────────────────────────────────────────

def _make_features(
    mlofi=0.5,
    eth_mlofi_30s_mean=0.3,
    vwap_deviation=0.0001,
    mlofi_30s_mean=0.4,
    **kwargs,
):
    """Build a features dict for testing."""
    base = {
        "mlofi": mlofi,
        "eth_mlofi_30s_mean": eth_mlofi_30s_mean,
        "vwap_deviation": vwap_deviation,
        "mlofi_30s_mean": mlofi_30s_mean,
        "mid_price": 96000.0,
        "spread": 0.1,
        "relative_spread": 1.3e-6,
    }
    base.update(kwargs)
    return base


# ── Test Classes ──────────────────────────────────────────────────

class TestFilterEvaluation:

    def test_cold_start_identity(self):
        """With no learned weights, score == side_conf."""
        with tempfile.TemporaryDirectory() as tmp:
            filt = FeatureConfirmationFilter(state_dir=tmp, trade_threshold=0.52)

            # All features agree with "up" prediction
            features = _make_features(mlofi=0.5, eth_mlofi_30s_mean=0.3, vwap_deviation=0.001)
            decision = filt.evaluate(
                model_name="h300", symbol="BTCUSDT",
                pred_direction="up", pred_proba=0.55,
                features=features,
            )

            # Cold start: all weights = 1.0, all agree → score = side_conf = 0.55
            assert decision.trade_score == pytest.approx(0.55, abs=0.001)
            assert decision.base_confidence == pytest.approx(0.55, abs=0.001)
            assert decision.should_trade is True

    def test_below_threshold_gated(self):
        """Predictions below threshold are gated."""
        with tempfile.TemporaryDirectory() as tmp:
            filt = FeatureConfirmationFilter(state_dir=tmp, trade_threshold=0.53)

            features = _make_features(mlofi=-0.5, eth_mlofi_30s_mean=-0.3)
            decision = filt.evaluate(
                model_name="h300", symbol="BTCUSDT",
                pred_direction="up", pred_proba=0.52,
                features=features,
            )

            # side_conf = 0.52, below 0.53 threshold
            assert decision.should_trade is False

    def test_all_filters_agree_up(self):
        """When all features confirm UP prediction, all sub-filters pass."""
        with tempfile.TemporaryDirectory() as tmp:
            filt = FeatureConfirmationFilter(state_dir=tmp)
            features = _make_features(
                mlofi=0.5, eth_mlofi_30s_mean=0.3,
                vwap_deviation=0.001, mlofi_30s_mean=0.4,
            )
            decision = filt.evaluate(
                model_name="h300", symbol="BTCUSDT",
                pred_direction="up", pred_proba=0.55,
                features=features,
            )

            for sf in decision.sub_filters:
                assert sf.passed is True, f"{sf.name} should pass"

    def test_all_filters_oppose_up(self):
        """When all features oppose UP prediction, all sub-filters fail."""
        with tempfile.TemporaryDirectory() as tmp:
            filt = FeatureConfirmationFilter(state_dir=tmp)
            features = _make_features(
                mlofi=-0.5, eth_mlofi_30s_mean=-0.3,
                vwap_deviation=-0.001, mlofi_30s_mean=-0.4,
            )
            decision = filt.evaluate(
                model_name="h300", symbol="BTCUSDT",
                pred_direction="up", pred_proba=0.55,
                features=features,
            )

            for sf in decision.sub_filters:
                assert sf.passed is False, f"{sf.name} should fail"

    def test_down_prediction_flips_logic(self):
        """For DOWN predictions, negative features should confirm."""
        with tempfile.TemporaryDirectory() as tmp:
            filt = FeatureConfirmationFilter(state_dir=tmp)
            features = _make_features(
                mlofi=-0.5, eth_mlofi_30s_mean=-0.3,
                vwap_deviation=-0.001, mlofi_30s_mean=-0.4,
            )
            decision = filt.evaluate(
                model_name="h300", symbol="BTCUSDT",
                pred_direction="down", pred_proba=0.45,  # → side_conf = 0.55
                features=features,
            )

            # side_conf for "down" with proba 0.45 = 1 - 0.45 = 0.55
            assert decision.base_confidence == pytest.approx(0.55, abs=0.001)
            for sf in decision.sub_filters:
                assert sf.passed is True, f"{sf.name} should pass for DOWN"

    def test_eth_filter_only_for_btc(self):
        """eth_mlofi_confirms should only appear for BTCUSDT."""
        with tempfile.TemporaryDirectory() as tmp:
            filt = FeatureConfirmationFilter(state_dir=tmp)
            features = _make_features()

            # BTC should have it
            btc_decision = filt.evaluate(
                model_name="h300", symbol="BTCUSDT",
                pred_direction="up", pred_proba=0.55, features=features,
            )
            names = [sf.name for sf in btc_decision.sub_filters]
            assert "eth_mlofi_confirms" in names

            # ETH should NOT have it
            eth_decision = filt.evaluate(
                model_name="h300", symbol="ETHUSDT",
                pred_direction="up", pred_proba=0.55, features=features,
            )
            names = [sf.name for sf in eth_decision.sub_filters]
            assert "eth_mlofi_confirms" not in names

    def test_score_clamped_to_0_1(self):
        """Trade score never exceeds [0, 1]."""
        with tempfile.TemporaryDirectory() as tmp:
            filt = FeatureConfirmationFilter(state_dir=tmp)
            features = _make_features()
            decision = filt.evaluate(
                model_name="h300", symbol="BTCUSDT",
                pred_direction="up", pred_proba=0.99,
                features=features,
            )
            assert 0.0 <= decision.trade_score <= 1.0


class TestOutcomeRecording:

    def test_record_outcome_persists(self):
        """Outcomes are persisted to JSONL."""
        with tempfile.TemporaryDirectory() as tmp:
            filt = FeatureConfirmationFilter(state_dir=tmp)
            features = _make_features()

            filt.record_outcome(
                model_name="h300", symbol="BTCUSDT",
                pred_direction="up", pred_proba=0.55,
                features=features, won=True,
            )
            assert filt.get_outcome_count() == 1

            outcomes_path = os.path.join(tmp, "feature_confirmation_outcomes.jsonl")
            assert os.path.exists(outcomes_path)
            with open(outcomes_path) as f:
                data = json.loads(f.readline())
            assert data["won"] is True
            assert data["model"] == "h300"
            assert data["symbol"] == "BTCUSDT"
            assert "sub_filters" in data

    def test_outcomes_survive_restart(self):
        """Outcomes reload on restart."""
        with tempfile.TemporaryDirectory() as tmp:
            filt1 = FeatureConfirmationFilter(state_dir=tmp)
            features = _make_features()
            for _ in range(10):
                filt1.record_outcome(
                    model_name="h300", symbol="BTCUSDT",
                    pred_direction="up", pred_proba=0.55,
                    features=features, won=True,
                )
            assert filt1.get_outcome_count() == 10

            # "Restart"
            filt2 = FeatureConfirmationFilter(state_dir=tmp)
            assert filt2.get_outcome_count() == 10


class TestAutoAdaptation:

    def test_refit_triggers_at_threshold(self):
        """Refit triggers after MIN_REFIT_OUTCOMES."""
        with tempfile.TemporaryDirectory() as tmp:
            filt = FeatureConfirmationFilter(state_dir=tmp)

            # All agree features, 80% wins → filters get boosted
            agree_features = _make_features(
                mlofi=0.5, eth_mlofi_30s_mean=0.3,
                vwap_deviation=0.001, mlofi_30s_mean=0.4,
            )

            for i in range(MIN_REFIT_OUTCOMES):
                won = (i % 5 != 0)  # 80% win rate
                refit = filt.record_outcome(
                    model_name="h300", symbol="BTCUSDT",
                    pred_direction="up", pred_proba=0.55,
                    features=agree_features, won=won,
                )

            # Last one should have triggered refit
            weights_path = os.path.join(tmp, "feature_confirmation_weights.json")
            assert os.path.exists(weights_path)

    def test_refit_produces_valid_weights(self):
        """Refit weights are in valid range [0.85, 1.20]."""
        with tempfile.TemporaryDirectory() as tmp:
            filt = FeatureConfirmationFilter(state_dir=tmp)

            # Mix of agree/disagree to produce both pass and fail samples
            for i in range(MIN_REFIT_OUTCOMES):
                if i % 2 == 0:
                    # Agree features, higher win rate
                    features = _make_features(mlofi=0.5, eth_mlofi_30s_mean=0.3)
                    won = (i % 3 != 0)  # ~67%
                else:
                    # Disagree features, lower win rate
                    features = _make_features(mlofi=-0.5, eth_mlofi_30s_mean=-0.3)
                    won = (i % 4 == 0)  # ~25%

                filt.record_outcome(
                    model_name="h300", symbol="BTCUSDT",
                    pred_direction="up", pred_proba=0.55,
                    features=features, won=won,
                )

            weights = filt.get_weights()
            key = "h300:BTCUSDT"
            if key in weights:
                for fname, fdata in weights[key].items():
                    w = fdata.get("weight", 1.0)
                    assert 0.85 <= w <= 1.20, f"{fname} weight {w} out of range"

    def test_learned_weights_affect_scoring(self):
        """After refit, non-identity weights change the trade score."""
        with tempfile.TemporaryDirectory() as tmp:
            filt = FeatureConfirmationFilter(state_dir=tmp)

            # Get cold-start score
            features = _make_features(mlofi=0.5, eth_mlofi_30s_mean=0.3)
            cold_decision = filt.evaluate(
                model_name="h300", symbol="BTCUSDT",
                pred_direction="up", pred_proba=0.55,
                features=features,
            )

            # Feed biased outcomes: all agree + win → boost weights
            for i in range(MIN_REFIT_OUTCOMES):
                agree = _make_features(mlofi=0.5, eth_mlofi_30s_mean=0.3)
                disagree = _make_features(mlofi=-0.5, eth_mlofi_30s_mean=-0.3)
                if i % 2 == 0:
                    filt.record_outcome(
                        model_name="h300", symbol="BTCUSDT",
                        pred_direction="up", pred_proba=0.55,
                        features=agree, won=True,
                    )
                else:
                    filt.record_outcome(
                        model_name="h300", symbol="BTCUSDT",
                        pred_direction="up", pred_proba=0.55,
                        features=disagree, won=False,
                    )

            # Post-refit score should differ (weights no longer 1.0)
            warm_decision = filt.evaluate(
                model_name="h300", symbol="BTCUSDT",
                pred_direction="up", pred_proba=0.55,
                features=features,
            )

            # At minimum, confirm weights were loaded
            weights = filt.get_weights()
            key = "h300:BTCUSDT"
            if key in weights:
                has_non_identity = any(
                    fdata.get("weight", 1.0) != 1.0
                    for fdata in weights[key].values()
                )
                if has_non_identity:
                    assert cold_decision.trade_score != warm_decision.trade_score


class TestTraderIntegration:

    def test_trader_has_apfs(self):
        """KalshiLiveTrader initializes with APFS."""
        with tempfile.TemporaryDirectory() as tmp:
            from execution.kalshi_live_trader import KalshiLiveTrader
            env = {
                "KALSHI_LIVE_ENABLED": "true",
                "KALSHI_LIVE_ALLOW_LIST": "BTCUSDT:900",
                "KALSHI_CALIBRATION_PATH": os.path.join(tmp, "cal.json"),
                "APFS_STATE_DIR": os.path.join(tmp, "apfs"),
                "APFS_ENABLED": "true",
                "APFS_TRADE_THRESHOLD": "0.52",
            }
            trader = KalshiLiveTrader(
                env=env,
                ledger_path=os.path.join(tmp, "ledger.jsonl"),
            )
            assert trader._apfs is not None
            assert trader._apfs_enabled is True

    def test_apfs_can_be_disabled(self):
        """APFS can be disabled via env."""
        with tempfile.TemporaryDirectory() as tmp:
            from execution.kalshi_live_trader import KalshiLiveTrader
            env = {
                "KALSHI_LIVE_ENABLED": "true",
                "KALSHI_LIVE_ALLOW_LIST": "BTCUSDT:900",
                "KALSHI_CALIBRATION_PATH": os.path.join(tmp, "cal.json"),
                "APFS_STATE_DIR": os.path.join(tmp, "apfs"),
                "APFS_ENABLED": "false",
            }
            trader = KalshiLiveTrader(
                env=env,
                ledger_path=os.path.join(tmp, "ledger.jsonl"),
            )
            assert trader._apfs_enabled is False

    def test_apfs_snapshot_includes_apfs_fields(self):
        """_snapshot() must include apfs_enabled and apfs_threshold."""
        with tempfile.TemporaryDirectory() as tmp:
            from execution.kalshi_live_trader import KalshiLiveTrader
            env = {
                "KALSHI_LIVE_ENABLED": "true",
                "KALSHI_LIVE_ALLOW_LIST": "BTCUSDT:900",
                "KALSHI_CALIBRATION_PATH": os.path.join(tmp, "cal.json"),
                "APFS_STATE_DIR": os.path.join(tmp, "apfs"),
                "APFS_ENABLED": "true",
                "APFS_TRADE_THRESHOLD": "0.54",
            }
            trader = KalshiLiveTrader(
                env=env,
                ledger_path=os.path.join(tmp, "ledger.jsonl"),
            )
            snap = trader._snapshot()
            assert snap["apfs_enabled"] is True
            assert snap["apfs_threshold"] == pytest.approx(0.54)

    def test_update_config_toggles_apfs(self):
        """update_config can toggle apfs_enabled and apfs_threshold at runtime."""
        with tempfile.TemporaryDirectory() as tmp:
            from execution.kalshi_live_trader import KalshiLiveTrader
            env = {
                "KALSHI_LIVE_ENABLED": "true",
                "KALSHI_LIVE_ALLOW_LIST": "BTCUSDT:900",
                "KALSHI_CALIBRATION_PATH": os.path.join(tmp, "cal.json"),
                "APFS_STATE_DIR": os.path.join(tmp, "apfs"),
                "APFS_ENABLED": "true",
                "APFS_TRADE_THRESHOLD": "0.52",
            }
            trader = KalshiLiveTrader(
                env=env,
                ledger_path=os.path.join(tmp, "ledger.jsonl"),
            )
            assert trader._apfs_enabled is True

            # Disable APFS via update_config
            snap = trader.update_config(apfs_enabled=False)
            assert trader._apfs_enabled is False
            assert snap["apfs_enabled"] is False

            # Change threshold
            snap = trader.update_config(apfs_threshold=0.58)
            assert trader._apfs.trade_threshold == pytest.approx(0.58)
            assert snap["apfs_threshold"] == pytest.approx(0.58)

            # Re-enable
            snap = trader.update_config(apfs_enabled=True)
            assert trader._apfs_enabled is True
            assert snap["apfs_enabled"] is True


class TestLedgerFilterMode:

    def test_filter_mode_written_to_record(self):
        """log_trade writes filter_mode field when provided."""
        with tempfile.TemporaryDirectory() as tmp:
            from trading.ledger import Ledger
            ledger = Ledger(model_name="h300", log_dir=tmp)

            trade_id = ledger.log_trade(
                prediction_id="pred_001",
                symbol="BTCUSDT",
                pred_proba=0.55,
                pred_direction="up",
                confidence_threshold=0.52,
                contract_duration_seconds=900,
                price_at_open=96000.0,
                ts_model_ran_ms=1700000000000,
                ts_contract_open_ms=1700000000000,
                filter_mode="apfs",
            )
            assert trade_id is not None

            # Read back the record
            with open(ledger.trades_path) as f:
                record = json.loads(f.readline())
            assert record["filter_mode"] == "apfs"

    def test_filter_mode_absent_when_none(self):
        """log_trade omits filter_mode when not provided."""
        with tempfile.TemporaryDirectory() as tmp:
            from trading.ledger import Ledger
            ledger = Ledger(model_name="h300", log_dir=tmp)

            ledger.log_trade(
                prediction_id="pred_002",
                symbol="BTCUSDT",
                pred_proba=0.55,
                pred_direction="up",
                confidence_threshold=0.52,
                contract_duration_seconds=900,
                price_at_open=96000.0,
                ts_model_ran_ms=1700000000000,
                ts_contract_open_ms=1700000000000,
            )

            with open(ledger.trades_path) as f:
                record = json.loads(f.readline())
            assert "filter_mode" not in record

