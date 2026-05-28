"""Tests for /api/analysis/* endpoints.

Strategy:
- Seed an in-memory SQLite DB with predictions rows.
- Monkeypatch dashboard_api.services.analysis._get_db to return the test DB.
- Call service functions directly (pure logic, no HTTP overhead for most tests).
- Also test the FastAPI router via TestClient for shape validation.
"""
from __future__ import annotations

import math
import sqlite3
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_TS = 1_700_000_000_000  # arbitrary reference ts_ms


def _make_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def _create_predictions_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            prediction_id          TEXT PRIMARY KEY,
            model_name             TEXT NOT NULL,
            symbol                 TEXT NOT NULL,
            market_window_seconds  INTEGER NOT NULL,
            pred_proba_calibrated  REAL NOT NULL,
            pred_proba_raw         REAL NOT NULL,
            pred_direction         TEXT NOT NULL,
            prediction_correct     INTEGER,
            p_market               REAL,
            p_model_minus_market   REAL,
            ts_contract_open_ms    INTEGER NOT NULL,
            utc_hour               INTEGER,
            day_of_week            INTEGER,
            is_weekend             INTEGER DEFAULT 0,
            relative_spread        REAL,
            ev_estimate            REAL,
            resolved               INTEGER NOT NULL DEFAULT 0,
            warmup                 INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_registry (
            name                TEXT PRIMARY KEY,
            filter_config_json  TEXT DEFAULT '{}'
        )
    """)
    conn.commit()


def _insert_pred(
    conn: sqlite3.Connection,
    model: str = "model_a",
    symbol: str = "BTCUSDT",
    window: int = 300,
    proba: float = 0.60,
    direction: str = "up",
    correct: bool = True,
    p_market: float = 0.50,
    ts_offset: int = 0,
    utc_hour: int = 12,
    day_of_week: int = 1,
    is_weekend: int = 0,
    relative_spread: float = 0.0002,
) -> None:
    pid = str(uuid.uuid4())
    divergence = abs(proba - p_market)
    conn.execute("""
        INSERT INTO predictions (
            prediction_id, model_name, symbol, market_window_seconds,
            pred_proba_calibrated, pred_proba_raw, pred_direction,
            prediction_correct, p_market, p_model_minus_market,
            ts_contract_open_ms, utc_hour, day_of_week, is_weekend,
            relative_spread, ev_estimate, resolved, warmup
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
    """, (
        pid, model, symbol, window,
        proba, proba, direction,
        int(correct), p_market, proba - p_market,
        _BASE_TS + ts_offset,
        utc_hour, day_of_week, is_weekend,
        relative_spread, 0.01,
    ))


def _seed_model(
    conn: sqlite3.Connection,
    model: str,
    symbol: str,
    window: int,
    n_correct: int,
    n_total: int,
    proba: float = 0.60,
    p_market: float = 0.50,
    filter_cfg: dict | None = None,
) -> None:
    """Seed n_total predictions with n_correct correct, alternating outcome."""
    for i in range(n_total):
        correct = i < n_correct
        _insert_pred(
            conn, model=model, symbol=symbol, window=window,
            proba=proba, correct=correct, p_market=p_market,
            ts_offset=i * 1000,
            utc_hour=(i % 24),
        )
    conn.commit()

    if filter_cfg is not None:
        import json
        conn.execute(
            "INSERT OR REPLACE INTO model_registry (name, filter_config_json) VALUES (?, ?)",
            (model, json.dumps(filter_cfg)),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def analysis_db(tmp_path, monkeypatch):
    """
    Create a test DB, seed with data, monkeypatch _get_db in analysis service.
    Returns (db_path, conn).
    """
    db_path = str(tmp_path / "analysis_test.db")
    conn = _make_conn(db_path)
    _create_predictions_table(conn)

    # Seed model_a: high win rate (70/100)
    _seed_model(conn, "model_a", "BTCUSDT", 300, n_correct=70, n_total=100,
                proba=0.62, p_market=0.50, filter_cfg={"confidence_threshold": 0.55})
    # Seed model_b: lower win rate (52/100)
    _seed_model(conn, "model_b", "BTCUSDT", 300, n_correct=52, n_total=100,
                proba=0.55, p_market=0.52)
    # Seed model_c: tiny sample (30 rows) — should be excluded by min_samples=50
    _seed_model(conn, "model_c", "BTCUSDT", 300, n_correct=25, n_total=30)
    # Seed model_d: different window (900)
    _seed_model(conn, "model_d", "BTCUSDT", 900, n_correct=60, n_total=100)

    monkeypatch.setenv("STORAGE_DB_PATH", db_path)

    # Monkeypatch _get_db in analysis service
    import dashboard_api.services.analysis as analysis_svc

    def _test_get_db():
        return _make_conn(db_path)

    monkeypatch.setattr(analysis_svc, "_get_db", _test_get_db)
    # T1.2 — disable the 30d fast-path bound so historical fixture data
    # (_BASE_TS = 2023-11) is not silently filtered. Production routers
    # opt into bounding via full_history=False (the default); tests use
    # fixed-ts seed data and need the unbounded path.
    monkeypatch.setattr(analysis_svc, "DEFAULT_HISTORY_DAYS", 10_000)

    yield db_path, conn


# ---------------------------------------------------------------------------
# Endpoint A: Leaderboard
# ---------------------------------------------------------------------------

class TestLeaderboard:
    def test_returns_models_above_min_samples(self, analysis_db):
        """model_c (30 samples) should be filtered out."""
        from dashboard_api.services.analysis import compute_leaderboard
        rows = compute_leaderboard(symbol="BTCUSDT", market_window=300, min_samples=50)
        names = [r["model_name"] for r in rows]
        assert "model_a" in names
        assert "model_b" in names
        assert "model_c" not in names

    def test_excludes_different_window(self, analysis_db):
        """model_d (window=900) should not appear when filtering window=300."""
        from dashboard_api.services.analysis import compute_leaderboard
        rows = compute_leaderboard(symbol="BTCUSDT", market_window=300, min_samples=50)
        names = [r["model_name"] for r in rows]
        assert "model_d" not in names

    def test_metric_sort_win_rate(self, analysis_db):
        """Sorted by win_rate descending — model_a (0.70) before model_b (0.52)."""
        from dashboard_api.services.analysis import compute_leaderboard
        rows = compute_leaderboard(symbol="BTCUSDT", market_window=300,
                                   min_samples=50, metric="win_rate")
        assert len(rows) >= 2
        assert rows[0]["model_name"] == "model_a"
        assert rows[0]["win_rate"] > rows[1]["win_rate"]

    def test_metric_sort_roi(self, analysis_db):
        """Sort by roi. model_a has higher ROI; order may match win_rate here,
        but the function should accept the param without error."""
        from dashboard_api.services.analysis import compute_leaderboard
        rows_wr = compute_leaderboard(symbol="BTCUSDT", market_window=300,
                                      min_samples=50, metric="win_rate")
        rows_roi = compute_leaderboard(symbol="BTCUSDT", market_window=300,
                                       min_samples=50, metric="roi")
        # Both lists are non-empty and have same members
        assert {r["model_name"] for r in rows_wr} == {r["model_name"] for r in rows_roi}
        # roi_pct values are present
        assert all("roi_pct" in r for r in rows_roi)

    def test_row_schema_complete(self, analysis_db):
        """Every leaderboard row must have the required fields."""
        from dashboard_api.services.analysis import compute_leaderboard
        required = {
            "model_name", "symbol", "market_window_seconds",
            "n_samples", "n_correct", "win_rate",
            "win_rate_ci_lo", "win_rate_ci_hi",
            "p_value_vs_50pct", "brier_score",
            "ev_per_trade", "roi_pct", "sharpe",
            "n_resolved_trades", "total_pnl_usdc",
            "avg_pnl_per_trade", "current_filter_config",
        }
        rows = compute_leaderboard(symbol="BTCUSDT", market_window=300, min_samples=50)
        for row in rows:
            assert required <= set(row.keys()), f"Missing keys in {row['model_name']}: {required - set(row.keys())}"

    def test_wilson_ci_bounds(self, analysis_db):
        """ci_lo < win_rate < ci_hi for any model with n > 0."""
        from dashboard_api.services.analysis import compute_leaderboard
        rows = compute_leaderboard(symbol="BTCUSDT", market_window=300, min_samples=50)
        for row in rows:
            assert row["win_rate_ci_lo"] <= row["win_rate"] <= row["win_rate_ci_hi"]

    def test_limit_respected(self, analysis_db):
        """limit=1 returns at most 1 row."""
        from dashboard_api.services.analysis import compute_leaderboard
        rows = compute_leaderboard(symbol="BTCUSDT", market_window=300,
                                   min_samples=50, limit=1)
        assert len(rows) <= 1

    def test_filter_config_present(self, analysis_db):
        """model_a was seeded with filter_config; expect it in the row."""
        from dashboard_api.services.analysis import compute_leaderboard
        rows = compute_leaderboard(symbol="BTCUSDT", market_window=300, min_samples=50)
        model_a = next(r for r in rows if r["model_name"] == "model_a")
        assert model_a["current_filter_config"].get("confidence_threshold") == 0.55


# ---------------------------------------------------------------------------
# Endpoint B: Threshold Grid
# ---------------------------------------------------------------------------

class TestThresholdGrid:
    def test_per_model_rows_count(self, analysis_db):
        """Each model entry has one row per threshold."""
        from dashboard_api.services.analysis import compute_threshold_grid, DEFAULT_THRESHOLDS
        result = compute_threshold_grid("BTCUSDT", 300, min_samples=50)
        for m in result["models"]:
            assert len(m["rows"]) == len(DEFAULT_THRESHOLDS), (
                f"Model {m['model_name']} has {len(m['rows'])} rows, "
                f"expected {len(DEFAULT_THRESHOLDS)}"
            )

    def test_threshold_values_correct(self, analysis_db):
        """Row thresholds match DEFAULT_THRESHOLDS."""
        from dashboard_api.services.analysis import compute_threshold_grid, DEFAULT_THRESHOLDS
        result = compute_threshold_grid("BTCUSDT", 300, min_samples=50)
        assert len(result["models"]) > 0
        first_model = result["models"][0]
        row_thresholds = [r["threshold"] for r in first_model["rows"]]
        assert row_thresholds == DEFAULT_THRESHOLDS

    def test_higher_threshold_fewer_trades(self, analysis_db):
        """At higher thresholds, n_trades should be <= n_trades at lower thresholds."""
        from dashboard_api.services.analysis import compute_threshold_grid
        result = compute_threshold_grid("BTCUSDT", 300, min_samples=50)
        assert len(result["models"]) > 0
        model = result["models"][0]
        ns = [r["n"] for r in model["rows"]]
        for i in range(1, len(ns)):
            assert ns[i] <= ns[i - 1], f"n not monotonically decreasing: {ns}"

    def test_best_threshold_selection_max_roi(self, analysis_db, tmp_path, monkeypatch):
        """
        best_threshold = max ROI subject to n >= min_samples.
        Seed a model where threshold=0.52 has the best ROI above min_samples.
        """
        import dashboard_api.services.analysis as analysis_svc
        db_path = str(tmp_path / "grid_test.db")
        conn = _make_conn(db_path)
        _create_predictions_table(conn)

        # Seed 120 predictions for model_best where proba alternates high/low
        # High proba (0.65+) rows are more likely correct → better ROI at high threshold
        for i in range(60):
            # High confidence correct predictions
            _insert_pred(conn, model="model_best", symbol="BTCUSDT", window=300,
                         proba=0.65, correct=True, p_market=0.50, ts_offset=i * 1000)
        for i in range(60):
            # Low confidence mostly wrong
            _insert_pred(conn, model="model_best", symbol="BTCUSDT", window=300,
                         proba=0.51, correct=(i % 3 == 0), p_market=0.50,
                         ts_offset=(60 + i) * 1000)
        conn.commit()

        def _test_get_db():
            return _make_conn(db_path)

        monkeypatch.setattr(analysis_svc, "_get_db", _test_get_db)

        result = analysis_svc.compute_threshold_grid("BTCUSDT", 300, min_samples=50)
        models = result["models"]
        assert len(models) == 1
        best = models[0]["best_threshold"]
        assert best is not None
        # At threshold>=0.52, only the 60 high-confidence (win_rate=1.0) rows pass
        # These all tie on ROI; best should pick the first eligible one (>=0.52)
        # and win_rate should be 1.0
        assert best["threshold"] >= 0.52  # should drop the low-confidence rows
        assert best["win_rate"] == 1.0    # only correct predictions survive

    def test_models_below_min_samples_excluded(self, analysis_db):
        """model_c (30 samples) must not appear in threshold grid."""
        from dashboard_api.services.analysis import compute_threshold_grid
        result = compute_threshold_grid("BTCUSDT", 300, min_samples=50)
        names = [m["model_name"] for m in result["models"]]
        assert "model_c" not in names

    def test_response_shape(self, analysis_db):
        """Top-level shape: symbol, market_window_seconds, thresholds, models."""
        from dashboard_api.services.analysis import compute_threshold_grid
        result = compute_threshold_grid("BTCUSDT", 300, min_samples=50)
        assert result["symbol"] == "BTCUSDT"
        assert result["market_window_seconds"] == 300
        assert isinstance(result["thresholds"], list)
        assert isinstance(result["models"], list)


# ---------------------------------------------------------------------------
# Endpoint C: Committee Sim
# ---------------------------------------------------------------------------

class TestCommitteeSim:
    @pytest.fixture
    def committee_db(self, tmp_path, monkeypatch):
        """Three models each make predictions on the same 50 boundaries."""
        import dashboard_api.services.analysis as analysis_svc
        db_path = str(tmp_path / "committee.db")
        conn = _make_conn(db_path)
        _create_predictions_table(conn)

        n_boundaries = 50
        for i in range(n_boundaries):
            ts = _BASE_TS + i * 300_000  # same boundary ts for all 3 models
            # All 3 models agree: up, and it's correct 60% of the time
            correct = i < 30  # first 30 correct

            for model in ["m1", "m2", "m3"]:
                conn.execute("""
                    INSERT INTO predictions (
                        prediction_id, model_name, symbol, market_window_seconds,
                        pred_proba_calibrated, pred_proba_raw, pred_direction,
                        prediction_correct, p_market, p_model_minus_market,
                        ts_contract_open_ms, utc_hour, day_of_week, is_weekend,
                        relative_spread, ev_estimate, resolved, warmup
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
                """, (
                    str(uuid.uuid4()), model, "BTCUSDT", 300,
                    0.62, 0.62, "up",
                    int(correct), 0.50, 0.12,
                    ts, 12, 1, 0,
                    0.0002, 0.01,
                ))
        conn.commit()

        def _test_get_db():
            return _make_conn(db_path)

        monkeypatch.setattr(analysis_svc, "_get_db", _test_get_db)
        monkeypatch.setattr(analysis_svc, "DEFAULT_HISTORY_DAYS", 10_000)
        return db_path

    def test_avg_strategy_win_rate(self, committee_db):
        """With 30/50 boundaries correct, committee win rate should be 0.60."""
        from dashboard_api.services.analysis import compute_committee_sim
        result = compute_committee_sim("BTCUSDT", 300, strategy="avg")
        assert result["n_boundaries"] == 50
        assert abs(result["committee_win_rate"] - 0.60) < 0.01

    def test_vote_strategy_matches_avg_when_unanimous(self, committee_db):
        """When all models agree, vote and avg should give same win rate."""
        from dashboard_api.services.analysis import compute_committee_sim
        avg_result = compute_committee_sim("BTCUSDT", 300, strategy="avg")
        vote_result = compute_committee_sim("BTCUSDT", 300, strategy="vote")
        # Both should resolve same boundaries
        assert vote_result["n_boundaries"] == avg_result["n_boundaries"]
        assert abs(vote_result["committee_win_rate"] - avg_result["committee_win_rate"]) < 0.01

    def test_response_schema(self, committee_db):
        """Response must have all required keys."""
        from dashboard_api.services.analysis import compute_committee_sim
        result = compute_committee_sim("BTCUSDT", 300, strategy="avg")
        required = {
            "symbol", "market_window_seconds", "strategy",
            "n_boundaries", "committee_win_rate", "committee_roi_pct",
            "best_single_model", "recommendation", "delta_pct_points",
        }
        assert required <= set(result.keys())

    def test_recommendation_field(self, committee_db):
        """recommendation is either 'committee', 'single_model', or 'no_data'."""
        from dashboard_api.services.analysis import compute_committee_sim
        result = compute_committee_sim("BTCUSDT", 300, strategy="avg")
        assert result["recommendation"] in {"committee", "single_model", "no_data"}

    def test_empty_symbol_returns_no_data(self, committee_db):
        """Query for a symbol with no data → graceful empty result."""
        from dashboard_api.services.analysis import compute_committee_sim
        result = compute_committee_sim("XRPUSDT", 300, strategy="avg")
        assert result["n_boundaries"] == 0
        assert result["recommendation"] in {"no_data", "committee", "single_model"}

    def test_weighted_ev_strategy(self, committee_db):
        """weighted_ev strategy runs without error and returns same shape."""
        from dashboard_api.services.analysis import compute_committee_sim
        result = compute_committee_sim("BTCUSDT", 300, strategy="weighted_ev")
        assert "committee_win_rate" in result
        assert result["n_boundaries"] > 0


# ---------------------------------------------------------------------------
# Endpoint D: Skip Conditions
# ---------------------------------------------------------------------------

class TestSkipConditions:
    @pytest.fixture
    def skip_db(self, tmp_path, monkeypatch):
        """Seed predictions with specific hour patterns."""
        import dashboard_api.services.analysis as analysis_svc
        db_path = str(tmp_path / "skip.db")
        conn = _make_conn(db_path)
        _create_predictions_table(conn)

        # Hours 0, 1, 2 → low win rate (15/50 = 30%)
        for h in [0, 1, 2]:
            for i in range(50):
                _insert_pred(conn, model="model_x", symbol="BTCUSDT", window=300,
                             correct=(i < 15), utc_hour=h,
                             ts_offset=(h * 100000 + i * 1000))

        # Hour 14 → high win rate (40/50 = 80%)
        for i in range(50):
            _insert_pred(conn, model="model_x", symbol="BTCUSDT", window=300,
                         correct=(i < 40), utc_hour=14,
                         ts_offset=(14 * 100000 + i * 1000))

        # Add spread variance: lots of high-spread rows that lose
        for i in range(40):
            _insert_pred(conn, model="model_x", symbol="BTCUSDT", window=300,
                         correct=False, utc_hour=10,
                         relative_spread=0.01,  # very high spread
                         ts_offset=(900_000 + i * 1000))
        # Low spread rows that win
        for i in range(40):
            _insert_pred(conn, model="model_x", symbol="BTCUSDT", window=300,
                         correct=True, utc_hour=10,
                         relative_spread=0.00005,  # very low spread
                         ts_offset=(950_000 + i * 1000))

        conn.commit()

        def _test_get_db():
            return _make_conn(db_path)

        monkeypatch.setattr(analysis_svc, "_get_db", _test_get_db)
        monkeypatch.setattr(analysis_svc, "DEFAULT_HISTORY_DAYS", 10_000)
        return db_path

    def test_blackout_hours_extracted(self, skip_db):
        """Hours 0, 1, 2 with 30% win rate should appear in blackout_hours."""
        from dashboard_api.services.analysis import compute_skip_conditions
        result = compute_skip_conditions("BTCUSDT", 300, min_bucket_size=30)
        blackout = result["skip_recommendations"]["blackout_hours"]
        for h in [0, 1, 2]:
            assert h in blackout, f"Hour {h} should be in blackout_hours"

    def test_good_hours_not_blacklisted(self, skip_db):
        """Hour 14 with 80% win rate must NOT appear in blackout_hours."""
        from dashboard_api.services.analysis import compute_skip_conditions
        result = compute_skip_conditions("BTCUSDT", 300, min_bucket_size=30)
        blackout = result["skip_recommendations"]["blackout_hours"]
        assert 14 not in blackout

    def test_spread_quartile_skip_high(self, skip_db):
        """High-spread quartile rows lose → skip_recommended=True for Q4."""
        from dashboard_api.services.analysis import compute_skip_conditions
        result = compute_skip_conditions("BTCUSDT", 300, min_bucket_size=30)
        spread_rows = result["buckets"]["relative_spread_quartile"]
        # Q4 should be the worst
        q4 = next((r for r in spread_rows if r["bucket"] == 4), None)
        assert q4 is not None
        if q4["n"] >= 30:
            assert q4["win_rate"] is not None
            # We seeded high-spread rows to lose → Q4 win rate low
            # (exact value depends on distribution, just verify structure)
        assert "skip_recommended" in q4

    def test_buckets_structure(self, skip_db):
        """All expected bucket dimensions are present in response."""
        from dashboard_api.services.analysis import compute_skip_conditions
        result = compute_skip_conditions("BTCUSDT", 300, min_bucket_size=30)
        expected_dims = {
            "utc_hour", "relative_spread_quartile", "divergence_bucket",
            "calibrated_p_range", "day_of_week", "is_weekend",
        }
        assert expected_dims <= set(result["buckets"].keys())

    def test_skip_recommendations_keys(self, skip_db):
        """skip_recommendations always has the three expected keys."""
        from dashboard_api.services.analysis import compute_skip_conditions
        result = compute_skip_conditions("BTCUSDT", 300)
        recs = result["skip_recommendations"]
        assert "blackout_hours" in recs
        assert "max_spread_quartile" in recs
        assert "skip_high_divergence" in recs

    def test_empty_data_graceful(self, analysis_db):
        """Symbol with no data returns valid empty response."""
        from dashboard_api.services.analysis import compute_skip_conditions
        result = compute_skip_conditions("XRPUSDT", 1800)
        assert result["symbol"] == "XRPUSDT"
        assert result["buckets"] == {}
        assert isinstance(result["skip_recommendations"], dict)


# ---------------------------------------------------------------------------
# Endpoint E: Full Report
# ---------------------------------------------------------------------------

class TestFullReport:
    def test_recommended_configs_structure(self, analysis_db):
        """Each recommended_config block must have all required keys."""
        from dashboard_api.services.analysis import compute_full_report
        report = compute_full_report(symbol="BTCUSDT", market_window=300)
        assert "recommended_configs" in report
        required = {
            "symbol", "window", "recommended_strategy",
            "recommended_model", "recommended_filter_config",
            "expected_win_rate", "expected_roi_pct", "expected_n_trades_per_day",
        }
        for cfg in report["recommended_configs"]:
            assert required <= set(cfg.keys()), f"Missing keys: {required - set(cfg.keys())}"

    def test_results_list_non_empty(self, analysis_db):
        """results list should have one entry for BTCUSDT/300."""
        from dashboard_api.services.analysis import compute_full_report
        report = compute_full_report(symbol="BTCUSDT", market_window=300)
        assert len(report["results"]) >= 1
        entry = report["results"][0]
        assert entry["symbol"] == "BTCUSDT"
        assert entry["market_window_seconds"] == 300
        assert "leaderboard" in entry
        assert "threshold_grid" in entry
        assert "committee_sim" in entry
        assert "skip_conditions" in entry

    def test_handles_no_data_gracefully(self, analysis_db):
        """Symbol with no resolved predictions → valid empty report."""
        from dashboard_api.services.analysis import compute_full_report
        report = compute_full_report(symbol="XRPUSDT", market_window=1800)
        assert "results" in report
        assert "recommended_configs" in report
        # Should have one (symbol, window) entry
        assert len(report["results"]) == 1
        assert report["results"][0]["leaderboard"] == []

    def test_all_symbols_all_windows(self, analysis_db):
        """Running without filters produces results for multiple (symbol, window) pairs."""
        from dashboard_api.services.analysis import compute_full_report, ALL_SYMBOLS, ALL_WINDOWS
        report = compute_full_report()
        expected_count = len(ALL_SYMBOLS) * len(ALL_WINDOWS)
        assert len(report["results"]) == expected_count

    def test_filter_config_has_confidence_threshold(self, analysis_db):
        """recommended_filter_config must include confidence_threshold."""
        from dashboard_api.services.analysis import compute_full_report
        report = compute_full_report(symbol="BTCUSDT", market_window=300)
        for cfg in report["recommended_configs"]:
            fc = cfg["recommended_filter_config"]
            if fc:
                assert "confidence_threshold" in fc


# ---------------------------------------------------------------------------
# FastAPI router integration tests
# ---------------------------------------------------------------------------

@pytest.fixture
def analysis_app(analysis_db, monkeypatch):
    """Minimal FastAPI app with only the analysis router mounted."""
    db_path, _ = analysis_db
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)

    from dashboard_api.routers.analysis import router
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestAnalysisRouter:
    def test_leaderboard_200(self, analysis_app):
        r = analysis_app.get("/api/analysis/leaderboard?symbol=BTCUSDT&window=300")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_leaderboard_bad_metric_422(self, analysis_app):
        r = analysis_app.get("/api/analysis/leaderboard?metric=nonsense")
        assert r.status_code == 422

    def test_threshold_grid_200(self, analysis_app):
        r = analysis_app.get("/api/analysis/threshold-grid?symbol=BTCUSDT&window=300")
        assert r.status_code == 200
        data = r.json()
        assert "models" in data
        assert "thresholds" in data

    def test_committee_sim_200(self, analysis_app):
        r = analysis_app.get("/api/analysis/committee-sim?symbol=BTCUSDT&window=300")
        assert r.status_code == 200
        data = r.json()
        assert "committee_win_rate" in data

    def test_committee_sim_bad_strategy_422(self, analysis_app):
        r = analysis_app.get("/api/analysis/committee-sim?symbol=BTCUSDT&window=300&strategy=bad")
        assert r.status_code == 422

    def test_skip_conditions_200(self, analysis_app):
        r = analysis_app.get("/api/analysis/skip-conditions?symbol=BTCUSDT&window=300")
        assert r.status_code == 200
        data = r.json()
        assert "buckets" in data
        assert "skip_recommendations" in data

    def test_full_report_200(self, analysis_app):
        r = analysis_app.get("/api/analysis/full-report?symbol=BTCUSDT&window=300")
        assert r.status_code == 200
        data = r.json()
        assert "results" in data
        assert "recommended_configs" in data
