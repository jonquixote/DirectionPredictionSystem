"""Paper-tier filter — runs paper trades under their own optimal config.

Independent of live-platform state: paper-active continues even when
live is suspended. Plan B's MVP gates: confidence, EV, blackout. Plan
B+ adds: regime, cooldown, exposure cap.
"""
from __future__ import annotations

from filters.pipeline import FilterStage, FilterDecision


def build_paper_filter_stage(
    *, confidence_threshold: float, ev_threshold: float,
) -> FilterStage:
    def stage(ctx: dict) -> FilterDecision:
        # 1. Confidence gate
        conf = ctx.get("calibrated_p", 0.0)
        if conf < confidence_threshold:
            return FilterDecision.block(
                reason="below_confidence",
                threshold=confidence_threshold, input_value=conf,
            )
        # 2. EV gate (universal)
        ev = ctx.get("ev", 0.0)
        if ev <= ev_threshold:
            return FilterDecision.block(
                reason="negative_ev",
                threshold=ev_threshold, input_value=ev,
            )
        # 3. Model conflict gate (Steering §4)
        if ctx.get("model_conflict", False):
            return FilterDecision.block(
                reason="model_conflict",
                input_value=None,
            )
        # 4. Blackout gate
        utc_hour = ctx.get("utc_hour")
        blackout = ctx.get("blackout_hours", [])
        if utc_hour is not None and utc_hour in blackout:
            return FilterDecision.block(
                reason="blackout",
                threshold=None, input_value=utc_hour,
            )
        return FilterDecision.pass_()
    return FilterStage(name="paper_filter", fn=stage)
