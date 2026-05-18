"""Tests for /api/settings (dashboard_settings router)."""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def seeded_app(tmp_path, monkeypatch):
    """Mini FastAPI app with seeded SQLite DB for settings endpoint tests."""
    db_path = str(tmp_path / "test_settings.db")
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)

    from storage.db import open_database, init_schema
    conn = open_database(db_path)
    init_schema(conn)
    conn.close()

    import dashboard_api.routers.dashboard_settings as ds

    def _test_get_conn():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(ds, "_get_conn", _test_get_conn)

    from dashboard_api.routers.dashboard_settings import router as settings_router

    app = FastAPI()
    app.include_router(settings_router, prefix="/api")
    return TestClient(app), db_path


# ── Tests ─────────────────────────────────────────────────────────

def test_put_then_get_roundtrip(seeded_app):
    """PUT a value then GET it back — value and key match."""
    client, _ = seeded_app
    r = client.put("/api/settings/theme", json={"value": "dark", "by": "user1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"] == "theme"
    assert body["value"] == "dark"

    r2 = client.get("/api/settings/theme")
    assert r2.status_code == 200
    assert r2.json()["value"] == "dark"
    assert r2.json()["key"] == "theme"


def test_get_unknown_key_returns_404(seeded_app):
    client, _ = seeded_app
    r = client.get("/api/settings/does_not_exist")
    assert r.status_code == 404


def test_put_overwrites_existing(seeded_app):
    """Second PUT to same key updates the value."""
    client, _ = seeded_app
    client.put("/api/settings/page_size", json={"value": 10})
    r = client.put("/api/settings/page_size", json={"value": 50})
    assert r.status_code == 200
    assert r.json()["value"] == 50

    r2 = client.get("/api/settings/page_size")
    assert r2.json()["value"] == 50


def test_delete_removes_key(seeded_app):
    """DELETE removes key; subsequent GET returns 404."""
    client, _ = seeded_app
    client.put("/api/settings/to_delete", json={"value": "bye"})
    r = client.delete("/api/settings/to_delete")
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    r2 = client.get("/api/settings/to_delete")
    assert r2.status_code == 404


def test_get_all_returns_dict(seeded_app):
    """GET /settings returns {settings: {key: value}} for all stored keys."""
    client, _ = seeded_app
    client.put("/api/settings/alpha", json={"value": 1})
    client.put("/api/settings/beta", json={"value": 2})

    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert "settings" in body
    assert body["settings"]["alpha"] == 1
    assert body["settings"]["beta"] == 2


def test_value_is_json_serialized(seeded_app):
    """PUT a list value; GET returns the same list (not a string)."""
    client, _ = seeded_app
    r = client.put("/api/settings/my_list", json={"value": [1, 2, 3]})
    assert r.status_code == 200
    assert r.json()["value"] == [1, 2, 3]

    r2 = client.get("/api/settings/my_list")
    assert r2.status_code == 200
    assert r2.json()["value"] == [1, 2, 3]
    assert isinstance(r2.json()["value"], list)


def test_delete_nonexistent_returns_404(seeded_app):
    """DELETE on a key that doesn't exist → 404."""
    client, _ = seeded_app
    r = client.delete("/api/settings/never_existed")
    assert r.status_code == 404


def test_put_complex_value_roundtrip(seeded_app):
    """PUT a nested dict; GET returns the same structure."""
    client, _ = seeded_app
    payload = {"filters": {"symbol": "BTC", "horizon": 300}, "active": True}
    r = client.put("/api/settings/dashboard_state", json={"value": payload})
    assert r.status_code == 200

    r2 = client.get("/api/settings/dashboard_state")
    assert r2.status_code == 200
    assert r2.json()["value"] == payload
