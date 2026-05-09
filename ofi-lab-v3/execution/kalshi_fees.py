"""
Kalshi fee model — parabolic curve, ceil-up to $0.0001 per Kalshi docs.

Formulas (per official Kalshi documentation):
    taker_fee  = ceil_to_0001(0.07   * P * (1 - P) * contracts)
    maker_fee  = ceil_to_0001(0.0175 * P * (1 - P) * contracts)
    settlement = 0  (no fee at resolution)

P is the YES price in [0, 1].

Key reference points at P=0.50:
    taker  = 1.75¢ / contract  → 3.5% effective rate
             must have +1.75pp true edge over market price
    maker  = 0.44¢ / contract  → 0.875% effective rate
             must have +0.44pp true edge over market price

This module is pure math — no I/O, no network, no state.
"""

from __future__ import annotations

import math

# Coefficients from Kalshi docs.
TAKER_COEF = 0.07
MAKER_COEF = 0.0175
ROUND_UP_TO = 0.0001  # round each fee UP to nearest 1/100¢


def _ceil_to(value: float, increment: float) -> float:
    """Round value UP to nearest increment."""
    return math.ceil(value / increment) * increment


def taker_fee(price: float, contracts: int = 1) -> float:
    """Taker fee in dollars. price in [0, 1], contracts >= 1."""
    raw = TAKER_COEF * price * (1.0 - price) * contracts
    return round(_ceil_to(raw, ROUND_UP_TO), 4)


def maker_fee(price: float, contracts: int = 1) -> float:
    """Maker fee in dollars. price in [0, 1], contracts >= 1."""
    raw = MAKER_COEF * price * (1.0 - price) * contracts
    return round(_ceil_to(raw, ROUND_UP_TO), 4)


def fee(price: float, contracts: int = 1, *, is_maker: bool = False) -> float:
    """Generic dispatcher."""
    return maker_fee(price, contracts) if is_maker else taker_fee(price, contracts)


def break_even_price(price: float, *, is_maker: bool = False) -> float:
    """
    Minimum true win probability needed to break even on a 1-contract entry.

    For a $1 payout contract, total cost = price + fee_per_contract.
    We need true_p * 1.0 >= price + fee → true_p >= price + fee_per_contract.
    """
    f = fee(price, contracts=1, is_maker=is_maker)
    return price + f


def expected_value(
    model_p: float,
    market_price: float,
    contracts: int = 1,
    *,
    is_maker: bool = False,
) -> float:
    """
    Expected value in dollars for a YES-side entry of N contracts at market_price,
    given true win probability model_p.

      EV = model_p * (1 - market_price - fee_per_contract) * contracts
         - (1 - model_p) * (market_price + fee_per_contract) * contracts

    Settlement fee is zero. Fee is paid up-front, regardless of outcome.
    """
    f_per_contract = fee(market_price, contracts=1, is_maker=is_maker)
    win_payoff = (1.0 - market_price - f_per_contract) * contracts
    loss_payoff = -(market_price + f_per_contract) * contracts
    return model_p * win_payoff + (1.0 - model_p) * loss_payoff


def kelly_fraction(
    model_p: float,
    market_price: float,
    *,
    is_maker: bool = False,
) -> float:
    """
    Full-Kelly fraction of bankroll to stake at market_price given true model_p.

    Net cost per contract (paid up-front) = market_price + fee
    Net payout per contract on win        = 1 - market_price - fee
    Odds ratio b = net_payout / net_cost

    Full-Kelly: f* = (b * p - (1 - p)) / b

    Returns 0 when no edge (negative or zero).
    """
    f_per_contract = fee(market_price, contracts=1, is_maker=is_maker)
    net_cost = market_price + f_per_contract
    net_payout = 1.0 - market_price - f_per_contract
    if net_cost <= 0 or net_payout <= 0:
        return 0.0
    b = net_payout / net_cost
    raw = (b * model_p - (1.0 - model_p)) / b
    return max(0.0, raw)


def effective_fee_blended(
    price: float,
    fill_probability: float,
    contracts: int = 1,
) -> float:
    """
    Blended effective fee for a maker order with given fill probability,
    assuming the alternative (when unfilled) is to sweep with a taker order.

      eff_fee = p_fill * maker_fee + (1 - p_fill) * taker_fee

    Useful as a v2 optimization once real fill-rate data is available.
    """
    return (
        fill_probability * maker_fee(price, contracts)
        + (1.0 - fill_probability) * taker_fee(price, contracts)
    )
