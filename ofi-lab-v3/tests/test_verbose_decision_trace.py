import json


def make_trader(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )


def test_verbose_trace_captures_every_filter(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    pid = t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=1_700_000_000_000,
        ts_model_ran_ms=1_700_000_000_000,
        pred_proba_raw=0.51, pred_proba_calibrated=0.50,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper", price_at_open=60_000.0,
    )
    t._write_verbose_trace_for_v2_filters(
        prediction_id=pid,
        envelope=t._build_envelope("900s_btc_v3_20260315", "paper"),
        filter_inputs={
            "confidence": (0.55, 0.51, False),
            "circuit_breaker": (50.0, 5.0, True),
            "clob_divergence": (0.02, 0.005, False),
            "volatility": (0.0005, 0.0003, True),
        },
        kelly_raw=0.05, kelly_capped=0.025,
        bankroll_used=200.0, per_trade_cap_usdc=5.0,
        fee_model="polymarket", fee_amount=0.018,
        platform_gate={"kalshi_allow_list": False},
        warmup=False, consensus_data=None,
    )
    row = t._db_conn.execute(
        "SELECT * FROM decision_traces WHERE prediction_id = ?", (pid,)
    ).fetchone()
    parsed = json.loads(row["filters_json"])
    names = {f["name"] for f in parsed}
    assert names == {"confidence", "circuit_breaker", "clob_divergence", "volatility"}
    confidence = next(f for f in parsed if f["name"] == "confidence")
    assert confidence["threshold"] == 0.55
    assert confidence["input_value"] == 0.51
    assert confidence["passed"] == 0
    assert row["fee_model"] == "polymarket"
    assert row["registry_load_generation"] == 0
