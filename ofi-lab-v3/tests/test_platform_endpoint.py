"""Tests for POST /api/models/{name}/platform endpoint."""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard_api.services.admin_auth import _USED_TOKENS


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_tokens():
    _USED_TOKENS.clear()
    yield
    _USED_TOKENS.clear()


@pytest.fixture
def seeded_app(tmp_path, monkeypatch):
    """Mini FastAPI app with seeded SQLite DB for platform endpoint tests."""
    db_path = str(tmp_path / "test_platform.db")
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)

    from storage.db import open_database, init_schema
    conn = open_database(db_path)
    init_schema(conn)

    # paper-only model with default platform_active_json
    conn.execute(
        "INSERT INTO model_registry "
        "(name, is_baseline, symbol, training_horizon_seconds, generation, "
        "paper_active, live_eligible, platform_active_json) "
        "VALUES ('h300_btc_v3_179d', 0, 'BTCUSDT', 300, 1, 1, 0, "
        "'{\"kalshi\":false,\"paper\":true,\"polymarket\":false}')"
    )
    # live-eligible model
    conn.execute(
        "INSERT INTO model_registry "
        "(name, is_baseline, symbol, training_horizon_seconds, generation, "
        "paper_active, live_eligible, platform_active_json) "
        "VALUES ('h300_btc_live', 0, 'BTCUSDT', 300, 1, 1, 1, "
        "'{\"kalshi\":false,\"paper\":true,\"polymarket\":false}')"
    )
    conn.commit()
    conn.close()

    import dashboard_api.routers.models_admin as ma

    def _test_get_conn():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(ma, "_get_conn", _test_get_conn)
    monkeypatch.setattr(ma, "_trigger_reload_meta", lambda: True)

    from dashboard_api.routers.models_admin import router as models_router
    from dashboard_api.routers.admin import router as admin_router

    app = FastAPI()
    app.include_router(models_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    return TestClient(app), db_path


def _get_platform(db_path: str, name: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT platform_active_json FROM model_registry WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    return json.loads(row["platform_active_json"] or "{}") if row else {}


def _get_audit_rows(db_path: str, name: str) -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM model_audit WHERE model_name = ? ORDER BY ts DESC", (name,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_token(client, action: str, target: str) -> str:
    r = client.post(
        "/api/admin/confirm_intent",
        json={"action": action, "target": target, "by": "op"},
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ── Tests ─────────────────────────────────────────────────────────

def test_platform_partial_update_preserves_other_keys(seeded_app):
    """Enabling kalshi preserves paper=True and polymarket=False."""
    client, db_path = seeded_app
    tok = _get_token(client, "set_platform", "h300_btc_live")
    r = client.post("/api/models/h300_btc_live/platform", json={
        "kalshi": True,
        "by": "op",
        "confirmation_token": tok,
    })
    assert r.status_code == 200, r.text
    pa = r.json()["platform_active"]
    assert pa["paper"] is True
    assert pa["kalshi"] is True
    assert pa["polymarket"] is False

    db_pa = _get_platform(db_path, "h300_btc_live")
    assert db_pa["paper"] is True
    assert db_pa["kalshi"] is True
    assert db_pa["polymarket"] is False


def test_platform_404_for_unknown_model(seeded_app):
    client, _ = seeded_app
    r = client.post("/api/models/nonexistent_xyz/platform", json={
        "paper": True,
        "by": "op",
    })
    assert r.status_code == 404


def test_platform_422_for_empty_body(seeded_app):
    """No platform flags → 422."""
    client, _ = seeded_app
    r = client.post("/api/models/h300_btc_v3_179d/platform", json={
        "by": "op",
    })
    assert r.status_code == 422


def test_platform_enabling_kalshi_on_live_model_requires_token(seeded_app):
    """live_eligible=1, enabling kalshi, no token → 403."""
    client, _ = seeded_app
    r = client.post("/api/models/h300_btc_live/platform", json={
        "kalshi": True,
        "by": "op",
    })
    assert r.status_code == 403
    assert "confirmation_token" in r.json()["detail"]


def test_platform_disabling_kalshi_on_live_model_no_token_needed(seeded_app):
    """live_eligible=1, disabling kalshi (False→False is no-op, or True→False) → 200 without token."""
    client, db_path = seeded_app
    # First enable kalshi with a token so we can then disable it
    tok = _get_token(client, "set_platform", "h300_btc_live")
    r = client.post("/api/models/h300_btc_live/platform", json={
        "kalshi": True,
        "by": "op",
        "confirmation_token": tok,
    })
    assert r.status_code == 200

    # Now disable — no token needed
    r2 = client.post("/api/models/h300_btc_live/platform", json={
        "kalshi": False,
        "by": "op",
    })
    assert r2.status_code == 200
    assert r2.json()["platform_active"]["kalshi"] is False


def test_platform_audit_row_written(seeded_app):
    """set_platform action written to model_audit."""
    client, db_path = seeded_app
    r = client.post("/api/models/h300_btc_v3_179d/platform", json={
        "paper": False,
        "by": "tester",
        "reason": "disabling paper",
    })
    assert r.status_code == 200, r.text
    audit = _get_audit_rows(db_path, "h300_btc_v3_179d")
    assert len(audit) >= 1
    assert audit[0]["action"] == "set_platform"
    assert audit[0]["by_user"] == "tester"
    assert "disabling paper" in audit[0]["detail"]


def test_platform_triggers_reload_meta(seeded_app, monkeypatch):
    """_trigger_reload_meta is called and trader_reloaded is reflected."""
    client, _ = seeded_app

    import dashboard_api.routers.models_admin as ma
    reload_called = []

    def _mock_reload():
        reload_called.append(True)
        return True

    monkeypatch.setattr(ma, "_trigger_reload_meta", _mock_reload)

    r = client.post("/api/models/h300_btc_v3_179d/platform", json={
        "paper": False,
        "by": "op",
    })
    assert r.status_code == 200, r.text
    assert len(reload_called) == 1
    assert r.json()["trader_reloaded"] is True
