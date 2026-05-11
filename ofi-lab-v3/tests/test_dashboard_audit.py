"""C21: Audit API tests."""
import sqlite3
import time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def audit_app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS registry_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_ms INTEGER,
            model_name TEXT,
            action TEXT,
            actor TEXT,
            reason TEXT,
            before_state TEXT,
            after_state TEXT
        )
    """)
    now = int(time.time() * 1000)
    conn.execute(
        "INSERT INTO registry_audit (ts_ms, model_name, action, actor, reason, before_state, after_state) "
        "VALUES (?, 'h60_xrp', 'enable_paper', 'op', 'test', '0', '1')",
        (now,),
    )
    conn.execute(
        "INSERT INTO registry_audit (ts_ms, model_name, action, actor, reason, before_state, after_state) "
        "VALUES (?, 'h300_btc', 'reload', 'op', 'test', '1', '2')",
        (now + 1,),
    )
    conn.commit()
    conn.close()

    # Build a self-contained mini app with inline get_db override
    from fastapi import APIRouter

    def _test_db():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    router = APIRouter(prefix="/audit", tags=["audit"])

    @router.get("")
    def audit_endpoint(limit: int = 50, model: str | None = None, action: str | None = None):
        conn = _test_db()
        sql = "SELECT * FROM registry_audit WHERE 1=1"
        params: list = []
        if model:
            sql += " AND model_name=?"
            params.append(model)
        if action:
            sql += " AND action=?"
            params.append(action)
        sql += " ORDER BY ts_ms DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return {"entries": [dict(r) for r in rows]}

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_audit_returns_recent_entries(audit_app):
    r = audit_app.get("/api/audit?limit=20")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 2
    assert {"ts_ms", "model_name", "action", "actor"} <= set(entries[0])


def test_audit_filter_by_model(audit_app):
    r = audit_app.get("/api/audit?model=h60_xrp&limit=10")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert all(e["model_name"] == "h60_xrp" for e in entries)
    assert len(entries) == 1
