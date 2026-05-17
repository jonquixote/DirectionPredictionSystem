"""Integration tests for CalibratorRegistry wiring into paper_trader."""

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from storage.calibrator_registry import CalibratorRegistry, MIN_N_FOR_FIT
from storage.db import open_database, init_schema

_BASE = 1_735_689_600_000

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


def seed_predictions_for_calibration(
    db: sqlite3.Connection,
    model_name: str,
    n: int,
) -> None:
    """Seed resolved predictions with outcome labels for calibration fitting."""
    global _pred_counter
    for i in range(n):
        raw_proba = 0.45 + (i % 100) / 100.0
        raw_proba = min(1.0, max(0.0, raw_proba))

        outcome_is_up = 1 if raw_proba > 0.55 else 0
        direction = "up" if raw_proba > 0.5 else "down"
        prediction_correct = 1 if (direction == "up" and outcome_is_up) or (direction == "down" and not outcome_is_up) else 0

        _pred_counter += 1
        ts_contract_open = _BASE + _pred_counter * 300_000

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
                f"calib_pred_{_pred_counter}",
                model_name,
                "test_hash",
                "feature_hash",
                "v1",
                300,
                1,
                "policy_hash",
                1,
                "",
                "BTCUSDT",
                300,
                "evaluation",
                ts_contract_open,
                ts_contract_open,
                ts_contract_open + 300000,
                raw_proba,
                raw_proba,
                direction,
                1 if raw_proba > 0.5 else 0,
                "paper",
                1,
                ts_contract_open + 300000,
                prediction_correct,
            ),
        )
    db.commit()


