"""Tests for preflight_v3.py end-to-end validation."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import pytest
import subprocess
import sys


@pytest.fixture
def populated_db(tmp_path):
    """Create a fully populated test database for preflight validation."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))

    # Apply schema
    schema = (Path(__file__).parent.parent / "storage" / "schema.sql").read_text()
    conn.executescript(schema)

    # Create model_registry table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_registry (
            name TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            is_baseline INTEGER DEFAULT 0
        )
    """)

    # Insert baseline and non-baseline models
    conn.execute("INSERT INTO model_registry (name, symbol, is_baseline) VALUES ('h300_btc', 'BTCUSDT', 1)")
    conn.execute("INSERT INTO model_registry (name, symbol, is_baseline) VALUES ('h60_eth', 'ETHUSDT', 0)")
    conn.commit()

    # Seed regime_features_latest first
    ts_updated_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    conn.execute(
        "INSERT INTO regime_features_latest ("
        "  symbol, vwap_dev_30s_std, mlofi_60s_std, relative_spread, "
        "  spread_5m_pct, mlofi_momentum, vwap_2m_deviation, ts_updated_ms, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "BTCUSDT", 0.12, 0.18, 0.005, 0.02, 0.55, 0.25,
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
            "ETHUSDT", 0.10, 0.15, 0.004, 0.015, 0.60, 0.28,
            ts_updated_ms,
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        )
    )

    # Seed predictions for backfill to work
    ts_now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    ts_start_ms = ts_now_ms - 30 * 24 * 60 * 60 * 1000

    # h300_btc predictions (250 samples for calibration)
    for i in range(250):
        ts_ms = ts_start_ms + i * 300_000
        proba = 0.4 if i < 75 else (0.6 if i < 175 else 0.8)
        outcome = 1 if i % 3 < 1 else 0 if proba == 0.4 else (1 if i % 3 < 2 else 0)
        conn.execute("""
            INSERT INTO predictions (
                prediction_id, model_name, model_artifact_hash, feature_names_hash,
                feature_version, training_horizon_seconds, registry_load_generation,
                policy_config_hash, decision_policy_version, calibration_map_hash,
                symbol, market_window_seconds, resolution_type,
                ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms,
                pred_proba_raw, pred_proba_calibrated, pred_direction,
                above_threshold, warmup, trade_eligible, platform,
                resolved, prediction_correct, contract_result
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"pred_btc_{i}", "h300_btc", "hash1", "hash2", "v1", 300, 1,
            "policy1", 1, "calib1", "BTCUSDT", 300, "evaluation",
            ts_ms, ts_ms, ts_ms + 300_000,
            proba, proba, "up",
            1, 0, 1, "paper",
            1, outcome, "up" if outcome else "down"
        ))

    # h60_eth predictions (fewer samples for regime thresholds)
    for i in range(100):
        ts_ms = ts_start_ms + i * 300_000
        conn.execute("""
            INSERT INTO predictions (
                prediction_id, model_name, model_artifact_hash, feature_names_hash,
                feature_version, training_horizon_seconds, registry_load_generation,
                policy_config_hash, decision_policy_version, calibration_map_hash,
                symbol, market_window_seconds, resolution_type,
                ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms,
                pred_proba_raw, pred_proba_calibrated, pred_direction,
                above_threshold, warmup, trade_eligible, platform,
                resolved, prediction_correct, contract_result
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"pred_eth_{i}", "h60_eth", "hash1", "hash2", "v1", 60, 1,
            "policy1", 1, "calib1", "ETHUSDT", 60, "evaluation",
            ts_ms, ts_ms, ts_ms + 60_000,
            0.5, 0.5, "up",
            1, 0, 1, "paper",
            1, 1 if i % 2 == 0 else 0, "up" if i % 2 == 0 else "down"
        ))

    conn.commit()
    conn.close()

    return str(db_path)


def test_preflight_end_to_end_populated_db(populated_db):
    """Test preflight on fully populated database (should pass)."""
    result = subprocess.run(
        [sys.executable, "-m", "scripts.preflight_v3", "--db", populated_db],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent
    )

    assert result.returncode == 0, f"preflight_v3 failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    # Verify success message
    assert "SUCCESS" in result.stdout or "All preflight checks passed" in result.stdout


def test_preflight_empty_db_warns(tmp_path):
    """Test preflight on empty database (should pass with warnings)."""
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))

    # Apply schema but no data
    schema = (Path(__file__).parent.parent / "storage" / "schema.sql").read_text()
    conn.executescript(schema)
    conn.close()

    result = subprocess.run(
        [sys.executable, "-m", "scripts.preflight_v3", "--db", str(db_path)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent
    )
    assert result.returncode == 0
    assert "WARN" in result.stdout


def test_preflight_skip_backfill_flag(populated_db):
    """Test preflight with --skip-backfill flag."""
    result = subprocess.run(
        [sys.executable, "-m", "scripts.preflight_v3", "--db", populated_db, "--skip-backfill"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent
    )

    # Should skip backfill and go straight to validation
    # May succeed or fail depending on existing data, but should attempt validation
    assert "VALIDATION" in result.stdout


def test_preflight_missing_db_fails():
    """Test preflight with non-existent database file."""
    result = subprocess.run(
        [sys.executable, "-m", "scripts.preflight_v3", "--db", "/nonexistent/path.db"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent
    )

    assert result.returncode != 0
    assert "ERROR" in result.stderr or "not found" in result.stderr.lower()


def test_backfill_scripts_exist():
    """Verify that both backfill scripts exist."""
    scripts_dir = Path(__file__).parent.parent / "scripts"
    assert (scripts_dir / "backfill_regime.py").exists()
    assert (scripts_dir / "backfill_calibration.py").exists()


def test_backfill_regime_script_has_cli():
    """Test that backfill_regime.py can be called with --help."""
    result = subprocess.run(
        [sys.executable, "-m", "scripts.backfill_regime", "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent
    )

    assert result.returncode == 0
    assert "--db" in result.stdout
    assert "--lookback-days" in result.stdout


def test_backfill_calibration_script_has_cli():
    """Test that backfill_calibration.py can be called with --help."""
    result = subprocess.run(
        [sys.executable, "-m", "scripts.backfill_calibration", "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent
    )

    assert result.returncode == 0
    assert "--db" in result.stdout
    assert "--lookback-days" in result.stdout
    assert "--n-bins" in result.stdout
