"""Test wiring of _evaluate_paper_filters into _run_predictions (Task C3)."""
import pytest
from unittest.mock import patch, MagicMock


def make_trader(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        testnet=True,
        confidence_threshold=0.55,
    )


def test_evaluate_paper_filters_method_exists(tmp_path, monkeypatch, tiny_model_path):
    """Verify _evaluate_paper_filters method exists and is callable."""
    trader = make_trader(tmp_path, monkeypatch, tiny_model_path)
    assert hasattr(trader, "_evaluate_paper_filters"), "PaperTrader missing _evaluate_paper_filters method"
    assert callable(trader._evaluate_paper_filters), "_evaluate_paper_filters is not callable"


def test_evaluate_paper_filters_returns_result(tmp_path, monkeypatch, tiny_model_path):
    """Verify _evaluate_paper_filters returns a PipelineResult with passed field."""
    trader = make_trader(tmp_path, monkeypatch, tiny_model_path)

    # Create a filter context
    filter_ctx = {
        "prediction_id": "test_pred_1",
        "model_name": "900s_btc_v3_20260315",
        "symbol": "BTCUSDT",
        "boundary_ms": 1_700_000_000_000,
        "pred_proba": 0.55,
        "pred_direction": "up",
        "above_threshold": True,
        "warmup": False,
        "confidence_threshold": 0.55,
        "active_filter_mode": "confidence_gate",
        "p_market": None,
        "regime_features": {},
    }

    result = trader._evaluate_paper_filters(filter_ctx)

    # Should have passed attribute
    assert hasattr(result, "passed"), "FilterResult missing 'passed' field"
    assert isinstance(result.passed, bool), "passed should be bool"
