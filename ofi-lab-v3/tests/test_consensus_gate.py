"""Tests for the consensus_required gate in trading/boundary_scorer.py.

Three scenarios:
  1. consensus_required=True + fleet consensus=1 → paper_trade WRITTEN
  2. consensus_required=True + fleet consensus=0 → paper_trade NOT written, prediction logged
  3. consensus_required=False (default)           → normal behaviour unchanged

The gate works by deferring paper_trade writes for models with
filter_config.consensus_required=True until after all models for the symbol
have scored. Consensus is computed locally from per_boundary_scores (all
directions must agree). Predictions are always logged immediately.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers (mirror test_boundary_scorer.py pattern)
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine in a fresh event loop."""
    prev = None
    try:
        prev = asyncio.get_event_loop()
    except RuntimeError:
        pass
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(prev)


from trading.boundary_scorer import BoundaryScorer


def _make_mock_trader(
    *,
    models: dict | None = None,
    model_meta: dict | None = None,
    feature_names: dict | None = None,
    filter_verdict_passed: bool = True,
    first_data_time_ms: int | None = 0,
) -> MagicMock:
    """Return a MagicMock shaped like PaperTrader for consensus gate tests."""
    if models is None:
        models = {"h300_btc": MagicMock()}
        models["h300_btc"].predict.return_value = [0.65]

    if model_meta is None:
        model_meta = {
            "h300_btc": {
                "symbol": "BTCUSDT",
                "training_horizon_seconds": 300,
                "filter_config": {"consensus_required": True},
            }
        }

    if feature_names is None:
        feature_names = {name: ["f1", "f2"] for name in models}

    bar = {"mid_price": 95000.0, "f1": 1.0, "f2": 2.0, "ts_ms": 0, "has_quotes": True}

    t = MagicMock()
    t.models = models
    t._model_meta = model_meta
    t.feature_names = feature_names
    t._prediction_count = 0
    t._trade_count = 0
    t._current_kalshi_bankroll = None
    t._first_data_time_ms = first_data_time_ms
    t._kalshi_trader = None
    t._http_session = None

    t.feature_computer.is_warmed_up.return_value = True
    t.feature_computer.get_1min_bar.return_value = bar

    t.filters = {
        "confidence_threshold": 0.52,
        "ev_threshold": 0.0,
        "max_book_age_seconds": 30,
    }

    t.calibrators.get.side_effect = KeyError("no calibrator")
    t._get_meta.side_effect = lambda mn: model_meta[mn]
    t._is_in_warmup.return_value = False
    t._is_model_in_warmup.return_value = False

    verdict = MagicMock()
    verdict.passed = filter_verdict_passed
    verdict.reason = None if filter_verdict_passed else "below_confidence"
    t._evaluate_paper_filters.return_value = verdict

    t._emit_prediction_rows.return_value = "pred-test-001_300e"
    t.kalshi_dispatch_eligible.return_value = False

    return t


# ---------------------------------------------------------------------------
# Test 1: consensus_required=True, consensus=1 → paper_trade written
# ---------------------------------------------------------------------------

def test_consensus_required_true_consensus_1_trade_written():
    """When all models agree (consensus=1), paper_trade IS written for consensus_required model."""
    # Two models, both predict "up" (same direction → consensus=1)
    models = {
        "eth_300_a": MagicMock(),
        "eth_300_b": MagicMock(),
    }
    models["eth_300_a"].predict.return_value = [0.70]  # direction: up
    models["eth_300_b"].predict.return_value = [0.68]  # direction: up

    model_meta = {
        "eth_300_a": {
            "symbol": "ETHUSDT",
            "training_horizon_seconds": 300,
            "filter_config": {"consensus_required": True},
        },
        "eth_300_b": {
            "symbol": "ETHUSDT",
            "training_horizon_seconds": 300,
            "filter_config": {"consensus_required": True},
        },
    }
    feature_names = {
        "eth_300_a": ["f1", "f2"],
        "eth_300_b": ["f1", "f2"],
    }

    t = _make_mock_trader(
        models=models,
        model_meta=model_meta,
        feature_names=feature_names,
        filter_verdict_passed=True,
    )
    # Make prediction_ids unique per model
    call_count = [0]
    def _emit_side_effect(**kw):
        call_count[0] += 1
        return f"pred-{kw['model_name']}-001_300e"
    t._emit_prediction_rows.side_effect = _emit_side_effect

    scorer = BoundaryScorer(trader=t)

    sel_result = MagicMock()
    sel_result.blocked = set()

    with patch("trading.model_selector.ModelSelector") as MockSel:
        MockSel.return_value.select.return_value = sel_result
        _run(scorer.score_boundary(now_ms=1_000_000, boundary_ms=900_000))

    # Both models passed filter → consensus=1 → paper_trade should be written
    assert t.sqlite_ledger.log_paper_trade.called, (
        "Expected log_paper_trade to be called when consensus=1"
    )
    # Predictions always logged regardless
    assert t._emit_prediction_rows.call_count == 2


