"""Tests for trading/provenance_builder.py.

Covers instantiation and each of the 4 extracted methods using a mock
"trader" object (same back-reference pattern used by ProvenanceBuilder
in production). Mock conventions follow test_resolution_checker.py.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from trading.provenance_builder import ProvenanceBuilder
from storage.provenance import ProvenanceEnvelope
from storage.decision_trace import FilterEval


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_trader(
    *,
    filters=None,
    model_envelopes=None,
    model_meta=None,
    policy_snapshot=None,
    decision_trace=None,
    calibrators=None,
    registry_state=None,
    sqlite_ledger=None,
) -> MagicMock:
    """Return a MagicMock shaped like PaperTrader for ProvenanceBuilder tests."""
    if filters is None:
        filters = {
            "confidence_threshold": 0.55,
            "per_symbol_confidence": {},
            "kelly_fraction": 0.5,
            "ev_threshold": 0.0,
            "circuit_breaker_drawdown": -50.0,
            "clob_divergence_min_edge": 0.02,
            "filter_mode": "paper",
            "blackout_hours_utc": [],
        }
    if model_envelopes is None:
        model_envelopes = {
            "test_model": {
                "model_artifact_hash": "a" * 64,
                "feature_names_hash": "b" * 64,
            }
        }
    if model_meta is None:
        model_meta = {
            "test_model": {
                "symbol": "BTCUSDT",
                "training_horizon_seconds": 900,
                "feature_version": "v3",
                "train_window_start": "2025-01-01",
                "train_window_end": "2025-03-01",
                "train_cutoff": "2025-03-01",
            }
        }

    # calibrators.get() returns a calibrator with ._bins
    if calibrators is None:
        mock_cal = MagicMock()
        mock_cal._bins = [0.0, 0.25, 0.5, 0.75, 1.0]
        calibrators = MagicMock()
        calibrators.get.return_value = mock_cal

    # policy_snapshot.capture() returns (version, hash)
    if policy_snapshot is None:
        policy_snapshot = MagicMock()
        policy_snapshot.capture.return_value = (0, "c" * 64)

    # registry_state.current_generation() returns 0
    if registry_state is None:
        registry_state = MagicMock()
        registry_state.current_generation.return_value = 0

    if decision_trace is None:
        decision_trace = MagicMock()

    if sqlite_ledger is None:
        sqlite_ledger = MagicMock()

    trader = MagicMock()
    trader.filters = filters
    trader._model_envelopes = model_envelopes
    trader._model_meta = model_meta
    trader.policy_snapshot = policy_snapshot
    trader.decision_trace = decision_trace
    trader.calibrators = calibrators
    trader.registry_state = registry_state
    trader.sqlite_ledger = sqlite_ledger
    return trader


def _make_builder(**kwargs) -> ProvenanceBuilder:
    trader = _make_mock_trader(**kwargs)
    return ProvenanceBuilder(trader=trader)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_provenance_builder_instantiation():
    """ProvenanceBuilder can be constructed with a mock trader."""
    pb = _make_builder()
    assert pb is not None
    assert pb._trader.filters["confidence_threshold"] == 0.55


def test_build_envelope_returns_envelope_with_correct_fields():
    """build_envelope returns a ProvenanceEnvelope with all key fields set."""
    pb = _make_builder()
    env = pb.build_envelope("test_model", platform="paper")

    assert isinstance(env, ProvenanceEnvelope)
    assert env.model_name == "test_model"
    assert env.model_artifact_hash == "a" * 64
    assert env.feature_names_hash == "b" * 64
    assert len(env.calibration_map_hash) == 64
    assert len(env.policy_config_hash) == 64
    assert env.feature_version == "v3"
    assert env.training_horizon_seconds == 900
    assert env.platform == "paper"
    assert env.registry_load_generation == 0
    assert env.decision_policy_version == 0


def test_build_envelope_platform_kalshi():
    """build_envelope reflects the platform argument."""
    pb = _make_builder()
    env = pb.build_envelope("test_model", platform="kalshi")
    assert env.platform == "kalshi"


def test_capture_policy_dict_returns_serializable():
    """capture_policy_dict returns a dict that round-trips through json.dumps."""
    pb = _make_builder()
    result = pb.capture_policy_dict()

    assert isinstance(result, dict)
    # Must be JSON-serializable (no sets, no numpy types, etc.)
    dumped = json.dumps(result)
    loaded = json.loads(dumped)
    assert loaded["confidence_threshold"] == 0.55
    assert isinstance(loaded["blackout_hours_utc"], list)


def test_capture_policy_dict_includes_expected_keys():
    """capture_policy_dict always includes all 8 filter keys."""
    pb = _make_builder(filters={})
    result = pb.capture_policy_dict()
    expected_keys = {
        "confidence_threshold", "per_symbol_confidence", "kelly_fraction",
        "ev_threshold", "circuit_breaker_drawdown", "clob_divergence_min_edge",
        "filter_mode", "blackout_hours_utc",
    }
    assert expected_keys == set(result.keys())


def test_record_compact_decision_calls_sqlite_ledger():
    """record_compact_decision delegates to sqlite_ledger.log_compact_decision."""
    ledger = MagicMock()
    pb = _make_builder(sqlite_ledger=ledger)

    pb.record_compact_decision(
        prediction_id="p_btc_123_900e",
        outcome="executed",
        reason=None,
        ev_estimate=0.018,
        kelly_fraction_capped=0.25,
        final_size_usdc=10.0,
        order_type="maker",
    )

    ledger.log_compact_decision.assert_called_once_with(
        prediction_id="p_btc_123_900e",
        decision_outcome="executed",
        decision_reason=None,
        ev_estimate=0.018,
        kelly_fraction_capped=0.25,
        final_size_usdc=10.0,
        order_type="maker",
    )


def test_record_compact_decision_suppressed():
    """record_compact_decision forwards suppressed outcome and reason."""
    ledger = MagicMock()
    pb = _make_builder(sqlite_ledger=ledger)

    pb.record_compact_decision(
        prediction_id="p_btc_456_900e",
        outcome="suppressed",
        reason="below_confidence",
        ev_estimate=None,
        kelly_fraction_capped=0.0,
        final_size_usdc=0.0,
        order_type="skipped",
    )

    _, kwargs = ledger.log_compact_decision.call_args
    assert kwargs["decision_outcome"] == "suppressed"
    assert kwargs["decision_reason"] == "below_confidence"


def test_write_verbose_trace_calls_decision_trace():
    """write_verbose_trace_for_v2_filters delegates to decision_trace.write."""
    dt = MagicMock()
    pb = _make_builder(decision_trace=dt)

    envelope = MagicMock()
    envelope.policy_config_hash = "c" * 64
    envelope.calibration_map_hash = "d" * 64
    envelope.registry_load_generation = 0

    filter_inputs = {
        "confidence": (0.55, 0.58, True),
        "ev_gate": (0.01, 0.02, True),
    }

    pb.write_verbose_trace_for_v2_filters(
        prediction_id="p_btc_789_900e",
        envelope=envelope,
        filter_inputs=filter_inputs,
        kelly_raw=0.30,
        kelly_capped=0.25,
        bankroll_used=1000.0,
        per_trade_cap_usdc=50.0,
        fee_model="maker",
        fee_amount=0.01,
        platform_gate=None,
        warmup=False,
        consensus_data=None,
    )

    dt.write.assert_called_once()
    _, kwargs = dt.write.call_args
    assert kwargs["prediction_id"] == "p_btc_789_900e"
    assert kwargs["kelly_raw"] == 0.30
    assert kwargs["kelly_capped"] == 0.25
    assert kwargs["warmup"] is False
    assert kwargs["policy_config_hash"] == "c" * 64
    assert kwargs["calibration_map_hash"] == "d" * 64
    assert kwargs["registry_load_generation"] == 0
    # filters list built from filter_inputs dict
    assert len(kwargs["filters"]) == 2


def test_write_verbose_trace_filter_eval_construction():
    """FilterEval objects are built correctly from filter_inputs dict."""
    dt = MagicMock()
    pb = _make_builder(decision_trace=dt)

    envelope = MagicMock()
    envelope.policy_config_hash = "e" * 64
    envelope.calibration_map_hash = "f" * 64
    envelope.registry_load_generation = 1

    pb.write_verbose_trace_for_v2_filters(
        prediction_id="p_test",
        envelope=envelope,
        filter_inputs={"only_filter": (0.5, 0.6, True)},
        kelly_raw=None,
        kelly_capped=None,
        bankroll_used=None,
        per_trade_cap_usdc=None,
        fee_model="unknown",
        fee_amount=None,
        platform_gate=None,
        warmup=True,
        consensus_data=None,
    )

    _, kwargs = dt.write.call_args
    fe = kwargs["filters"][0]
    assert isinstance(fe, FilterEval)
    assert fe.name == "only_filter"
    assert fe.threshold == 0.5
    assert fe.input_value == 0.6
    assert fe.passed is True
