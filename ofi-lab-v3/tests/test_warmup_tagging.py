import config

_BASE = 1_735_689_600_000


def make_trader(tmp_path, monkeypatch, tiny_model_path, warmup_seconds=5):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    monkeypatch.setattr(config, "WARMUP_SECONDS", warmup_seconds)
    from trading.paper_trader import PaperTrader
    t = PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )
    return t


def test_in_warmup_returns_true_immediately_after_boot(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path, warmup_seconds=5)
    now_ms = t._boot_ts_ms + 1000
    assert t.is_in_warmup(now_ms) is True


def test_in_warmup_returns_false_past_window(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path, warmup_seconds=5)
    now_ms = t._boot_ts_ms + 6000
    assert t.is_in_warmup(now_ms) is False


def test_emit_prediction_rows_stamps_warmup_flag(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path, warmup_seconds=10)
    boundary = _BASE
    t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=boundary, ts_model_ran_ms=boundary,
        pred_proba_raw=0.55, pred_proba_calibrated=0.55,
        pred_direction="up", above_threshold=False,
        warmup=t.is_in_warmup(boundary),
        platform="paper", price_at_open=60_000.0,
    )
    rows = t._db_conn.execute("SELECT warmup FROM predictions").fetchall()
    assert all(r["warmup"] == 1 for r in rows)
