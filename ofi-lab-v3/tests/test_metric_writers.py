"""Tests for trading/metric_writers.py.

Covers instantiation plus the two write paths (decay_metrics and
model_overlap) using a real in-memory SQLite DB with the v3 schema.
"""
from __future__ import annotations

import sqlite3

from storage.db import open_database, init_schema
from storage.registry_state import RegistryState
from trading.metric_writers import MetricWriters
from trading.overlap_writer import ModelScore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path) -> sqlite3.Connection:
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    return conn


def _make_writers(conn: sqlite3.Connection) -> MetricWriters:
    rs = RegistryState(conn)
    rs.bootstrap_if_empty(reason="test")
    return MetricWriters(db_conn=conn, registry_state=rs)


def _insert_resolved_trade(
    conn: sqlite3.Connection,
    *,
    idx: int,
    model_name: str = "test_model",
    symbol: str = "BTCUSDT",
    window: int = 900,
    net_pnl: float = 0.05,
    stake: float = 1.0,
    pred_proba: float = 0.6,
    correct: int = 1,
) -> None:
    ts = 1_700_000_000_000 + idx * 60_000
    conn.execute(
        "INSERT INTO predictions ("
        " prediction_id, model_name, model_artifact_hash,"
        " feature_names_hash, feature_version, training_horizon_seconds,"
        " registry_load_generation, policy_config_hash,"
        " decision_policy_version, calibration_map_hash,"
        " symbol, market_window_seconds, resolution_type,"
        " ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms,"
        " pred_proba_raw, pred_proba_calibrated, pred_direction,"
        " above_threshold, warmup, platform"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            f"p_{model_name}_{idx}", model_name, "a" * 64,
            "f" * 64, "v3", window,
            0, "c" * 64, 0, "d" * 64,
            symbol, window, "evaluation",
            ts, ts, ts + window * 1000,
            pred_proba, pred_proba, "up", 1, 0, "paper",
        ),
    )
    conn.execute(
        "INSERT INTO paper_trades ("
        " trade_id, prediction_id, model_name, model_artifact_hash,"
        " policy_config_hash, decision_policy_version, calibration_map_hash,"
        " registry_load_generation, feature_version,"
        " training_horizon_seconds, symbol, market_window_seconds,"
        " resolution_type, ts_model_ran_ms, ts_contract_open_ms,"
        " ts_resolve_at_ms, pred_proba_raw, pred_proba_calibrated,"
        " pred_direction, confidence_threshold_used, simulated_stake_usdc,"
        " warmup, platform, decision_outcome, net_pnl, prediction_correct,"
        " resolved"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            f"t_{model_name}_{idx}", f"p_{model_name}_{idx}", model_name, "a" * 64,
            "c" * 64, 0, "d" * 64, 0, "v3", window,
            symbol, window, "evaluation",
            ts, ts, ts + window * 1000,
            pred_proba, pred_proba, "up",
            0.55, stake, 0, "paper", "executed",
            net_pnl, correct,
            1,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_metric_writers_instantiation(tmp_path):
    """MetricWriters can be constructed with real db + registry_state."""
    conn = _make_db(tmp_path)
    mw = _make_writers(conn)
    assert mw._db_conn is conn
    # Lazy caches start None
    assert mw._decay_writer_cache is None
    assert mw._overlap_writer_cache is None
    # Properties initialise on first access
    dw = mw._decay_writer
    assert dw is not None
    ow = mw._overlap_writer
    assert ow is not None
    # Second access returns same instance (cached)
    assert mw._decay_writer is dw
    assert mw._overlap_writer is ow


def test_refresh_decay_metrics_writes_rows(tmp_path):
    """refresh_decay_metrics writes at least one row to decay_metrics for a
    seeded (model, symbol, window) triple."""
    conn = _make_db(tmp_path)
    mw = _make_writers(conn)

    for i in range(10):
        _insert_resolved_trade(
            conn, idx=i,
            model_name="test_model",
            symbol="BTCUSDT",
            window=900,
            net_pnl=0.03 if i % 2 == 0 else -0.02,
            stake=1.0,
            pred_proba=0.55 + 0.01 * i,
            correct=1 if i % 2 == 0 else 0,
        )

    before = conn.execute("SELECT COUNT(*) FROM decay_metrics").fetchone()[0]
    mw.refresh_decay_metrics(window_size=10)
    after = conn.execute("SELECT COUNT(*) FROM decay_metrics").fetchone()[0]

    assert after > before, "Expected at least one decay_metrics row to be written"

    row = conn.execute(
        "SELECT * FROM decay_metrics"
        " WHERE model_name = 'test_model'"
        "   AND symbol = 'BTCUSDT' AND market_window_seconds = 900"
    ).fetchone()
    assert row is not None
    assert row["sample_count"] == 10
    assert row["window_size"] == 10
    assert row["rolling_win_rate"] == 0.5  # 5 out of 10 correct


def test_record_overlap_for_boundary_writes_rows(tmp_path):
    """record_overlap_for_boundary delegates to OverlapWriter and writes to
    the model_overlap table."""
    conn = _make_db(tmp_path)
    mw = _make_writers(conn)

    scores = [
        ModelScore("model_a", "up", 0.62, weight=0.5),
        ModelScore("model_b", "up", 0.58, weight=0.5),
    ]
    mw.record_overlap_for_boundary(
        ts_contract_open_ms=1_700_000_000_000,
        symbol="BTCUSDT",
        market_window_seconds=900,
        scores=scores,
    )

    row = conn.execute(
        "SELECT * FROM model_overlap"
        " WHERE ts_contract_open_ms = 1700000000000"
        "   AND symbol = 'BTCUSDT' AND market_window_seconds = 900"
    ).fetchone()
    assert row is not None, "Expected a model_overlap row to be written"
    assert row["consensus"] == 1
    assert row["consensus_direction"] == "up"
    # Weighted confidence: (0.62 * 0.5 + 0.58 * 0.5) = 0.60
    assert abs(row["weighted_confidence"] - 0.60) < 1e-9
