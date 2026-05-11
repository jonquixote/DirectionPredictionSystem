from execution.conflict import (
    rolling_win_rate, ewma_win_rate, ewma_weight,
    detect_conflict, ConflictResult,
)


def test_rolling_win_rate_simple_average():
    outcomes = [True, True, False, True, True]
    assert rolling_win_rate(outcomes) == 0.8


def test_rolling_win_rate_empty_returns_zero():
    assert rolling_win_rate([]) == 0.0


def test_ewma_win_rate_recent_outcomes_dominate():
    # 5 wins followed by 5 losses; with alpha=0.5 the recent losses
    # should pull the EWMA below 0.5.
    outcomes = [True]*5 + [False]*5
    rate = ewma_win_rate(outcomes, alpha=0.5)
    assert rate < 0.4


def test_ewma_weight_clamps_to_min_when_no_history():
    w = ewma_weight(outcomes=[], alpha=0.05, min_weight=0.1, min_samples=50)
    assert w == 0.1


def test_ewma_weight_returns_recency_weighted_after_min_samples():
    outcomes = [True]*100
    w = ewma_weight(outcomes, alpha=0.05, min_weight=0.1, min_samples=50)
    assert w > 0.5  # all wins → near 1.0


def test_detect_conflict_when_two_models_disagree():
    result = detect_conflict({
        "m1": "up",
        "m2": "down",
    })
    assert isinstance(result, ConflictResult)
    assert result.has_conflict is True
    assert sorted(result.models) == ["m1", "m2"]


def test_detect_conflict_when_all_agree():
    result = detect_conflict({"m1": "up", "m2": "up"})
    assert result.has_conflict is False
