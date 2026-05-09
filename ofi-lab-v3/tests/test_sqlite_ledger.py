import pytest

from storage.db import open_database, init_schema
from storage.policy_snapshot import PolicySnapshot
from storage.provenance import ProvenanceEnvelope
from storage.registry_state import RegistryState
from storage.sqlite_ledger import SQLiteLedger
from storage.window_planner import plan_resolution_rows


@pytest.fixture
def ledger(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    RegistryState(conn).bootstrap_if_empty()
    PolicySnapshot(conn).capture({"confidence_threshold": 0.55})
    return SQLiteLedger(conn), conn


def _envelope(**overrides):
    base = dict(
        model_name="900s_btc_v3_20260315",
        model_artifact_hash="a"*64,
        feature_names_hash="b"*64,
        feature_version="v3",
        training_horizon_seconds=900,
        train_window_start="2025-04-01",
        train_window_end="2026-03-15",
        train_cutoff="2026-03-15",
        registry_load_generation=0,
        policy_config_hash="c"*64,
        decision_policy_version=0,
        calibration_map_hash="d"*64,
        platform="paper",
    )
    base.update(overrides)
    return ProvenanceEnvelope(**base)


def test_log_prediction_inserts_one_native_plus_evaluations(ledger):
    led, conn = ledger
    boundary = 1_700_000_000_000
    rows = plan_resolution_rows(boundary, 900, [300, 900, 1800, 3600])
    pid = led.log_prediction_set(
        envelope=_envelope(),
        symbol="BTCUSDT",
        ts_model_ran_ms=boundary - 1000,
        ts_contract_open_ms=boundary,
        rows=rows,
        pred_proba_raw=0.54,
        pred_proba_calibrated=0.51,
        pred_direction="up",
        above_threshold=False,
        warmup=False,
        platform="paper",
        p_market=0.50,
        utc_hour=12, day_of_week=2, is_weekend=0,
        relative_spread=0.00012,
    )
    inserted = conn.execute("SELECT * FROM predictions").fetchall()
    assert len(inserted) == 4
    by_window = {r["market_window_seconds"]: r for r in inserted}
    assert by_window[900]["resolution_type"] == "native"
    for w in (300, 1800, 3600):
        assert by_window[w]["resolution_type"] == "evaluation"
    # All rows share the same group prediction_id prefix and base
    # provenance.
    base_ids = {r["prediction_id"].rsplit("_", 1)[0] for r in inserted}
    assert len(base_ids) == 1
    assert pid == sorted(r["prediction_id"] for r in inserted
                          if r["resolution_type"] == "native")[0]


def test_idempotent_index_blocks_double_score_at_same_generation(ledger):
    import sqlite3
    led, conn = ledger
    boundary = 1_700_000_000_000
    rows = plan_resolution_rows(boundary, 900, [900])
    led.log_prediction_set(
        envelope=_envelope(), symbol="BTCUSDT",
        ts_model_ran_ms=boundary, ts_contract_open_ms=boundary,
        rows=rows, pred_proba_raw=0.5, pred_proba_calibrated=0.5,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper",
    )
    with pytest.raises(sqlite3.IntegrityError):
        led.log_prediction_set(
            envelope=_envelope(), symbol="BTCUSDT",
            ts_model_ran_ms=boundary, ts_contract_open_ms=boundary,
            rows=rows, pred_proba_raw=0.6, pred_proba_calibrated=0.6,
            pred_direction="up", above_threshold=False, warmup=False,
            platform="paper",
        )


def test_new_generation_can_rescore_same_boundary(ledger):
    led, conn = ledger
    boundary = 1_700_000_000_000
    rows = plan_resolution_rows(boundary, 900, [900])
    led.log_prediction_set(
        envelope=_envelope(registry_load_generation=0),
        symbol="BTCUSDT",
        ts_model_ran_ms=boundary, ts_contract_open_ms=boundary,
        rows=rows, pred_proba_raw=0.5, pred_proba_calibrated=0.5,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper",
    )
    led.log_prediction_set(
        envelope=_envelope(registry_load_generation=1),
        symbol="BTCUSDT",
        ts_model_ran_ms=boundary, ts_contract_open_ms=boundary,
        rows=rows, pred_proba_raw=0.6, pred_proba_calibrated=0.6,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper",
    )
    n = conn.execute("SELECT count(*) AS n FROM predictions").fetchone()["n"]
    assert n == 2  # one row each generation


def test_warmup_flag_is_persisted(ledger):
    led, conn = ledger
    rows = plan_resolution_rows(1_000_000, 900, [900])
    led.log_prediction_set(
        envelope=_envelope(), symbol="BTCUSDT",
        ts_model_ran_ms=1_000_000, ts_contract_open_ms=1_000_000,
        rows=rows, pred_proba_raw=0.5, pred_proba_calibrated=0.5,
        pred_direction="up", above_threshold=False, warmup=True,
        platform="paper",
    )
    row = conn.execute("SELECT warmup FROM predictions").fetchone()
    assert row["warmup"] == 1


def test_log_paper_trade_links_to_prediction(ledger):
    led, conn = ledger
    rows = plan_resolution_rows(1_700_000_000_000, 900, [900])
    pid = led.log_prediction_set(
        envelope=_envelope(), symbol="BTCUSDT",
        ts_model_ran_ms=1_700_000_000_000,
        ts_contract_open_ms=1_700_000_000_000,
        rows=rows, pred_proba_raw=0.55, pred_proba_calibrated=0.53,
        pred_direction="up", above_threshold=True, warmup=False,
        platform="paper",
    )
    tid = led.log_paper_trade(
        prediction_id=pid,
        envelope=_envelope(),
        symbol="BTCUSDT",
        market_window_seconds=900,
        resolution_type="native",
        ts_model_ran_ms=1_700_000_000_000,
        ts_contract_open_ms=1_700_000_000_000,
        ts_resolve_at_ms=1_700_000_000_000 + 900_000,
        pred_proba_raw=0.55,
        pred_proba_calibrated=0.53,
        pred_direction="up",
        confidence_threshold_used=0.52,
        simulated_stake_usdc=10.0,
        decision_outcome="executed",
        decision_reason=None,
        ev_estimate=0.018,
        kelly_fraction_capped=0.25,
        final_size_usdc=10.0,
        order_type="maker",
        warmup=False,
        platform="paper",
    )
    row = conn.execute(
        "SELECT prediction_id, decision_outcome, ev_estimate"
        " FROM paper_trades WHERE trade_id = ?", (tid,)
    ).fetchone()
    assert row["prediction_id"] == pid
    assert row["decision_outcome"] == "executed"
    assert abs(row["ev_estimate"] - 0.018) < 1e-9


def test_log_compact_decision_writes_inline_fields(ledger):
    led, conn = ledger
    rows = plan_resolution_rows(1_000_000, 900, [900])
    pid = led.log_prediction_set(
        envelope=_envelope(), symbol="BTCUSDT",
        ts_model_ran_ms=1_000_000, ts_contract_open_ms=1_000_000,
        rows=rows, pred_proba_raw=0.55, pred_proba_calibrated=0.53,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper",
    )
    led.log_compact_decision(
        prediction_id=pid,
        decision_outcome="suppressed",
        decision_reason="below_confidence",
        ev_estimate=-0.001,
        kelly_fraction_capped=0.0,
        final_size_usdc=0.0,
        order_type="skipped",
    )
    row = conn.execute(
        "SELECT decision_outcome, decision_reason FROM predictions"
        " WHERE prediction_id = ?", (pid,)
    ).fetchone()
    assert row["decision_outcome"] == "suppressed"
    assert row["decision_reason"] == "below_confidence"