class TestCalibratorWiring:
    """Integration tests for paper_trader ↔ CalibratorRegistry interaction."""

    def test_calibrate_returns_hash_for_fitted_model(self, db, registry):
        """After refit, calibrate returns non-None hash."""
        seed_predictions_for_calibration(db, "h300_btc", MIN_N_FOR_FIT)
        registry.refit("h300_btc")

        raw = 0.55
        cal, hash_val = registry.calibrate("h300_btc", raw)

        assert hash_val is not None, "fitted model should return a hash"
        assert isinstance(hash_val, str), "hash should be a string"
        assert len(hash_val) == 16, "hash should be 16 chars (truncated SHA256)"

    def test_hash_persisted_in_database(self, db, registry):
        """Fitted map hash is persisted to calibration_map table."""
        seed_predictions_for_calibration(db, "h300_btc", MIN_N_FOR_FIT)
        registry.refit("h300_btc")

        _, hash_from_calibrate = registry.calibrate("h300_btc", 0.55)

        row = db.execute(
            "SELECT map_hash FROM calibration_map WHERE model_name=?",
            ("h300_btc",),
        ).fetchone()

        assert row is not None, "calibration_map entry should exist"
        assert row[0] == hash_from_calibrate, "hash from calibrate should match DB"

    def test_calibration_map_includes_provenance_fields(self, db, registry):
        """calibration_map table has all required provenance columns."""
        seed_predictions_for_calibration(db, "h300_btc", MIN_N_FOR_FIT)
        registry.refit("h300_btc")

        row = db.execute(
            "SELECT model_name, x_json, y_json, fit_at_ms, n_obs, map_hash, fit_method "
            "FROM calibration_map WHERE model_name=?",
            ("h300_btc",),
        ).fetchone()

        assert row is not None
        model_name, x_json, y_json, fit_at_ms, n_obs, map_hash, fit_method = row

        assert model_name == "h300_btc"
        assert isinstance(x_json, str)
        assert isinstance(y_json, str)
        assert fit_at_ms > 0
        assert n_obs == MIN_N_FOR_FIT
        assert len(map_hash) == 16
        assert fit_method == "isotonic"

        x_arr = json.loads(x_json)
        y_arr = json.loads(y_json)
        assert isinstance(x_arr, list)
        assert isinstance(y_arr, list)
        assert len(x_arr) == len(y_arr)

    def test_multiple_models_isolated_in_calibration_map(self, db, registry):
        """Different models get separate entries in calibration_map."""
        seed_predictions_for_calibration(db, "h300_btc", MIN_N_FOR_FIT)
        seed_predictions_for_calibration(db, "h60_xrp", MIN_N_FOR_FIT)

        registry.refit("h300_btc")
        registry.refit("h60_xrp")

        rows = db.execute(
            "SELECT model_name, n_obs FROM calibration_map ORDER BY model_name"
        ).fetchall()

        assert len(rows) == 2
        assert rows[0][0] == "h300_btc"
        assert rows[0][1] == MIN_N_FOR_FIT
        assert rows[1][0] == "h60_xrp"
        assert rows[1][1] == MIN_N_FOR_FIT

    def test_calibration_map_updates_on_refit(self, db, registry):
        """Refitting updates the calibration_map entry."""
        seed_predictions_for_calibration(db, "h300_btc", MIN_N_FOR_FIT)
        registry.refit("h300_btc")

        first_row = db.execute(
            "SELECT map_hash, n_obs, fit_at_ms FROM calibration_map WHERE model_name=?",
            ("h300_btc",),
        ).fetchone()

        time.sleep(0.01)
        seed_predictions_for_calibration(db, "h300_btc", 50)
        registry.refit("h300_btc")

        second_row = db.execute(
            "SELECT map_hash, n_obs, fit_at_ms FROM calibration_map WHERE model_name=?",
            ("h300_btc",),
        ).fetchone()

        assert second_row[1] == first_row[1] + 50, "n_obs should reflect additional predictions"
        assert second_row[2] >= first_row[2], "fit_at_ms should be updated"

    def test_get_hash_returns_none_for_unfitted_model(self, registry):
        """get_hash returns None when model hasn't been fitted."""
        hash_val = registry.get_hash("nonexistent_model")
        assert hash_val is None

    def test_get_hash_returns_fitted_hash(self, db, registry):
        """get_hash returns the map_hash for a fitted model."""
        seed_predictions_for_calibration(db, "h300_btc", MIN_N_FOR_FIT)
        registry.refit("h300_btc")

        hash_val = registry.get_hash("h300_btc")
        assert hash_val is not None
        assert len(hash_val) == 16

    def test_calibrated_probability_bounded(self, db, registry):
        """calibrate always returns probability in [0, 1]."""
        seed_predictions_for_calibration(db, "h300_btc", MIN_N_FOR_FIT)
        registry.refit("h300_btc")

        for raw in [0.0, 0.1, 0.5, 0.9, 1.0]:
            cal, _ = registry.calibrate("h300_btc", raw)
            assert 0.0 <= cal <= 1.0, f"calibrated probability {cal} not in [0, 1]"

    def test_hash_in_prediction_row_schema(self, db):
        """calibration_map_hash column exists in predictions table."""
        columns = [col[1] for col in db.execute("PRAGMA table_info(predictions)").fetchall()]
        assert "calibration_map_hash" in columns, "predictions table should have calibration_map_hash column"

    def test_paper_trader_can_pass_hash_to_prediction_row(self, db, registry):
        """Simulate paper_trader flow: calibrate → get hash → store in prediction."""
        seed_predictions_for_calibration(db, "h300_btc", MIN_N_FOR_FIT)
        registry.refit("h300_btc")

        model_name = "h300_btc"
        raw_proba = 0.58
        pred_direction = "up"

        cal_proba, calib_hash = registry.calibrate(model_name, raw_proba)

        ts_contract_open = _BASE + 999_999 * 300_000
        db.execute(
            "INSERT INTO predictions "
            "(prediction_id, model_name, model_artifact_hash, feature_names_hash, "
            "feature_version, training_horizon_seconds, registry_load_generation, "
            "policy_config_hash, decision_policy_version, calibration_map_hash, "
            "symbol, market_window_seconds, resolution_type, "
            "ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms, "
            "pred_proba_raw, pred_proba_calibrated, pred_direction, above_threshold, "
            "platform, resolved) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "test_pred_1",
                model_name,
                "model_hash",
                "feature_hash",
                "v1",
                300,
                1,
                "policy_hash",
                1,
                calib_hash or "",
                "BTCUSDT",
                300,
                "evaluation",
                ts_contract_open,
                ts_contract_open,
                ts_contract_open + 300000,
                raw_proba,
                cal_proba,
                pred_direction,
                1,
                "paper",
                0,
            ),
        )
        db.commit()

        row = db.execute(
            "SELECT pred_proba_raw, pred_proba_calibrated, calibration_map_hash "
            "FROM predictions WHERE prediction_id=?",
            ("test_pred_1",),
        ).fetchone()

        assert row is not None
        assert row[0] == raw_proba
        assert row[1] == cal_proba
        assert row[2] == calib_hash
