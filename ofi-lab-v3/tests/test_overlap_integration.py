"""T36: record_overlap_for_boundary integration test."""


def test_record_overlap_for_boundary_writes_row(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    from trading.overlap_writer import ModelScore

    t = PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )
    boundary = 1_700_000_000_000
    t.record_overlap_for_boundary(
        ts_contract_open_ms=boundary,
        symbol="BTCUSDT",
        market_window_seconds=900,
        scores=[
            ModelScore("900s_btc_v3_20260315", "up", 0.55, weight=1.0),
            ModelScore("60s_btc_v3_20260315", "up", 0.52, weight=0.5),
        ],
    )
    rows = t._db_conn.execute(
        "SELECT consensus, weighted_confidence FROM model_overlap"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["consensus"] == 1
