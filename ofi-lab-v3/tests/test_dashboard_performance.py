"""Step 11: Performance analysis endpoint tests — portfolio, threshold sweep, pareto."""
import pytest
from dashboard_api.services.metrics import (
    portfolio_metrics, threshold_sweep, pareto_frontier,
    compute_realized_net, SYSTEM_FEE,
)


def _make_trade(direction="up", correct=True, p_market=0.55, proba=0.60):
    return {
        "pred_direction": direction,
        "prediction_correct": correct,
        "p_market": p_market,
        "pred_proba": proba,
        "pred_proba_calibrated": proba,
    }


class TestPortfolioMetrics:
    def test_empty_trades(self):
        result = portfolio_metrics([])
        assert result["total_trades"] == 0
        assert result["total_roi_pct"] == 0.0

    def test_all_wins(self):
        trades = [_make_trade(correct=True, p_market=0.55) for _ in range(10)]
        result = portfolio_metrics(trades)
        assert result["total_trades"] == 10
        assert result["win_rate"] == 1.0
        assert result["total_ne"] > 0
        assert result["total_roi_pct"] > 0
        assert result["max_drawdown"] == 0.0

    def test_mixed_trades(self):
        trades = [
            _make_trade(correct=True, p_market=0.55),
            _make_trade(correct=False, p_market=0.55),
            _make_trade(correct=True, p_market=0.60),
            _make_trade(correct=True, p_market=0.50),
        ]
        result = portfolio_metrics(trades)
        assert result["total_trades"] == 4
        assert 0 < result["win_rate"] < 1
        assert "profit_factor" in result
        assert "sharpe_ratio" in result
        assert "sortino_ratio" in result
        assert result["max_drawdown"] >= 0

    def test_all_losses(self):
        trades = [_make_trade(correct=False, p_market=0.55) for _ in range(5)]
        result = portfolio_metrics(trades)
        assert result["win_rate"] == 0.0
        assert result["total_ne"] < 0
        assert result["profit_factor"] == 0.0


class TestThresholdSweep:
    def test_returns_points(self):
        trades = [_make_trade(proba=0.55 + i * 0.02) for i in range(10)]
        result = threshold_sweep(trades)
        assert len(result) > 0
        assert all("threshold" in p for p in result)
        assert all("win_rate" in p for p in result)
        assert all("roi_pct" in p for p in result)
        assert all("n_trades" in p for p in result)

    def test_higher_threshold_fewer_trades(self):
        trades = [
            _make_trade(proba=0.55),
            _make_trade(proba=0.65),
            _make_trade(proba=0.75),
        ]
        result = threshold_sweep(trades, thresholds=[0.50, 0.60, 0.70])
        # At 0.50 threshold: all 3 pass
        assert result[0]["n_trades"] == 3
        # At 0.60 threshold: 2 pass
        assert result[1]["n_trades"] == 2
        # At 0.70 threshold: 1 passes
        assert result[2]["n_trades"] == 1

    def test_empty_trades(self):
        result = threshold_sweep([])
        assert all(p["n_trades"] == 0 for p in result)


class TestParetoFrontier:
    def test_returns_non_dominated(self):
        sweep = [
            {"threshold": 0.50, "win_rate": 0.45, "roi_pct": -2.0, "n_trades": 100},
            {"threshold": 0.55, "win_rate": 0.50, "roi_pct": 0.5, "n_trades": 80},
            {"threshold": 0.60, "win_rate": 0.55, "roi_pct": 1.0, "n_trades": 50},
            {"threshold": 0.65, "win_rate": 0.60, "roi_pct": 0.8, "n_trades": 30},
        ]
        frontier = pareto_frontier(sweep)
        assert len(frontier) > 0
        # All frontier points should have positive or improving trade-off
        for i in range(len(frontier)):
            for j in range(len(frontier)):
                if i != j:
                    p1, p2 = frontier[i], frontier[j]
                    # No point strictly dominates another
                    assert not (
                        p2["win_rate"] >= p1["win_rate"] and
                        (p2.get("roi_pct", 0) or 0) >= (p1.get("roi_pct", 0) or 0) and
                        (p2["win_rate"] > p1["win_rate"] or (p2.get("roi_pct", 0) or 0) > (p1.get("roi_pct", 0) or 0))
                    )

    def test_empty_sweep(self):
        assert pareto_frontier([]) == []
