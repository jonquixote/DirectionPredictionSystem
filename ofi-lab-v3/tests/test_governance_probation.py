"""Tests for Phase 4b: governance probation evaluator tick.

Uses an in-memory SQLite DB seeded with model_registry and model_tier_score.
Runs the probation tick directly and asserts tier transitions.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _past_iso(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _future_iso(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE model_registry (
            name TEXT PRIMARY KEY,
            symbol TEXT,
            training_horizon_seconds INTEGER,
            train_days INTEGER,
            tier TEXT DEFAULT 'watch',
            kelly_multiplier REAL DEFAULT 0.0,
            tier_assigned_at TEXT,
            tier_assigned_by TEXT,
            probation_start_at TEXT,
            probation_end_at TEXT,
            parent_model_name TEXT,
            paper_active INTEGER DEFAULT 0,
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
        CREATE TABLE model_tier_score (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            model_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            market_window_seconds INTEGER NOT NULL,
            regime_label TEXT,
            composite_score REAL NOT NULL,
            component_live_rwev REAL,
            component_paper_rwev REAL,
            component_walk_forward_ev REAL,
            component_calibration_drift REAL,
            component_decay_slope REAL,
            component_stability REAL,
            weights_json TEXT,
            sample_count INTEGER NOT NULL
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
    """)
    return conn


def _seed_mwt_for_model(conn, name, tier="watch", kelly=0.0, primary_window=300):
    """Seed model_window_tier rows for a model (3 windows, tier at primary only)."""
    for win in (300, 900, 1800):
        w_tier = tier if win == primary_window else "watch"
        w_kelly = kelly if win == primary_window else 0.0
        conn.execute(
            "INSERT OR REPLACE INTO model_window_tier "
            "(model_name, market_window_seconds, tier, kelly_multiplier) VALUES (?, ?, ?, ?)",
            (name, win, w_tier, w_kelly),
        )
    conn.commit()


def _seed_model(conn, name, tier="watch", parent=None, probation_end_offset_h=-1, paper_active=1):
    """Insert a model whose probation has ended (offset_h < 0 means in the past)."""
    prob_start = _past_iso(abs(probation_end_offset_h) + 2)
    prob_end = _past_iso(abs(probation_end_offset_h)) if probation_end_offset_h < 0 else _future_iso(probation_end_offset_h)
    conn.execute(
        "INSERT OR REPLACE INTO model_registry "
        "(name, symbol, training_horizon_seconds, train_days, tier, kelly_multiplier, "
        "probation_start_at, probation_end_at, parent_model_name, paper_active, "
        "primary_market_window_seconds) "
        "VALUES (?, 'BTCUSDT', 300, 330, ?, 0.0, ?, ?, ?, ?, 300)",
        (name, tier, prob_start, prob_end, parent, paper_active),
    )
    conn.execute(
        "INSERT OR IGNORE INTO cell_governance "
        "(cell_key, symbol, horizon_seconds, training_days, incumbent_model_name, challenger_model_name) "
        "VALUES ('BTCUSDT_300_330', 'BTCUSDT', 300, 330, ?, ?)",
        (parent, name),
    )
    # Phase 57: seed model_window_tier for the challenger
    kelly = 0.0  # challengers start at watch
    _seed_mwt_for_model(conn, name, tier=tier, kelly=kelly)
    conn.commit()


def _seed_scores(conn, model_name, scores: list[float], window_start_h=3, window_end_h=0):
    """Insert composite score rows within the probation window."""
    start = _past_iso(window_start_h)
    end = _past_iso(window_end_h)
    for i, score in enumerate(scores):
        ts_offset = window_start_h - i * (window_start_h / max(len(scores), 1))
        ts = _past_iso(ts_offset)
        conn.execute(
            "INSERT INTO model_tier_score "
            "(ts, model_name, symbol, market_window_seconds, regime_label, "
            "composite_score, sample_count) VALUES (?, ?, 'BTCUSDT', 300, '*', ?, 50)",
            (ts, model_name, score),
        )
    conn.commit()


def _import_tick():
    """Import the probation tick directly from main.py without FastAPI startup."""
    import sys, os
    # Add dashboard_api to path
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard_api"))
    from dashboard_api.main import _run_governance_probation_tick, _cutover_get_db
    return _run_governance_probation_tick, _cutover_get_db


class _NoCloseConn:
    """Wrap a connection to prevent the tick from closing it."""
    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass  # no-op

    def __getattr__(self, item):
        return getattr(self._conn, item)


