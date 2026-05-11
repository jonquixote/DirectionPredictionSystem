"""Tests for backfill_regime.py."""
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest


@pytest.fixture
def test_db(tmp_path):
    """Create a test database with schema and sample data."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))

    # Apply schema
    schema = (Path(__file__).parent.parent / "storage" / "schema.sql").read_text()
    conn.executescript(schema)

    # Create minimal model_registry table for testing
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_registry (
            name TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            is_baseline INTEGER DEFAULT 0
        )
    """)

    # Insert test symbols
    conn.execute("INSERT INTO model_registry (name, symbol) VALUES ('h300_btc', 'BTCUSDT')")
    conn.execute("INSERT INTO model_registry (name, symbol) VALUES ('h300_eth', 'ETHUSDT')")
    conn.commit()

    # Seed regime_features_latest with sample data
    ts_updated_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    conn.execute(
        "INSERT INTO regime_features_latest ("
        "  symbol, vwap_dev_30s_std, mlofi_60s_std, relative_spread, "
        "  spread_5m_pct, mlofi_momentum, vwap_2m_deviation, ts_updated_ms, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "BTCUSDT",
            0.12,
            0.18,
            0.005,
            0.02,
            0.55,
            0.25,
            ts_updated_ms,
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        )
    )

    conn.execute(
        "INSERT INTO regime_features_latest ("
        "  symbol, vwap_dev_30s_std, mlofi_60s_std, relative_spread, "
        "  spread_5m_pct, mlofi_momentum, vwap_2m_deviation, ts_updated_ms, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ETHUSDT",
            0.10,
            0.15,
            0.004,
            0.015,
            0.60,
            0.28,
            ts_updated_ms,
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        )
    )

    conn.commit()
    conn.close()

    return str(db_path)


def test_backfill_regime_populates_thresholds(test_db):
    """Test that backfill_regime.py populates regime_thresholds."""
    from scripts.backfill_regime import backfill_regime

    backfill_regime(test_db, lookback_days=14)

    conn = sqlite3.connect(test_db)
    cursor = conn.execute("SELECT symbol, thresholds_json FROM regime_thresholds")
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 2, f"Expected 2 symbols in regime_thresholds, got {len(rows)}"

    symbols = {row[0]: json.loads(row[1]) for row in rows}
    assert "BTCUSDT" in symbols
    assert "ETHUSDT" in symbols

    # Verify thresholds structure
    for sym, thresh in symbols.items():
        for sig in ["vwap_dev_30s_std", "mlofi_60s_std", "relative_spread",
                    "spread_5m_pct", "mlofi_momentum", "vwap_2m_deviation"]:
            assert sig in thresh
            assert "p25" in thresh[sig]
            assert "p50" in thresh[sig]
            assert "p75" in thresh[sig]


def test_backfill_regime_populates_latest_features(test_db):
    """Test that backfill_regime.py populates regime_features_latest."""
    from scripts.backfill_regime import backfill_regime

    backfill_regime(test_db, lookback_days=14)

    conn = sqlite3.connect(test_db)
    cursor = conn.execute("""
        SELECT symbol, vwap_dev_30s_std, mlofi_60s_std, relative_spread,
               spread_5m_pct, mlofi_momentum, vwap_2m_deviation
        FROM regime_features_latest
    """)
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 2, f"Expected 2 symbols in regime_features_latest, got {len(rows)}"

    for row in rows:
        symbol = row[0]
        assert symbol in ["BTCUSDT", "ETHUSDT"]
        # Verify all features are not null
        assert row[1] is not None, f"{symbol}: vwap_dev_30s_std is None"
        assert row[2] is not None, f"{symbol}: mlofi_60s_std is None"
        assert row[3] is not None, f"{symbol}: relative_spread is None"
        assert row[4] is not None, f"{symbol}: spread_5m_pct is None"
        assert row[5] is not None, f"{symbol}: mlofi_momentum is None"
        assert row[6] is not None, f"{symbol}: vwap_2m_deviation is None"


def test_backfill_regime_idempotent(test_db):
    """Test that running backfill twice gives same result."""
    from scripts.backfill_regime import backfill_regime

    backfill_regime(test_db, lookback_days=14)

    conn = sqlite3.connect(test_db)
    cursor = conn.execute("SELECT symbol, thresholds_json FROM regime_thresholds")
    first_run = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()

    # Run again
    backfill_regime(test_db, lookback_days=14)

    conn = sqlite3.connect(test_db)
    cursor = conn.execute("SELECT symbol, thresholds_json FROM regime_thresholds")
    second_run = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()

    assert first_run == second_run, "Backfill is not idempotent"


def test_backfill_regime_thresholds_structure(test_db):
    """Test that thresholds have the correct structure and values."""
    from scripts.backfill_regime import backfill_regime

    backfill_regime(test_db, lookback_days=14)

    conn = sqlite3.connect(test_db)
    cursor = conn.execute(
        "SELECT thresholds_json FROM regime_thresholds WHERE symbol = 'BTCUSDT'"
    )
    thresh_json = cursor.fetchone()[0]
    conn.close()

    thresh = json.loads(thresh_json)

    # Verify structure: all signals present with p25, p50, p75
    for sig in ["vwap_dev_30s_std", "mlofi_60s_std", "relative_spread",
                "spread_5m_pct", "mlofi_momentum", "vwap_2m_deviation"]:
        assert sig in thresh
        assert "p25" in thresh[sig]
        assert "p50" in thresh[sig]
        assert "p75" in thresh[sig]

    # For BTCUSDT with base value 0.12:
    # p25 = 0.096, p50 = 0.12, p75 = 0.144
    expected_p25 = 0.096
    expected_p75 = 0.144
    computed_p25 = thresh["vwap_dev_30s_std"]["p25"]
    computed_p75 = thresh["vwap_dev_30s_std"]["p75"]

    assert abs(computed_p25 - expected_p25) < 0.001
    assert abs(computed_p75 - expected_p75) < 0.001
