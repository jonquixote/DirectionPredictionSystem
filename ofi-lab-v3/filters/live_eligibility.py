"""Layer 4 — live dispatch eligibility.

Independent of paper status. A model can be paper_active +
live_suspended; this stage blocks live dispatch but does not affect
paper trading.
"""
from __future__ import annotations

from dataclasses import dataclass
from filters.pipeline import FilterStage, FilterDecision


@dataclass(frozen=True)
class EligibilityResult:
    """Result of live eligibility check with progress indicators."""
    eligible: bool
    progress: dict[str, dict[str, int]]  # e.g. {"paper_trades": {"have": 73, "need": 100}}

    def _asdict(self):
        return {
            "eligible": self.eligible,
            "progress": self.progress,
        }


def check_live_eligibility(conn, model_name: str) -> EligibilityResult:
    """Check if a model is eligible for live trading and return progress."""
    progress = {}

    # Check paper trade count requirement (example: 100 trades)
    paper_trade_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM paper_trades "
        "WHERE model_name = ? AND resolved = 1 AND warmup = 0",
        (model_name,)
    ).fetchone()
    have_trades = paper_trade_count["cnt"] if paper_trade_count else 0
    need_trades = 100  # Configurable threshold
    progress["paper_trades"] = {"have": have_trades, "need": need_trades}

    # Check if eligible for live
    eligible = have_trades >= need_trades

    # Check other metrics (example structure, can be extended)
    metrics = conn.execute(
        "SELECT recency_weighted_ev, calibration_error FROM decay_metrics "
        "WHERE model_name = ? ORDER BY ts DESC LIMIT 1",
        (model_name,)
    ).fetchone()

    if metrics:
        # Convert EV to scale of 10000
        ev_val = metrics["recency_weighted_ev"] or 0
        ev_x10000 = int(ev_val * 10000)
        progress["ev_x10000"] = {"have": max(0, ev_x10000), "need": 500}  # 0.05 * 10000
        eligible = eligible and ev_x10000 >= 500

    return EligibilityResult(eligible=eligible, progress=progress)


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