# ---------------------------------------------------------------------------
# Test 2: consensus_required=True, consensus=0 → trade NOT written, prediction logged
# ---------------------------------------------------------------------------

def test_consensus_required_true_no_consensus_trade_suppressed():
    """When models disagree (consensus=0), paper_trade NOT written; prediction IS logged."""
    models = {
        "eth_300_a": MagicMock(),
        "eth_300_b": MagicMock(),
    }
    models["eth_300_a"].predict.return_value = [0.70]  # direction: up
    models["eth_300_b"].predict.return_value = [0.32]  # direction: down  ← split!

    model_meta = {
        "eth_300_a": {
            "symbol": "ETHUSDT",
            "training_horizon_seconds": 300,
            "filter_config": {"consensus_required": True},
        },
        "eth_300_b": {
            "symbol": "ETHUSDT",
            "training_horizon_seconds": 300,
            "filter_config": {"consensus_required": True},
        },
    }
    feature_names = {
        "eth_300_a": ["f1", "f2"],
        "eth_300_b": ["f1", "f2"],
    }

    t = _make_mock_trader(
        models=models,
        model_meta=model_meta,
        feature_names=feature_names,
        filter_verdict_passed=True,
    )
    call_count = [0]
    def _emit_side_effect(**kw):
        call_count[0] += 1
        return f"pred-{kw['model_name']}-001_300e"
    t._emit_prediction_rows.side_effect = _emit_side_effect

    scorer = BoundaryScorer(trader=t)

    sel_result = MagicMock()
    sel_result.blocked = set()

    with patch("trading.model_selector.ModelSelector") as MockSel:
        MockSel.return_value.select.return_value = sel_result
        _run(scorer.score_boundary(now_ms=1_000_000, boundary_ms=900_000))

    # Predictions always logged (2 models × ETHUSDT)
    assert t._emit_prediction_rows.call_count == 2, (
        "Predictions should always be logged regardless of consensus"
    )

    # No paper_trade should be written when consensus=0
    assert not t.sqlite_ledger.log_paper_trade.called, (
        "paper_trade must NOT be written when consensus=0 (split directions)"
    )

    # _record_compact_decision should be called for suppression
    assert t._record_compact_decision.called


# ---------------------------------------------------------------------------
# Test 3: consensus_required=False (default) → normal behaviour, trade written
# ---------------------------------------------------------------------------

def test_consensus_required_false_default_trade_written_immediately():
    """Without consensus_required, model with proba>threshold triggers paper_trade immediately."""
    models = {
        "btc_solo": MagicMock(),
    }
    models["btc_solo"].predict.return_value = [0.70]

    model_meta = {
        "btc_solo": {
            "symbol": "BTCUSDT",
            "training_horizon_seconds": 300,
            "filter_config": {},  # no consensus_required key
        }
    }
    feature_names = {"btc_solo": ["f1", "f2"]}

    t = _make_mock_trader(
        models=models,
        model_meta=model_meta,
        feature_names=feature_names,
        filter_verdict_passed=True,
    )
    t._emit_prediction_rows.return_value = "pred-btc-solo-001_300e"

    scorer = BoundaryScorer(trader=t)

    sel_result = MagicMock()
    sel_result.blocked = set()

    with patch("trading.model_selector.ModelSelector") as MockSel:
        MockSel.return_value.select.return_value = sel_result
        _run(scorer.score_boundary(now_ms=1_000_000, boundary_ms=900_000))

    # Prediction always logged
    assert t._emit_prediction_rows.call_count == 1

    # Paper trade written immediately (no deferral) when consensus_required not set
    assert t.sqlite_ledger.log_paper_trade.called, (
        "paper_trade should be written normally when consensus_required is absent"
    )
