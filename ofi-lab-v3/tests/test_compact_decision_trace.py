"""Test compact decision trace integration on prediction rows."""


def make_trader(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )


def test_executed_decision_records_compact_trace(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    pid = t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=1_700_000_000_000,
        ts_model_ran_ms=1_700_000_000_000,
        pred_proba_raw=0.58, pred_proba_calibrated=0.55,
        pred_direction="up", above_threshold=True, warmup=False,
        platform="paper", price_at_open=60_000.0,
    )
    t._record_compact_decision(
        prediction_id=pid,
        outcome="executed", reason=None,
        ev_estimate=0.018, kelly_fraction_capped=0.25,
        final_size_usdc=10.0, order_type="maker",
    )
    row = t._db_conn.execute(
        "SELECT decision_outcome, decision_reason, ev_estimate,"
        " kelly_fraction_capped, final_size_usdc, order_type"
        " FROM predictions WHERE prediction_id = ?", (pid,)
    ).fetchone()
    assert row["decision_outcome"] == "executed"
    assert row["decision_reason"] is None
    assert row["order_type"] == "maker"


def test_suppressed_decision_records_reason(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    pid = t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=1_700_000_000_000,
        ts_model_ran_ms=1_700_000_000_000,
        pred_proba_raw=0.51, pred_proba_calibrated=0.50,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper", price_at_open=60_000.0,
    )
    t._record_compact_decision(
        prediction_id=pid,
        outcome="suppressed", reason="below_confidence",
        ev_estimate=None, kelly_fraction_capped=0.0,
        final_size_usdc=0.0, order_type="skipped",
    )
    row = t._db_conn.execute(
        "SELECT decision_outcome, decision_reason FROM predictions"
        " WHERE prediction_id = ?", (pid,)
    ).fetchone()
    assert row["decision_outcome"] == "suppressed"
    assert row["decision_reason"] == "below_confidence"
