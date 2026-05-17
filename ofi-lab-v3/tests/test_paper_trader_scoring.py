"""Direct test on the scoring helper that emits prediction rows."""

_BASE = 1_735_689_600_000


def make_trader(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )


def test_emit_prediction_rows_writes_three_evaluation_rows(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    pid = t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315",
        symbol="BTCUSDT",
        boundary_ms=_BASE,
        ts_model_ran_ms=_BASE - 500,
        pred_proba_raw=0.54, pred_proba_calibrated=0.51,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper", p_market=0.50,
        utc_hour=12, day_of_week=2, is_weekend=0,
        relative_spread=0.00012,
        price_at_open=60_000.0,
    )
    rows = t._db_conn.execute(
        "SELECT market_window_seconds, resolution_type, ts_resolve_at_ms"
        " FROM predictions WHERE model_name=?",
        ("900s_btc_v3_20260315",)).fetchall()
    by_w = {r["market_window_seconds"]: r for r in rows}
    for w in (300, 900, 1800):
        assert by_w[w]["resolution_type"] == "evaluation"
        assert by_w[w]["ts_resolve_at_ms"] == _BASE + w * 1000
    assert len(list(t.pending_queue.iter_all())) == 3


def test_h60_horizon_emits_three_evaluation_rows(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    t = PaperTrader(
        model_paths={"60s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )
    t._emit_prediction_rows(
        model_name="60s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=_BASE,
        ts_model_ran_ms=_BASE,
        pred_proba_raw=0.55, pred_proba_calibrated=0.55,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper", price_at_open=60_000.0,
    )
    rows = t._db_conn.execute(
        "SELECT market_window_seconds, resolution_type, ts_resolve_at_ms"
        " FROM predictions").fetchall()
    by_w = {r["market_window_seconds"]: r for r in rows}
    for w in (300, 900, 1800):
        assert by_w[w]["resolution_type"] == "evaluation"
