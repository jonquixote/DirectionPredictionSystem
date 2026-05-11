"""Stale-price / stale-book rejection.

Used in both paper and live execution layers. A stale price means
the model's input may not reflect the current market, and trading on
it can produce phantom edge. An empty book means we can't price the
contract at all.
"""
from __future__ import annotations

from filters.pipeline import FilterStage, FilterDecision


def build_stale_price_stage(*, max_age_seconds: float) -> FilterStage:
    def stage(ctx: dict) -> FilterDecision:
        age = ctx.get("book_age_seconds", 0.0)
        if age > max_age_seconds:
            return FilterDecision.block(
                reason="stale_price",
                threshold=max_age_seconds, input_value=age,
            )
        return FilterDecision.pass_(
            threshold=max_age_seconds, input_value=age,
        )
    return FilterStage(name="stale_price", fn=stage)


def build_stale_book_stage() -> FilterStage:
    def stage(ctx: dict) -> FilterDecision:
        if not ctx.get("book_has_quotes", False):
            return FilterDecision.block(
                reason="empty_book", input_value=0.0,
            )
        return FilterDecision.pass_(input_value=1.0)
    return FilterStage(name="stale_book", fn=stage)
