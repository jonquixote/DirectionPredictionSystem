"""Expected-value calculator for paper (Polymarket) and Kalshi.

Universal EV formula:
    EV = calibrated_p * payout_if_win - (1 - calibrated_p) * cost_if_lose - fees - spread_cost

Per-platform fee models:
  - Polymarket: 0.072 * p * (1-p) * stake (concave around 0.5)
  - Kalshi maker: ~0.5% of payout; kalshi taker: ~1% of payout (rough
    approximation; live values come from execution/kalshi_fees.py
    when wired in Plan B+)
"""
from __future__ import annotations

from dataclasses import dataclass


POLYMARKET_FEE_COEF = 0.072
KALSHI_MAKER_FEE_COEF = 0.005
KALSHI_TAKER_FEE_COEF = 0.010


@dataclass(frozen=True)
class EVResult:
    ev: float
    fee: float
    payout_if_win: float
    cost_if_lose: float
    kelly_fraction: float


def compute_ev_polymarket(
    *, calibrated_p: float, p_market: float, stake: float,
    fee_coef: float = POLYMARKET_FEE_COEF,
) -> EVResult:
    """EV under Polymarket binary-option pricing.

    Buying YES at p_market; if YES resolves true, payout = stake * (1 - p_market) / p_market.
    If NO resolves, lose stake.
    """
    if p_market <= 0 or p_market >= 1:
        return EVResult(ev=-stake, fee=0.0, payout_if_win=0.0,
                        cost_if_lose=stake, kelly_fraction=0.0)
    payout_if_win = stake * (1 - p_market) / p_market
    cost_if_lose = stake
    fee = fee_coef * calibrated_p * (1 - calibrated_p) * stake
    ev = (
        calibrated_p * payout_if_win
        - (1 - calibrated_p) * cost_if_lose
        - fee
    )
    # Kelly fraction = (b*p - q) / b
    b = payout_if_win / stake  # net odds
    p = calibrated_p
    q = 1.0 - p
    kelly = (b * p - q) / b if b > 0 else 0.0
    kelly = max(0.0, min(1.0, kelly))
    return EVResult(ev=ev, fee=fee, payout_if_win=payout_if_win,
                    cost_if_lose=cost_if_lose, kelly_fraction=kelly)


def compute_ev_kalshi(
    *, calibrated_p: float, market_yes_price: float, stake: float,
    side: str, order_type: str,
) -> EVResult:
    """EV under Kalshi binary contract pricing.

    Kalshi prices contracts in [0, 1]. Buying YES at price y wins
    payout (1 - y) per contract; buying NO at price (1-y) wins payout y.
    side: 'yes' or 'no'.
    order_type: 'maker' or 'taker' (different fee tiers).
    """
    if market_yes_price <= 0 or market_yes_price >= 1:
        return EVResult(ev=-stake, fee=0.0, payout_if_win=0.0,
                        cost_if_lose=stake, kelly_fraction=0.0)
    if side == "yes":
        entry_price = market_yes_price
        win_p = calibrated_p
    elif side == "no":
        entry_price = 1.0 - market_yes_price
        win_p = 1.0 - calibrated_p
    else:
        raise ValueError(f"side must be yes or no, got {side!r}")
    contracts = stake / entry_price if entry_price > 0 else 0.0
    payout_if_win = contracts * (1.0 - entry_price)
    cost_if_lose = stake
    fee_coef = (
        KALSHI_MAKER_FEE_COEF if order_type == "maker"
        else KALSHI_TAKER_FEE_COEF
    )
    fee = fee_coef * payout_if_win
    ev = win_p * payout_if_win - (1 - win_p) * cost_if_lose - fee
    b = payout_if_win / stake if stake > 0 else 0.0
    kelly = (b * win_p - (1 - win_p)) / b if b > 0 else 0.0
    kelly = max(0.0, min(1.0, kelly))
    return EVResult(ev=ev, fee=fee, payout_if_win=payout_if_win,
                    cost_if_lose=cost_if_lose, kelly_fraction=kelly)
