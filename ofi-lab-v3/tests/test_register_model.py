"""Tests for scripts.register_model."""
import json
import sqlite3
from pathlib import Path

import pytest

from storage.db import open_database, init_schema


@pytest.fixture
def db_conn():
    """In-memory SQLite database with schema."""
    conn = open_database(":memory:")
    init_schema(conn)
    return conn


def test_register_model_writes_sqlite_row(tmp_path, db_conn):
    """Test that register_model correctly writes a fleet model to the registry."""
    # Create fake training output. Name layout is asserted below.
    out = tmp_path / "fleet" / "h180_xrp_v3_330d_20260426"
    out.mkdir(parents=True)
    (out / "model.lgb").write_bytes(b"FAKE_LGB_DATA")
    (out / "feature_names.json").write_text(json.dumps(["mlofi", "ofi"]))
    (out / "metrics.json").write_text(
        json.dumps(
            {
                "horizon_seconds": 180,
                "symbol": "XRPUSDT",
                "train_days": 330,
                "train_window_start": "2025-05-31",
                "train_window_end": "2026-04-26",
                "auc": 0.512,
                "brier": 0.247,
                "feature_version": "v3",
            }
        )
    )

    from scripts.register_model import register_model

    registered_name = register_model(
        conn=db_conn, artifact_dir=str(out), evaluation_windows=[300, 900, 1800]
    )

    # Bug C fix: name now includes the train_window_end as YYYYMMDD suffix.
    assert registered_name == "h180_xrp_v3_330d_20260426"

    row = db_conn.execute(
        "SELECT * FROM model_registry WHERE name=?", ("h180_xrp_v3_330d_20260426",)
    ).fetchone()

    assert row is not None
    assert row["symbol"] == "XRPUSDT"
    assert row["training_horizon_seconds"] == 180
    assert row["train_days"] == 330
    assert row["is_baseline"] == 0
    # Phase 5: new non-baseline rows land as paper_active=0 with a
    # scheduled cutover. The dashboard scheduler loop flips them to 1
    # when cutover_scheduled_at arrives.
    assert row["paper_active"] == 0
    assert row["cutover_state"] == "scheduled"
    assert row["cutover_scheduled_at"] is not None
    assert row["cutover_decided_by"] == "auto"
    assert row["live_eligible"] == 0
    assert json.loads(row["evaluation_windows"]) == [300, 900, 1800]
    assert row["artifact_hash"] is not None
    assert len(row["artifact_hash"]) == 64  # SHA256 is 64 hex chars


def test_register_model_does_not_touch_baseline(db_conn, tmp_path):
    """Test that registering a baseline model doesn't change its is_baseline flag."""
    # Insert a baseline model with the canonical (now dated) naming convention:
    # h{horizon}_{symbol}_v3_{train_days}d_{YYYYMMDD}
    baseline_name = "h300_btc_v3_330d_20260426"
    db_conn.execute(
        "INSERT INTO model_registry "
        "(name, is_baseline, symbol, training_horizon_seconds, lifecycle_state) "
        "VALUES (?, 1, 'BTCUSDT', 300, 'active')",
        (baseline_name,),
    )
    db_conn.commit()

    # Create a model artifact matching that baseline
    out = tmp_path / "fleet" / baseline_name
    out.mkdir(parents=True)
    (out / "model.lgb").write_bytes(b"FAKE_LGB_DATA_BASELINE")
    (out / "feature_names.json").write_text(json.dumps(["mlofi", "ofi"]))
    (out / "metrics.json").write_text(
        json.dumps(
            {
                "horizon_seconds": 300,
                "symbol": "BTCUSDT",
                "train_days": 330,
                "train_window_start": "2025-05-31",
                "train_window_end": "2026-04-26",
                "feature_version": "v3",
            }
        )
    )

    from scripts.register_model import register_model

    register_model(conn=db_conn, artifact_dir=str(out), evaluation_windows=[300, 900, 1800])

    # Check that is_baseline is still 1 (protection against overwriting baseline)
    row = db_conn.execute(
        "SELECT is_baseline, artifact_hash FROM model_registry WHERE name=?",
        (baseline_name,),
    ).fetchone()
    assert row["is_baseline"] == 1
    assert row["artifact_hash"] is not None  # artifact hash was updated
