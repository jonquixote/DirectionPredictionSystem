"""Test wiring of refresh_decay_metrics scheduling every 4 boundaries (Task C4)."""
import pytest


def make_trader(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        testnet=True,
        confidence_threshold=0.55,
    )


def test_refresh_decay_metrics_method_exists(tmp_path, monkeypatch, tiny_model_path):
    """Verify refresh_decay_metrics method exists."""
    trader = make_trader(tmp_path, monkeypatch, tiny_model_path)
    assert hasattr(trader, "refresh_decay_metrics"), "PaperTrader missing refresh_decay_metrics method"
    assert callable(trader.refresh_decay_metrics), "refresh_decay_metrics is not callable"


def test_boundary_count_and_interval_configured(tmp_path, monkeypatch, tiny_model_path):
    """Verify _boundary_count and _decay_refresh_interval are initialized."""
    trader = make_trader(tmp_path, monkeypatch, tiny_model_path)
    assert hasattr(trader, "_boundary_count"), "PaperTrader missing _boundary_count"
    assert hasattr(trader, "_decay_refresh_interval"), "PaperTrader missing _decay_refresh_interval"
    assert trader._decay_refresh_interval == 4, f"_decay_refresh_interval should be 4, got {trader._decay_refresh_interval}"
