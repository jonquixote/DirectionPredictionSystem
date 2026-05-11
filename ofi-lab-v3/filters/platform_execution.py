"""Layer 5 — platform-specific execution gates (Kalshi MVP).

Per-platform orderability checks: market exists, book has quotes,
price not extreme. Plan B+ adds maker/taker availability, min/max
contract size, allow-list checks.
"""
from __future__ import annotations

from filters.pipeline import FilterStage, FilterDecision


def build_kalshi_execution_stage() -> FilterStage:
    def stage(ctx: dict) -> FilterDecision:
        if not ctx.get("kalshi_market_exists", False):
            return FilterDecision.block(reason="no_market", input_value=None)
        if not ctx.get("kalshi_book_has_quotes", False):
            return FilterDecision.block(reason="empty_book", input_value=None)
        if ctx.get("extreme_price", False):
            return FilterDecision.block(reason="extreme_price", input_value=None)
        return FilterDecision.pass_()
    return FilterStage(name="platform_execution", fn=stage)
