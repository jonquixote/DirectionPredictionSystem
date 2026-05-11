"""C12-C15, C20: Models admin API integration tests.

Uses a minimal FastAPI TestClient with a seeded in-memory DB.
"""
import os
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard_api.services.admin_auth import _USED_TOKENS


@pytest.fixture(autouse=True)
def _clear_tokens():
    _USED_TOKENS.clear()
    yield
    _USED_TOKENS.clear()


@pytest.fixture
def seeded_app(tmp_path, monkeypatch):
    """Create a mini FastAPI app with seeded SQLite for testing."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)

    # Use real init_schema from storage
    from storage.db import open_database, init_schema
    conn = open_database(db_path)
    init_schema(conn)

    # Seed models
    conn.execute(
        "INSERT INTO model_registry (name, is_baseline, symbol, training_horizon_seconds, generation, paper_active, live_eligible) "
        "VALUES ('h300_btc', 1, 'BTCUSDT', 900, 1, 1, 1)"
    )
    conn.execute(
        "INSERT INTO model_registry (name, is_baseline, symbol, training_horizon_seconds, generation, paper_active, live_eligible) "
        "VALUES ('h60_xrp', 0, 'XRPUSDT', 60, 2, 0, 0)"
    )
    conn.commit()
    conn.close()

    # Monkey-patch get_db to return connection to our test DB
    import dashboard_api.routers.models_admin as ma
    def _test_get_conn():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(ma, "_get_conn", _test_get_conn)

    from dashboard_api.routers.models_admin import router as models_router
    from dashboard_api.routers.admin import router as admin_router

    app = FastAPI()
    app.include_router(models_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    return TestClient(app), db_path


# ── C12: List + detail ──────────────────────────────────────────

def test_list_models_returns_all_rows(seeded_app):
    c, _ = seeded_app
    r = c.get("/api/models/list")
    assert r.status_code == 200
    data = r.json()
    assert "models" in data
    names = [m["name"] for m in data["models"]]
    assert "h300_btc" in names
    assert "h60_xrp" in names
    h300 = next(m for m in data["models"] if m["name"] == "h300_btc")
    assert h300["is_baseline"] == 1
    assert "lifecycle_state" in h300
    assert "paper_active" in h300
    assert "live_eligible" in h300


def test_get_model_detail(seeded_app):
    c, _ = seeded_app
    r = c.get("/api/models/h300_btc")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "h300_btc"
    assert "calibration_summary" in body
    assert "overlap" in body
    assert "recent_audit" in body


def test_get_model_404(seeded_app):
    c, _ = seeded_app
    r = c.get("/api/models/nonexistent")
    assert r.status_code == 404


# ── C13: Paper toggles ──────────────────────────────────────────

def test_enable_paper(seeded_app):
    c, db_path = seeded_app
    r = c.post("/api/models/h60_xrp/enable_paper", json={"by": "op"})
    assert r.status_code == 200
    assert r.json()["paper_active"] is True
    # Check audit
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    audit = conn.execute(
        "SELECT * FROM model_audit WHERE model_name='h60_xrp' ORDER BY ts DESC"
    ).fetchone()
    assert audit["action"] == "enable_paper"


def test_disable_paper(seeded_app):
    c, _ = seeded_app
    c.post("/api/models/h60_xrp/enable_paper", json={"by": "op"})
    r = c.post("/api/models/h60_xrp/disable_paper", json={"by": "op", "reason": "drift"})
    assert r.status_code == 200
    assert r.json()["paper_active"] is False


def test_paper_toggle_does_not_affect_live(seeded_app):
    c, db_path = seeded_app
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE model_registry SET live_eligible=1 WHERE name='h60_xrp'")
    conn.commit()
    conn.close()
    c.post("/api/models/h60_xrp/disable_paper", json={"by": "op"})
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT live_eligible FROM model_registry WHERE name='h60_xrp'"
    ).fetchone()
    assert row["live_eligible"] == 1


# ── C14: Live toggles with confirmation ─────────────────────────

def test_enable_live_requires_confirmation_token(seeded_app):
    c, _ = seeded_app
    r = c.post("/api/models/h60_xrp/enable_live", json={"by": "op"})
    assert r.status_code == 400
    assert "confirmation" in r.json()["detail"].lower()


def test_enable_live_with_valid_token_succeeds(seeded_app):
    c, _ = seeded_app
    tok = c.post(
        "/api/admin/confirm_intent",
        json={"action": "enable_live", "target": "h60_xrp", "by": "op"},
    ).json()["token"]
    r = c.post(
        "/api/models/h60_xrp/enable_live",
        json={"by": "op", "confirmation_token": tok},
    )
    assert r.status_code == 200
    assert r.json()["live_eligible"] is True


def test_disable_live_no_token_needed(seeded_app):
    c, _ = seeded_app
    r = c.post("/api/models/h60_xrp/disable_live", json={"by": "op"})
    assert r.status_code == 200
    assert r.json()["live_eligible"] is False


# ── C15: Reload with generation increment ───────────────────────

def test_reload_increments_generation(seeded_app):
    c, _ = seeded_app
    tok = c.post(
        "/api/admin/confirm_intent",
        json={"action": "reload", "target": "h60_xrp", "by": "op"},
    ).json()["token"]
    r = c.post(
        "/api/models/h60_xrp/reload",
        json={"by": "op", "confirmation_token": tok},
    )
    assert r.status_code == 200
    assert r.json()["generation"] >= 3  # was 2


# ── C20: Rollback ───────────────────────────────────────────────

def test_rollback_non_baseline(seeded_app):
    c, _ = seeded_app
    tok = c.post(
        "/api/admin/confirm_intent",
        json={"action": "rollback", "target": "h60_xrp", "by": "op"},
    ).json()["token"]
    r = c.post(
        "/api/models/h60_xrp/rollback",
        json={"by": "op", "confirmation_token": tok, "target_generation": 1},
    )
    assert r.status_code == 200
    assert r.json()["generation"] == 1


def test_rollback_blocked_for_baseline(seeded_app):
    c, _ = seeded_app
    tok = c.post(
        "/api/admin/confirm_intent",
        json={"action": "rollback", "target": "h300_btc", "by": "op"},
    ).json()["token"]
    r = c.post(
        "/api/models/h300_btc/rollback",
        json={"by": "op", "confirmation_token": tok, "target_generation": 0},
    )
    assert r.status_code == 412
    assert "baseline" in r.json()["detail"].lower()
