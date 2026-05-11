import pytest

from execution.ev import (
    compute_ev_polymarket, compute_ev_kalshi, EVResult,
    POLYMARKET_FEE_COEF,
)


def test_ev_polymarket_zero_at_p_equals_market():
    # If calibrated_p == p_market, no edge; EV ≈ 0 minus fees.
    r = compute_ev_polymarket(
        calibrated_p=0.50, p_market=0.50, stake=10.0,
    )
    assert r.fee == POLYMARKET_FEE_COEF * 0.50 * 0.50 * 10.0
    assert r.ev < 0  # fees > 0 → EV < 0 at break-even probability


def test_ev_polymarket_positive_when_edge_exceeds_fees():
    # At calibrated 0.55 vs market 0.50, payout ratio improves.
    r = compute_ev_polymarket(
        calibrated_p=0.55, p_market=0.50, stake=10.0,
    )
    assert r.ev > 0
    assert r.payout_if_win > 0
    assert r.cost_if_lose > 0


def test_ev_polymarket_negative_under_high_fee():
    # If we artificially inflate the fee the EV must flip sign.
    r = compute_ev_polymarket(
        calibrated_p=0.51, p_market=0.50, stake=10.0,
        fee_coef=0.5,  # absurd fee to force negative EV
    )
    assert r.ev < 0


def test_ev_kalshi_maker_lower_fee_than_taker():
    r_maker = compute_ev_kalshi(
        calibrated_p=0.55, market_yes_price=0.50, stake=10.0,
        side="yes", order_type="maker",
    )
    r_taker = compute_ev_kalshi(
        calibrated_p=0.55, market_yes_price=0.50, stake=10.0,
        side="yes", order_type="taker",
    )
    assert r_maker.fee < r_taker.fee


def test_ev_kalshi_kelly_fraction_present():
    r = compute_ev_kalshi(
        calibrated_p=0.55, market_yes_price=0.50, stake=10.0,
        side="yes", order_type="maker",
    )
    # Kelly fraction = (b*p - q) / b where b = (1-price)/price
    assert 0.0 <= r.kelly_fraction <= 1.0
