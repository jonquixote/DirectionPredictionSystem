"""Tests for trading.fleet_loader."""
import json
import sqlite3

import pytest

from storage.db import open_database, init_schema


@pytest.fixture
def db_conn():
    """In-memory SQLite database with schema."""
    conn = open_database(":memory:")
    init_schema(conn)
    return conn


def test_fleet_loader_returns_paper_active_models(db_conn):
    """Test that load_active_fleet returns only paper_active, non-suspended models."""
    db_conn.executescript(
        """
    INSERT INTO model_registry (name, is_baseline, paper_active, live_eligible,
        lifecycle_state, symbol, training_horizon_seconds, artifact_path,
        feature_names_path, evaluation_windows)
    VALUES
      ('h300_btc', 1, 1, 0, 'active', 'BTCUSDT', 900, '/tmp/h300/model.lgb',
       '/tmp/h300/feature_names.json', '[300,900,1800]'),
      ('h60_xrp_v3_90d', 0, 1, 0, 'active', 'XRPUSDT', 60,
       '/tmp/h60/model.lgb', '/tmp/h60/feature_names.json', '[300,900,1800]'),
      ('h180_eth_v3_90d', 0, 0, 0, 'suspended', 'ETHUSDT', 180,
       '/tmp/dead/model.lgb', '/tmp/dead/feature_names.json', '[300,900,1800]');
    """
    )

    from trading.fleet_loader import load_active_fleet

    fleet = load_active_fleet(db_conn)
    names = sorted(c["name"] for c in fleet)
    assert names == ["h300_btc", "h60_xrp_v3_90d"]
    assert "h180_eth_v3_90d" not in names  # paper_active=0 + suspended

    # Verify structure
    for c in fleet:
        assert "artifact_path" in c
        assert "evaluation_windows" in c
        assert isinstance(c["evaluation_windows"], list)
        assert c["evaluation_windows"] == [300, 900, 1800]


def test_fleet_loader_parses_evaluation_windows_json(db_conn):
    """Test that evaluation_windows is parsed from JSON to list."""
    db_conn.execute(
        "INSERT INTO model_registry (name, is_baseline, paper_active, lifecycle_state, "
        "symbol, training_horizon_seconds, evaluation_windows) "
        "VALUES (?, 0, 1, 'active', 'BTCUSDT', 300, ?)",
        ("h300_btc_test", json.dumps([300])),
    )
    db_conn.commit()

    from trading.fleet_loader import load_active_fleet

    fleet = load_active_fleet(db_conn)
    assert len(fleet) == 1
    assert fleet[0]["evaluation_windows"] == [300]


def test_fleet_loader_defaults_evaluation_windows(db_conn):
    """Test that NULL evaluation_windows defaults to [300, 900, 1800]."""
    db_conn.execute(
        "INSERT INTO model_registry (name, is_baseline, paper_active, lifecycle_state, "
        "symbol, training_horizon_seconds) "
        "VALUES (?, 0, 1, 'active', 'BTCUSDT', 300)",
        ("h300_btc_default",),
    )
    db_conn.commit()

    from trading.fleet_loader import load_active_fleet

    fleet = load_active_fleet(db_conn)
    assert len(fleet) == 1
    assert fleet[0]["evaluation_windows"] == [300, 900, 1800]
