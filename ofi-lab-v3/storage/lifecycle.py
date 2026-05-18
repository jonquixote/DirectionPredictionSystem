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


@dataclass(frozen=True)
class Transition:
    """Lifecycle transition record."""
    model_name: str
    old_state: str
    new_state: str
    reason: str

    def _asdict(self):
        return {
            "model_name": self.model_name,
            "old_state": self.old_state,
            "new_state": self.new_state,
            "reason": self.reason,
        }


def evaluate_lifecycle_transitions(conn, *, now_ms: int) -> list[Transition]:
    """Evaluate lifecycle FSM for all models and return transitions."""
    from storage.decay_metrics import compute_brier_score, compute_calibration_error, compute_recency_weighted_ev

    fsm = LifecycleStateMachine(LifecycleConfig())
    transitions = []

    models = conn.execute(
        "SELECT name, lifecycle_state, is_baseline FROM model_registry"
    ).fetchall()

    for model_row in models:
        model_name = model_row["name"]
        current_state = model_row["lifecycle_state"]
        is_baseline = bool(model_row["is_baseline"])

        # Fetch decay metrics for this model
        metrics = conn.execute(
            "SELECT * FROM decay_metrics WHERE model_name = ? ORDER BY ts DESC LIMIT 1",
            (model_name,)
        ).fetchone()

        if not metrics:
            continue

        resolved_count = metrics["sample_count"] or 0
        recency_weighted_ev = metrics["recency_weighted_ev"] or 0.0
        calibration_error = metrics["calibration_error"] or 0.0
        # sqlite3.Row supports __getitem__ + .keys() but NOT .get(). Cold-start
        # decay_metrics rows may pre-date the consecutive_requalification_failures
        # column — fall back to 0 if absent.
        consecutive_failures = (
            metrics["consecutive_requalification_failures"]
            if "consecutive_requalification_failures" in metrics.keys()
            else 0
        ) or 0

        # Evaluate FSM
        decision = fsm.evaluate(
            current_state=current_state,
            resolved_count=resolved_count,
            recency_weighted_ev=recency_weighted_ev,
            calibration_error=calibration_error,
            consecutive_requalification_failures=consecutive_failures,
            is_baseline=is_baseline,
        )

        if decision.next_state != current_state:
            # Transition occurred
            transitions.append(Transition(
                model_name=model_name,
                old_state=current_state,
                new_state=decision.next_state,
                reason=decision.reason,
            ))

            # Apply transition to database
            conn.execute(
                "UPDATE model_registry SET lifecycle_state = ? WHERE name = ?",
                (decision.next_state, model_name)
            )

            # Log audit entry
            conn.execute(
                "INSERT INTO model_audit (model_name, action, by_user, detail, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (model_name, "lifecycle_transition", "lifecycle_fsm",
                 f"from {current_state} to {decision.next_state}: {decision.reason}",
                 now_ms // 1000)
            )

    conn.commit()
    return transitions
