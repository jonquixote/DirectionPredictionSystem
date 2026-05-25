import pytest

from storage.decay_metrics import (
    compute_brier_score, compute_calibration_error, compute_rolling_ev,
    compute_recency_weighted_ev,
)


def test_brier_zero_when_predictions_perfect():
    assert compute_brier_score([(1.0, True), (0.0, False)]) == 0.0


def test_brier_high_when_predictions_inverted():
    score = compute_brier_score([(0.9, False), (0.1, True)])
    assert score > 0.7


def test_calibration_error_zero_when_well_calibrated():
    # Predict 60% three times, observe 2/3 wins ≈ 0.667
    rows = [(0.60, True)] * 2 + [(0.60, False)]
    err = compute_calibration_error(rows, bin_width=0.1)
    # Bin centered at 0.60; predicted=0.60, actual=2/3≈0.667
    assert err < 0.10


def test_rolling_ev_simple_average():
    ev_values = [0.01, 0.02, -0.01, 0.005]
    assert abs(compute_rolling_ev(ev_values) - 0.00625) < 1e-9


def test_rolling_ev_empty_returns_zero():
    assert compute_rolling_ev([]) == 0.0


def test_recency_weighted_ev_recent_dominates():
    # 10 wins of $0.05 followed by 10 losses of -$0.05; recency
    # weighting should pull EV negative.
    ev_values = [0.05] * 10 + [-0.05] * 10
    rate = compute_recency_weighted_ev(ev_values, alpha=0.3)
    assert rate < 0.0


def test_evaluate_decay_triggers_writes_rwev_drop(tmp_path):
    """Synthesize decreasing recency_weighted_ev history; assert rwev_drop triggers."""
    import sqlite3
    from storage.decay_writer import DecayWriter
    db = sqlite3.connect(str(tmp_path / "test.db"))
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE decay_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, model_name TEXT, symbol TEXT,
            market_window_seconds INTEGER, window_size INTEGER,
            rolling_ev REAL, recency_weighted_ev REAL,
            rolling_win_rate REAL, brier_score REAL,
            calibration_error REAL, sample_count INTEGER
        );
        CREATE TABLE decay_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, model_name TEXT, symbol TEXT,
            market_window_seconds INTEGER, eval_type TEXT,
            metric_value REAL, threshold REAL, triggered INTEGER,
            detail_json TEXT
        );
    """)
    # Seed 7 days of decay_metrics history with rwev declining 0.10 → 0.02
    base_rwev = 0.10
    for d in range(7, 0, -1):
        for hour in range(0, 24, 4):
            db.execute(
                "INSERT INTO decay_metrics(ts, model_name, symbol, market_window_seconds,"
                " window_size, rolling_ev, recency_weighted_ev, rolling_win_rate,"
                " brier_score, calibration_error, sample_count)"
                " VALUES (datetime('now', ?, ?), 'h60_btc_v3_89d_test', 'BTCUSDT', 300,"
                "         100, ?, ?, 0.55, 0.24, 0.05, 100)",
                (f'-{d} day', f'+{hour} hour', base_rwev, base_rwev),
            )
            base_rwev = max(0.02, base_rwev - 0.005)
    db.commit()
    # Build a minimal MetricWriters and call _evaluate_decay_triggers directly
    from trading.metric_writers import MetricWriters
    mw = MetricWriters.__new__(MetricWriters)  # bypass __init__
    mw._db_conn = db
    mw._decay_writer_cache = DecayWriter(db)
    # Current rwev far below 7d max
    mw._evaluate_decay_triggers(
        model_name='h60_btc_v3_89d_test', symbol='BTCUSDT',
        market_window_seconds=300,
        current_rwev=0.01, current_brier=0.25, current_calib_err=0.05,
        sample_count=100,
    )
    db.commit()
    rows = db.execute(
        "SELECT eval_type, triggered FROM decay_evaluations WHERE model_name='h60_btc_v3_89d_test'"
    ).fetchall()
    eval_types_triggered = {(r["eval_type"], r["triggered"]) for r in rows}
    assert ("rwev_drop", 1) in eval_types_triggered, f"rwev_drop should trigger; got {eval_types_triggered}"


def test_evaluate_decay_triggers_skips_when_n_below_30():
    """Assert no evaluation rows written if sample_count < 30."""
    import sqlite3
    from storage.decay_writer import DecayWriter
    from trading.metric_writers import MetricWriters
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE decay_metrics (id INTEGER PRIMARY KEY, ts TEXT);
        CREATE TABLE decay_evaluations (id INTEGER PRIMARY KEY, ts TEXT, model_name TEXT, symbol TEXT,
            market_window_seconds INTEGER, eval_type TEXT, metric_value REAL, threshold REAL,
            triggered INTEGER, detail_json TEXT);
    """)
    mw = MetricWriters.__new__(MetricWriters)
    mw._db_conn = db
    mw._decay_writer_cache = DecayWriter(db)
    mw._evaluate_decay_triggers(
        model_name='m', symbol='BTCUSDT', market_window_seconds=300,
        current_rwev=0.01, current_brier=0.25, current_calib_err=0.05,
        sample_count=5,
    )
    n = db.execute("SELECT COUNT(*) FROM decay_evaluations").fetchone()[0]
    assert n == 0
