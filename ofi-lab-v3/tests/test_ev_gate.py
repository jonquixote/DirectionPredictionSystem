from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from trading.boundary_scorer import BoundaryScorer
from tests.test_boundary_scorer import _make_mock_trader, _run


@pytest.mark.parametrize("p_market, expected_up, expected_down", [
    (0.30, 0.30, 0.70),
    (0.50, 0.50, 0.50),
    (0.70, 0.70, 0.30),
])
def test_ev_gate_direction_pricing(p_market, expected_up, expected_down):
    """Verify that UP uses p_market directly and DOWN uses 1.0 - p_market."""
    
    # 1. Test UP direction (model predicts 0.6)
    models_up = {"model_up": MagicMock()}
    models_up["model_up"].predict.return_value = [0.6]
    
    model_meta_up = {
        "model_up": {
            "symbol": "BTCUSDT",
            "training_horizon_seconds": 300,
            "filter_config": {},
        }
    }
    
    trader_up = _make_mock_trader(models=models_up, model_meta=model_meta_up)
    trader_up._http_session = MagicMock()
    
    scorer_up = BoundaryScorer(trader=trader_up)
    
    # Mock get_p_market and compute_ev_polymarket
    with patch("trading.polymarket_discovery.get_p_market", new_callable=AsyncMock) as mock_get_p, \
         patch("execution.ev.compute_ev_polymarket") as mock_compute_ev:
        
        mock_get_p.return_value = p_market
        mock_compute_ev.return_value = MagicMock(ev=0.05)
        
        # Run scorer
        _run(scorer_up.score_boundary(now_ms=1000000, boundary_ms=900000))
        
        # Verify call to compute_ev_polymarket
        mock_compute_ev.assert_called_once()
        kwargs = mock_compute_ev.call_args.kwargs
        assert abs(kwargs["p_market"] - expected_up) < 1e-6

    # 2. Test DOWN direction (model predicts 0.4)
    models_down = {"model_down": MagicMock()}
    models_down["model_down"].predict.return_value = [0.4]
    
    model_meta_down = {
        "model_down": {
            "symbol": "BTCUSDT",
            "training_horizon_seconds": 300,
            "filter_config": {},
        }
    }
    
    trader_down = _make_mock_trader(models=models_down, model_meta=model_meta_down)
    trader_down._http_session = MagicMock()
    
    scorer_down = BoundaryScorer(trader=trader_down)
    
    with patch("trading.polymarket_discovery.get_p_market", new_callable=AsyncMock) as mock_get_p, \
         patch("execution.ev.compute_ev_polymarket") as mock_compute_ev:
        
        mock_get_p.return_value = p_market
        mock_compute_ev.return_value = MagicMock(ev=0.05)
        
        # Run scorer
        _run(scorer_down.score_boundary(now_ms=1000000, boundary_ms=900000))
        
        # Verify call to compute_ev_polymarket
        mock_compute_ev.assert_called_once()
        kwargs = mock_compute_ev.call_args.kwargs
        assert abs(kwargs["p_market"] - expected_down) < 1e-6
