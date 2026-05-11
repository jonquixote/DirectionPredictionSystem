"""C17: Regime API integration tests.

Tests /api/regime/current and /api/regime/thresholds endpoints.
Uses a minimal FastAPI TestClient with a seeded in-memory DB.
"""
import json
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def seeded_db_with_regime(tmp_path, monkeypatch):
    """Create a test DB with regime tables and seed data."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Create regime tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS regime_thresholds (
            symbol TEXT PRIMARY KEY,
            thresholds_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS regime_features_latest (
            symbol TEXT PRIMARY KEY,
            vwap_dev_30s_std REAL,
            mlofi_60s_std REAL,
            relative_spread REAL,
            spread_5m_pct REAL,
            mlofi_momentum REAL,
            vwap_2m_deviation REAL,
            ts_updated_ms INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)

    # Seed threshold data for BTCUSDT
    thresholds_btc = {
        "vwap_dev_30s_std": {"p25": 0.01, "p50": 0.02, "p75": 0.03},
        "mlofi_60s_std": {"p25": 0.02, "p50": 0.04, "p75": 0.06},
        "relative_spread": {"p25": 0.0001, "p50": 0.0002, "p75": 0.0003},
        "spread_5m_pct": {"p25": 0.001, "p50": 0.002, "p75": 0.003},
        "mlofi_momentum": {"p25": -0.01, "p50": 0.0, "p75": 0.01},
        "vwap_2m_deviation": {"p25": 0.001, "p50": 0.002, "p75": 0.003},
    }
    conn.execute(
        "INSERT INTO regime_thresholds (symbol, thresholds_json) VALUES (?, ?)",
        ("BTCUSDT", json.dumps(thresholds_btc)),
    )

    # Seed features for BTCUSDT (should bucket as "low", "low", "low" based on thresholds)
    conn.execute(
        """INSERT INTO regime_features_latest
           (symbol, vwap_dev_30s_std, mlofi_60s_std, relative_spread,
            spread_5m_pct, mlofi_momentum, vwap_2m_deviation, ts_updated_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("BTCUSDT", 0.005, 0.01, 0.00005, 0.0005, -0.005, 0.0005, 1234567890000),
    )

    # Also add XRPUSDT with thresholds for multi-symbol test
    thresholds_xrp = {
        "vwap_dev_30s_std": {"p25": 0.001, "p50": 0.002, "p75": 0.003},
        "mlofi_60s_std": {"p25": 0.001, "p50": 0.002, "p75": 0.003},
        "relative_spread": {"p25": 0.0001, "p50": 0.0002, "p75": 0.0003},
        "spread_5m_pct": {"p25": 0.0001, "p50": 0.0002, "p75": 0.0003},
        "mlofi_momentum": {"p25": -0.001, "p50": 0.0, "p75": 0.001},
        "vwap_2m_deviation": {"p25": 0.0001, "p50": 0.0002, "p75": 0.0003},
    }
    conn.execute(
        "INSERT INTO regime_thresholds (symbol, thresholds_json) VALUES (?, ?)",
        ("XRPUSDT", json.dumps(thresholds_xrp)),
    )

    conn.commit()
    conn.close()

    # Monkey-patch get_db for routers
    import dashboard_api.routers.regime as regime_router
    import dashboard_api.routers.calibration as cal_router

    def _test_get_db():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(regime_router, "get_db", _test_get_db)
    monkeypatch.setattr(cal_router, "get_db", _test_get_db)

    from dashboard_api.routers.regime import router as regime_r

    app = FastAPI()
    app.include_router(regime_r, prefix="/api")
    return TestClient(app), db_path


# ── C17 Tests ──────────────────────────────────────────────────────

def test_regime_current_returns_per_symbol_tags(seeded_db_with_regime):
    """Test /api/regime/current returns volatility/liquidity/trend tags."""
    c, _ = seeded_db_with_regime
    r = c.get("/api/regime/current?symbol=BTCUSDT")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "BTCUSDT"
    # Check that all regime axes are present and have valid values
    assert "volatility" in body
    assert "liquidity" in body
    assert "trend" in body
    assert body["volatility"] in {"low", "medium", "high", "unknown"}
    assert body["liquidity"] in {"deep", "normal", "thin", "unknown"}
    assert body["trend"] in {"trending", "choppy", "mean_reverting", "unknown"}


def test_regime_current_404_for_missing_symbol(seeded_db_with_regime):
    """Test /api/regime/current returns 404 for unknown symbol."""
    c, _ = seeded_db_with_regime
    r = c.get("/api/regime/current?symbol=NONEXISTENT")
    assert r.status_code == 404


def test_regime_thresholds_returns_quartiles(seeded_db_with_regime):
    """Test /api/regime/thresholds returns per-symbol threshold dicts."""
    c, _ = seeded_db_with_regime
    r = c.get("/api/regime/thresholds")
    assert r.status_code == 200
    body = r.json()
    assert "thresholds" in body
    assert "BTCUSDT" in body["thresholds"]
    assert "XRPUSDT" in body["thresholds"]

    btc = body["thresholds"]["BTCUSDT"]
    # Check structure: per-signal quantiles
    assert "vwap_dev_30s_std" in btc
    assert "mlofi_60s_std" in btc
    assert "relative_spread" in btc

    # Check that each has p25, p50, p75
    assert "p25" in btc["vwap_dev_30s_std"]
    assert "p50" in btc["vwap_dev_30s_std"]
    assert "p75" in btc["vwap_dev_30s_std"]
