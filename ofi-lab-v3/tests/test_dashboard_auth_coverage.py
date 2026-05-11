"""C28: Dashboard auth coverage tests.

Verify all POST endpoints under /api/models/*, /api/admin/*, /api/kill_switch*
require authentication. GET endpoints should be open (in dev mode).
"""
import os
import sqlite3
import pytest
from fastapi.testclient import TestClient


# These are the protected endpoints from test_agent_native_parity.py
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
def auth_client(tmp_path, monkeypatch):
    """Create a test client with dev mode auth (credentials not set)."""
    db_path = str(tmp_path / "auth_test.db")
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)
    monkeypatch.setattr(
        "dashboard_api.services.kill_switch_state.STATE_PATH",
        tmp_path / "kill.json",
    )
    
    # Ensure DASHBOARD_USER and DASHBOARD_PASS are NOT set (dev mode)
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_PASS", raising=False)

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

    import dashboard_api.routers.models_admin as ma
    def _test_db():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(ma, "_get_conn", _test_db)

    import dashboard_api.services.db as db_mod
    monkeypatch.setattr(db_mod, "get_db", _test_db)

    from fastapi import FastAPI
    from dashboard_api.routers.models_admin import router as models_router
    from dashboard_api.routers.overlap import router as overlap_router
    from dashboard_api.routers.kill_switch import router as ks_router
    from dashboard_api.routers.audit import router as audit_router
    from dashboard_api.routers.admin import router as admin_router
    from dashboard_api.services.auth import verify_credentials
    from fastapi import Depends

    app = FastAPI()
    for rt in [models_router, overlap_router, ks_router, audit_router, admin_router]:
        app.include_router(
            rt,
            prefix="/api",
            dependencies=[Depends(verify_credentials)],
        )

    from dashboard_api.services.admin_auth import _USED_TOKENS
    _USED_TOKENS.clear()

    return TestClient(app)


def test_all_endpoints_accessible_in_dev_mode(auth_client):
    """In dev mode (no creds set), all endpoints should be accessible."""
    for method, path in ENDPOINTS:
        r = auth_client.request(method, path, json={})
        # Should not be 401 (not 401 because auth is disabled in dev)
        assert r.status_code != 401, (
            f"{method} {path} returned 401 in dev mode — auth should be disabled. "
            f"Got {r.status_code}: {r.text}"
        )


def test_endpoints_exist_and_routed(auth_client):
    """All endpoints should be registered (not 404)."""
    for method, path in ENDPOINTS:
        r = auth_client.request(method, path, json={})
        assert r.status_code != 404, (
            f"{method} {path} not registered (got {r.status_code})"
        )


def test_all_post_endpoints_protected(tmp_path, monkeypatch):
    """All POST endpoints require auth when credentials are set."""
    db_path = str(tmp_path / "prod_test.db")
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)
    monkeypatch.setattr(
        "dashboard_api.services.kill_switch_state.STATE_PATH",
        tmp_path / "kill.json",
    )

    # Set credentials to enable auth
    monkeypatch.setenv("DASHBOARD_USER", "testuser")
    monkeypatch.setenv("DASHBOARD_PASS", "testpass")

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

    # Re-import auth module to pick up new env vars
    import importlib
    import dashboard_api.services.auth
    importlib.reload(dashboard_api.services.auth)
    from dashboard_api.services.auth import verify_credentials

    from fastapi import FastAPI, Depends
    from dashboard_api.routers.models_admin import router as models_router
    from dashboard_api.routers.overlap import router as overlap_router
    from dashboard_api.routers.kill_switch import router as ks_router
    from dashboard_api.routers.audit import router as audit_router
    from dashboard_api.routers.admin import router as admin_router

    app = FastAPI()
    for rt in [models_router, overlap_router, ks_router, audit_router, admin_router]:
        app.include_router(
            rt,
            prefix="/api",
            dependencies=[Depends(verify_credentials)],
        )

    from dashboard_api.services.admin_auth import _USED_TOKENS
    _USED_TOKENS.clear()

    client = TestClient(app)

    # Test that POST endpoints return 401 without auth
    post_endpoints = [ep for method, ep in ENDPOINTS if method == "POST"]
    for path in post_endpoints:
        r = client.post(path, json={})
        assert r.status_code == 401, (
            f"POST {path} should return 401 without auth, got {r.status_code}"
        )

    # Test that auth with correct credentials works
    for path in post_endpoints:
        r = client.post(path, json={}, auth=("testuser", "testpass"))
        # Should not be 401 (may be 400, 422, etc. due to missing required fields, but not 401)
        assert r.status_code != 401, (
            f"POST {path} returned 401 with correct credentials"
        )
