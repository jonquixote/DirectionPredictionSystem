"""Property tests for filters.regime_gate.resolve_thresholds.

Both the dashboard simulate path (services/analysis.py) and the live trader
path (trading/boundary_scorer.py) call the same `resolve_thresholds` helper,
so this single test pins their behaviour.
"""
import pytest

from filters.regime_gate import resolve_thresholds, validate_regime_gates


def test_no_regime_gates_returns_base():
    assert resolve_thresholds({}, {"volatility": "high"}, 0.52, 0.0) == (0.52, 0.0)
    assert resolve_thresholds(None, {"volatility": "high"}, 0.52, 0.0) == (0.52, 0.0)


def test_no_regime_returns_base():
    fc = {"regime_gates": {"volatility": {"high": {"confidence_threshold": 0.60}}}}
    assert resolve_thresholds(fc, None, 0.52, 0.0) == (0.52, 0.0)
    assert resolve_thresholds(fc, {}, 0.52, 0.0) == (0.52, 0.0)


def test_volatility_high_override():
    fc = {
        "regime_gates": {
            "volatility": {
                "high": {"confidence_threshold": 0.60, "ev_threshold": 0.01},
                "low": {"confidence_threshold": 0.50},
            }
        }
    }
    assert resolve_thresholds(fc, {"volatility": "high"}, 0.52, 0.0) == (0.60, 0.01)
    assert resolve_thresholds(fc, {"volatility": "low"}, 0.52, 0.0) == (0.50, 0.0)
    assert resolve_thresholds(fc, {"volatility": "medium"}, 0.52, 0.0) == (0.52, 0.0)


def test_partial_override_keeps_base_for_missing_keys():
    """ev_threshold not specified — should keep base value."""
    fc = {"regime_gates": {"volatility": {"high": {"confidence_threshold": 0.60}}}}
    ct, ev = resolve_thresholds(fc, {"volatility": "high"}, 0.52, 0.01)
    assert ct == 0.60
    assert ev == 0.01


def test_multi_axis_later_axis_overrides():
    """liquidity gate applied after volatility, so it overrides keys it specifies."""
    fc = {
        "regime_gates": {
            "volatility": {"high": {"confidence_threshold": 0.60, "ev_threshold": 0.01}},
            "liquidity":  {"thin": {"confidence_threshold": 0.65}},
        }
    }
    ct, ev = resolve_thresholds(fc, {"volatility": "high", "liquidity": "thin"}, 0.52, 0.0)
    assert ct == 0.65  # liquidity override wins
    assert ev == 0.01  # only volatility set ev


def test_unknown_regime_value_falls_through():
    fc = {"regime_gates": {"volatility": {"high": {"confidence_threshold": 0.60}}}}
    ct, ev = resolve_thresholds(fc, {"volatility": "unknown"}, 0.52, 0.0)
    assert ct == 0.52
    assert ev == 0.0


def test_validate_regime_gates_ok():
    validate_regime_gates(None)
    validate_regime_gates({})
    validate_regime_gates({"volatility": {"high": {"confidence_threshold": 0.6}}})


def test_validate_regime_gates_rejects_bad_axis():
    with pytest.raises(ValueError):
        validate_regime_gates({"velocity": {"high": {"confidence_threshold": 0.6}}})


def test_validate_regime_gates_rejects_unknown_key():
    with pytest.raises(ValueError):
        validate_regime_gates(
            {"volatility": {"high": {"min_recency_weighted_ev": 0.1}}}
        )


def test_validate_regime_gates_rejects_non_numeric_threshold():
    with pytest.raises(ValueError):
        validate_regime_gates(
            {"volatility": {"high": {"confidence_threshold": "0.6"}}}
        )


def test_parity_simulate_vs_live_path():
    """Property test: simulate's _apply_filter_row threshold logic uses the
    same resolve_thresholds() helper as the live trader. Verifying the
    helper is the single source of truth here is the parity guarantee.
    Both paths must import from filters.regime_gate.
    """
    import inspect

    # Verify analysis.py imports and uses resolve_thresholds
    from dashboard_api.services import analysis as analysis_mod
    src = inspect.getsource(analysis_mod._apply_filter_row)
    assert "resolve_thresholds" in src, (
        "services/analysis.py:_apply_filter_row must call resolve_thresholds"
    )

    # Verify boundary_scorer.py imports and uses resolve_thresholds
    from trading import boundary_scorer as bs_mod
    src = inspect.getsource(bs_mod)
    assert "from filters.regime_gate import resolve_thresholds" in src, (
        "trading/boundary_scorer.py must import resolve_thresholds from filters.regime_gate"
    )
    assert "resolve_thresholds(" in src, (
        "trading/boundary_scorer.py must call resolve_thresholds"
    )
