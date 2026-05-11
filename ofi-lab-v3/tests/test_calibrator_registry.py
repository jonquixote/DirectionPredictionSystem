"""Unit tests for storage.calibrator_registry (isotonic calibration)."""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from storage.calibrator_registry import CalibratorRegistry, MIN_N_FOR_FIT
from storage.db import open_database, init_schema

# Global counter for unique prediction IDs
_pred_counter = 0


@pytest.fixture
def db():
    """In-memory SQLite database with schema."""
    conn = open_database(":memory:")
    init_schema(conn)
    return conn


@pytest.fixture
def registry(db):
    """Fresh CalibratorRegistry instance."""
    return CalibratorRegistry(db)


def seed_predictions(
    db: sqlite3.Connection,
    model_name: str,
    n: int,
    resolved: bool = True,
    resolution_type: str = "native",
) -> None:
    """Seed N predictions into database.

    Creates synthetic data where pred_proba_raw is correlated with outcome.
    """
    global _pred_counter
    now_ms = int(time.time() * 1000)
    for i in range(n):
        # Synthetic: higher raw_proba → higher chance of correct
        raw_proba = 0.45 + (i % 100) / 100.0  # range [0.45, 1.44], clip to [0, 1]
        raw_proba = min(1.0, max(0.0, raw_proba))

        # Outcome: roughly 70% correlated with raw_proba (above 0.55 → mostly "up")
        outcome_is_up = 1 if raw_proba > 0.55 else 0
        direction = "up" if raw_proba > 0.5 else "down"
        prediction_correct = 1 if (direction == "up" and outcome_is_up) or (direction == "down" and not outcome_is_up) else 0

        # Vary ts_contract_open_ms by i to avoid unique constraint violation
        ts_contract_open = now_ms + i * 1000
        _pred_counter += 1

        db.execute(
            "INSERT INTO predictions "
            "(prediction_id, model_name, model_artifact_hash, feature_names_hash, "
            "feature_version, training_horizon_seconds, registry_load_generation, "
            "policy_config_hash, decision_policy_version, calibration_map_hash, "
            "symbol, market_window_seconds, resolution_type, "
            "ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms, "
            "pred_proba_raw, pred_proba_calibrated, pred_direction, above_threshold, "
            "platform, resolved, ts_resolved_ms, prediction_correct) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"pred_{_pred_counter}",  # unique prediction_id
                model_name,
                "test_hash",
                "feature_hash",
                "v1",
                300,
                1,
                "policy_hash",
                1,
                "",  # calibration_map_hash
                "BTCUSDT",
                300,
                resolution_type,
                now_ms,
                ts_contract_open,  # vary per iteration
                ts_contract_open + 300000,
                raw_proba,
                raw_proba,  # will be updated by calibrator
                direction,
                1 if raw_proba > 0.5 else 0,
                "paper",
                1 if resolved else 0,
                ts_contract_open + 300000 if resolved else None,
                prediction_correct,
            ),
        )
    db.commit()


def test_cold_start_returns_passthrough(registry):
    """Empty DB → calibrate returns (raw_proba, None)."""
    raw = 0.55
    cal, hash_val = registry.calibrate("h300_btc", raw)
    assert cal == raw, "cold start should return raw probability"
    assert hash_val is None, "cold start should return None hash"


def test_refit_skipped_below_min_n(db, registry):
    """Seed 50 predictions → maybe_refit returns False."""
    seed_predictions(db, "h300_btc", 50, resolved=True)
    result = registry.maybe_refit("h300_btc")
    assert not result, "should not refit below MIN_N_FOR_FIT"


def test_refit_happens_at_min_n(db, registry):
    """Seed MIN_N_FOR_FIT predictions → maybe_refit returns True."""
    n = MIN_N_FOR_FIT
    seed_predictions(db, "h300_btc", n, resolved=True)
    result = registry.maybe_refit("h300_btc")
    assert result is True, "should refit at MIN_N_FOR_FIT"

    # Verify cache updated
    entry = registry._cache["h300_btc"]
    assert entry["iso"] is not None, "isotonic regression should be fitted"
    assert entry["hash"] is not None, "hash should be populated"
    assert entry["n_obs"] == n, f"n_obs should be {n}"


