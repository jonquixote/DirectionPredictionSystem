"""T26: _evaluate_paper_filters integration test."""


def make_trader(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )


def test_evaluate_paper_filters_returns_pipeline_result(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    result = t._evaluate_paper_filters(
        ctx={
            "calibrated_p": 0.60, "ev": 0.020, "spread_pct": 0.0001,
            "utc_hour": 12, "blackout_hours": [],
            "model_conflict": False, "book_age_seconds": 5.0,
            "book_has_quotes": True,
        },
    )
    assert result.passed is True
    # 3 stages run: stale_book, stale_price, paper_filter
    names = [e.name for e in result.evals]
    assert "paper_filter" in names


def test_evaluate_paper_filters_blocks_negative_ev(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    result = t._evaluate_paper_filters(
        ctx={
            "calibrated_p": 0.60, "ev": -0.005, "spread_pct": 0.0001,
            "utc_hour": 12, "blackout_hours": [],
            "model_conflict": False, "book_age_seconds": 5.0,
            "book_has_quotes": True,
        },
    )
    assert result.passed is False
    assert result.reason == "negative_ev"
