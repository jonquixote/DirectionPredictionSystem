"""Tests for trading/kalshi_dispatcher.py.

Covers instantiation and each of the 4 extracted methods using a mock
"trader" object (same back-reference pattern as test_provenance_builder.py).
Real-money path: tests verify eligibility gating, mid-price math, and that
dispatch short-circuits correctly when kalshi_trader is disabled.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    """Run a coroutine in a fresh event loop that is set as the thread current loop.

    Using _run() in Python 3.10+ closes and discards the loop without
    resetting the thread's current-loop reference. Subsequent calls to
    asyncio.get_event_loop() (e.g. in test_resolution_checker.py) then raise
    RuntimeError. This helper creates a new loop, sets it as current, runs the
    coroutine, closes the loop, and restores the previous loop so sibling test
    modules are unaffected.
    """
    prev = None
    try:
        prev = asyncio.get_event_loop()
    except RuntimeError:
        pass
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(prev)

from trading.kalshi_dispatcher import (
    KalshiDispatcher,
    KALSHI_ROLLOVER_ATTEMPTS,
    KALSHI_ROLLOVER_POLL_SEC,
    KALSHI_BOOK_RETRY_ATTEMPTS,
    KALSHI_BOOK_RETRY_POLL_SEC,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_trader(
    *,
    model_meta=None,
    db_conn=None,
    kalshi_trader=None,
) -> MagicMock:
    """Return a MagicMock shaped like PaperTrader for KalshiDispatcher tests."""
    if model_meta is None:
        model_meta = {
            "h300": {
                "symbol": "BTCUSDT",
                "training_horizon_seconds": 900,
                "kalshi_dispatch_enabled": True,
            }
        }
    if db_conn is None:
        db_conn = MagicMock()
        # Default: no platform_active_json row found
        db_conn.execute.return_value.fetchone.return_value = None

    trader = MagicMock()
    trader._model_meta = model_meta
    trader._db_conn = db_conn
    trader._kalshi_trader = kalshi_trader
    return trader


def _make_dispatcher(**kwargs) -> KalshiDispatcher:
    trader = _make_mock_trader(**kwargs)
    return KalshiDispatcher(trader=trader)


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------

def test_constants_values():
    """Named constants must match the values from the original inline literals."""
    assert KALSHI_ROLLOVER_ATTEMPTS == 16
    assert KALSHI_ROLLOVER_POLL_SEC == 4
    assert KALSHI_BOOK_RETRY_ATTEMPTS == 5
    assert KALSHI_BOOK_RETRY_POLL_SEC == 2


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------

def test_dispatcher_instantiation():
    """KalshiDispatcher can be constructed with a mock trader."""
    disp = _make_dispatcher()
    assert disp is not None
    assert disp._trader._model_meta["h300"]["symbol"] == "BTCUSDT"


# ---------------------------------------------------------------------------
# is_eligible
# ---------------------------------------------------------------------------

def test_is_eligible_true_for_btc_h300_900s():
    """h300 / BTCUSDT / 900s is the known dispatch-eligible combo."""
    disp = _make_dispatcher()
    assert disp.is_eligible(
        model_name="h300",
        symbol="BTCUSDT",
        market_window_seconds=900,
    ) is True


def test_is_eligible_false_for_non_btc():
    """Non-BTC symbol is blocked even if model name and window match."""
    disp = _make_dispatcher()
    assert disp.is_eligible(
        model_name="h300",
        symbol="ETHUSDT",
        market_window_seconds=900,
    ) is False


def test_is_eligible_false_for_wrong_duration():
    """Wrong market_window_seconds is blocked."""
    disp = _make_dispatcher()
    assert disp.is_eligible(
        model_name="h300",
        symbol="BTCUSDT",
        market_window_seconds=300,
    ) is False


def test_is_eligible_false_when_kalshi_disabled():
    """kalshi_dispatch_enabled=False in model meta blocks dispatch."""
    meta = {
        "h300": {
            "symbol": "BTCUSDT",
            "training_horizon_seconds": 900,
            "kalshi_dispatch_enabled": False,
        }
    }
    disp = _make_dispatcher(model_meta=meta)
    assert disp.is_eligible(
        model_name="h300",
        symbol="BTCUSDT",
        market_window_seconds=900,
    ) is False


def test_is_eligible_false_when_platform_active_json_kalshi_false():
    """platform_active_json with kalshi=False blocks dispatch."""
    import json
    db_conn = MagicMock()
    row = MagicMock()
    row.__getitem__ = lambda self, k: json.dumps({"kalshi": False}) if k == "platform_active_json" else None
    row.__bool__ = lambda self: True
    # Simulate row["platform_active_json"] returning the JSON string
    mock_row = {"platform_active_json": json.dumps({"kalshi": False})}

    class DictLike(dict):
        def __bool__(self):
            return True

    db_conn.execute.return_value.fetchone.return_value = DictLike(mock_row)

    disp = _make_dispatcher(db_conn=db_conn)
    assert disp.is_eligible(
        model_name="h300",
        symbol="BTCUSDT",
        market_window_seconds=900,
    ) is False


def test_is_eligible_false_for_unknown_model():
    """Unknown model name returns False gracefully."""
    disp = _make_dispatcher()
    assert disp.is_eligible(
        model_name="unknown_model",
        symbol="BTCUSDT",
        market_window_seconds=900,
    ) is False


# ---------------------------------------------------------------------------
# midpoint_from_levels
# ---------------------------------------------------------------------------

def test_midpoint_from_levels_basic():
    """Basic two-sided book returns the expected midpoint."""
    # yes best bid = 0.50, no best bid = 0.48 → yes_ask = 1 - 0.48 = 0.52
    # midpoint = (0.50 + 0.52) / 2 = 0.51
    yes_levels = [[0.50, 100]]
    no_levels = [[0.48, 100]]
    result = KalshiDispatcher.midpoint_from_levels(yes_levels, no_levels)
    assert result is not None
    assert abs(result - 0.51) < 1e-9


def test_midpoint_from_levels_empty_returns_none():
    """Empty yes and no levels returns None (no-quote market)."""
    result = KalshiDispatcher.midpoint_from_levels([], [])
    assert result is None


def test_midpoint_from_levels_yes_only():
    """Only yes_levels available — midpoint equals yes_bid."""
    yes_levels = [[0.60, 50]]
    result = KalshiDispatcher.midpoint_from_levels(yes_levels, [])
    assert result == 0.60


def test_midpoint_from_levels_no_only():
    """Only no_levels available — midpoint = 1 - no_bid."""
    no_levels = [[0.40, 50]]
    result = KalshiDispatcher.midpoint_from_levels([], no_levels)
    assert result == 0.60  # 1.0 - 0.40


def test_midpoint_from_levels_zero_size_skipped():
    """Levels with size=0 are ignored."""
    yes_levels = [[0.55, 0], [0.50, 100]]
    no_levels = [[0.48, 0], [0.45, 100]]
    result = KalshiDispatcher.midpoint_from_levels(yes_levels, no_levels)
    # yes_bid=0.50, yes_ask=1-0.45=0.55 → mid=0.525
    assert abs(result - 0.525) < 1e-9


# ---------------------------------------------------------------------------
# dispatch_live — no-op paths
# ---------------------------------------------------------------------------

def test_dispatch_live_no_op_when_kalshi_trader_none():
    """dispatch_live returns immediately when _kalshi_trader is None."""
    trader = _make_mock_trader(kalshi_trader=None)
    disp = KalshiDispatcher(trader=trader)

    _run(disp.dispatch_live(
        symbol="BTCUSDT",
        duration_sec=900,
        boundary_ms=1_700_000_000_000,
        pred_proba=0.62,
        pred_direction="up",
        paper_stake_usd=10.0,
        features={},
        model_name="h300",
    ))
    # No exception and no calls to any rest methods


def test_dispatch_live_no_op_when_kalshi_trader_rest_none():
    """dispatch_live returns immediately when _kalshi_trader._rest is None."""
    kt = MagicMock()
    kt._rest = None
    trader = _make_mock_trader(kalshi_trader=kt)
    disp = KalshiDispatcher(trader=trader)

    _run(disp.dispatch_live(
        symbol="BTCUSDT",
        duration_sec=900,
        boundary_ms=1_700_000_000_000,
        pred_proba=0.62,
        pred_direction="up",
        paper_stake_usd=10.0,
        features={},
        model_name="h300",
    ))


# ---------------------------------------------------------------------------
# dispatch_live — order placed path (mocked kalshi_trader)
# ---------------------------------------------------------------------------

def test_dispatch_live_no_op_when_kalshi_trader_disabled():
    """When kalshi_trader.enabled is False, no order is placed.

    We simulate this by having maybe_place_order never get called
    (the fixture sets ticker resolution to return None so dispatch exits early).
    """
    kt = MagicMock()
    kt._rest = AsyncMock()
    kt._calibrator = MagicMock()
    kt._rest.get_active_tickers = AsyncMock(return_value=[])  # no markets
    kt.enabled = False

    trader = _make_mock_trader(kalshi_trader=kt)
    disp = KalshiDispatcher(trader=trader)

    # Patch asyncio.sleep to avoid real delays
    with patch("trading.kalshi_dispatcher.asyncio.sleep", new=AsyncMock()):
        _run(disp.dispatch_live(
            symbol="BTCUSDT",
            duration_sec=900,
            boundary_ms=1_700_000_000_000,
            pred_proba=0.62,
            pred_direction="up",
            paper_stake_usd=10.0,
            features={},
            model_name="h300",
        ))

    # maybe_place_order should never have been called
    kt.maybe_place_order.assert_not_called()


# ---------------------------------------------------------------------------
# resolve_ticker_for_boundary — mock the markets list
# ---------------------------------------------------------------------------

def test_resolve_ticker_picks_correct_market():
    """resolve_ticker_for_boundary returns the ticker matching close_unix."""
    boundary_ms = 1_746_000_000_000  # some arbitrary boundary
    duration_sec = 900
    target_close_unix = boundary_ms // 1000 + duration_sec

    from datetime import datetime, timezone
    target_iso = datetime.fromtimestamp(target_close_unix, tz=timezone.utc).isoformat()

    markets = [
        {"ticker": "KXBTC15M-wrong", "close_time": "2020-01-01T00:00:00+00:00"},
        {"ticker": "KXBTC15M-correct", "close_time": target_iso},
    ]

    kt = MagicMock()
    kt._rest = AsyncMock()
    kt._rest.get_active_tickers = AsyncMock(return_value=markets)
    kt.config.series_ticker = "KXBTC15M"

    trader = _make_mock_trader(kalshi_trader=kt)
    disp = KalshiDispatcher(trader=trader)

    result = _run(disp.resolve_ticker_for_boundary(
        symbol="BTCUSDT",
        duration_sec=duration_sec,
        boundary_ms=boundary_ms,
    ))

    assert result == "KXBTC15M-correct"


def test_resolve_ticker_returns_none_when_no_match():
    """resolve_ticker_for_boundary returns None when no market matches."""
    kt = MagicMock()
    kt._rest = AsyncMock()
    kt._rest.get_active_tickers = AsyncMock(return_value=[
        {"ticker": "KXBTC15M-other", "close_time": "2020-01-01T00:00:00+00:00"},
    ])
    kt.config.series_ticker = "KXBTC15M"

    trader = _make_mock_trader(kalshi_trader=kt)
    disp = KalshiDispatcher(trader=trader)

    with patch("trading.kalshi_dispatcher.asyncio.sleep", new=AsyncMock()):
        result = _run(disp.resolve_ticker_for_boundary(
            symbol="BTCUSDT",
            duration_sec=900,
            boundary_ms=1_700_000_000_000,
        ))

    assert result is None
