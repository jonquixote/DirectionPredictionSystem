"""C19: Kill switch persistence and API tests."""
import pytest

from dashboard_api.services import kill_switch_state as ks


@pytest.fixture(autouse=True)
def _tmp_kill_path(tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "STATE_PATH", tmp_path / "kill.json")
    yield


def test_kill_switch_persists_to_disk():
    ks.engage(reason="drawdown_exceeded", by="op")
    assert ks.is_engaged() is True
    state = ks.read_state()
    assert state["reason"] == "drawdown_exceeded"


def test_disengage_removes_file():
    ks.engage(reason="manual", by="op")
    assert ks.is_engaged() is True
    ks.disengage(by="op")
    assert ks.is_engaged() is False


def test_resume_requires_confirmation_token():
    """Resume via API requires a confirmation token."""
    import os
    os.environ.setdefault("STORAGE_DB_PATH", "/tmp/_ks_test.db")
    from fastapi.testclient import TestClient
    from dashboard_api.services.admin_auth import _USED_TOKENS
    _USED_TOKENS.clear()

    # We need a minimal app import
    from dashboard_api.routers.kill_switch import router as ks_router
    from dashboard_api.routers.admin import router as admin_router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(ks_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")

    c = TestClient(app)

    ks.engage(reason="manual", by="op")
    r = c.post("/api/kill_switch/confirm_resume", json={"by": "op"})
    assert r.status_code == 400

    tok = c.post(
        "/api/admin/confirm_intent",
        json={"action": "kill_switch_resume", "target": "global", "by": "op"},
    ).json()["token"]
    r = c.post(
        "/api/kill_switch/confirm_resume",
        json={"by": "op", "confirmation_token": tok},
    )
    assert r.status_code == 200
    assert ks.is_engaged() is False
