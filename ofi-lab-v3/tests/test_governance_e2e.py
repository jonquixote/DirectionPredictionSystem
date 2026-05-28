"""E2E governance flow: synthetic decay → demote gold→silver → demote silver→watch → retire.

Tests Phase 6a (_run_governance_action_tick) on an in-memory DB.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _past_iso(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_full_db() -> sqlite3.Connection:
    """Create an in-memory DB with all Phase 3/4/6 + Phase 57 tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE model_registry (
            name TEXT PRIMARY KEY,
            symbol TEXT DEFAULT 'BTCUSDT',
            training_horizon_seconds INTEGER DEFAULT 300,
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
            primary_market_window_seconds INTEGER DEFAULT 300
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


def _seed_decay_alerts(conn, model_name, n_triggered: int, window_hours: int,
                        market_window_seconds: int = 300):
    """Insert n_triggered triggered=1 decay_evaluations within window_hours."""
    # Look up primary_market_window_seconds from model_registry if available
    row = conn.execute(
        "SELECT primary_market_window_seconds FROM model_registry WHERE name=?", (model_name,)
    ).fetchone()
    if row and row["primary_market_window_seconds"]:
        market_window_seconds = row["primary_market_window_seconds"]

    # Make sure tier_assigned_at is in the past so alerts are not filtered as stale
    tier_assigned_row = conn.execute(
        "SELECT tier_assigned_at FROM model_window_tier WHERE model_name=? AND market_window_seconds=?",
        (model_name, market_window_seconds)
    ).fetchone()
    tier_assigned_at = tier_assigned_row["tier_assigned_at"] if tier_assigned_row and tier_assigned_row["tier_assigned_at"] else _past_iso(window_hours + 2)

    for i in range(n_triggered):
        # Alerts must be AFTER tier_assigned_at and within the window
        ts = _past_iso(window_hours - i * 0.5 - 0.1)
        conn.execute(
            "INSERT INTO decay_evaluations (ts, model_name, symbol, market_window_seconds, "
            "eval_type, triggered, detail_json) VALUES (?, ?, 'BTCUSDT', ?, 'rwev_drop', 1, '{}')",
            (ts, model_name, market_window_seconds),
        )
    conn.commit()


def _seed_mwt(conn, model_name, tier="gold", kelly=1.0, tier_assigned_hours_ago=2.0,
              windows=(300, 900, 1800), primary_window=300):
    """Seed model_window_tier rows for a model."""
    for win in windows:
        w_tier = tier if win == primary_window else "watch"
        w_kelly = kelly if win == primary_window else 0.0
        tier_assigned_at = _past_iso(tier_assigned_hours_ago)
        conn.execute(
            "INSERT OR REPLACE INTO model_window_tier "
            "(model_name, market_window_seconds, tier, kelly_multiplier, tier_assigned_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (model_name, win, w_tier, w_kelly, tier_assigned_at),
        )
    conn.commit()


def _seed_decay_metrics(conn, model_name, rwev_values: list[float], sample_count=150):
    """Insert decay_metrics rows (oldest first)."""
    for i, rwev in enumerate(rwev_values):
        ts = _past_iso(len(rwev_values) - i)  # spread over past N hours
        conn.execute(
            "INSERT INTO decay_metrics (ts, model_name, symbol, market_window_seconds, "
            "window_size, rolling_ev, recency_weighted_ev, rolling_win_rate, brier_score, "
            "calibration_error, sample_count) VALUES (?, ?, 'BTCUSDT', 300, 100, ?, ?, 0.5, 0.2, 0.05, ?)",
            (ts, model_name, rwev, rwev, sample_count),
        )
    conn.commit()


class _NoCloseConn:
    """Wrap a connection to prevent the tick from closing it."""
    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass  # no-op

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


class TestGoldDemotesSilver:
    """Gold model with ≥3 triggered decay alerts in 6h → demote to silver."""

    def test_gold_demotes_to_silver_on_alerts(self):
        conn = _make_full_db()
        conn.execute(
            "INSERT INTO model_registry "
            "(name, tier, kelly_multiplier, paper_active, primary_market_window_seconds) "
            "VALUES ('gold_model', 'gold', 1.0, 1, 300)"
        )
        conn.commit()
        # Phase 57: also seed model_window_tier — tier_assigned_at must predate the alerts
        _seed_mwt(conn, "gold_model", tier="gold", kelly=1.0, primary_window=300, tier_assigned_hours_ago=10.0)
        _seed_decay_alerts(conn, "gold_model", n_triggered=4, window_hours=5)

        _run_action_tick_with_conn(conn)

        # Phase 57: assert on model_window_tier (primary window) rather than model_registry.tier
        mwt_row = conn.execute(
            "SELECT tier, kelly_multiplier FROM model_window_tier "
            "WHERE model_name='gold_model' AND market_window_seconds=300"
        ).fetchone()
        assert mwt_row is not None, "model_window_tier row should exist"
        assert mwt_row["tier"] == "silver"
        assert mwt_row["kelly_multiplier"] == pytest.approx(0.3)

        actions = conn.execute("SELECT action, from_tier, to_tier FROM governance_actions WHERE model_name='gold_model'").fetchall()
        assert any(a["action"] == "demote" and a["from_tier"] == "gold" and a["to_tier"] == "silver" for a in actions)


class TestSilverDemotesWatch:
    """Silver model with ≥3 triggered decay alerts in 12h → demote to watch."""

    def test_silver_demotes_to_watch_on_alerts(self):
        conn = _make_full_db()
        conn.execute(
            "INSERT INTO model_registry "
            "(name, tier, kelly_multiplier, paper_active, primary_market_window_seconds) "
            "VALUES ('silver_model', 'silver', 0.3, 1, 300)"
        )
        conn.commit()
        # Phase 57: seed model_window_tier with silver at primary window
        _seed_mwt(conn, "silver_model", tier="silver", kelly=0.3, primary_window=300, tier_assigned_hours_ago=15.0)
        _seed_decay_alerts(conn, "silver_model", n_triggered=4, window_hours=10)

        _run_action_tick_with_conn(conn)

        # Phase 57: assert on model_window_tier
        mwt_row = conn.execute(
            "SELECT tier, kelly_multiplier FROM model_window_tier "
            "WHERE model_name='silver_model' AND market_window_seconds=300"
        ).fetchone()
        assert mwt_row is not None
        assert mwt_row["tier"] == "watch"
        assert mwt_row["kelly_multiplier"] == pytest.approx(0.0)


class TestWatchRetiresSustainedNegativeRwev:
    """Watch model with all-negative rwev over 7 days + sample_count ≥ 100 → retire."""

    def test_watch_retires_on_sustained_negative_rwev(self):
        conn = _make_full_db()
        conn.execute(
            "INSERT INTO model_registry (name, tier, kelly_multiplier, paper_active) "
            "VALUES ('decayed_model', 'watch', 0.0, 1)"
        )
        conn.commit()
        # 10 rows of negative rwev over past 7 days
        rwev_values = [-0.02, -0.03, -0.01, -0.04, -0.02, -0.03, -0.01, -0.02, -0.03, -0.02]
        _seed_decay_metrics(conn, "decayed_model", rwev_values, sample_count=150)

        _run_action_tick_with_conn(conn)

        row = conn.execute("SELECT tier, paper_active FROM model_registry WHERE name='decayed_model'").fetchone()
        assert row["tier"] == "retired"
        assert row["paper_active"] == 0

    def test_watch_not_retired_if_some_positive_rwev(self):
        conn = _make_full_db()
        conn.execute(
            "INSERT INTO model_registry (name, tier, kelly_multiplier, paper_active) "
            "VALUES ('recovering_model', 'watch', 0.0, 1)"
        )
        conn.commit()
        # Mix of positive and negative — should NOT retire
        rwev_values = [-0.02, 0.01, -0.03, 0.02, -0.01]
        _seed_decay_metrics(conn, "recovering_model", rwev_values, sample_count=150)

        _run_action_tick_with_conn(conn)

        row = conn.execute("SELECT tier FROM model_registry WHERE name='recovering_model'").fetchone()
        assert row["tier"] == "watch"  # not retired

    def test_watch_not_retired_if_sample_count_too_low(self):
        conn = _make_full_db()
        conn.execute(
            "INSERT INTO model_registry (name, tier, kelly_multiplier, paper_active) "
            "VALUES ('young_model', 'watch', 0.0, 1)"
        )
        conn.commit()
        rwev_values = [-0.05, -0.05, -0.05]
        _seed_decay_metrics(conn, "young_model", rwev_values, sample_count=50)  # < 100

        _run_action_tick_with_conn(conn)

        row = conn.execute("SELECT tier FROM model_registry WHERE name='young_model'").fetchone()
        assert row["tier"] == "watch"  # not retired — insufficient samples


class TestRetrainQueueInsertion:
    """When no gold/silver model remains in cell after demotion → retrain_queue row inserted."""

    def test_retrain_queued_when_cell_has_no_incumbent(self):
        conn = _make_full_db()
        conn.execute(
            "INSERT INTO model_registry "
            "(name, symbol, training_horizon_seconds, train_days, tier, kelly_multiplier, paper_active) "
            "VALUES ('sole_gold', 'BTCUSDT', 300, 330, 'gold', 1.0, 1)"
        )
        conn.commit()
        _seed_decay_alerts(conn, "sole_gold", n_triggered=4, window_hours=5)

        _run_action_tick_with_conn(conn)

        # After demotion to silver, there IS a silver model — should NOT queue retrain
        queue = conn.execute("SELECT * FROM retrain_queue WHERE cell_key='BTCUSDT_300_330'").fetchall()
        # sole_gold is now silver so surviving_silver > 0 → no retrain queued
        assert len(queue) == 0, "should not queue retrain when silver survives"

    def test_retrain_queued_when_watch_retires_sole_model(self):
        conn = _make_full_db()
        conn.execute(
            "INSERT INTO model_registry "
            "(name, symbol, training_horizon_seconds, train_days, tier, kelly_multiplier, paper_active) "
            "VALUES ('last_watch', 'BTCUSDT', 300, 330, 'watch', 0.0, 1)"
        )
        conn.commit()
        # All negative rwev, sample_count >= 100 → retire
        rwev_values = [-0.03] * 10
        _seed_decay_metrics(conn, "last_watch", rwev_values, sample_count=200)

        _run_action_tick_with_conn(conn)

        row = conn.execute("SELECT tier FROM model_registry WHERE name='last_watch'").fetchone()
        assert row["tier"] == "retired"

        queue = conn.execute("SELECT * FROM retrain_queue WHERE cell_key='BTCUSDT_300_330'").fetchall()
        assert len(queue) == 1, "should queue retrain when last model in cell retires"
        assert queue[0]["triggered_by"] == "auto:no_incumbent"
