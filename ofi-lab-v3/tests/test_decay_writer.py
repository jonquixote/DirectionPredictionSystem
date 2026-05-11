from storage.db import open_database, init_schema
from storage.decay_writer import DecayWriter


def test_write_decay_snapshot_persists_row(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    w = DecayWriter(conn)
    w.write_snapshot(
        model_name="900s_btc_v3_20260315",
        symbol="BTCUSDT",
        market_window_seconds=900,
        window_size=100,
        rolling_ev=0.018,
        recency_weighted_ev=0.022,
        rolling_win_rate=0.55,
        brier_score=0.21,
        calibration_error=0.03,
        sample_count=100,
    )
    row = conn.execute("SELECT * FROM decay_metrics").fetchone()
    assert row["model_name"] == "900s_btc_v3_20260315"
    assert abs(row["rolling_ev"] - 0.018) < 1e-9
    assert abs(row["recency_weighted_ev"] - 0.022) < 1e-9
    assert row["sample_count"] == 100


def test_write_evaluation_persists_row(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    w = DecayWriter(conn)
    w.write_evaluation(
        model_name="900s_btc_v3_20260315",
        symbol="BTCUSDT",
        market_window_seconds=900,
        eval_type="psi",
        metric_value=0.18,
        threshold=0.10,
        triggered=True,
        detail={"reference_period": "30d"},
    )
    row = conn.execute("SELECT * FROM decay_evaluations").fetchone()
    assert row["eval_type"] == "psi"
    assert row["triggered"] == 1
    import json
    assert json.loads(row["detail_json"])["reference_period"] == "30d"
