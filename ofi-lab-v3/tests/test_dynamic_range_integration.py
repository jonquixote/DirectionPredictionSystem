"""Test dynamic XRP range integration (Task C8)."""
import pytest


@pytest.fixture
def db_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    from storage.db import open_database, init_schema
    db = open_database(str(tmp_path / "v3.db"))
    init_schema(db)
    return db


def test_range_computer_imports_correctly():
    """Verify range_computer module imports and can be called."""
    from data.range_computer import compute_mid_price_range
    assert callable(compute_mid_price_range), "compute_mid_price_range should be callable"


def make_trader_with_xrp(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    trader = PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        testnet=True,
        confidence_threshold=0.55,
    )
    # Add XRP to tracked symbols for this test
    trader._tracked_symbols = ["BTCUSDT", "XRPUSDT"]
    return trader


def test_paper_trader_has_refresh_price_ranges(tmp_path, monkeypatch, tiny_model_path):
    """Verify PaperTrader has refresh_price_ranges method."""
    trader = make_trader_with_xrp(tmp_path, monkeypatch, tiny_model_path)

    # Should have the method
    assert hasattr(trader, "refresh_price_ranges"), "PaperTrader missing refresh_price_ranges method"
    assert callable(trader.refresh_price_ranges), "refresh_price_ranges is not callable"
