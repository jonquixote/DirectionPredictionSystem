"""Tests for GET /api/models/combos endpoint."""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def seeded_app(tmp_path, monkeypatch):
    """Mini FastAPI app with seeded model_registry for combos tests."""
    db_path = str(tmp_path / "test_combos.db")
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)

    from storage.db import open_database, init_schema
    conn = open_database(db_path)
    init_schema(conn)

    # Active model — standard 3 windows
    conn.execute(
        "INSERT INTO model_registry "
        "(name, is_baseline, symbol, training_horizon_seconds, generation, "
        "paper_active, live_eligible, evaluation_windows) "
        "VALUES ('h60_btc_v3_89d', 0, 'BTCUSDT', 60, 1, 1, 0, '[300,900,1800]')"
    )
    # Active model — different symbol
    conn.execute(
        "INSERT INTO model_registry "
        "(name, is_baseline, symbol, training_horizon_seconds, generation, "
        "paper_active, live_eligible, evaluation_windows) "
        "VALUES ('h60_eth_v3_89d', 0, 'ETHUSDT', 60, 1, 1, 0, '[300,900,1800]')"
    )
    # Inactive model — should be excluded
    conn.execute(
        "INSERT INTO model_registry "
        "(name, is_baseline, symbol, training_horizon_seconds, generation, "
        "paper_active, live_eligible, evaluation_windows) "
        "VALUES ('h60_btc_inactive', 0, 'BTCUSDT', 60, 1, 0, 0, '[300,900,1800]')"
    )
    conn.commit()
    conn.close()

    import dashboard_api.routers.models_admin as ma

    def _test_get_conn():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(ma, "_get_conn", _test_get_conn)

    from dashboard_api.routers.models_admin import router as models_router

    app = FastAPI()
    app.include_router(models_router, prefix="/api")
    return TestClient(app), db_path


@pytest.fixture
def fleet_app(tmp_path, monkeypatch):
    """Mini app seeded with 84-model fleet (21 models × 4 symbols × 3 windows)."""
    db_path = str(tmp_path / "test_fleet.db")
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)

    from storage.db import open_database, init_schema
    conn = open_database(db_path)
    init_schema(conn)

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    for sym in symbols:
        for i in range(21):
            name = f"h60_{sym.lower()[:3]}_v3_m{i:02d}"
            conn.execute(
                "INSERT INTO model_registry "
                "(name, is_baseline, symbol, training_horizon_seconds, generation, "
                "paper_active, live_eligible, evaluation_windows) "
                "VALUES (?, 0, ?, 60, 1, 1, 0, '[300,900,1800]')",
                (name, sym),
            )
    conn.commit()
    conn.close()

    import dashboard_api.routers.models_admin as ma

    def _test_get_conn():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(ma, "_get_conn", _test_get_conn)

    from dashboard_api.routers.models_admin import router as models_router

    app = FastAPI()
    app.include_router(models_router, prefix="/api")
    return TestClient(app)


# ── Tests ─────────────────────────────────────────────────────────

def test_combos_returns_active_models_only(seeded_app):
    """paper_active=0 models must not appear in combos."""
    client, _ = seeded_app
    r = client.get("/api/models/combos")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    model_names = {c["model"] for c in data}
    assert "h60_btc_inactive" not in model_names
    assert "h60_btc_v3_89d" in model_names
    assert "h60_eth_v3_89d" in model_names


def test_combos_emits_one_row_per_duration(seeded_app):
    """Each active model × 3 durations → 2 active models × 3 = 6 rows."""
    client, _ = seeded_app
    r = client.get("/api/models/combos")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert len(data) == 6
    btc_rows = [c for c in data if c["model"] == "h60_btc_v3_89d"]
    assert len(btc_rows) == 3
    assert {c["duration"] for c in btc_rows} == {300, 900, 1800}
    assert all(c["symbol"] == "BTCUSDT" for c in btc_rows)


def test_combos_84_model_fleet(fleet_app):
    """84-model fleet (21 per symbol × 4 symbols) × 3 durations = 252 combos."""
    r = fleet_app.get("/api/models/combos")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert len(data) == 252


def test_combos_response_shape(seeded_app):
    """Each combo row has model, symbol, duration keys with correct types."""
    client, _ = seeded_app
    r = client.get("/api/models/combos")
    assert r.status_code == 200, r.text
    for row in r.json()["data"]:
        assert isinstance(row["model"], str)
        assert isinstance(row["symbol"], str)
        assert isinstance(row["duration"], int)
