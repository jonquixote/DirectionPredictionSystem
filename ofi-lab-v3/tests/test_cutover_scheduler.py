"""Phase 5 — scheduled cutover system tests.

Covers:
- Schema migration adds the four cutover_* columns idempotently.
- register_model writes paper_active=0 + cutover_state='scheduled'
  + cutover_scheduled_at ≈ NOW+24h for new non-baseline rows.
- Baseline rows keep paper_active untouched on re-registration.
- The scheduler tick promotes due rows and leaves future rows alone.
- Endpoint logic: 404 unknown, baseline rejected, past 'at' rejected,
  bulk transaction returns per-name results.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from storage.db import open_database, init_schema


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def db_conn():
    conn = open_database(":memory:")
    init_schema(conn)
    return conn


def _seed_artifact(tmp_path: Path, *, name: str, symbol: str, horizon: int,
                   train_days: int, train_window_end: str) -> Path:
    out = tmp_path / name
    out.mkdir(parents=True)
    (out / "model.lgb").write_bytes(b"FAKE_LGB")
    (out / "feature_names.json").write_text(json.dumps(["mlofi", "ofi"]))
    (out / "metrics.json").write_text(
        json.dumps({
            "horizon_seconds": horizon,
            "symbol": symbol,
            "train_days": train_days,
            "train_window_start": "2025-07-24",
            "train_window_end": train_window_end,
            "feature_version": "v3",
        })
    )
    return out


# ── 1. Schema migration ─────────────────────────────────────────


def test_migration_adds_cutover_columns(db_conn):
    """All four cutover_* columns exist after init_schema."""
    cols = {row[1] for row in db_conn.execute(
        "PRAGMA table_info(model_registry)"
    ).fetchall()}
    assert "cutover_scheduled_at" in cols
    assert "cutover_state" in cols
    assert "cutover_decided_by" in cols
    assert "cutover_decided_at" in cols


def test_migration_is_idempotent(tmp_path):
    """Running init_schema twice does not error and columns remain unique."""
    db_path = str(tmp_path / "idem.db")
    conn = open_database(db_path)
    init_schema(conn)
    init_schema(conn)  # second run must not raise
    cols = [row[1] for row in conn.execute(
        "PRAGMA table_info(model_registry)"
    ).fetchall()]
    # No duplicate column names
    assert len(cols) == len(set(cols))
    # Cutover columns still present
    assert "cutover_state" in cols


def test_migration_backfills_existing_rows(tmp_path):
    """Pre-existing model_registry rows are backfilled to cutover_state='cutover'."""
    db_path = str(tmp_path / "backfill.db")
    # Pre-create the table without cutover columns by calling init_schema then
    # inserting a row, then re-running migrations — the backfill UPDATE should
    # populate NULL cutover_state values.
    conn = open_database(db_path)
    init_schema(conn)
    # Manually NULL the cutover state on a fresh row, then re-run migrations.
    conn.execute(
        "INSERT INTO model_registry (name, is_baseline, symbol, "
        "training_horizon_seconds, paper_active, lifecycle_state, created_at) "
        "VALUES ('legacy_btc_h60', 0, 'BTCUSDT', 60, 1, 'active', "
        "'2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "UPDATE model_registry SET cutover_state = NULL, "
        "cutover_decided_by = NULL, cutover_decided_at = NULL "
        "WHERE name = 'legacy_btc_h60'"
    )
    init_schema(conn)  # re-run migrations
    row = conn.execute(
        "SELECT cutover_state, cutover_decided_by, cutover_decided_at "
        "FROM model_registry WHERE name = 'legacy_btc_h60'"
    ).fetchone()
    assert row["cutover_state"] == "cutover"
    assert row["cutover_decided_by"] == "auto"
    assert row["cutover_decided_at"] == "2026-01-01T00:00:00Z"


# ── 2. register_model behavior ──────────────────────────────────


def test_register_model_non_baseline_lands_scheduled(db_conn, tmp_path):
    """New non-baseline row → paper_active=0, state=scheduled, +24h."""
    from scripts.register_model import register_model

    out = _seed_artifact(
        tmp_path, name="h60_btc_v3_90d_20260519",
        symbol="BTCUSDT", horizon=60, train_days=90,
        train_window_end="2026-05-19",
    )
    name = register_model(
        conn=db_conn, artifact_dir=str(out),
        evaluation_windows=[300, 900, 1800],
    )
    row = db_conn.execute(
        "SELECT paper_active, cutover_state, cutover_scheduled_at, "
        "cutover_decided_by, cutover_decided_at FROM model_registry "
        "WHERE name = ?", (name,),
    ).fetchone()

    assert row["paper_active"] == 0
    assert row["cutover_state"] == "scheduled"
    assert row["cutover_decided_by"] == "auto"
    assert row["cutover_decided_at"] is not None
    # Scheduled time ≈ now + 24h
    scheduled = datetime.fromisoformat(row["cutover_scheduled_at"].replace("Z", "+00:00"))
    expected = datetime.now(timezone.utc) + timedelta(hours=24)
    delta = abs((scheduled - expected).total_seconds())
    assert delta < 60, f"scheduled_at {scheduled} drifted >60s from {expected}"


def test_register_model_respects_custom_delay(db_conn, tmp_path):
    """--cutover-delay-hours flows through to cutover_scheduled_at."""
    from scripts.register_model import register_model

    out = _seed_artifact(
        tmp_path, name="h300_eth_v3_180d_20260519",
        symbol="ETHUSDT", horizon=300, train_days=180,
        train_window_end="2026-05-19",
    )
    register_model(
        conn=db_conn, artifact_dir=str(out),
        evaluation_windows=[300],
        cutover_delay_hours=0.5,
    )
    row = db_conn.execute(
        "SELECT cutover_scheduled_at FROM model_registry "
        "WHERE name = 'h300_eth_v3_180d_20260519'"
    ).fetchone()
    scheduled = datetime.fromisoformat(row["cutover_scheduled_at"].replace("Z", "+00:00"))
    expected = datetime.now(timezone.utc) + timedelta(hours=0.5)
    assert abs((scheduled - expected).total_seconds()) < 60


def test_register_model_baseline_stays_active(db_conn, tmp_path):
    """Pre-existing baseline retains paper_active=1 + cutover_state='cutover'."""
    from scripts.register_model import register_model

    name = "h300_btc_v3_330d_20260426"
    db_conn.execute(
        "INSERT INTO model_registry (name, is_baseline, symbol, "
        "training_horizon_seconds, paper_active, lifecycle_state) "
        "VALUES (?, 1, 'BTCUSDT', 300, 1, 'active')",
        (name,),
    )
    db_conn.commit()
    out = _seed_artifact(
        tmp_path, name=name, symbol="BTCUSDT", horizon=300,
        train_days=330, train_window_end="2026-04-26",
    )
    register_model(
        conn=db_conn, artifact_dir=str(out), evaluation_windows=[300],
    )
    row = db_conn.execute(
        "SELECT is_baseline, paper_active, cutover_state "
        "FROM model_registry WHERE name = ?", (name,),
    ).fetchone()
    assert row["is_baseline"] == 1
    assert row["paper_active"] == 1
    # Backfill set cutover_state='cutover' for the legacy row.
    assert row["cutover_state"] == "cutover"


# ── 3. Scheduler tick ───────────────────────────────────────────


def _insert_scheduled(conn, *, name: str, scheduled_at: str,
                     state: str = "scheduled", is_baseline: int = 0):
    conn.execute(
        "INSERT INTO model_registry (name, is_baseline, symbol, "
        "training_horizon_seconds, paper_active, lifecycle_state, "
        "cutover_state, cutover_scheduled_at, cutover_decided_by, "
        "cutover_decided_at) "
        "VALUES (?, ?, 'BTCUSDT', 60, 0, 'active', ?, ?, 'auto', ?)",
        (name, is_baseline, state, scheduled_at, scheduled_at),
    )
    conn.commit()


class _ConnWrap:
    """Wrap a sqlite3 connection so the scheduler's conn.close() is a no-op."""
    def __init__(self, c):
        self._c = c
    def __getattr__(self, k):
        return getattr(self._c, k)
    def close(self):
        return None


