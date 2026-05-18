"""B.4: POST /api/models/{name}/filter endpoint tests.

Covers:
  - paper-only model filter update + audit row
  - validation errors (422) for out-of-range values
  - 404 for unknown model
  - live-eligible model requires confirmation token (403)
  - clear_keys removes keys from filter_config_json
  - reload_meta httpx call is triggered
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard_api.services.admin_auth import _USED_TOKENS


# ── Fixtures ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_tokens():
    _USED_TOKENS.clear()
    yield
    _USED_TOKENS.clear()


@pytest.fixture
def seeded_app(tmp_path, monkeypatch):
    """Mini FastAPI app with seeded SQLite DB for filter endpoint tests."""
    db_path = str(tmp_path / "test_filter.db")
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)

    from storage.db import open_database, init_schema
    conn = open_database(db_path)
    init_schema(conn)

    # paper-only model
    conn.execute(
        "INSERT INTO model_registry "
        "(name, is_baseline, symbol, training_horizon_seconds, generation, paper_active, live_eligible, filter_config_json) "
        "VALUES ('h60_btc_30d', 0, 'BTCUSDT', 60, 1, 1, 0, '{}')"
    )
    # live-eligible model
    conn.execute(
        "INSERT INTO model_registry "
        "(name, is_baseline, symbol, training_horizon_seconds, generation, paper_active, live_eligible, filter_config_json) "
        "VALUES ('h300_btc_live', 0, 'BTCUSDT', 300, 1, 1, 1, '{}')"
    )
    conn.commit()
    conn.close()

    import dashboard_api.routers.models_admin as ma

    def _test_get_conn():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(ma, "_get_conn", _test_get_conn)
    # Suppress actual httpx calls unless test overrides
    monkeypatch.setattr(ma, "_trigger_reload_meta", lambda: True)

    from dashboard_api.routers.models_admin import router as models_router
    from dashboard_api.routers.admin import router as admin_router

    app = FastAPI()
    app.include_router(models_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    return TestClient(app), db_path


def _get_filter(db_path: str, name: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT filter_config_json FROM model_registry WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    return json.loads(row["filter_config_json"] or "{}") if row else {}


def _get_audit_rows(db_path: str, name: str) -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM model_audit WHERE model_name = ? ORDER BY ts DESC", (name,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Tests ────────────────────────────────────────────────────────

def test_post_filter_updates_registry(seeded_app):
    """Paper-only model: filter_config_json updated + audit row written."""
    client, db_path = seeded_app
    r = client.post("/api/models/h60_btc_30d/filter", json={
        "by": "operator",
        "confidence_threshold": 0.55,
        "ev_threshold": 0.005,
        "blackout_hours": [21, 22, 23, 0, 1, 2, 3],
        "warmup_seconds": 1800,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "h60_btc_30d"
    fc = body["filter_config"]
    assert fc["confidence_threshold"] == pytest.approx(0.55)
    assert fc["ev_threshold"] == pytest.approx(0.005)
    assert fc["blackout_hours"] == [0, 1, 2, 3, 21, 22, 23]
    assert fc["warmup_seconds"] == 1800
    assert "applied_at" in body
    assert body["trader_reloaded"] is True

    # DB updated
    db_fc = _get_filter(db_path, "h60_btc_30d")
    assert db_fc["confidence_threshold"] == pytest.approx(0.55)

    # Audit row written
    audit = _get_audit_rows(db_path, "h60_btc_30d")
    assert len(audit) >= 1
    assert audit[0]["action"] == "set_filter"
    assert audit[0]["by_user"] == "operator"


def test_post_filter_validates_confidence_range(seeded_app):
    """confidence_threshold=1.5 → 422."""
    client, _ = seeded_app
    r = client.post("/api/models/h60_btc_30d/filter", json={
        "by": "op",
        "confidence_threshold": 1.5,
    })
    assert r.status_code == 422


def test_post_filter_validates_ev_range(seeded_app):
    """ev_threshold=2.0 → 422."""
    client, _ = seeded_app
    r = client.post("/api/models/h60_btc_30d/filter", json={
        "by": "op",
        "ev_threshold": 2.0,
    })
    assert r.status_code == 422


def test_post_filter_validates_blackout_hours(seeded_app):
    """blackout_hours=[24] → 422."""
    client, _ = seeded_app
    r = client.post("/api/models/h60_btc_30d/filter", json={
        "by": "op",
        "blackout_hours": [24],
    })
    assert r.status_code == 422


def test_post_filter_validates_negative_warmup(seeded_app):
    """warmup_seconds=-1 → 422."""
    client, _ = seeded_app
    r = client.post("/api/models/h60_btc_30d/filter", json={
        "by": "op",
        "warmup_seconds": -1,
    })
    assert r.status_code == 422


def test_post_filter_404_for_unknown_model(seeded_app):
    """Unknown model name → 404."""
    client, _ = seeded_app
    r = client.post("/api/models/nonexistent_xyz/filter", json={"by": "op"})
    assert r.status_code == 404


def test_post_filter_live_model_requires_confirmation_token(seeded_app):
    """live_eligible=1 model without token → 403."""
    client, _ = seeded_app
    r = client.post("/api/models/h300_btc_live/filter", json={
        "by": "op",
        "confidence_threshold": 0.6,
    })
    assert r.status_code == 403
    assert "confirmation_token" in r.json()["detail"]


def test_post_filter_live_model_with_valid_token(seeded_app):
    """live_eligible=1 model with valid token → 200."""
    client, db_path = seeded_app
    tok = client.post(
        "/api/admin/confirm_intent",
        json={"action": "set_filter", "target": "h300_btc_live", "by": "op"},
    ).json()["token"]
    r = client.post("/api/models/h300_btc_live/filter", json={
        "by": "op",
        "confidence_threshold": 0.62,
        "confirmation_token": tok,
    })
    assert r.status_code == 200
    assert r.json()["filter_config"]["confidence_threshold"] == pytest.approx(0.62)


def test_post_filter_clears_keys(seeded_app):
    """clear_keys removes specified keys from filter_config_json."""
    client, db_path = seeded_app
    # First set some values
    client.post("/api/models/h60_btc_30d/filter", json={
        "by": "op",
        "confidence_threshold": 0.55,
        "blackout_hours": [21, 22],
        "warmup_seconds": 600,
    })
    # Now clear blackout_hours
    r = client.post("/api/models/h60_btc_30d/filter", json={
        "by": "op",
        "clear_keys": ["blackout_hours"],
    })
    assert r.status_code == 200, r.text
    fc = r.json()["filter_config"]
    assert "blackout_hours" not in fc
    # other keys preserved
    assert fc["confidence_threshold"] == pytest.approx(0.55)
    assert fc["warmup_seconds"] == 600

    # DB consistent
    db_fc = _get_filter(db_path, "h60_btc_30d")
    assert "blackout_hours" not in db_fc


def test_post_filter_triggers_reload_meta_call(seeded_app, monkeypatch):
    """Assert POST http://127.0.0.1:8080/reload_meta is called."""
    client, _ = seeded_app

    import dashboard_api.routers.models_admin as ma

    reload_called = []

    def _mock_reload():
        reload_called.append(True)
        return True

    monkeypatch.setattr(ma, "_trigger_reload_meta", _mock_reload)

    r = client.post("/api/models/h60_btc_30d/filter", json={
        "by": "op",
        "confidence_threshold": 0.57,
    })
    assert r.status_code == 200
    assert len(reload_called) == 1
    assert r.json()["trader_reloaded"] is True


