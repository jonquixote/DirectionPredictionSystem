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

    from storage.db import open_database, init_schema
    conn = open_database(db_path)
    init_schema(conn)

    # Seed models
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

    def _test_db():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

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
