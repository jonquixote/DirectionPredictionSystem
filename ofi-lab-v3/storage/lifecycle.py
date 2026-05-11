"""Lifecycle state machine with hysteresis.

Live axis: prediction_only → live_eligible → live_active →
            live_suspended → requalification → retired
Paper axis: paper_active ↔ paper_paused (independent — handled
            outside this FSM).

Hysteresis: the EV / calibration thresholds for suspend are looser
than for reactivate, preventing rapid flapping.

Baseline protection: a model with is_baseline=True never enters
'retired'. It can be live_suspended (and re-enter requalification)
but the FSM rejects retirement.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LifecycleConfig:
    eligibility_min_resolved: int = 200
    eligibility_min_recency_weighted_ev: float = 0.05
    eligibility_max_calibration_error: float = 0.05
    suspend_recency_weighted_ev: float = 0.02
    suspend_calibration_error: float = 0.10
    reactivate_recency_weighted_ev: float = 0.05
    reactivate_calibration_error: float = 0.05
    requalification_failures_to_retire: int = 3


@dataclass(frozen=True)
class LifecycleDecision:
    next_state: str
    reason: str


class LifecycleStateMachine:
    def __init__(self, config: LifecycleConfig) -> None:
        self._cfg = config

    def evaluate(
        self, *,
        current_state: str,
        resolved_count: int,
        recency_weighted_ev: float,
        calibration_error: float,
        consecutive_requalification_failures: int,
        is_baseline: bool,
    ) -> LifecycleDecision:
        c = self._cfg
        if current_state == "prediction_only":
            if (
                resolved_count >= c.eligibility_min_resolved
                and recency_weighted_ev >= c.eligibility_min_recency_weighted_ev
                and calibration_error <= c.eligibility_max_calibration_error
            ):
                return LifecycleDecision("live_eligible", "eligibility_met")
            return LifecycleDecision("prediction_only", "below_eligibility")

        if current_state == "live_eligible":
            # Manual promotion or auto-promote in Plan B; default: stay
            return LifecycleDecision("live_eligible", "awaiting_promotion")

        if current_state == "live_active":
            if recency_weighted_ev < c.suspend_recency_weighted_ev:
                return LifecycleDecision(
                    "live_suspended",
                    f"recency_weighted_ev {recency_weighted_ev:.4f} < "
                    f"suspend threshold {c.suspend_recency_weighted_ev}",
                )
            if calibration_error > c.suspend_calibration_error:
                return LifecycleDecision(
                    "live_suspended",
                    f"calibration_error {calibration_error:.4f} > "
                    f"suspend threshold {c.suspend_calibration_error}",
                )
            return LifecycleDecision("live_active", "stable")

        if current_state == "live_suspended":
            # Move to requalification after enough fresh resolved
            return LifecycleDecision("requalification", "cooling_complete")

        if current_state == "requalification":
            if (
                recency_weighted_ev >= c.reactivate_recency_weighted_ev
                and calibration_error <= c.reactivate_calibration_error
            ):
                return LifecycleDecision("live_active", "metrics_recovered")
            if (
                consecutive_requalification_failures
                >= c.requalification_failures_to_retire
            ):
                if is_baseline:
                    return LifecycleDecision(
                        "live_suspended",
                        "baseline cannot retire — staying suspended",
                    )
                return LifecycleDecision(
                    "retired",
                    f"requalification failed "
                    f"{consecutive_requalification_failures}× consecutive",
                )
            return LifecycleDecision("requalification", "metrics_below_reactivate")

        if current_state == "retired":
            return LifecycleDecision("retired", "terminal")

        # Unknown state → no-op
        return LifecycleDecision(current_state, "unknown_state")
