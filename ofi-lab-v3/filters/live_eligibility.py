"""Layer 4 — live dispatch eligibility.

Independent of paper status. A model can be paper_active +
live_suspended; this stage blocks live dispatch but does not affect
paper trading.
"""
from __future__ import annotations

from filters.pipeline import FilterStage, FilterDecision


def build_live_eligibility_stage() -> FilterStage:
    def stage(ctx: dict) -> FilterDecision:
        if ctx.get("lifecycle_state") != "live_active":
            return FilterDecision.block(
                reason="lifecycle_state_not_live_active",
                input_value=None,
            )
        if not ctx.get("kalshi_live_enabled", False):
            return FilterDecision.block(
                reason="kalshi_live_disabled",
                input_value=None,
            )
        return FilterDecision.pass_()
    return FilterStage(name="live_eligibility", fn=stage)
