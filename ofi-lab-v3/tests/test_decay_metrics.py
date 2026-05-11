import pytest

from storage.decay_metrics import (
    compute_brier_score, compute_calibration_error, compute_rolling_ev,
    compute_recency_weighted_ev,
)


def test_brier_zero_when_predictions_perfect():
    assert compute_brier_score([(1.0, True), (0.0, False)]) == 0.0


def test_brier_high_when_predictions_inverted():
    score = compute_brier_score([(0.9, False), (0.1, True)])
    assert score > 0.7


def test_calibration_error_zero_when_well_calibrated():
    # Predict 60% three times, observe 2/3 wins ≈ 0.667
    rows = [(0.60, True)] * 2 + [(0.60, False)]
    err = compute_calibration_error(rows, bin_width=0.1)
    # Bin centered at 0.60; predicted=0.60, actual=2/3≈0.667
    assert err < 0.10


def test_rolling_ev_simple_average():
    ev_values = [0.01, 0.02, -0.01, 0.005]
    assert abs(compute_rolling_ev(ev_values) - 0.00625) < 1e-9


def test_rolling_ev_empty_returns_zero():
    assert compute_rolling_ev([]) == 0.0


def test_recency_weighted_ev_recent_dominates():
    # 10 wins of $0.05 followed by 10 losses of -$0.05; recency
    # weighting should pull EV negative.
    ev_values = [0.05] * 10 + [-0.05] * 10
    rate = compute_recency_weighted_ev(ev_values, alpha=0.3)
    assert rate < 0.0