def _run_tick_with_conn(conn):
    """Run the probation tick with a monkey-patched DB connection."""
    import dashboard_api.main as main_mod
    original = main_mod._cutover_get_db
    main_mod._cutover_get_db = lambda: _NoCloseConn(conn)
    # Also patch _trigger_reload_fleet to no-op
    try:
        import dashboard_api.routers.models_admin as ma_mod
        orig_trf = getattr(ma_mod, "_trigger_reload_fleet", None)
        ma_mod._trigger_reload_fleet = lambda: True
    except Exception:
        orig_trf = None
        ma_mod = None
    try:
        result = main_mod._run_governance_probation_tick()
    finally:
        main_mod._cutover_get_db = original
        if ma_mod is not None and orig_trf is not None:
            ma_mod._trigger_reload_fleet = orig_trf
    return result


class TestGoodChallengerPromotes:
    """Challenger beats incumbent by >5% → promote challenger, demote incumbent."""

    def test_challenger_promoted_incumbent_demoted(self):
        conn = _make_db()
        # Incumbent is gold — seed model_registry AND model_window_tier
        conn.execute(
            "INSERT INTO model_registry "
            "(name, symbol, training_horizon_seconds, train_days, tier, kelly_multiplier, "
            "paper_active, primary_market_window_seconds) "
            "VALUES ('incumbent', 'BTCUSDT', 300, 330, 'gold', 1.0, 1, 300)"
        )
        conn.commit()
        _seed_mwt_for_model(conn, "incumbent", tier="gold", kelly=1.0, primary_window=300)
        _seed_model(conn, "challenger", tier="watch", parent="incumbent")
        _seed_scores(conn, "challenger", [0.15, 0.20, 0.18])   # avg ~0.177
        _seed_scores(conn, "incumbent", [0.08, 0.09, 0.10])    # avg ~0.090
        # ratio = 0.177 / 0.090 ≈ 1.97 > 1.05 → promote

        n = _run_tick_with_conn(conn)

        # Phase 57: check model_window_tier at primary window (300)
        ch_mwt = conn.execute(
            "SELECT tier, kelly_multiplier FROM model_window_tier WHERE model_name='challenger' AND market_window_seconds=300"
        ).fetchone()
        inc_mwt = conn.execute(
            "SELECT tier, kelly_multiplier FROM model_window_tier WHERE model_name='incumbent' AND market_window_seconds=300"
        ).fetchone()

        assert n >= 1
        assert ch_mwt is not None
        assert ch_mwt["tier"] == "silver", f"expected silver, got {ch_mwt['tier']}"
        assert ch_mwt["kelly_multiplier"] == pytest.approx(0.3)
        assert inc_mwt is not None
        assert inc_mwt["tier"] == "silver", f"expected silver for incumbent, got {inc_mwt['tier']}"
        # governance_actions should have promote + demote
        actions = conn.execute("SELECT action, model_name FROM governance_actions").fetchall()
        action_map = {r["model_name"]: r["action"] for r in actions}
        assert action_map.get("challenger") == "promote"
        assert action_map.get("incumbent") == "demote"


class TestBadChallengerRetired:
    """Challenger underperforms incumbent by >5% → retire."""

    def test_challenger_retired(self):
        conn = _make_db()
        conn.execute(
            "INSERT INTO model_registry "
            "(name, symbol, training_horizon_seconds, train_days, tier, kelly_multiplier, "
            "paper_active, primary_market_window_seconds) "
            "VALUES ('incumbent', 'BTCUSDT', 300, 330, 'gold', 1.0, 1, 300)"
        )
        conn.commit()
        _seed_mwt_for_model(conn, "incumbent", tier="gold", kelly=1.0, primary_window=300)
        _seed_model(conn, "bad_challenger", tier="watch", parent="incumbent")
        _seed_scores(conn, "bad_challenger", [0.04, 0.03, 0.02])   # avg ~0.030
        _seed_scores(conn, "incumbent", [0.12, 0.14, 0.11])         # avg ~0.123
        # ratio = 0.030 / 0.123 ≈ 0.24 < 0.95 → retire

        _run_tick_with_conn(conn)

        # Phase 57: check model_window_tier
        mwt_row = conn.execute(
            "SELECT tier FROM model_window_tier WHERE model_name='bad_challenger' AND market_window_seconds=300"
        ).fetchone()
        assert mwt_row is not None
        assert mwt_row["tier"] == "retired"
        actions = conn.execute("SELECT action FROM governance_actions WHERE model_name='bad_challenger'").fetchall()
        assert any(a["action"] == "retire" for a in actions)