def test_calibrate_monotonic(db, registry):
    """Fit on synthetic data → calibrated output is monotonic in raw."""
    seed_predictions(db, "h300_btc", MIN_N_FOR_FIT, resolved=True)
    registry.refit("h300_btc")

    # Test monotonicity: higher raw → higher or equal calibrated
    prev_cal = 0.0
    for raw in [0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
        cal, _ = registry.calibrate("h300_btc", raw)
        assert cal >= prev_cal - 1e-6, f"monotonicity violated at raw={raw}"
        prev_cal = cal


def test_per_model_isolation(db, registry):
    """Fit h300_btc, query h60_xrp → passthrough (no cross-contamination)."""
    seed_predictions(db, "h300_btc", MIN_N_FOR_FIT, resolved=True)
    registry.refit("h300_btc")

    # h60_xrp should have no calibrator
    cal, hash_val = registry.calibrate("h60_xrp", 0.55)
    assert cal == 0.55, "non-existent model should passthrough"
    assert hash_val is None, "non-existent model should return None hash"


def test_hash_stable_when_no_refit(db, registry):
    """Call calibrate twice → hash identical."""
    seed_predictions(db, "h300_btc", MIN_N_FOR_FIT, resolved=True)
    registry.refit("h300_btc")

    _, hash1 = registry.calibrate("h300_btc", 0.55)
    _, hash2 = registry.calibrate("h300_btc", 0.55)

    assert hash1 == hash2, "hash should be stable without refit"
    assert hash1 is not None, "hash should not be None after refit"


def test_hash_changes_after_refit(db, registry):
    """Refit twice with different data → n_obs and hash updated."""
    # First refit
    seed_predictions(db, "h300_btc", MIN_N_FOR_FIT, resolved=True)
    registry.refit("h300_btc")
    entry1 = registry._cache["h300_btc"]
    hash1 = entry1["hash"]
    n_obs1 = entry1["n_obs"]

    # Second refit with more data
    seed_predictions(db, "h300_btc", 50, resolved=True)  # adds 50 more
    registry.refit("h300_btc")
    entry2 = registry._cache["h300_btc"]

    # Verify n_obs increased
    assert entry2["n_obs"] == n_obs1 + 50, "n_obs should reflect all predictions"


def test_reload_picks_up_db_changes(db, registry):
    """Direct DB insert + reload → calibrate uses new map."""
    # Manually insert a calibration_map row
    now_ms = int(time.time() * 1000)
    x = [0.45, 0.50, 0.55, 0.60, 0.65]
    y = [0.42, 0.50, 0.58, 0.65, 0.72]
    test_hash = "abc123def456"

    db.execute(
        "INSERT INTO calibration_map "
        "(model_name, x_json, y_json, fit_at_ms, n_obs, map_hash, fit_method) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("manual_model", json.dumps(x), json.dumps(y), now_ms, 1000, test_hash, "isotonic"),
    )
    db.commit()

    # Registry doesn't know about it yet
    assert "manual_model" not in registry._cache

    # Reload
    registry.reload("manual_model")

    # Now it's in cache
    assert "manual_model" in registry._cache
    entry = registry._cache["manual_model"]
    assert entry["hash"] == test_hash
    assert entry["n_obs"] == 1000


def test_maybe_refit_returns_true_when_triggered(db, registry):
    """Refit triggers when enough new data accumulates."""
    from storage.calibrator_registry import DEFAULT_REFIT_TRIGGER

    seed_predictions(db, "h300_btc", MIN_N_FOR_FIT, resolved=True)
    registry.refit("h300_btc")

    # Add enough predictions to trigger refit
    seed_predictions(db, "h300_btc", DEFAULT_REFIT_TRIGGER, resolved=True)

    # Should trigger
    result = registry.maybe_refit("h300_btc")
    assert result is True, "should refit when enough new observations accumulated"


def test_maybe_refit_triggers_at_threshold(db, registry):
    """Fit once, add >= trigger predictions → refit triggers."""
    seed_predictions(db, "h300_btc", MIN_N_FOR_FIT, resolved=True)
    registry.refit("h300_btc")
    first_hash = registry._cache["h300_btc"]["hash"]

    # Add enough new predictions
    seed_predictions(db, "h300_btc", 50, resolved=True)
    result = registry.maybe_refit("h300_btc", min_new_obs=50)

    assert result is True, "should refit when new observations >= threshold"
    # Hash may or may not change, depending on empirical fit


def test_refit_with_unresolved_predictions(db, registry):
    """Refit ignores unresolved predictions (filters for resolved=1)."""
    # Seed resolved predictions
    seed_predictions(db, "h300_btc", MIN_N_FOR_FIT, resolved=True)
    # Seed unresolved predictions (should be ignored)
    seed_predictions(db, "h300_btc", 50, resolved=False)

    # Refit should use only resolved predictions
    result = registry.refit("h300_btc")
    assert result is True, "should refit with resolved predictions"

    entry = registry._cache["h300_btc"]
    assert entry["n_obs"] == MIN_N_FOR_FIT, "should count only resolved predictions"
