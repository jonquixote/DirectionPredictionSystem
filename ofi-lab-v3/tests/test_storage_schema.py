import sqlite3
import pytest

from storage.db import open_database, init_schema


EXPECTED_TABLES = {
    "predictions",
    "paper_trades",
    "decision_traces",
    "calibration_outcomes",
    "registry_audit",
    "policy_audit",
}

EXPECTED_INDEXES_INCLUDE = {
    "idx_pred_idempotent",
    "idx_pred_native_for_decay",
    "idx_trace_pid",
    "idx_cal_native",
    "idx_audit_generation",
    "idx_policy_version",
}


def test_init_schema_creates_all_tables(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert EXPECTED_TABLES.issubset(names), f"missing: {EXPECTED_TABLES - names}"


def test_init_schema_creates_critical_indexes(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    names = {r["name"] for r in rows}
    missing = EXPECTED_INDEXES_INCLUDE - names
    assert not missing, f"missing indexes: {missing}"


def test_init_schema_is_idempotent(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    init_schema(conn)  # second call must not raise
    rows = conn.execute(
        "SELECT count(*) AS n FROM sqlite_master WHERE type='table'"
    ).fetchone()
    assert rows["n"] >= len(EXPECTED_TABLES)


def test_resolution_type_check_constraint_enforced(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO predictions ("
            " prediction_id, model_name, model_artifact_hash, feature_names_hash,"
            " feature_version, training_horizon_seconds, registry_load_generation,"
            " policy_config_hash, decision_policy_version, calibration_map_hash,"
            " symbol, market_window_seconds, resolution_type,"
            " ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms,"
            " pred_proba_raw, pred_proba_calibrated, pred_direction, above_threshold,"
            " platform"
            ") VALUES ("
            " 'p1','m','h','f','v3',900,0,'ph',0,'ch',"
            " 'BTCUSDT',900,'BOGUS',1,2,3,0.5,0.5,'up',0,'paper'"
            ")"
        )
