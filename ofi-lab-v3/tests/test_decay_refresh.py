"""T29: refresh_decay_metrics integration test."""


def test_refresh_decay_metrics_writes_snapshot_per_model(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    t = PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )
    # Insert 10 evaluation paper_trade rows with PnL values
    # First insert matching prediction rows (FK reference)
    conn = t._db_conn
    for i in range(10):
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
                f"p{i}", "900s_btc_v3_20260315", "a"*64,
                "f"*64, "v3", 900,
                0, "c"*64, 0, "d"*64,
                "BTCUSDT", 900, "evaluation",
                i*1000, i*1000, i*1000+900_000,
                0.55, 0.55, "up", 1, 0, "paper",
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
                f"t{i}", f"p{i}", "900s_btc_v3_20260315", "a"*64, "c"*64,
                0, "d"*64, 0, "v3", 900, "BTCUSDT", 900, "evaluation",
                i*1000, i*1000, i*1000+900_000, 0.55, 0.55, "up",
                0.55, 10.0, 0, "paper", "executed",
                0.05 if i % 2 == 0 else -0.04,  # alternating PnL
                1 if i % 2 == 0 else 0,
                1,
            ),
        )

    t.refresh_decay_metrics(window_size=10)

    rows = conn.execute(
        "SELECT * FROM decay_metrics WHERE model_name='900s_btc_v3_20260315'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["sample_count"] == 10
    assert rows[0]["rolling_ev"] is not None
    assert rows[0]["rolling_win_rate"] == 0.5  # 5/10