def test_scheduler_tick_promotes_due_rows(db_conn, monkeypatch):
    """Tick promotes scheduled rows whose time is in the past."""
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    _insert_scheduled(db_conn, name="due_model", scheduled_at=past)
    _insert_scheduled(db_conn, name="future_model", scheduled_at=future)

    import dashboard_api.main as dm
    monkeypatch.setattr(dm, "_cutover_get_db", lambda: _ConnWrap(db_conn))

    promoted = dm._run_cutover_scheduler_tick()
    assert "due_model" in promoted
    assert "future_model" not in promoted

    due_row = db_conn.execute(
        "SELECT paper_active, cutover_state, cutover_decided_by "
        "FROM model_registry WHERE name = 'due_model'"
    ).fetchone()
    assert due_row["paper_active"] == 1
    assert due_row["cutover_state"] == "cutover"
    assert due_row["cutover_decided_by"] == "auto"

    future_row = db_conn.execute(
        "SELECT paper_active, cutover_state "
        "FROM model_registry WHERE name = 'future_model'"
    ).fetchone()
    assert future_row["paper_active"] == 0
    assert future_row["cutover_state"] == "scheduled"


def test_scheduler_tick_skips_baseline_rows(db_conn, monkeypatch):
    """Baseline rows are not flipped even if scheduled is set."""
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _insert_scheduled(db_conn, name="baseline_due", scheduled_at=past, is_baseline=1)

    import dashboard_api.main as dm
    monkeypatch.setattr(dm, "_cutover_get_db", lambda: _ConnWrap(db_conn))

    promoted = dm._run_cutover_scheduler_tick()
    assert "baseline_due" not in promoted


