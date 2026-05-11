"""Test eligibility progress indicators (Task C23)."""
import pytest


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    from storage.db import open_database, init_schema
    db_conn = open_database(str(tmp_path / "v3.db"))
    init_schema(db_conn)

    # Seed with a model that has some paper trades but not enough for live
    db_conn.execute(
        "INSERT INTO model_registry (name, is_baseline, paper_active, live_eligible) "
        "VALUES ('h60_xrp', 0, 1, 0)"
    )
    db_conn.commit()

    # Add some paper trades (not 100 yet)
    for i in range(73):
        pred_id = f"pred_{i}"
        # First insert prediction
        db_conn.execute(
            "INSERT INTO predictions ("
            "prediction_id, model_name, symbol, "
            "model_artifact_hash, feature_names_hash, feature_version, training_horizon_seconds, "
            "policy_config_hash, decision_policy_version, calibration_map_hash, "
            "market_window_seconds, resolution_type, "
            "ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms, "
            "pred_proba_raw, pred_proba_calibrated, pred_direction, "
            "above_threshold, platform, registry_load_generation) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pred_id, "h60_xrp", "XRPUSDT",
             f"hash_{i}", f"fname_{i}", "v1", 60,
             f"policy_{i}", 1, f"cal_{i}",
             900, "native",
             i*900_000, i*900_000, i*900_000 + 900_000,
             0.55, 0.55, "up", 1, "paper", 1),
        )
        # Then insert paper trade
        db_conn.execute(
            "INSERT INTO paper_trades ("
            "trade_id, prediction_id, model_name, symbol, "
            "model_artifact_hash, policy_config_hash, decision_policy_version, "
            "calibration_map_hash, feature_version, training_horizon_seconds, "
            "registry_load_generation, market_window_seconds, "
            "ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms, "
            "pred_proba_raw, pred_proba_calibrated, pred_direction, "
            "confidence_threshold_used, resolution_type, warmup, "
            "decision_outcome, platform, resolved) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"trade_{i}", pred_id, "h60_xrp", "XRPUSDT",
             f"hash_{i}", f"policy_{i}", 1, f"cal_{i}", "v1", 60,
             1, 900,
             i*900_000, i*900_000, i*900_000 + 900_000,
             0.55, 0.55, "up", 0.55, "native", 0,
             "executed", "paper", 1),
        )
    db_conn.commit()

    return db_conn


def test_eligibility_returns_progress(seeded_db):
    """Verify EligibilityResult includes progress field."""
    from filters.live_eligibility import check_live_eligibility

    elig = check_live_eligibility(seeded_db, "h60_xrp")

    # Model should not yet be eligible
    assert not elig.eligible

    # Should have progress field with paper_trades info
    assert hasattr(elig, "progress") or "progress" in elig._asdict()

    progress = elig.progress if hasattr(elig, "progress") else elig._asdict().get("progress", {})
    assert isinstance(progress, dict)
    assert "paper_trades" in progress
    assert progress["paper_trades"]["have"] == 73
    assert progress["paper_trades"]["need"] > 0
