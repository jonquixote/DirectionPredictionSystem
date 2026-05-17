"""Tests for registry-driven Kalshi dispatch gating (Steering 10c)."""


def make_trader_two_models(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={
            "900s_btc_v3_20260315": tiny_model_path,
            "60s_btc_v3_20260315":  tiny_model_path,
        },
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )


def test_only_dispatch_enabled_models_dispatch(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader_two_models(tmp_path, monkeypatch, tiny_model_path)
    # Default flags: baseline kalshi_dispatch_enabled=True, others False
    assert t.kalshi_dispatch_eligible(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        market_window_seconds=900,
    ) is True
    assert t.kalshi_dispatch_eligible(
        model_name="60s_btc_v3_20260315", symbol="BTCUSDT",
        market_window_seconds=60,
    ) is False


def test_dispatch_only_on_model_horizon(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader_two_models(tmp_path, monkeypatch, tiny_model_path)
    # Even baseline rejects evaluation-window dispatch
    assert t.kalshi_dispatch_eligible(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        market_window_seconds=300,
    ) is False
    assert t.kalshi_dispatch_eligible(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        market_window_seconds=900,
    ) is True


def test_dispatch_rejects_unknown_model(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader_two_models(tmp_path, monkeypatch, tiny_model_path)
    assert t.kalshi_dispatch_eligible(
        model_name="ghost", symbol="BTCUSDT", market_window_seconds=900,
    ) is False