# ── 4. Endpoints (logic via helpers) ────────────────────────────


def test_endpoint_404_on_unknown(db_conn):
    from fastapi import HTTPException
    from dashboard_api.routers.models_admin import _apply_cutover_now

    with pytest.raises(HTTPException) as exc:
        _apply_cutover_now(db_conn, "nope", "tester")
    assert exc.value.status_code == 404


def test_endpoint_rejects_baseline(db_conn):
    from fastapi import HTTPException
    from dashboard_api.routers.models_admin import _apply_cutover_now

    db_conn.execute(
        "INSERT INTO model_registry (name, is_baseline, symbol, "
        "training_horizon_seconds, paper_active, lifecycle_state, "
        "cutover_state) "
        "VALUES ('h300_btc_base', 1, 'BTCUSDT', 300, 1, 'active', 'cutover')"
    )
    db_conn.commit()
    with pytest.raises(HTTPException) as exc:
        _apply_cutover_now(db_conn, "h300_btc_base", "tester")
    assert exc.value.status_code == 409


def test_endpoint_reschedule_rejects_past(db_conn):
    from fastapi import HTTPException
    from dashboard_api.routers.models_admin import _apply_reschedule

    _insert_scheduled(
        db_conn, name="r1",
        scheduled_at=(datetime.now(timezone.utc) + timedelta(hours=1))
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    past = (datetime.now(timezone.utc) - timedelta(hours=1)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    with pytest.raises(HTTPException) as exc:
        _apply_reschedule(db_conn, "r1", past, "tester")
    assert exc.value.status_code == 400


def test_endpoint_cutover_now_flips_state(db_conn):
    from dashboard_api.routers.models_admin import _apply_cutover_now

    _insert_scheduled(
        db_conn, name="ready",
        scheduled_at=(datetime.now(timezone.utc) + timedelta(hours=12))
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    out = _apply_cutover_now(db_conn, "ready", "alice")
    db_conn.commit()
    assert out["cutover_state"] == "cutover"
    row = db_conn.execute(
        "SELECT paper_active, cutover_state, cutover_decided_by "
        "FROM model_registry WHERE name = 'ready'"
    ).fetchone()
    assert row["paper_active"] == 1
    assert row["cutover_state"] == "cutover"
    assert row["cutover_decided_by"] == "alice"


def test_endpoint_skip_keeps_paper_inactive(db_conn):
    from dashboard_api.routers.models_admin import _apply_skip_cutover

    _insert_scheduled(
        db_conn, name="skip_me",
        scheduled_at=(datetime.now(timezone.utc) + timedelta(hours=12))
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    out = _apply_skip_cutover(db_conn, "skip_me", "bob")
    db_conn.commit()
    assert out["cutover_state"] == "skipped"
    row = db_conn.execute(
        "SELECT paper_active, cutover_state FROM model_registry "
        "WHERE name = 'skip_me'"
    ).fetchone()
    assert row["paper_active"] == 0
    assert row["cutover_state"] == "skipped"


def test_bulk_cutover_via_endpoint(tmp_path, monkeypatch):
    """End-to-end: bulk endpoint succeeds, per-row results returned."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    db_path = str(tmp_path / "bulk.db")
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)
    conn = open_database(db_path)
    init_schema(conn)
    for n in ("a", "b", "c"):
        _insert_scheduled(
            conn, name=n,
            scheduled_at=(datetime.now(timezone.utc) + timedelta(hours=1))
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    # Also insert a baseline row to verify mixed-success behavior.
    conn.execute(
        "INSERT INTO model_registry (name, is_baseline, symbol, "
        "training_horizon_seconds, paper_active, lifecycle_state, "
        "cutover_state) "
        "VALUES ('baseline_x', 1, 'BTCUSDT', 60, 1, 'active', 'cutover')"
    )
    conn.commit()
    conn.close()

    import dashboard_api.routers.models_admin as ma

    def _test_get_conn():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(ma, "_get_conn", _test_get_conn)

    app = FastAPI()
    app.include_router(ma.router, prefix="/api")
    client = TestClient(app)
    resp = client.post("/api/models/bulk-cutover", json={
        "names": ["a", "b", "c", "baseline_x", "unknown"],
        "action": "cutover",
        "decided_by": "carol",
    })
    assert resp.status_code == 200
    body = resp.json()
    results = {r["name"]: r for r in body["results"]}
    assert results["a"]["ok"] is True
    assert results["b"]["ok"] is True
    assert results["c"]["ok"] is True
    assert results["baseline_x"]["ok"] is False
    assert results["baseline_x"]["status_code"] == 409
    assert results["unknown"]["ok"] is False
    assert results["unknown"]["status_code"] == 404

    # Verify side effects persisted
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    for n in ("a", "b", "c"):
        row = c.execute(
            "SELECT paper_active, cutover_state FROM model_registry WHERE name = ?",
            (n,)
        ).fetchone()
        assert row["paper_active"] == 1
        assert row["cutover_state"] == "cutover"


def test_list_models_exposes_cutover_columns(tmp_path, monkeypatch):
    """GET /api/models/list returns the four cutover_* fields per row."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    db_path = str(tmp_path / "list.db")
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)
    conn = open_database(db_path)
    init_schema(conn)
    _insert_scheduled(
        conn, name="m1",
        scheduled_at=(datetime.now(timezone.utc) + timedelta(hours=1))
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    conn.close()

    import dashboard_api.routers.models_admin as ma

    def _test_get_conn():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(ma, "_get_conn", _test_get_conn)

    app = FastAPI()
    app.include_router(ma.router, prefix="/api")
    client = TestClient(app)
    resp = client.get("/api/models/list")
    assert resp.status_code == 200
    row = next(r for r in resp.json()["models"] if r["name"] == "m1")
    assert "cutover_scheduled_at" in row
    assert "cutover_state" in row
    assert "cutover_decided_by" in row
    assert "cutover_decided_at" in row
    assert row["cutover_state"] == "scheduled"
