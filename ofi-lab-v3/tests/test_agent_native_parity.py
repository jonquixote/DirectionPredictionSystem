"""C26: Agent-native parity check.

Verifies every UI toggle/button corresponds to a registered API endpoint.
"""
import sqlite3
import os
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


ENDPOINTS = [
    ("GET",  "/api/models/list"),
    ("GET",  "/api/models/h300_btc"),
    ("POST", "/api/models/h60_xrp/enable_paper"),
    ("POST", "/api/models/h60_xrp/disable_paper"),
    ("POST", "/api/models/h60_xrp/enable_live"),
    ("POST", "/api/models/h60_xrp/disable_live"),
    ("POST", "/api/models/h60_xrp/reload"),
    ("POST", "/api/models/h60_xrp/rollback"),
    ("GET",  "/api/overlap"),
    ("GET",  "/api/audit"),
    ("GET",  "/api/kill_switch"),
    ("POST", "/api/kill_switch"),
    ("POST", "/api/kill_switch/confirm_resume"),
    ("POST", "/api/admin/confirm_intent"),
]


@pytest.fixture
def parity_client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "parity.db")
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)
    monkeypatch.setattr(
        "dashboard_api.services.kill_switch_state.STATE_PATH",
        tmp_path / "kill.json",
    )

    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS model_registry (
        name TEXT PRIMARY KEY, is_baseline INTEGER DEFAULT 0,
        lifecycle_state TEXT DEFAULT 'active', paper_active INTEGER DEFAULT 0,
        live_eligible INTEGER DEFAULT 0, symbol TEXT,
        training_horizon_seconds INTEGER, generation INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS registry_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts_ms INTEGER,
        model_name TEXT, action TEXT, actor TEXT, reason TEXT,
        before_state TEXT, after_state TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS model_overlap (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts_contract_open_ms INTEGER,
        symbol TEXT, market_window_seconds INTEGER, consensus INTEGER,
        weighted_confidence REAL, model_details TEXT, registry_load_generation INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS decay_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT, model_name TEXT,
        symbol TEXT, market_window_seconds INTEGER, window_size INTEGER,
        rolling_ev REAL, recency_weighted_ev REAL, rolling_win_rate REAL,
        brier_score REAL, calibration_error REAL, sample_count INTEGER,
        ts_ms INTEGER DEFAULT 0
    )""")
    conn.execute(
        "INSERT INTO model_registry (name, is_baseline, symbol, training_horizon_seconds) "
        "VALUES ('h300_btc', 1, 'BTCUSDT', 900)"
    )
    conn.execute(
        "INSERT INTO model_registry (name, is_baseline, symbol, training_horizon_seconds) "
        "VALUES ('h60_xrp', 0, 'XRPUSDT', 60)"
    )
    conn.commit()
    conn.close()

    import dashboard_api.routers.models_admin as ma
    def _test_db():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(ma, "_get_conn", _test_db)

    import dashboard_api.services.db as db_mod
    monkeypatch.setattr(db_mod, "get_db", _test_db)

    from dashboard_api.routers.models_admin import router as models_router
    from dashboard_api.routers.overlap import router as overlap_router
    from dashboard_api.routers.kill_switch import router as ks_router
    from dashboard_api.routers.audit import router as audit_router
    from dashboard_api.routers.admin import router as admin_router

    app = FastAPI()
    for rt in [models_router, overlap_router, ks_router, audit_router, admin_router]:
        app.include_router(rt, prefix="/api")

    from dashboard_api.services.admin_auth import _USED_TOKENS
    _USED_TOKENS.clear()

    return TestClient(app)


def test_all_endpoints_registered(parity_client):
    for method, path in ENDPOINTS:
        r = parity_client.request(method, path, json={})
        assert r.status_code != 404, f"{method} {path} not registered (got {r.status_code})"