def test_post_filter_reload_unreachable_still_returns_200(seeded_app, monkeypatch):
    """If reload_meta is unreachable, endpoint still returns 200 with trader_reloaded=False."""
    client, _ = seeded_app

    import dashboard_api.routers.models_admin as ma
    monkeypatch.setattr(ma, "_trigger_reload_meta", lambda: False)

    r = client.post("/api/models/h60_btc_30d/filter", json={
        "by": "op",
        "confidence_threshold": 0.57,
    })
    assert r.status_code == 200
    assert r.json()["trader_reloaded"] is False


def test_post_filter_blackout_hours_deduped_and_sorted(seeded_app):
    """blackout_hours input is deduped + sorted in stored value."""
    client, db_path = seeded_app
    r = client.post("/api/models/h60_btc_30d/filter", json={
        "by": "op",
        "blackout_hours": [23, 0, 1, 23, 22],
    })
    assert r.status_code == 200
    assert r.json()["filter_config"]["blackout_hours"] == [0, 1, 22, 23]


def test_post_filter_response_has_canonical_sorted_keys(seeded_app):
    """filter_config_json stored with sorted keys (canonical JSON)."""
    client, db_path = seeded_app
    r = client.post("/api/models/h60_btc_30d/filter", json={
        "by": "op",
        "warmup_seconds": 300,
        "confidence_threshold": 0.6,
        "ev_threshold": 0.01,
    })
    assert r.status_code == 200
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT filter_config_json FROM model_registry WHERE name='h60_btc_30d'"
    ).fetchone()
    conn.close()
    raw = row[0]
    # Canonical = sorted keys
    parsed = json.loads(raw)
    expected_keys = sorted(parsed.keys())
    assert list(parsed.keys()) == expected_keys
