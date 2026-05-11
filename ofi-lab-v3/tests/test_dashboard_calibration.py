"""C18: Calibration API integration tests.

Tests /api/calibration/{model_name} endpoint.
Uses a minimal FastAPI TestClient with a seeded in-memory DB.
"""
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def seeded_db_with_calibration(tmp_path, monkeypatch):
    """Create a test DB with calibration tables and seed data."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Create calibration tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calibration_bins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_name TEXT NOT NULL,
            bin_lo REAL NOT NULL,
            bin_hi REAL NOT NULL,
            observed_freq REAL NOT NULL,
            n INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cal_bins_model
            ON calibration_bins(model_name, bin_lo)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS calibration_summary (
            model_name TEXT PRIMARY KEY,
            brier REAL,
            log_loss REAL,
            n_obs INTEGER,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)

    # Seed calibration data for h300_btc
    # Histogram: 10 bins
    bins_data = [
        ("h300_btc", 0.0, 0.1, 0.02, 5),
        ("h300_btc", 0.1, 0.2, 0.05, 15),
        ("h300_btc", 0.2, 0.3, 0.08, 25),
        ("h300_btc", 0.3, 0.4, 0.12, 40),
        ("h300_btc", 0.4, 0.5, 0.15, 50),
        ("h300_btc", 0.5, 0.6, 0.18, 60),
        ("h300_btc", 0.6, 0.7, 0.20, 70),
        ("h300_btc", 0.7, 0.8, 0.25, 85),
        ("h300_btc", 0.8, 0.9, 0.30, 100),
        ("h300_btc", 0.9, 1.0, 0.40, 135),
    ]

    for model_name, bin_lo, bin_hi, obs_freq, n in bins_data:
        conn.execute(
            "INSERT INTO calibration_bins (model_name, bin_lo, bin_hi, observed_freq, n) "
            "VALUES (?, ?, ?, ?, ?)",
            (model_name, bin_lo, bin_hi, obs_freq, n),
        )

    # Summary metrics
    conn.execute(
        "INSERT INTO calibration_summary (model_name, brier, log_loss, n_obs) "
        "VALUES (?, ?, ?, ?)",
        ("h300_btc", 0.182, 0.421, 585),
    )

    # Also add another model for coverage
    conn.execute(
        "INSERT INTO calibration_bins (model_name, bin_lo, bin_hi, observed_freq, n) "
        "VALUES (?, ?, ?, ?, ?)",
        ("h60_xrp", 0.0, 0.5, 0.10, 20),
    )
    conn.execute(
        "INSERT INTO calibration_bins (model_name, bin_lo, bin_hi, observed_freq, n) "
        "VALUES (?, ?, ?, ?, ?)",
        ("h60_xrp", 0.5, 1.0, 0.50, 100),
    )
    conn.execute(
        "INSERT INTO calibration_summary (model_name, brier, log_loss, n_obs) "
        "VALUES (?, ?, ?, ?)",
        ("h60_xrp", 0.210, 0.512, 120),
    )

    conn.commit()
    conn.close()

    # Monkey-patch get_db for routers
    import dashboard_api.routers.calibration as cal_router

    def _test_get_db():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(cal_router, "get_db", _test_get_db)

    from dashboard_api.routers.calibration import router as cal_r

    app = FastAPI()
    app.include_router(cal_r, prefix="/api")
    return TestClient(app), db_path


# ── C18 Tests ──────────────────────────────────────────────────────

def test_calibration_returns_bins_for_model(seeded_db_with_calibration):
    """Test /api/calibration/{model} returns bins + brier/log_loss."""
    c, _ = seeded_db_with_calibration
    r = c.get("/api/calibration/h300_btc")
    assert r.status_code == 200
    body = r.json()
    assert body["model_name"] == "h300_btc"
    assert "bins" in body
    assert len(body["bins"]) == 10
    # Check first bin structure
    first_bin = body["bins"][0]
    assert {"bin_lo", "bin_hi", "observed_freq", "n"} <= set(first_bin.keys())
    assert first_bin["bin_lo"] == 0.0
    assert first_bin["bin_hi"] == 0.1
    # Check summary metrics
    assert "brier" in body
    assert "log_loss" in body
    assert "n_obs" in body
    assert body["brier"] == 0.182
    assert body["log_loss"] == 0.421
    assert body["n_obs"] == 585


def test_calibration_bins_ordered_by_bin_lo(seeded_db_with_calibration):
    """Test bins are returned in ascending order by bin_lo."""
    c, _ = seeded_db_with_calibration
    r = c.get("/api/calibration/h300_btc")
    assert r.status_code == 200
    bins = r.json()["bins"]
    bin_los = [b["bin_lo"] for b in bins]
    assert bin_los == sorted(bin_los)


def test_calibration_404_for_missing_model(seeded_db_with_calibration):
    """Test /api/calibration/{model} returns 404 for unknown model."""
    c, _ = seeded_db_with_calibration
    r = c.get("/api/calibration/nonexistent_model")
    assert r.status_code == 404


def test_calibration_multiple_models(seeded_db_with_calibration):
    """Test /api/calibration works for multiple models."""
    c, _ = seeded_db_with_calibration
    r1 = c.get("/api/calibration/h300_btc")
    r2 = c.get("/api/calibration/h60_xrp")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["model_name"] == "h300_btc"
    assert r2.json()["model_name"] == "h60_xrp"
    assert len(r1.json()["bins"]) == 10
    assert len(r2.json()["bins"]) == 2
