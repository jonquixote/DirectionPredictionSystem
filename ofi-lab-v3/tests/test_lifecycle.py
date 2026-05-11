import pytest

from storage.lifecycle import (
    LifecycleStateMachine, LifecycleDecision, LifecycleConfig,
)


CONFIG = LifecycleConfig(
    eligibility_min_resolved=200,
    eligibility_min_recency_weighted_ev=0.05,
    eligibility_max_calibration_error=0.05,
    suspend_recency_weighted_ev=0.02,
    suspend_calibration_error=0.10,
    reactivate_recency_weighted_ev=0.05,
    reactivate_calibration_error=0.05,
    requalification_failures_to_retire=3,
)


def test_prediction_only_promotes_when_eligible():
    fsm = LifecycleStateMachine(CONFIG)
    decision = fsm.evaluate(
        current_state="prediction_only",
        resolved_count=250,
        recency_weighted_ev=0.06,
        calibration_error=0.04,
        consecutive_requalification_failures=0,
        is_baseline=False,
    )
    assert decision.next_state == "live_eligible"
    assert decision.reason == "eligibility_met"


def test_prediction_only_stays_when_not_enough_data():
    fsm = LifecycleStateMachine(CONFIG)
    decision = fsm.evaluate(
        current_state="prediction_only",
        resolved_count=50,
        recency_weighted_ev=0.06,
        calibration_error=0.04,
        consecutive_requalification_failures=0,
        is_baseline=False,
    )
    assert decision.next_state == "prediction_only"


def test_live_active_suspends_when_ev_drops():
    fsm = LifecycleStateMachine(CONFIG)
    decision = fsm.evaluate(
        current_state="live_active",
        resolved_count=300,
        recency_weighted_ev=0.01,  # below 0.02 suspend threshold
        calibration_error=0.04,
        consecutive_requalification_failures=0,
        is_baseline=False,
    )
    assert decision.next_state == "live_suspended"
    assert "ev" in decision.reason.lower()


def test_hysteresis_suspend_threshold_below_reactivate_threshold():
    # Suspend at 0.02, reactivate at 0.05 → gap of 0.03 prevents flapping.
    assert CONFIG.suspend_recency_weighted_ev < CONFIG.reactivate_recency_weighted_ev


def test_live_suspended_does_not_immediately_reactivate_on_low_ev():
    fsm = LifecycleStateMachine(CONFIG)
    decision = fsm.evaluate(
        current_state="live_suspended",
        resolved_count=300,
        recency_weighted_ev=0.03,  # above suspend (0.02) but below reactivate (0.05)
        calibration_error=0.04,
        consecutive_requalification_failures=0,
        is_baseline=False,
    )
    # Stays suspended (or moves to requalification cooling) — does NOT
    # jump straight to live_active.
    assert decision.next_state in ("live_suspended", "requalification")


def test_requalification_promotes_to_live_active_when_metrics_recover():
    fsm = LifecycleStateMachine(CONFIG)
    decision = fsm.evaluate(
        current_state="requalification",
        resolved_count=400,
        recency_weighted_ev=0.06,
        calibration_error=0.04,
        consecutive_requalification_failures=0,
        is_baseline=False,
    )
    assert decision.next_state == "live_active"


def test_requalification_retires_after_repeated_failures():
    fsm = LifecycleStateMachine(CONFIG)
    decision = fsm.evaluate(
        current_state="requalification",
        resolved_count=400,
        recency_weighted_ev=0.01,
        calibration_error=0.20,
        consecutive_requalification_failures=3,
        is_baseline=False,
    )
    assert decision.next_state == "retired"


def test_baseline_never_retired():
    fsm = LifecycleStateMachine(CONFIG)
    decision = fsm.evaluate(
        current_state="requalification",
        resolved_count=400,
        recency_weighted_ev=0.01,
        calibration_error=0.20,
        consecutive_requalification_failures=10,
        is_baseline=True,
    )
    # Baseline can be live_suspended but never retired.
    assert decision.next_state != "retired"