class TestWithinToleranceExtends:
    """Challenger within ±5% of incumbent → extend probation."""

    def test_probation_extended(self):
        conn = _make_db()
        conn.execute(
            "INSERT INTO model_registry "
            "(name, symbol, training_horizon_seconds, train_days, tier, kelly_multiplier, "
            "paper_active, primary_market_window_seconds) "
            "VALUES ('incumbent', 'BTCUSDT', 300, 330, 'gold', 1.0, 1, 300)"
        )
        conn.commit()
        _seed_mwt_for_model(conn, "incumbent", tier="gold", kelly=1.0, primary_window=300)
        _seed_model(conn, "near_challenger", tier="watch", parent="incumbent")
        _seed_scores(conn, "near_challenger", [0.101, 0.099, 0.100])  # avg ~0.100
        _seed_scores(conn, "incumbent", [0.10, 0.10, 0.10])            # avg 0.100
        # ratio ≈ 1.0 → within ±5%

        _run_tick_with_conn(conn)

        row = conn.execute("SELECT tier, probation_end_at FROM model_registry WHERE name='near_challenger'").fetchone()
        # Should still be watch (extended), not retired (model_registry.tier unchanged in extend path)
        assert row["tier"] == "watch"
        # probation_end_at should have been updated to future
        new_end = row["probation_end_at"]
        assert new_end > _now_iso(), f"probation_end_at not extended: {new_end}"

    def test_max_extensions_retires(self):
        """After 2 extensions, challenger is retired."""
        conn = _make_db()
        conn.execute(
            "INSERT INTO model_registry "
            "(name, symbol, training_horizon_seconds, train_days, tier, kelly_multiplier, "
            "paper_active, primary_market_window_seconds) "
            "VALUES ('incumbent', 'BTCUSDT', 300, 330, 'gold', 1.0, 1, 300)"
        )
        conn.commit()
        _seed_mwt_for_model(conn, "incumbent", tier="gold", kelly=1.0, primary_window=300)
        _seed_model(conn, "stubborn_challenger", tier="watch", parent="incumbent")
        _seed_scores(conn, "stubborn_challenger", [0.100])
        _seed_scores(conn, "incumbent", [0.100])
        # Pre-set extension_count = 2 in cell_governance
        conn.execute(
            "UPDATE cell_governance SET notes = ? WHERE cell_key = 'BTCUSDT_300_330'",
            (json.dumps({"extension_count": 2}),),
        )
        conn.commit()

        _run_tick_with_conn(conn)

        # Phase 57: check model_window_tier (primary window 300)
        mwt_row = conn.execute(
            "SELECT tier FROM model_window_tier WHERE model_name='stubborn_challenger' AND market_window_seconds=300"
        ).fetchone()
        assert mwt_row is not None, "model_window_tier row should exist"
        assert mwt_row["tier"] == "retired"


class TestNoIncumbentDirectPromote:
    """No incumbent → direct promote to silver if composite > 0."""

    def test_no_incumbent_positive_score_promotes_to_silver(self):
        conn = _make_db()
        _seed_model(conn, "orphan_challenger", tier="watch", parent=None)
        _seed_scores(conn, "orphan_challenger", [0.05, 0.08, 0.06])

        _run_tick_with_conn(conn)

        # Phase 57: check model_window_tier (primary window 300)
        mwt_row = conn.execute(
            "SELECT tier FROM model_window_tier WHERE model_name='orphan_challenger' AND market_window_seconds=300"
        ).fetchone()
        assert mwt_row is not None
        assert mwt_row["tier"] == "silver"

    def test_no_incumbent_negative_score_retires(self):
        conn = _make_db()
        _seed_model(conn, "bad_orphan", tier="watch", parent=None)
        _seed_scores(conn, "bad_orphan", [-0.05, -0.03])

        _run_tick_with_conn(conn)

        # Phase 57: check model_window_tier (primary window 300)
        mwt_row = conn.execute(
            "SELECT tier FROM model_window_tier WHERE model_name='bad_orphan' AND market_window_seconds=300"
        ).fetchone()
        assert mwt_row is not None
        assert mwt_row["tier"] == "retired"
