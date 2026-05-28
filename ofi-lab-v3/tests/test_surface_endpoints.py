"""Tests for T2-be-surfaces endpoints.

Covers:
  - GET /api/governance/actions
  - GET /api/training/queue
  - GET /api/system/capacity  (no-data / unavailable path)
  - GET /api/models_registry/summary
  - GET /api/models/list      (tier_by_window extension)

Strategy:
- Seed an in-memory SQLite DB.
- Monkeypatch _get_db in each router/service module.
- Call router functions directly for unit-level checks.
- Use TestClient for shape validation on key endpoints.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    """Create the tables needed by surface endpoints."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS governance_actions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              TEXT NOT NULL,
            model_name      TEXT NOT NULL,
            action          TEXT NOT NULL,
            from_tier       TEXT,
            to_tier         TEXT,
            triggered_by    TEXT NOT NULL,
            reason_json     TEXT NOT NULL,
            cell_key        TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS retrain_queue (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cell_key        TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            horizon_seconds INTEGER NOT NULL,
            training_days   INTEGER NOT NULL,
            requested_at    TEXT NOT NULL,
            triggered_by    TEXT NOT NULL,
            picked_up_at    TEXT,
            picked_up_by    TEXT,
            notes           TEXT,
            UNIQUE(cell_key, requested_at)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_registry (
            name                    TEXT PRIMARY KEY,
            is_baseline             INTEGER NOT NULL DEFAULT 0,
            paper_active            INTEGER NOT NULL DEFAULT 1,
            live_eligible           INTEGER NOT NULL DEFAULT 0,
            lifecycle_state         TEXT NOT NULL DEFAULT 'prediction_only',
            symbol                  TEXT,
            training_horizon_seconds INTEGER,
            generation              INTEGER NOT NULL DEFAULT 0,
            fleet_version           TEXT,
            train_window_end        TEXT,
            created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            cutover_scheduled_at    TEXT,
            cutover_state           TEXT DEFAULT 'cutover',
            cutover_decided_by      TEXT,
            cutover_decided_at      TEXT,
            artifact_path           TEXT,
            feature_names_path      TEXT,
            artifact_hash           TEXT,
            train_window_start      TEXT,
            train_days              INTEGER,
            feature_version         TEXT DEFAULT 'v3',
            evaluation_windows      TEXT DEFAULT '[300,900,1800]',
            filter_config_json      TEXT DEFAULT '{}',
            platform_active_json    TEXT DEFAULT '{"paper":true}'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_window_tier (
            model_name              TEXT NOT NULL,
            market_window_seconds   INTEGER NOT NULL,
            tier                    TEXT NOT NULL DEFAULT 'watch',
            kelly_multiplier        REAL NOT NULL DEFAULT 0.0,
            tier_assigned_at        TEXT,
            tier_assigned_by        TEXT,
            PRIMARY KEY (model_name, market_window_seconds)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decay_metrics (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                     TEXT NOT NULL,
            model_name             TEXT NOT NULL,
            symbol                 TEXT NOT NULL,
            market_window_seconds  INTEGER NOT NULL,
            window_size            INTEGER NOT NULL,
            rolling_ev             REAL,
            recency_weighted_ev    REAL,
            rolling_win_rate       REAL,
            brier_score            REAL,
            calibration_error      REAL,
            sample_count           INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_audit (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            model_name  TEXT NOT NULL,
            action      TEXT NOT NULL,
            by_user     TEXT NOT NULL,
            detail      TEXT,
            ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)
    conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts_ago(seconds: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_gov_action(
    conn,
    model_name: str = "model_a",
    action: str = "demote",
    from_tier: str = "gold",
    to_tier: str = "silver",
    ts: str | None = None,
    reason_json: str = '{"why": "test"}',
    cell_key: str = "BTCUSDT:300",
) -> None:
    conn.execute(
        "INSERT INTO governance_actions "
        "(ts, model_name, action, from_tier, to_tier, triggered_by, reason_json, cell_key) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ts or _now_iso(), model_name, action, from_tier, to_tier,
         "auto:governance", reason_json, cell_key),
    )
    conn.commit()


def _insert_queue_row(
    conn,
    cell_key: str = "BTCUSDT:300",
    symbol: str = "BTCUSDT",
    horizon: int = 300,
    training_days: int = 90,
    triggered_by: str = "auto:capacity",
    picked_up_at: str | None = None,
    ts: str | None = None,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO retrain_queue "
        "(cell_key, symbol, horizon_seconds, training_days, requested_at, "
        " triggered_by, picked_up_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cell_key, symbol, horizon, training_days,
         ts or _now_iso(), triggered_by, picked_up_at),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def surface_db(tmp_path, monkeypatch):
    """Create a test DB with all surface-endpoint tables, monkeypatch _get_db."""
    db_path = str(tmp_path / "surface_test.db")
    conn = _make_conn(db_path)
    _create_tables(conn)
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)

    def _test_get_db():
        return _make_conn(db_path)

    # Patch _get_db in all relevant router modules
    import dashboard_api.routers.governance as gov_mod
    import dashboard_api.routers.training as train_mod
    import dashboard_api.routers.system as sys_mod
    import dashboard_api.routers.models_summary as ms_mod
    import dashboard_api.routers.models_admin as ma_mod

    monkeypatch.setattr(gov_mod, "_get_db", _test_get_db)
    monkeypatch.setattr(train_mod, "_get_db", _test_get_db)
    monkeypatch.setattr(sys_mod, "_get_db", _test_get_db)
    monkeypatch.setattr(ms_mod, "_get_db", _test_get_db)
    monkeypatch.setattr(ma_mod, "get_db", _test_get_db)

    yield db_path, conn


def _make_app(monkeypatch):
    """Build a minimal FastAPI app with the surface routers mounted, no auth."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import dashboard_api.routers.governance as gov_mod
    import dashboard_api.routers.training as train_mod
    import dashboard_api.routers.system as sys_mod
    import dashboard_api.routers.models_summary as ms_mod
    import dashboard_api.routers.models_admin as ma_mod

    app = FastAPI()
    app.include_router(gov_mod.router, prefix="/api")
    app.include_router(train_mod.router, prefix="/api")
    app.include_router(sys_mod.router, prefix="/api")
    app.include_router(ms_mod.router, prefix="/api")
    app.include_router(ma_mod.router, prefix="/api")
    return TestClient(app)


# ---------------------------------------------------------------------------
# Test 1: governance actions — returns recent
# ---------------------------------------------------------------------------

def test_governance_actions_returns_recent(surface_db, monkeypatch):
    db_path, conn = surface_db

    # Seed 3 recent actions
    _insert_gov_action(conn, model_name="model_a", action="demote")
    _insert_gov_action(conn, model_name="model_b", action="promote")
    _insert_gov_action(conn, model_name="model_c", action="retire")

    from dashboard_api.routers.governance import _fetch_actions
    result = _fetch_actions(since_seconds=86400, since_ms=None, limit=50, action_filter=None)

    assert result["count"] == 3
    assert len(result["actions"]) == 3
    names = {a["model_name"] for a in result["actions"]}
    assert names == {"model_a", "model_b", "model_c"}

    # Summary should tally correctly
    assert result["summary"]["demote"] >= 1
    assert result["summary"]["promote"] >= 1
    assert result["summary"]["retire"] >= 1


# ---------------------------------------------------------------------------
# Test 2: governance actions — since_ms filter
# ---------------------------------------------------------------------------

def test_governance_actions_since_filter(surface_db, monkeypatch):
    db_path, conn = surface_db

    # 1 old action (2 hours ago) + 1 recent action (5 minutes ago)
    old_ts = _ts_ago(7200)   # 2 hours ago
    recent_ts = _ts_ago(300)  # 5 minutes ago

    _insert_gov_action(conn, model_name="old_model", action="demote", ts=old_ts,
                       cell_key="BTCUSDT:300_old")
    _insert_gov_action(conn, model_name="recent_model", action="promote", ts=recent_ts,
                       cell_key="BTCUSDT:300_new")

    # Query with since_ms = now - 1 hour
    since_ms = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp() * 1000)

    from dashboard_api.routers.governance import _fetch_actions
    result = _fetch_actions(since_seconds=None, since_ms=since_ms, limit=50, action_filter=None)

    names = [a["model_name"] for a in result["actions"]]
    assert "recent_model" in names
    assert "old_model" not in names


# ---------------------------------------------------------------------------
# Test 3: training queue — returns pending + summary correct
# ---------------------------------------------------------------------------

def test_training_queue_returns_pending(surface_db, monkeypatch):
    db_path, conn = surface_db

    # 2 pending rows, 1 in-flight
    _insert_queue_row(conn, cell_key="BTCUSDT:300", symbol="BTCUSDT",
                      picked_up_at=None)
    _insert_queue_row(conn, cell_key="ETHUSDT:900", symbol="ETHUSDT",
                      picked_up_at=None,
                      ts=_ts_ago(60))
    _insert_queue_row(conn, cell_key="SOLUSDT:1800", symbol="SOLUSDT",
                      picked_up_at=_now_iso(),
                      ts=_ts_ago(120))

    from dashboard_api.routers.training import _fetch_queue
    result = _fetch_queue(status_filter=None, limit=100)

    assert result["count"] == 3
    assert result["summary"]["pending"] == 2
    assert result["summary"]["in_flight"] == 1

    # Filter for pending only
    result_pending = _fetch_queue(status_filter="pending", limit=100)
    assert result_pending["count"] == 2
    for item in result_pending["queue"]:
        assert item["status"] == "pending"


# ---------------------------------------------------------------------------
# Test 4: system capacity — unavailable when no data and journalctl fails
# ---------------------------------------------------------------------------

def test_system_capacity_unavailable_when_no_data(surface_db, monkeypatch):
    db_path, conn = surface_db

    # Patch subprocess.run to simulate journalctl failure
    import subprocess as _subprocess

    def _fake_run(*args, **kwargs):
        raise FileNotFoundError("journalctl not found")

    monkeypatch.setattr(_subprocess, "run", _fake_run)

    from dashboard_api.routers.system import _fetch_capacity
    result = _fetch_capacity()

    assert result["source"] == "unavailable"
    assert result["boundary_p50_ms"] is None
    assert result["boundary_p95_ms"] is None
    assert result["boundary_max_ms"] is None
    assert result["n_boundaries_sampled"] == 0
    # Should not crash — n_models_active may be 0 (no model_registry rows seeded)
    assert "n_models_active" in result
    assert "window_minutes" in result


# ---------------------------------------------------------------------------
# Test 5: models_registry summary — tier_counts_by_window structure
# ---------------------------------------------------------------------------

def test_models_summary_tier_counts(surface_db, monkeypatch):
    db_path, conn = surface_db

    # Seed model_registry rows
    now = _now_iso()
    conn.execute(
        "INSERT INTO model_registry (name, fleet_version, paper_active, is_baseline, "
        "train_window_end, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        ("model_x", "fleet_v1", 1, 0, now, now, now),
    )
    conn.execute(
        "INSERT INTO model_registry (name, fleet_version, paper_active, is_baseline, "
        "train_window_end, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        ("model_y", "fleet_v1", 1, 0, now, now, now),
    )
    conn.commit()

    # Seed model_window_tier: model_x gold@300 silver@900 watch@1800
    #                         model_y watch@300 gold@900  silver@1800
    tiers = [
        ("model_x", 300, "gold"),
        ("model_x", 900, "silver"),
        ("model_x", 1800, "watch"),
        ("model_y", 300, "watch"),
        ("model_y", 900, "gold"),
        ("model_y", 1800, "silver"),
    ]
    for model_name, win, tier in tiers:
        conn.execute(
            "INSERT OR REPLACE INTO model_window_tier "
            "(model_name, market_window_seconds, tier, kelly_multiplier) "
            "VALUES (?, ?, ?, ?)",
            (model_name, win, tier, 1.0 if tier == "gold" else 0.5),
        )
    conn.commit()

    from dashboard_api.routers.models_summary import _fetch_summary
    result = _fetch_summary()

    assert result["active_models"] == 2
    assert result["total_models"] >= 2
    assert result["distinct_fleets"] >= 1

    tcbw = result["tier_counts_by_window"]
    # All 3 windows should be present
    for win in ("300", "900", "1800"):
        assert win in tcbw, f"window {win} missing from tier_counts_by_window"

    # 300: 1 gold, 1 watch
    assert tcbw["300"]["gold"] == 1
    assert tcbw["300"]["watch"] == 1

    # 900: 1 gold, 1 silver
    assert tcbw["900"]["gold"] == 1
    assert tcbw["900"]["silver"] == 1

    # 1800: 1 silver, 1 watch
    assert tcbw["1800"]["silver"] == 1
    assert tcbw["1800"]["watch"] == 1


# ---------------------------------------------------------------------------
# Test 6: models/list — includes tier_by_window per model
# ---------------------------------------------------------------------------

def test_models_list_includes_tier_by_window(surface_db, monkeypatch):
    db_path, conn = surface_db

    now = _now_iso()
    conn.execute(
        "INSERT INTO model_registry (name, fleet_version, paper_active, is_baseline, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?)",
        ("list_model_a", "fleet_v2", 1, 0, now, now),
    )
    conn.commit()

    # Seed tier rows for list_model_a
    conn.execute(
        "INSERT OR REPLACE INTO model_window_tier "
        "(model_name, market_window_seconds, tier, kelly_multiplier) VALUES (?,?,?,?)",
        ("list_model_a", 300, "gold", 1.0),
    )
    conn.execute(
        "INSERT OR REPLACE INTO model_window_tier "
        "(model_name, market_window_seconds, tier, kelly_multiplier) VALUES (?,?,?,?)",
        ("list_model_a", 900, "silver", 0.5),
    )
    conn.execute(
        "INSERT OR REPLACE INTO model_window_tier "
        "(model_name, market_window_seconds, tier, kelly_multiplier) VALUES (?,?,?,?)",
        ("list_model_a", 1800, "watch", 0.0),
    )
    conn.commit()

    # Call list_models directly
    import dashboard_api.routers.models_admin as ma_mod
    result = ma_mod.list_models()

    models = result["models"]
    target = next((m for m in models if m["name"] == "list_model_a"), None)
    assert target is not None, "list_model_a not found in response"

    tbw = target["tier_by_window"]
    assert tbw is not None
    assert tbw[300] == "gold"
    assert tbw[900] == "silver"
    assert tbw[1800] == "watch"
