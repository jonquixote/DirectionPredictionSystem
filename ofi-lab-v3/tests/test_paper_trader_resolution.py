"""Resolution loop with multi-window pending entries.

Uses a fake feature computer that returns deterministic close prices.
Native rows must update calibration_outcomes; evaluation rows must not.
"""
import asyncio


class FakeFeatureComputer:
    def __init__(self):
        self._prices = {}

    def set_price(self, symbol, ts_ms, price):
        self._prices[(symbol, ts_ms)] = price

    def price_at(self, symbol, ts_ms):
        return self._prices[(symbol, ts_ms)]


def make_trader(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    t = PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )
    t.feature_computer = FakeFeatureComputer()
    return t


def test_native_resolution_writes_calibration_outcome(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    boundary = 1_700_000_000_000
    t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=boundary, ts_model_ran_ms=boundary,
        pred_proba_raw=0.55, pred_proba_calibrated=0.55,
        pred_direction="up", above_threshold=True, warmup=False,
        platform="paper", price_at_open=60_000.0,
    )
    # Provide close prices for all windows that may ripen
    for w in (300, 900, 1800, 3600):
        t.feature_computer.set_price("BTCUSDT", boundary + w * 1000, 60_100.0)
    # Run loop with now_ms past native (900s) and 300s eval
    asyncio.new_event_loop().run_until_complete(
        t._check_prediction_resolutions_v3(now_ms=boundary + 901_000)
    )
    cal = t._db_conn.execute(
        "SELECT count(*) AS n FROM calibration_outcomes"
    ).fetchone()
    assert cal["n"] == 1
    pred = t._db_conn.execute(
        "SELECT contract_result, prediction_correct, market_window_seconds"
        " FROM predictions WHERE resolved=1"
    ).fetchall()
    native = [r for r in pred if r["market_window_seconds"] == 900]
    assert native[0]["contract_result"] == "up"
    assert native[0]["prediction_correct"] == 1


def test_evaluation_resolution_does_not_write_calibration(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    boundary = 1_700_000_000_000
    t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=boundary, ts_model_ran_ms=boundary,
        pred_proba_raw=0.55, pred_proba_calibrated=0.55,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper", price_at_open=60_000.0,
    )
    # Only the 300s eval ripens
    t.feature_computer.set_price("BTCUSDT", boundary + 300_000, 60_050.0)
    asyncio.new_event_loop().run_until_complete(
        t._check_prediction_resolutions_v3(now_ms=boundary + 301_000)
    )
    cal = t._db_conn.execute(
        "SELECT count(*) AS n FROM calibration_outcomes"
    ).fetchone()
    assert cal["n"] == 0
    eval_row = t._db_conn.execute(
        "SELECT resolved, contract_result FROM predictions"
        " WHERE market_window_seconds=300 AND resolution_type='evaluation'"
    ).fetchone()
    assert eval_row["resolved"] == 1
    assert eval_row["contract_result"] == "up"
