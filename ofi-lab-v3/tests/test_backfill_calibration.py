"""Tests for backfill_calibration.py."""
import sqlite3
from datetime import datetime, timezone
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

    # Insert test models
    conn.execute("INSERT INTO model_registry (name, symbol, is_baseline) VALUES ('h300_btc', 'BTCUSDT', 1)")
    conn.execute("INSERT INTO model_registry (name, symbol, is_baseline) VALUES ('h60_eth', 'ETHUSDT', 0)")
    conn.commit()

    # Seed predictions with known proba/outcome distribution
    ts_now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    ts_start_ms = ts_now_ms - 30 * 24 * 60 * 60 * 1000  # 30 days ago

    # Generate predictions for h300_btc with well-calibrated outcomes
    # Low proba (0.4) -> 40% win rate, Medium (0.6) -> 60%, High (0.8) -> 80%
    predictions = [
        # Low confidence tier: expect ~40% correct
        (0.40, 1 if i % 3 < 1 else 0) for i in range(75)
    ] + [
        # Medium confidence tier: expect ~60% correct
        (0.60, 1 if i % 3 < 2 else 0) for i in range(100)
    ] + [
        # High confidence tier: expect ~80% correct
        (0.80, 1 if i % 5 < 4 else 0) for i in range(75)
    ]

    for i, (proba, outcome) in enumerate(predictions):
        ts_ms = ts_start_ms + i * 30 * 60 * 1000
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
            f"pred_btc_{i}",
            "h300_btc",
            "hash1",
            "hash2",
            "v1",
            300,
            1,
            "policy1",
            1,
            "calib1",
            "BTCUSDT",
            300,
            "native",
            ts_ms,
            ts_ms,
            ts_ms + 300_000,
            proba,
            proba,
            "up",
            1,
            0,
            1,
            "paper",
            1,
            outcome,
            "up" if outcome else "down"
        ))

    # Generate predictions for h60_eth (non-baseline) with fewer samples
    # Only 50 samples -> will be skipped
    for i in range(50):
        ts_ms = ts_start_ms + i * 60 * 60 * 1000
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
            f"pred_eth_{i}",
            "h60_eth",
            "hash1",
            "hash2",
            "v1",
            60,
            1,
            "policy1",
            1,
            "calib1",
            "ETHUSDT",
            60,
            "native",
            ts_ms,
            ts_ms,
            ts_ms + 60_000,
            0.5,
            0.5,
            "up",
            1,
            0,
            1,
            "paper",
            1,
            1 if i % 2 == 0 else 0,
            "up" if i % 2 == 0 else "down"
        ))

    conn.commit()
    conn.close()

    return str(db_path)


def test_backfill_calibration_populates_summary(test_db):
    """Test that backfill_calibration.py populates calibration_summary."""
    from scripts.backfill_calibration import backfill_calibration

    backfill_calibration(test_db, lookback_days=30, n_bins=10)

    conn = sqlite3.connect(test_db)
    cursor = conn.execute("""
        SELECT model_name, brier, log_loss, n_obs
        FROM calibration_summary
    """)
    rows = cursor.fetchall()
    conn.close()

    # Only h300_btc should be populated (>= 100 samples)
    assert len(rows) == 1, f"Expected 1 model in calibration_summary, got {len(rows)}"

    model_name, brier, log_loss, n_obs = rows[0]
    assert model_name == "h300_btc"
    assert n_obs == 250, f"Expected 250 observations, got {n_obs}"
    assert 0.0 <= brier <= 0.5, f"Brier score out of reasonable range: {brier}"
    assert 0.0 <= log_loss <= 1.0, f"Log loss out of reasonable range: {log_loss}"


def test_backfill_calibration_populates_bins(test_db):
    """Test that backfill_calibration.py populates calibration_bins."""
    from scripts.backfill_calibration import backfill_calibration

    backfill_calibration(test_db, lookback_days=30, n_bins=10)

    conn = sqlite3.connect(test_db)
    cursor = conn.execute("""
        SELECT model_name, bin_lo, bin_hi, observed_freq, n
        FROM calibration_bins
        ORDER BY bin_lo
    """)
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) > 0, "No calibration bins were populated"

    # All bins should be for h300_btc
    for row in rows:
        model_name, bin_lo, bin_hi, observed_freq, n = row
        assert model_name == "h300_btc"
        assert bin_lo < bin_hi
        assert 0.0 <= observed_freq <= 1.0
        assert n > 0

    # Total observations across bins should match
    total_obs = sum(row[4] for row in rows)
    assert total_obs == 250


def test_backfill_calibration_skips_small_models(test_db):
    """Test that models with < 100 samples are skipped."""
    from scripts.backfill_calibration import backfill_calibration

    backfill_calibration(test_db, lookback_days=30, n_bins=10)

    conn = sqlite3.connect(test_db)
    cursor = conn.execute("""
        SELECT model_name FROM calibration_summary WHERE model_name = 'h60_eth'
    """)
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 0, "h60_eth should not be in calibration_summary (< 100 samples)"


def test_backfill_calibration_brier_reasonable(test_db):
    """Test that Brier score is reasonable for our synthetic well-calibrated data."""
    from scripts.backfill_calibration import backfill_calibration

    backfill_calibration(test_db, lookback_days=30, n_bins=10)

    conn = sqlite3.connect(test_db)
    cursor = conn.execute("""
        SELECT brier FROM calibration_summary WHERE model_name = 'h300_btc'
    """)
    brier = cursor.fetchone()[0]
    conn.close()

    # For well-calibrated predictions, Brier should be reasonably low (< 0.3)
    assert brier < 0.3, f"Brier score too high for well-calibrated data: {brier}"


def test_backfill_calibration_log_loss_reasonable(test_db):
    """Test that log loss is reasonable for our synthetic well-calibrated data."""
    from scripts.backfill_calibration import backfill_calibration

    backfill_calibration(test_db, lookback_days=30, n_bins=10)

    conn = sqlite3.connect(test_db)
    cursor = conn.execute("""
        SELECT log_loss FROM calibration_summary WHERE model_name = 'h300_btc'
    """)
    log_loss = cursor.fetchone()[0]
    conn.close()

    # For well-calibrated predictions, log loss should be reasonably low (< 0.7)
    assert log_loss < 0.7, f"Log loss too high for well-calibrated data: {log_loss}"


def test_backfill_calibration_idempotent(test_db):
    """Test that running backfill twice gives same result."""
    from scripts.backfill_calibration import backfill_calibration

    backfill_calibration(test_db, lookback_days=30, n_bins=10)

    conn = sqlite3.connect(test_db)
    cursor = conn.execute("""
        SELECT model_name, brier, log_loss, n_obs FROM calibration_summary
        ORDER BY model_name
    """)
    first_run = cursor.fetchall()
    conn.close()

    # Run again
    backfill_calibration(test_db, lookback_days=30, n_bins=10)

    conn = sqlite3.connect(test_db)
    cursor = conn.execute("""
        SELECT model_name, brier, log_loss, n_obs FROM calibration_summary
        ORDER BY model_name
    """)
    second_run = cursor.fetchall()
    conn.close()

    assert first_run == second_run, "Backfill is not idempotent"
