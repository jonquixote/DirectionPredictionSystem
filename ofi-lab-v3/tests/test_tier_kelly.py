"""Tests for Phase 5a: _compute_stake honors kelly_multiplier from model_meta."""
import pytest
from unittest.mock import MagicMock, patch

SIMULATED_STAKE_USDC = 10.0


def _make_trader_stub(kelly_multiplier: float, tier: str = "gold") -> MagicMock:
    """Create a minimal PaperTrader-like object with _compute_stake accessible."""
    from trading.paper_trader import PaperTrader, SIMULATED_STAKE_USDC as _sim_stake
    # We'll call the method directly via unbound call, mocking self
    trader = MagicMock()
    trader.filters = {
        "kelly_sizing_enabled": True,
        "kelly_fraction": 0.5,
        "kelly_bankroll_usdc": 1000.0,
        "kelly_max_bet_usdc": 200.0,
    }
    # Phase 57: populate kelly_by_window in addition to legacy scalar
    # Primary window assumed to be 900 for test stubs
    trader._model_meta = {
        "test_model": {
            "kelly_multiplier": kelly_multiplier,  # legacy scalar
            "tier": tier,
            # Phase 57: per-window dicts (same value at primary window for back-compat)
            "kelly_by_window": {300: kelly_multiplier, 900: kelly_multiplier, 1800: kelly_multiplier},
            "tier_by_window": {300: tier, 900: tier, 1800: tier},
        }
    }
    trader._running_pnl = {"test_model": 0.0}
    trader._current_kalshi_bankroll = None
    return trader


def _call_compute_stake(trader, model_name, pred_proba, pred_direction, p_market):
    """Call PaperTrader._compute_stake unbound (no market_window_seconds = legacy path)."""
    from trading.paper_trader import PaperTrader
    return PaperTrader._compute_stake(trader, model_name, pred_proba, pred_direction, p_market)


class TestKellyMultiplierGold:
    """Gold tier (multiplier=1.0) should scale normally."""

    def test_gold_tier_nonzero_stake(self):
        trader = _make_trader_stub(kelly_multiplier=1.0, tier="gold")
        stake = _call_compute_stake(trader, "test_model", 0.65, "up", 0.50)
        assert stake > SIMULATED_STAKE_USDC, f"expected stake > $10, got ${stake}"

    def test_gold_tier_positive_edge_gets_kelly_stake(self):
        trader = _make_trader_stub(kelly_multiplier=1.0, tier="gold")
        # p_market=0.4, pred=0.7 → strong edge → stake should be meaningful
        stake = _call_compute_stake(trader, "test_model", 0.70, "up", 0.40)
        assert stake > 1.0


class TestKellyMultiplierSilver:
    """Silver tier (multiplier=0.3) should produce a smaller stake than gold."""

    def test_silver_stake_less_than_gold(self):
        silver_trader = _make_trader_stub(kelly_multiplier=0.3, tier="silver")
        gold_trader = _make_trader_stub(kelly_multiplier=1.0, tier="gold")
        silver_stake = _call_compute_stake(silver_trader, "test_model", 0.65, "up", 0.50)
        gold_stake = _call_compute_stake(gold_trader, "test_model", 0.65, "up", 0.50)
        assert silver_stake < gold_stake, f"silver={silver_stake} should be < gold={gold_stake}"

    def test_silver_stake_positive(self):
        trader = _make_trader_stub(kelly_multiplier=0.3, tier="silver")
        stake = _call_compute_stake(trader, "test_model", 0.65, "up", 0.50)
        # Silver should still produce a positive scaled stake (though may be floored at $1)
        assert stake >= 1.0


class TestKellyMultiplierWatch:
    """Watch tier (multiplier=0.0) should return SIMULATED_STAKE_USDC flat."""

    def test_watch_returns_flat_stake(self):
        trader = _make_trader_stub(kelly_multiplier=0.0, tier="watch")
        stake = _call_compute_stake(trader, "test_model", 0.65, "up", 0.50)
        assert stake == SIMULATED_STAKE_USDC, f"expected flat ${SIMULATED_STAKE_USDC}, got ${stake}"

    def test_watch_returns_flat_even_with_high_edge(self):
        trader = _make_trader_stub(kelly_multiplier=0.0, tier="watch")
        stake = _call_compute_stake(trader, "test_model", 0.99, "up", 0.05)
        assert stake == SIMULATED_STAKE_USDC


class TestKellyMultiplierRetired:
    """Retired tier (multiplier=0.0) same as watch — returns flat stake."""

    def test_retired_returns_flat_stake(self):
        trader = _make_trader_stub(kelly_multiplier=0.0, tier="retired")
        stake = _call_compute_stake(trader, "test_model", 0.65, "up", 0.50)
        assert stake == SIMULATED_STAKE_USDC


class TestKellyNoMetaEntry:
    """Missing model_meta entry should default to multiplier=0.0 → flat stake."""

    def test_missing_meta_returns_flat_stake(self):
        trader = _make_trader_stub(kelly_multiplier=1.0)
        trader._model_meta = {}  # model not in meta
        stake = _call_compute_stake(trader, "test_model", 0.65, "up", 0.50)
        assert stake == SIMULATED_STAKE_USDC
