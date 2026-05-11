import pytest

from storage.lifecycle import (
    LifecycleStateMachine, LifecycleConfig, LifecycleDecision,
)


CFG = LifecycleConfig(
    eligibility_min_resolved=200,
    eligibility_min_recency_weighted_ev=0.05,
    eligibility_max_calibration_error=0.05,
    suspend_recency_weighted_ev=0.02,
    suspend_calibration_error=0.10,
    reactivate_recency_weighted_ev=0.05,
    reactivate_calibration_error=0.05,
    requalification_failures_to_retire=3,
)


def test_baseline_in_requalification_with_failures_stays_suspended():
    fsm = LifecycleStateMachine(CFG)
    for failures in (3, 5, 10, 100):
        d = fsm.evaluate(
            current_state="requalification",
            resolved_count=400,
            recency_weighted_ev=-0.10,
            calibration_error=0.30,
            consecutive_requalification_failures=failures,
            is_baseline=True,
        )
        assert d.next_state != "retired", f"failed at {failures} failures"
        assert d.next_state in ("live_suspended", "requalification")


def test_non_baseline_retires_after_threshold():
    fsm = LifecycleStateMachine(CFG)
    d = fsm.evaluate(
        current_state="requalification",
        resolved_count=400,
        recency_weighted_ev=-0.10,
        calibration_error=0.30,
        consecutive_requalification_failures=3,
        is_baseline=False,
    )
    assert d.next_state == "retired"


def test_baseline_can_still_be_suspended_from_live_active():
    fsm = LifecycleStateMachine(CFG)
    d = fsm.evaluate(
        current_state="live_active",
        resolved_count=400,
        recency_weighted_ev=0.005,
        calibration_error=0.04,
        consecutive_requalification_failures=0,
        is_baseline=True,
    )
    assert d.next_state == "live_suspended"
