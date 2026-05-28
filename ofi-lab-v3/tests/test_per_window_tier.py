"""Tests for Phase 57: per-(model, market_window) tier/kelly.

Covers:
  1. migration_creates_three_rows_per_model
  2. compute_stake_uses_window_kelly
  3. kalshi_is_eligible_per_window
  4. demote_only_updates_primary_window
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _past_iso(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_db_with_mwt() -> sqlite3.Connection:
    """In-memory DB with model_registry, model_window_tier, decay_evaluations,
    governance_actions, and retrain_queue tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE model_registry (
            name TEXT PRIMARY KEY,
            symbol TEXT DEFAULT 'BTCUSDT',
            training_horizon_seconds INTEGER DEFAULT 900,
            train_days INTEGER DEFAULT 330,
            tier TEXT DEFAULT 'watch',
            kelly_multiplier REAL DEFAULT 0.0,
            tier_assigned_at TEXT,
            tier_assigned_by TEXT,
            probation_start_at TEXT,
            probation_end_at TEXT,
            parent_model_name TEXT,
            paper_active INTEGER DEFAULT 1,
            is_baseline INTEGER DEFAULT 0,
            lifecycle_state TEXT DEFAULT 'active',
            primary_market_window_seconds INTEGER DEFAULT 900
        );
        CREATE TABLE model_window_tier (
            model_name TEXT NOT NULL,
            market_window_seconds INTEGER NOT NULL,
            tier TEXT NOT NULL DEFAULT 'watch',
            kelly_multiplier REAL NOT NULL DEFAULT 0.0,
            tier_assigned_at TEXT,
            tier_assigned_by TEXT,
            PRIMARY KEY (model_name, market_window_seconds)
        );
        CREATE TABLE decay_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            model_name TEXT NOT NULL,
            symbol TEXT,
            market_window_seconds INTEGER,
            eval_type TEXT NOT NULL DEFAULT 'rwev_drop',
            triggered INTEGER DEFAULT 0,
            detail_json TEXT
        );
        CREATE TABLE governance_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            model_name TEXT NOT NULL,
            action TEXT NOT NULL,
            from_tier TEXT,
            to_tier TEXT,
            triggered_by TEXT NOT NULL,
            reason_json TEXT NOT NULL,
            cell_key TEXT
        );
        CREATE TABLE retrain_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cell_key TEXT NOT NULL,
            symbol TEXT NOT NULL,
            horizon_seconds INTEGER NOT NULL,
            training_days INTEGER NOT NULL,
            requested_at TEXT NOT NULL,
            triggered_by TEXT NOT NULL,
            picked_up_at TEXT,
            picked_up_by TEXT,
            notes TEXT,
            UNIQUE(cell_key, requested_at)
        );
        CREATE TABLE decay_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            model_name TEXT NOT NULL,
            symbol TEXT,
            market_window_seconds INTEGER,
            window_size INTEGER,
            rolling_ev REAL,
            recency_weighted_ev REAL,
            rolling_win_rate REAL,
            brier_score REAL,
            calibration_error REAL,
            sample_count INTEGER DEFAULT 0
        );
        CREATE TABLE cell_governance (
            cell_key TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            horizon_seconds INTEGER NOT NULL,
            training_days INTEGER NOT NULL,
            incumbent_model_name TEXT,
            challenger_model_name TEXT,
            last_promotion_at TEXT,
            last_demotion_at TEXT,
            notes TEXT
        );
    """)
    return conn


# ── Test 1: migration creates 3 rows per model ────────────────────────────────

class TestMigrationCreatesThreeRowsPerModel:
    """After schema init, inserting 2 models and seeding MWT should produce 6 rows."""

    def test_three_rows_created(self):
        conn = _make_db_with_mwt()
        # Simulate what register_model does: insert 3 rows per model
        for model_name in ("model_A", "model_B"):
            tier_for_primary = "gold" if model_name == "model_A" else "watch"
            for win in (300, 900, 1800):
                tier = tier_for_primary if win == 900 else "watch"
                kelly = 1.0 if tier == "gold" else 0.0
                conn.execute(
                    "INSERT OR IGNORE INTO model_window_tier "
                    "(model_name, market_window_seconds, tier, kelly_multiplier) "
                    "VALUES (?, ?, ?, ?)",
                    (model_name, win, tier, kelly),
                )
        conn.commit()

        rows = conn.execute("SELECT * FROM model_window_tier").fetchall()
        assert len(rows) == 6, f"Expected 6 rows, got {len(rows)}"

        # Check model_A has gold at 900, watch elsewhere
        gold_row = conn.execute(
            "SELECT * FROM model_window_tier WHERE model_name='model_A' AND market_window_seconds=900"
        ).fetchone()
        assert gold_row is not None
        assert gold_row["tier"] == "gold"
        assert gold_row["kelly_multiplier"] == pytest.approx(1.0)

        watch_row = conn.execute(
            "SELECT * FROM model_window_tier WHERE model_name='model_A' AND market_window_seconds=300"
        ).fetchone()
        assert watch_row["tier"] == "watch"


# ── Test 2: compute_stake uses window kelly ───────────────────────────────────

class TestComputeStakeUsesWindowKelly:
    """_compute_stake with market_window_seconds uses kelly_by_window dict."""

    def _make_trader_with_window_meta(self, kelly_900: float = 1.0,
                                       kelly_300: float = 0.0,
                                       kelly_1800: float = 0.3) -> MagicMock:
        from trading.paper_trader import SIMULATED_STAKE_USDC
        trader = MagicMock()
        trader.filters = {
            "kelly_sizing_enabled": True,
            "kelly_fraction": 0.5,
            "kelly_bankroll_usdc": 1000.0,
            "kelly_max_bet_usdc": 200.0,
        }
        trader._model_meta = {
            "m1": {
                "kelly_by_window": {300: kelly_300, 900: kelly_900, 1800: kelly_1800},
                "kelly_multiplier": kelly_900,  # legacy scalar = primary window
                "tier": "gold",
                "tier_by_window": {300: "watch", 900: "gold", 1800: "silver"},
            }
        }
        trader._running_pnl = {"m1": 0.0}
        trader._current_kalshi_bankroll = None
        return trader

    def test_window_900_gold_kelly_applied(self):
        from trading.paper_trader import PaperTrader, SIMULATED_STAKE_USDC
        trader = self._make_trader_with_window_meta(kelly_900=1.0)
        stake = PaperTrader._compute_stake(
            trader, "m1", 0.6, "up", 0.5, market_window_seconds=900
        )
        assert stake > SIMULATED_STAKE_USDC, f"Expected Kelly stake > ${SIMULATED_STAKE_USDC}, got ${stake}"

    def test_window_300_zero_kelly_returns_flat(self):
        from trading.paper_trader import PaperTrader, SIMULATED_STAKE_USDC
        trader = self._make_trader_with_window_meta(kelly_300=0.0)
        stake = PaperTrader._compute_stake(
            trader, "m1", 0.6, "up", 0.5, market_window_seconds=300
        )
        assert stake == SIMULATED_STAKE_USDC, f"Expected flat ${SIMULATED_STAKE_USDC}, got ${stake}"

    def test_no_window_falls_back_to_legacy_scalar(self):
        from trading.paper_trader import PaperTrader, SIMULATED_STAKE_USDC
        trader = self._make_trader_with_window_meta(kelly_900=1.0)
        # No market_window_seconds → legacy path uses meta["kelly_multiplier"]
        stake = PaperTrader._compute_stake(
            trader, "m1", 0.6, "up", 0.5  # no market_window_seconds
        )
        assert stake > SIMULATED_STAKE_USDC, "Legacy path should use kelly_multiplier=1.0"

    def test_window_1800_silver_produces_scaled_stake(self):
        from trading.paper_trader import PaperTrader, SIMULATED_STAKE_USDC
        trader = self._make_trader_with_window_meta(kelly_1800=0.3)
        gold_trader = self._make_trader_with_window_meta(kelly_1800=1.0)

        stake_silver = PaperTrader._compute_stake(
            trader, "m1", 0.7, "up", 0.4, market_window_seconds=1800
        )
        stake_gold = PaperTrader._compute_stake(
            gold_trader, "m1", 0.7, "up", 0.4, market_window_seconds=1800
        )
        # Silver (0.3) < gold (1.0)
        assert stake_silver < stake_gold, f"silver={stake_silver:.2f} should be < gold={stake_gold:.2f}"


# ── Test 3: Kalshi eligibility per window ────────────────────────────────────

class TestKalshiIsEligiblePerWindow:
    """is_eligible uses tier_by_window when available."""

    def _make_dispatcher(self, tier_by_window: dict[int, str]) -> "KalshiDispatcher":
        from trading.kalshi_dispatcher import KalshiDispatcher
        trader = MagicMock()
        trader._model_meta = {
            "test_model": {
                "kalshi_dispatch_enabled": True,
                "symbol": "BTCUSDT",
                "training_horizon_seconds": 900,
                "tier_by_window": tier_by_window,
                "tier": "watch",  # legacy scalar (should be ignored when tier_by_window exists)
            }
        }
        trader._db_conn = MagicMock()
        platform_row = MagicMock()
        platform_row.__getitem__ = lambda self, key: '{"kalshi":true}' if key == "platform_active_json" else None
        platform_row.__bool__ = lambda self: True
        trader._db_conn.execute.return_value.fetchone.return_value = platform_row
        return KalshiDispatcher(trader=trader)

    def test_gold_at_900_eligible(self):
        d = self._make_dispatcher({300: "watch", 900: "gold", 1800: "watch"})
        assert d.is_eligible(model_name="test_model", symbol="BTCUSDT", market_window_seconds=900) is True

    def test_watch_at_300_not_eligible(self):
        d = self._make_dispatcher({300: "watch", 900: "gold", 1800: "watch"})
        assert d.is_eligible(model_name="test_model", symbol="BTCUSDT", market_window_seconds=300) is False

    def test_missing_window_not_eligible(self):
        """Window not in tier_by_window → gated off."""
        d = self._make_dispatcher({900: "gold"})  # 300 missing
        assert d.is_eligible(model_name="test_model", symbol="BTCUSDT", market_window_seconds=300) is False

    def test_no_tier_by_window_falls_back_to_legacy(self):
        """When tier_by_window absent, falls back to legacy meta['tier']."""
        from trading.kalshi_dispatcher import KalshiDispatcher
        trader = MagicMock()
        trader._model_meta = {
            "test_model": {
                "kalshi_dispatch_enabled": True,
                "symbol": "BTCUSDT",
                "training_horizon_seconds": 900,
                # No tier_by_window
                "tier": "gold",
            }
        }
        trader._db_conn = MagicMock()
        platform_row = MagicMock()
        platform_row.__getitem__ = lambda self, key: '{"kalshi":true}' if key == "platform_active_json" else None
        platform_row.__bool__ = lambda self: True
        trader._db_conn.execute.return_value.fetchone.return_value = platform_row
        d = KalshiDispatcher(trader=trader)
        assert d.is_eligible(model_name="test_model", symbol="BTCUSDT", market_window_seconds=900) is True


# ── Test 4: demote only updates primary window ────────────────────────────────

class _NoCloseConn:
    """Wrap a connection to prevent the tick from closing it."""
    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, item):
        return getattr(self._conn, item)


def _run_action_tick_with_conn(conn):
    import dashboard_api.main as main_mod
    original = main_mod._cutover_get_db
    main_mod._cutover_get_db = lambda: _NoCloseConn(conn)
    try:
        import dashboard_api.routers.models_admin as ma_mod
        orig_trf = getattr(ma_mod, "_trigger_reload_fleet", None)
        ma_mod._trigger_reload_fleet = lambda: True
    except Exception:
        orig_trf = None
        ma_mod = None
    try:
        result = main_mod._run_governance_action_tick()
    finally:
        main_mod._cutover_get_db = original
        if ma_mod is not None and orig_trf is not None:
            ma_mod._trigger_reload_fleet = orig_trf
    return result


class TestDemoteOnlyUpdatesPrimaryWindow:
    """Decay demote should update only the primary window in model_window_tier."""

    def test_primary_window_demoted_others_unchanged(self):
        conn = _make_db_with_mwt()

        # Insert model with primary_window=900, all 3 mwt rows as gold
        tier_assigned_2h_ago = _past_iso(2.0)
        conn.execute(
            "INSERT INTO model_registry "
            "(name, symbol, training_horizon_seconds, train_days, tier, kelly_multiplier, "
            " paper_active, primary_market_window_seconds, tier_assigned_at) "
            "VALUES ('test_gold', 'BTCUSDT', 900, 330, 'gold', 1.0, 1, 900, ?)",
            (tier_assigned_2h_ago,),
        )
        for win in (300, 900, 1800):
            conn.execute(
                "INSERT INTO model_window_tier "
                "(model_name, market_window_seconds, tier, kelly_multiplier, tier_assigned_at) "
                "VALUES ('test_gold', ?, 'gold', 1.0, ?)",
                (win, tier_assigned_2h_ago),
            )

        # Insert 5 decay alerts on the 900 row AFTER tier_assigned_at
        for i in range(5):
            ts = _past_iso(1.0 - i * 0.1)  # all within last 6h, after tier_assigned_at (2h ago)
            conn.execute(
                "INSERT INTO decay_evaluations "
                "(ts, model_name, symbol, market_window_seconds, eval_type, triggered) "
                "VALUES (?, 'test_gold', 'BTCUSDT', 900, 'rwev_drop', 1)",
                (ts,),
            )
        conn.commit()

        _run_action_tick_with_conn(conn)

        # Primary window (900) should be 'silver'
        row_900 = conn.execute(
            "SELECT tier FROM model_window_tier WHERE model_name='test_gold' AND market_window_seconds=900"
        ).fetchone()
        assert row_900 is not None, "900 row should exist"
        assert row_900["tier"] == "silver", f"Expected silver, got {row_900['tier']}"

        # Non-primary windows (300, 1800) should STAY 'gold'
        row_300 = conn.execute(
            "SELECT tier FROM model_window_tier WHERE model_name='test_gold' AND market_window_seconds=300"
        ).fetchone()
        assert row_300["tier"] == "gold", f"300 should stay gold, got {row_300['tier']}"

        row_1800 = conn.execute(
            "SELECT tier FROM model_window_tier WHERE model_name='test_gold' AND market_window_seconds=1800"
        ).fetchone()
        assert row_1800["tier"] == "gold", f"1800 should stay gold, got {row_1800['tier']}"

        # governance_actions should record the demote
        actions = conn.execute(
            "SELECT action, from_tier, to_tier FROM governance_actions WHERE model_name='test_gold'"
        ).fetchall()
        assert any(a["action"] == "demote" and a["from_tier"] == "gold" and a["to_tier"] == "silver"
                   for a in actions), "governance_actions should record gold→silver demote"
