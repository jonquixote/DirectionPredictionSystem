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


def test_evaluate_decay_triggers_per_fleet_baseline_only(tmp_path):
    """Baseline uses peers in same fleet_version only; older fleet is NOT included."""
    import sqlite3
    from storage.decay_writer import DecayWriter
    from trading.metric_writers import MetricWriters

    db = sqlite3.connect(str(tmp_path / "test.db"))
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE model_registry (
            name TEXT PRIMARY KEY,
            fleet_version TEXT
        );
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
    # Old fleet model — strong rwev=0.30 (would contaminate baseline if joined cross-fleet)
    db.execute("INSERT INTO model_registry(name, fleet_version) VALUES (?, ?)",
               ("old_btc_v3_89d_20260427", "2026-04-27"))
    db.execute("INSERT INTO model_registry(name, fleet_version) VALUES (?, ?)",
               ("new_btc_v3_89d_20260506", "2026-05-06"))
    # 80 hours of new fleet history at modest rwev = 0.05; sample_count = 100 each
    for h in range(80, 0, -1):
        db.execute(
            "INSERT INTO decay_metrics(ts, model_name, symbol, market_window_seconds,"
            " window_size, rolling_ev, recency_weighted_ev, rolling_win_rate,"
            " brier_score, calibration_error, sample_count)"
            " VALUES (datetime('now', ?), 'new_btc_v3_89d_20260506', 'BTCUSDT', 300,"
            "         100, 0.05, 0.05, 0.52, 0.247, 0.05, 100)",
            (f"-{h} hour",),
        )
    # Old fleet model also has 80h of strong rwev=0.30 history — should NOT influence baseline
    for h in range(80, 0, -1):
        db.execute(
            "INSERT INTO decay_metrics(ts, model_name, symbol, market_window_seconds,"
            " window_size, rolling_ev, recency_weighted_ev, rolling_win_rate,"
            " brier_score, calibration_error, sample_count)"
            " VALUES (datetime('now', ?), 'old_btc_v3_89d_20260427', 'BTCUSDT', 300,"
            "         100, 0.30, 0.30, 0.65, 0.20, 0.05, 100)",
            (f"-{h} hour",),
        )
    db.commit()
    mw = MetricWriters.__new__(MetricWriters)
    mw._db_conn = db
    mw._decay_writer_cache = DecayWriter(db)
    # Current new-fleet rwev=0.05 vs baseline that SHOULD use only new-fleet peers (~0.05).
    # If cross-fleet contamination existed, baseline would be ~0.18 and triggered=1.
    mw._evaluate_decay_triggers(
        model_name="new_btc_v3_89d_20260506", symbol="BTCUSDT",
        market_window_seconds=300,
        current_rwev=0.05, current_brier=0.247, current_calib_err=0.05,
        sample_count=100,
    )
    db.commit()
    rwev_rows = db.execute(
        "SELECT triggered, threshold FROM decay_evaluations"
        " WHERE model_name='new_btc_v3_89d_20260506' AND eval_type='rwev_drop'"
    ).fetchall()
    # Either no row (grace) or threshold reflects new-fleet peers only.
    if rwev_rows:
        # Threshold should be ~ (0.05 p75 - 0.02) = 0.03, far from old fleet's 0.30.
        thr = rwev_rows[0]["threshold"]
        assert thr < 0.10, f"baseline threshold {thr} too high — old fleet contamination"
        # And current_rwev=0.05 > threshold=0.03 so should NOT trigger.
        assert rwev_rows[0]["triggered"] == 0


def test_evaluate_decay_triggers_grace_when_fleet_lt_72h(tmp_path):
    """Fleet with < 72h of history: rwev_drop / brier_rise should NOT fire (skipped)."""
    import sqlite3
    from storage.decay_writer import DecayWriter
    from trading.metric_writers import MetricWriters

    db = sqlite3.connect(str(tmp_path / "test.db"))
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE model_registry (name TEXT PRIMARY KEY, fleet_version TEXT);
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
    db.execute("INSERT INTO model_registry(name, fleet_version) VALUES (?, ?)",
               ("new_btc_v3_89d_20260506", "2026-05-06"))
    # Only 24h of history — below 72h grace window
    for h in range(24, 0, -1):
        db.execute(
            "INSERT INTO decay_metrics(ts, model_name, symbol, market_window_seconds,"
            " window_size, rolling_ev, recency_weighted_ev, rolling_win_rate,"
            " brier_score, calibration_error, sample_count)"
            " VALUES (datetime('now', ?), 'new_btc_v3_89d_20260506', 'BTCUSDT', 300,"
            "         100, 0.10, 0.10, 0.55, 0.24, 0.06, 100)",
            (f"-{h} hour",),
        )
    db.commit()
    mw = MetricWriters.__new__(MetricWriters)
    mw._db_conn = db
    mw._decay_writer_cache = DecayWriter(db)
    mw._evaluate_decay_triggers(
        model_name="new_btc_v3_89d_20260506", symbol="BTCUSDT",
        market_window_seconds=300,
        current_rwev=0.01, current_brier=0.30, current_calib_err=0.05,
        sample_count=100,
    )
    db.commit()
    rwev_n = db.execute(
        "SELECT COUNT(*) FROM decay_evaluations"
        " WHERE model_name='new_btc_v3_89d_20260506' AND eval_type='rwev_drop'"
    ).fetchone()[0]
    brier_n = db.execute(
        "SELECT COUNT(*) FROM decay_evaluations"
        " WHERE model_name='new_btc_v3_89d_20260506' AND eval_type='brier_rise'"
    ).fetchone()[0]
    assert rwev_n == 0, "rwev_drop must be skipped during grace window"
    assert brier_n == 0, "brier_rise must be skipped during grace window"
    # calibration_drift is absolute (no grace) — should still write
    calib_n = db.execute(
        "SELECT COUNT(*) FROM decay_evaluations"
        " WHERE model_name='new_btc_v3_89d_20260506' AND eval_type='calibration_drift'"
    ).fetchone()[0]
    assert calib_n == 1
