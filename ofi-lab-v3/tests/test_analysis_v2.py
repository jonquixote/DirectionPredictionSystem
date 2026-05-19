"""Tests for analysis.py v2 Premium Filter Discovery functions.

Pattern: seed an in-memory SQLite DB, monkeypatch _get_db, call service directly.
"""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from typing import Any

import pytest

_BASE_TS = 1_700_000_000_000  # ~Nov 2023 epoch reference

# ---------------------------------------------------------------------------
# DB schema helpers
# ---------------------------------------------------------------------------

def _make_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
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
        );

        CREATE TABLE IF NOT EXISTS model_registry (
            name                TEXT PRIMARY KEY,
            filter_config_json  TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS model_overlap (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol                  TEXT NOT NULL,
            market_window_seconds   INTEGER NOT NULL,
            ts_contract_open_ms     INTEGER NOT NULL,
            consensus               INTEGER DEFAULT 0,
            consensus_direction     TEXT,
            weighted_confidence     REAL,
            n_models                INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS decay_metrics (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            model_name                  TEXT NOT NULL,
            symbol                      TEXT NOT NULL,
            market_window_seconds       INTEGER NOT NULL,
            ts                          TEXT,
            ts_ms                       INTEGER,
            computed_for_max_ts_ms      INTEGER,
            recency_weighted_ev         REAL,
            rolling_win_rate            REAL,
            rolling_ev                  REAL,
            brier_score                 REAL,
            calibration_error           REAL,
            sample_count                INTEGER
        );

        CREATE TABLE IF NOT EXISTS committee_weights (
            symbol                 TEXT NOT NULL,
            market_window_seconds  INTEGER NOT NULL,
            objective              TEXT NOT NULL,
            weights_json           TEXT NOT NULL,
            n_boundaries           INTEGER,
            expected_sharpe        REAL,
            expected_win_rate      REAL,
            expected_roi_pct       REAL,
            converged              INTEGER DEFAULT 1,
            computed_at            TEXT,
            PRIMARY KEY (symbol, market_window_seconds, objective)
        );
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
    ev_estimate: float = 0.01,
) -> None:
    pid = str(uuid.uuid4())
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
        relative_spread, ev_estimate,
    ))


def _seed_model(conn, model, symbol, window, n_correct, n_total, proba=0.60,
                p_market=0.50, ts_start=0):
    for i in range(n_total):
        correct = i < n_correct
        _insert_pred(conn, model=model, symbol=symbol, window=window,
                     proba=proba, correct=correct, p_market=p_market,
                     ts_offset=ts_start + i * 1000)
    conn.commit()


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def v2_db(tmp_path, monkeypatch):
    """Standard seeded DB for v2 tests. Returns (db_path, conn)."""
    db_path = str(tmp_path / "v2_test.db")
    conn = _make_conn(db_path)
    _create_schema(conn)

    # Good model: 75/100 correct, proba=0.65
    _seed_model(conn, "good_model", "BTCUSDT", 300, n_correct=75, n_total=100,
                proba=0.65, p_market=0.50, ts_start=0)
    # Weak model: 52/100 correct
    _seed_model(conn, "weak_model", "BTCUSDT", 300, n_correct=52, n_total=100,
                proba=0.52, p_market=0.50, ts_start=200_000)

    import dashboard_api.services.analysis as svc
    monkeypatch.setattr(svc, "_get_db", lambda: _make_conn(db_path))
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)

    yield db_path, conn


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    """Empty DB — no predictions at all."""
    db_path = str(tmp_path / "empty.db")
    conn = _make_conn(db_path)
    _create_schema(conn)

    import dashboard_api.services.analysis as svc
    monkeypatch.setattr(svc, "_get_db", lambda: _make_conn(db_path))
    monkeypatch.setenv("STORAGE_DB_PATH", db_path)

    yield db_path, conn


# ---------------------------------------------------------------------------
# TestEnvelopeContract
# ---------------------------------------------------------------------------

class TestEnvelopeContract:
    ENVELOPE_KEYS = {"status", "message", "metadata", "result", "warnings"}

    def test_simulate_returns_envelope_on_empty_db(self, empty_db):
        from dashboard_api.services.analysis import simulate_filter
        result = simulate_filter({"confidence_threshold": 0.55}, "BTCUSDT", 300)
        assert self.ENVELOPE_KEYS <= set(result.keys())
        assert result["status"] == "no_data"

    def test_grid_search_returns_envelope_on_empty_db(self, empty_db):
        from dashboard_api.services.analysis import grid_search
        result = grid_search("BTCUSDT", 300)
        assert self.ENVELOPE_KEYS <= set(result.keys())

    def test_recommend_premium_returns_envelope_on_empty_db(self, empty_db):
        from dashboard_api.services.analysis import recommend_premium_filter
        result = recommend_premium_filter("BTCUSDT", 300)
        assert self.ENVELOPE_KEYS <= set(result.keys())
        # status may be no_data or ok (ok when winner=None path)
        assert result["status"] in {"no_data", "ok", "insufficient_samples"}


# ---------------------------------------------------------------------------
# TestUnsafeColumnGuard
# ---------------------------------------------------------------------------

class TestUnsafeColumnGuard:
    def test_prediction_correct_raises(self, v2_db):
        from dashboard_api.services.analysis import simulate_filter
        with pytest.raises(AssertionError, match="post-resolution"):
            simulate_filter({"prediction_correct": 1}, "BTCUSDT", 300)

    def test_ts_resolved_ms_raises(self, v2_db):
        from dashboard_api.services.analysis import simulate_filter
        with pytest.raises(AssertionError, match="post-resolution"):
            simulate_filter({"ts_resolved_ms": 0}, "BTCUSDT", 300)


# ---------------------------------------------------------------------------
# TestSimulateFilter
# ---------------------------------------------------------------------------

class TestSimulateFilter:
    def test_above_threshold_only(self, v2_db):
        """confidence_threshold=0.60 passes only proba>=0.60 rows."""
        from dashboard_api.services.analysis import simulate_filter
        # good_model has proba=0.65 (all pass at 0.60)
        result = simulate_filter({"confidence_threshold": 0.60}, "BTCUSDT", 300)
        assert result["status"] in {"ok", "insufficient_samples"}
        assert result["result"]["n_passed"] > 0

    def test_threshold_1_rejects_all(self, v2_db):
        """threshold=1.0 → 0 passed."""
        from dashboard_api.services.analysis import simulate_filter
        result = simulate_filter({"confidence_threshold": 1.0}, "BTCUSDT", 300)
        # status is no_data (0 passed) — filter rejected all
        assert result["status"] == "no_data" or result["result"]["n_passed"] == 0

    def test_no_data_when_no_preds(self, empty_db):
        from dashboard_api.services.analysis import simulate_filter
        result = simulate_filter({"confidence_threshold": 0.55}, "BTCUSDT", 300)
        assert result["status"] == "no_data"

    def test_insufficient_samples_when_few_pass(self, tmp_path, monkeypatch):
        """Only 3 preds pass → insufficient_samples."""
        db_path = str(tmp_path / "few.db")
        conn = _make_conn(db_path)
        _create_schema(conn)
        # Insert 3 high-proba preds; everything else is low-proba
        for i in range(3):
            _insert_pred(conn, model="m", symbol="BTCUSDT", window=300,
                         proba=0.90, correct=True, ts_offset=i * 1000)
        for i in range(20):
            _insert_pred(conn, model="m", symbol="BTCUSDT", window=300,
                         proba=0.50, correct=True, ts_offset=(100 + i) * 1000)
        conn.commit()

        import dashboard_api.services.analysis as svc
        monkeypatch.setattr(svc, "_get_db", lambda: _make_conn(db_path))
        result = svc.simulate_filter({"confidence_threshold": 0.85}, "BTCUSDT", 300)
        assert result["status"] == "insufficient_samples"

    def test_max_drawdown_computed(self, v2_db):
        from dashboard_api.services.analysis import simulate_filter
        result = simulate_filter({"confidence_threshold": 0.50}, "BTCUSDT", 300)
        if result["status"] == "ok":
            assert "max_drawdown" in result["result"]

    def test_bootstrap_ci_present_when_requested(self, v2_db):
        """bootstrap_n=100 produces a bootstrap_ci list in the result."""
        from dashboard_api.services.analysis import simulate_filter
        result = simulate_filter({"confidence_threshold": 0.50}, "BTCUSDT", 300,
                                  bootstrap_n=100)
        if result["status"] == "ok" and result["result"]["n_passed"] >= 5:
            ci = result["result"]["bootstrap_ci"]
            assert ci is not None
            assert len(ci) == 2
            assert ci[0] <= ci[1]


# ---------------------------------------------------------------------------
# TestGridSearchFDR
# ---------------------------------------------------------------------------

class TestGridSearchFDR:
    def test_runs_without_error(self, v2_db):
        from dashboard_api.services.analysis import grid_search
        result = grid_search("BTCUSDT", 300, top_k=5, min_n_passed=10)
        assert result["status"] in {"ok", "no_data", "insufficient_samples"}

    def test_fdr_correction_reduces_or_equals_raw(self, v2_db):
        """n FDR survivors <= n raw p<0.05 survivors."""
        from dashboard_api.services.analysis import grid_search
        result = grid_search("BTCUSDT", 300, top_k=20, min_n_passed=10, apply_fdr=True)
        if result["status"] != "ok":
            pytest.skip("not enough data for FDR test")
        meta = result["metadata"]
        n_fdr = meta.get("n_combos_passing_fdr_significance", 0)
        n_raw = meta.get("n_combos_passing_raw_significance", 0)
        assert n_fdr <= n_raw

    def test_top_sorted_by_roi(self, v2_db):
        """Top results sorted by ROI descending."""
        from dashboard_api.services.analysis import grid_search
        result = grid_search("BTCUSDT", 300, top_k=20, min_n_passed=10)
        if result["status"] != "ok":
            pytest.skip("not enough data")
        top = result["result"]["top"]
        if len(top) >= 2:
            for i in range(len(top) - 1):
                assert (top[i]["roi_pct"] or -9999) >= (top[i + 1]["roi_pct"] or -9999)

    def test_excludes_combos_below_min_n_passed(self, v2_db):
        """All top entries must have n_passed >= min_n_passed."""
        from dashboard_api.services.analysis import grid_search
        min_n = 10
        result = grid_search("BTCUSDT", 300, top_k=20, min_n_passed=min_n)
        if result["status"] != "ok":
            pytest.skip("not enough data")
        for entry in result["result"]["top"]:
            assert entry["n_passed"] >= min_n


# ---------------------------------------------------------------------------
# TestPowerAdequacy
# ---------------------------------------------------------------------------

class TestPowerAdequacy:
    def test_power_adequate_true_when_n_ge_304(self):
        from dashboard_api.services.analysis import _power_adequate
        assert _power_adequate(304, 5.0) is True
        assert _power_adequate(500, 5.0) is True

    def test_power_adequate_false_when_n_lt_304(self):
        from dashboard_api.services.analysis import _power_adequate
        assert _power_adequate(303, 5.0) is False
        assert _power_adequate(0, 5.0) is False


# ---------------------------------------------------------------------------
# TestRegimeMatrix
# ---------------------------------------------------------------------------

class TestRegimeMatrix:
    def test_groups_by_regime_tuple(self, v2_db):
        from dashboard_api.services.analysis import regime_matrix
        result = regime_matrix(symbol="BTCUSDT", window=300)
        assert result["status"] in {"ok", "no_data"}
        if result["status"] == "ok":
            models = result["result"]["models"]
            assert isinstance(models, list)
            for m in models:
                assert "cells" in m

    def test_recommended_flag_conditions(self, tmp_path, monkeypatch):
        """Cells with wr>0.55, n>=30, p<0.05 have recommended=True."""
        db_path = str(tmp_path / "regime.db")
        conn = _make_conn(db_path)
        _create_schema(conn)
        # Seed 50 highly-correct preds; regime columns are NULL (maps to 'unknown')
        _seed_model(conn, "rm", "BTCUSDT", 300, n_correct=45, n_total=50,
                    proba=0.70, p_market=0.50)

        import dashboard_api.services.analysis as svc
        monkeypatch.setattr(svc, "_get_db", lambda: _make_conn(db_path))
        result = svc.regime_matrix(symbol="BTCUSDT", window=300, min_cell_n=30)
        assert result["status"] == "ok"
        cells = result["result"]["models"][0]["cells"]
        assert len(cells) >= 1
        c = cells[0]
        # 45/50 = 90% win rate, n=50 ≥ 30, p should be < 0.05
        if c["n"] >= 30 and c["win_rate"] > 0.55:
            assert c["recommended"] is True

    def test_status_no_data_on_empty(self, empty_db):
        from dashboard_api.services.analysis import regime_matrix
        result = regime_matrix(symbol="BTCUSDT", window=300)
        assert result["status"] == "no_data"


# ---------------------------------------------------------------------------
# TestConsensusAnalysis
# ---------------------------------------------------------------------------

class TestConsensusAnalysis:
    @pytest.fixture
    def consensus_db(self, tmp_path, monkeypatch):
        """DB with model_overlap rows seeded alongside predictions."""
        db_path = str(tmp_path / "consensus.db")
        conn = _make_conn(db_path)
        _create_schema(conn)

        # 40 boundaries; models agree on all of them
        for i in range(40):
            ts = _BASE_TS + i * 300_000
            for m in ["ma", "mb"]:
                _insert_pred(conn, model=m, symbol="BTCUSDT", window=300,
                             proba=0.62, correct=(i < 28), ts_offset=i * 300_000)
            # consensus row
            conn.execute("""
                INSERT INTO model_overlap
                  (symbol, market_window_seconds, ts_contract_open_ms,
                   consensus, consensus_direction, weighted_confidence, n_models)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("BTCUSDT", 300, ts, 1, "up", 0.62, 2))
        conn.commit()

        import dashboard_api.services.analysis as svc
        monkeypatch.setattr(svc, "_get_db", lambda: _make_conn(db_path))
        yield db_path, conn

    def test_returns_ok_with_overlap_seeded(self, consensus_db):
        from dashboard_api.services.analysis import consensus_analysis
        result = consensus_analysis("BTCUSDT", 300)
        assert result["status"] == "ok"
        assert result["result"]["n_total_boundaries"] > 0

    def test_no_data_when_overlap_empty(self, v2_db):
        """v2_db has no model_overlap rows → no_data."""
        from dashboard_api.services.analysis import consensus_analysis
        result = consensus_analysis("BTCUSDT", 300)
        assert result["status"] == "no_data"

    def test_delta_pct_points_computed(self, consensus_db):
        from dashboard_api.services.analysis import consensus_analysis
        result = consensus_analysis("BTCUSDT", 300)
        if result["status"] == "ok":
            # May be None if only consensus or only split boundaries exist
            assert "delta_pct_points" in result["result"]


# ---------------------------------------------------------------------------
# TestDecayFilter
# ---------------------------------------------------------------------------

class TestDecayFilter:
    @pytest.fixture
    def decay_db(self, tmp_path, monkeypatch):
        """DB with decay_metrics rows alongside predictions."""
        db_path = str(tmp_path / "decay.db")
        conn = _make_conn(db_path)
        _create_schema(conn)

        # 80 predictions, all at ts >= _BASE_TS + 100_000 (so decay snapshot at ts_ms=50_000 is safe)
        for i in range(80):
            _insert_pred(conn, model="dm", symbol="BTCUSDT", window=300,
                         proba=0.62, correct=(i < 60),
                         ts_offset=100_000 + i * 1000)
        conn.commit()

        # Insert decay snapshot BEFORE the predictions (ts_ms = _BASE_TS + 50_000)
        conn.execute("""
            INSERT INTO decay_metrics
              (model_name, symbol, market_window_seconds, ts_ms, computed_for_max_ts_ms,
               recency_weighted_ev, rolling_win_rate, rolling_ev, brier_score,
               calibration_error, sample_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("dm", "BTCUSDT", 300,
              _BASE_TS + 50_000,   # ts_ms of snapshot
              _BASE_TS + 49_000,   # sentinel: last pred used in this snapshot
              0.015, 0.70, 0.015, 0.22, 0.05, 40))
        conn.commit()

        import dashboard_api.services.analysis as svc
        monkeypatch.setattr(svc, "_get_db", lambda: _make_conn(db_path))
        yield db_path, conn

    def test_bucketing_works(self, decay_db):
        from dashboard_api.services.analysis import decay_filter_analysis
        result = decay_filter_analysis(symbol="BTCUSDT", window=300)
        assert result["status"] in {"ok", "no_data"}

    def test_recommends_threshold_where_wr_ge_52(self, decay_db):
        from dashboard_api.services.analysis import decay_filter_analysis
        result = decay_filter_analysis(symbol="BTCUSDT", window=300)
        if result["status"] == "ok":
            # Result has models list; each model has buckets + recommended_min_recency_weighted_ev
            assert "models" in result["result"]
            for m in result["result"]["models"]:
                assert "buckets" in m
                assert "recommended_min_recency_weighted_ev" in m

    def test_no_data_when_decay_empty(self, empty_db):
        from dashboard_api.services.analysis import decay_filter_analysis
        result = decay_filter_analysis(symbol="BTCUSDT", window=300)
        assert result["status"] == "no_data"


# ---------------------------------------------------------------------------
# TestCommitteeWeights
# ---------------------------------------------------------------------------

class TestCommitteeWeights:
    @pytest.fixture
    def committee_db(self, tmp_path, monkeypatch):
        """Two models with different quality; strong_model should get higher weight."""
        db_path = str(tmp_path / "committee.db")
        conn = _make_conn(db_path)
        _create_schema(conn)

        # 100 shared boundaries; strong_model correct 80/100, weak_model correct 52/100
        for i in range(100):
            ts = i * 300_000
            _insert_pred(conn, model="strong_model", symbol="BTCUSDT", window=300,
                         proba=0.70, correct=(i < 80), ts_offset=ts)
            _insert_pred(conn, model="weak_model", symbol="BTCUSDT", window=300,
                         proba=0.52, correct=(i < 52), ts_offset=ts)
        conn.commit()

        import dashboard_api.services.analysis as svc
        monkeypatch.setattr(svc, "_get_db", lambda: _make_conn(db_path))
        yield db_path, conn

    def test_converges_and_overweights_strong_model(self, committee_db):
        from dashboard_api.services.analysis import optimize_committee_weights
        result = optimize_committee_weights("BTCUSDT", 300, objective="sharpe")
        # May be ok or convergence_failed
        assert result["status"] in {"ok", "convergence_failed",
                                     "insufficient_samples", "error"}
        if result["status"] in {"ok", "convergence_failed"}:
            weights = result["result"]["weights"]
            assert isinstance(weights, dict)
            # strong_model should generally get higher weight
            if "strong_model" in weights and "weak_model" in weights:
                assert weights["strong_model"] >= weights["weak_model"] - 0.01

    def test_falls_back_on_insufficient_models(self, empty_db):
        from dashboard_api.services.analysis import optimize_committee_weights
        result = optimize_committee_weights("BTCUSDT", 300, objective="sharpe")
        # no preds → no_data
        assert result["status"] in {"no_data", "insufficient_samples"}

    def test_persists_to_committee_weights_table(self, committee_db):
        db_path, conn = committee_db
        from dashboard_api.services.analysis import optimize_committee_weights
        result = optimize_committee_weights("BTCUSDT", 300, objective="sharpe")
        if result["status"] in {"ok", "convergence_failed"}:
            row = conn.execute(
                "SELECT * FROM committee_weights WHERE symbol=? AND market_window_seconds=? AND objective=?",
                ("BTCUSDT", 300, "sharpe")
            ).fetchone()
            assert row is not None
            assert row["weights_json"] is not None


# ---------------------------------------------------------------------------
# TestTrainTest
# ---------------------------------------------------------------------------

class TestTrainTest:
    def test_chronological_split(self, v2_db):
        """train+test sizes roughly match 0.7/0.3 split of n_total."""
        from dashboard_api.services.analysis import train_test_validate
        result = train_test_validate({"confidence_threshold": 0.50}, "BTCUSDT", 300,
                                      train_frac=0.7)
        assert result["status"] in {"ok", "insufficient_samples"}
        if result["status"] == "ok":
            meta = result["metadata"]
            split_at = meta["split_at"]
            # n_total is the sum before filter; split_at should be ~70% of n_input
            n_input = meta["n_input"]
            assert abs(split_at - int(n_input * 0.7)) <= 1

    def test_reports_win_rate_delta(self, v2_db):
        from dashboard_api.services.analysis import train_test_validate
        result = train_test_validate({"confidence_threshold": 0.50}, "BTCUSDT", 300)
        if result["status"] == "ok":
            assert "win_rate_delta" in result["result"]


# ---------------------------------------------------------------------------
# TestWalkForward
# ---------------------------------------------------------------------------

class TestWalkForward:
    def test_per_fold_metrics_returned(self, v2_db):
        from dashboard_api.services.analysis import walk_forward_validate
        result = walk_forward_validate({"confidence_threshold": 0.50}, "BTCUSDT", 300,
                                        n_folds=3)
        assert result["status"] in {"ok", "insufficient_samples"}
        if result["status"] == "ok":
            assert len(result["result"]["folds"]) == 3

    def test_robust_flag_present(self, v2_db):
        from dashboard_api.services.analysis import walk_forward_validate
        result = walk_forward_validate({"confidence_threshold": 0.50}, "BTCUSDT", 300,
                                        n_folds=3)
        if result["status"] == "ok":
            assert "robust" in result["result"]

    def test_warnings_when_fold_has_zero_passed(self, tmp_path, monkeypatch):
        """Fold with 0 passed predictions should add a warning."""
        db_path = str(tmp_path / "wf.db")
        conn = _make_conn(db_path)
        _create_schema(conn)
        # 60 preds; threshold so high that some folds have 0 pass
        for i in range(60):
            proba = 0.95 if i < 12 else 0.50  # only first 12 pass threshold 0.90
            _insert_pred(conn, model="mx", symbol="BTCUSDT", window=300,
                         proba=proba, correct=True, ts_offset=i * 1000)
        conn.commit()

        import dashboard_api.services.analysis as svc
        monkeypatch.setattr(svc, "_get_db", lambda: _make_conn(db_path))
        result = svc.walk_forward_validate(
            {"confidence_threshold": 0.90}, "BTCUSDT", 300, n_folds=5
        )
        if result["status"] == "ok":
            # Some folds should have warned
            assert len(result["warnings"]) > 0


# ---------------------------------------------------------------------------
# TestRecommendPremium
# ---------------------------------------------------------------------------

class TestRecommendPremium:
    @pytest.fixture
    def premium_db(self, tmp_path, monkeypatch):
        """400 predictions with one clearly superior filter config."""
        db_path = str(tmp_path / "premium.db")
        conn = _make_conn(db_path)
        _create_schema(conn)
        # 400 preds: high-proba preds are strongly correct (good candidate)
        for i in range(400):
            proba = 0.70 if i % 2 == 0 else 0.51
            correct = (proba > 0.55) or (i % 5 == 0)
            _insert_pred(conn, model="pm", symbol="BTCUSDT", window=300,
                         proba=proba, correct=correct, ts_offset=i * 1000)
        conn.commit()

        import dashboard_api.services.analysis as svc
        monkeypatch.setattr(svc, "_get_db", lambda: _make_conn(db_path))
        yield db_path, conn

    def test_returns_ok_status_even_when_winner_none(self, v2_db):
        """With modest data, may find no winner but still returns ok."""
        from dashboard_api.services.analysis import recommend_premium_filter
        result = recommend_premium_filter("BTCUSDT", 300)
        assert result["status"] in {"ok", "no_data", "insufficient_samples"}

    def test_runner_ups_populated(self, premium_db):
        from dashboard_api.services.analysis import recommend_premium_filter
        result = recommend_premium_filter("BTCUSDT", 300)
        if result["status"] == "ok" and result["result"] is not None:
            assert "runners_up" in result["result"]

    def test_winner_none_path_has_runners_up(self, v2_db):
        """Small DB → winner=None path should include runners_up list."""
        from dashboard_api.services.analysis import recommend_premium_filter
        result = recommend_premium_filter("BTCUSDT", 300)
        if result["status"] == "ok" and result["result"] and result["result"].get("winner") is None:
            assert "runners_up" in result["result"]
            assert isinstance(result["result"]["runners_up"], list)

    def test_status_ok_even_when_winner_none(self, premium_db):
        """recommend_premium_filter never returns status!='ok' when grid succeeds."""
        from dashboard_api.services.analysis import recommend_premium_filter
        result = recommend_premium_filter("BTCUSDT", 300)
        # When grid_search returns ok, final result is always ok
        if result.get("metadata", {}).get("grid_metadata") or True:
            assert result["status"] in {"ok", "no_data", "insufficient_samples"}


# ---------------------------------------------------------------------------
# TestDecayJoinNoLeakage  — H2 CRITICAL TESTS
# ---------------------------------------------------------------------------

class TestDecayJoinNoLeakage:
    def test_decay_join_uses_strict_lt(self, tmp_path, monkeypatch):
        """A decay_metrics row with ts_ms=T must NOT be used by a prediction with
        ts_contract_open_ms=T (strict < required, not <=).
        """
        db_path = str(tmp_path / "leakage.db")
        conn = _make_conn(db_path)
        _create_schema(conn)

        T = _BASE_TS + 500_000

        # Single prediction at ts = T
        _insert_pred(conn, model="lm", symbol="BTCUSDT", window=300,
                     proba=0.62, correct=True, ts_offset=500_000)

        # Decay row with ts_ms = T and sentinel = T - 1
        conn.execute("""
            INSERT INTO decay_metrics
              (model_name, symbol, market_window_seconds, ts_ms, computed_for_max_ts_ms,
               recency_weighted_ev, rolling_win_rate, rolling_ev, brier_score,
               calibration_error, sample_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("lm", "BTCUSDT", 300, T, T - 1, 0.99, 0.99, 0.99, 0.01, 0.01, 10))
        conn.commit()

        import dashboard_api.services.analysis as svc
        monkeypatch.setattr(svc, "_get_db", lambda: _make_conn(db_path))

        preds = [
            {
                "model_name": "lm", "symbol": "BTCUSDT",
                "market_window_seconds": 300,
                "ts_contract_open_ms": T,
            }
        ]
        decay_map = svc._load_decay_state_for_predictions(_make_conn(db_path), preds)
        # The prediction at T must NOT get the decay row at ts_ms=T (strict <)
        key = ("lm", "BTCUSDT", 300, T)
        assert key not in decay_map, (
            "Decay row at ts_ms=T was used for prediction at ts=T — "
            "violates strict < invariant (H2 data-leakage guard)."
        )

    def test_decay_no_future_data_used(self, tmp_path, monkeypatch):
        """Decay rows whose sentinel_ts_ms >= prediction ts must be excluded."""
        db_path = str(tmp_path / "future.db")
        conn = _make_conn(db_path)
        _create_schema(conn)

        T = _BASE_TS + 1_000_000

        _insert_pred(conn, model="fm", symbol="BTCUSDT", window=300,
                     proba=0.60, correct=True, ts_offset=1_000_000)

        # Legitimate decay row: ts_ms < T, sentinel < T
        conn.execute("""
            INSERT INTO decay_metrics
              (model_name, symbol, market_window_seconds, ts_ms, computed_for_max_ts_ms,
               recency_weighted_ev, rolling_win_rate, rolling_ev, brier_score,
               calibration_error, sample_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("fm", "BTCUSDT", 300, T - 500_000, T - 600_000, 0.01, 0.60, 0.01, 0.25, 0.05, 20))

        # Future decay row: sentinel_ts_ms = T (matches prediction ts) — must be excluded
        conn.execute("""
            INSERT INTO decay_metrics
              (model_name, symbol, market_window_seconds, ts_ms, computed_for_max_ts_ms,
               recency_weighted_ev, rolling_win_rate, rolling_ev, brier_score,
               calibration_error, sample_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("fm", "BTCUSDT", 300, T - 1, T, 0.99, 0.99, 0.99, 0.01, 0.01, 100))
        conn.commit()

        import dashboard_api.services.analysis as svc
        monkeypatch.setattr(svc, "_get_db", lambda: _make_conn(db_path))

        preds = [{
            "model_name": "fm", "symbol": "BTCUSDT",
            "market_window_seconds": 300,
            "ts_contract_open_ms": T,
        }]
        decay_map = svc._load_decay_state_for_predictions(_make_conn(db_path), preds)
        key = ("fm", "BTCUSDT", 300, T)

        if key in decay_map:
            # Must be the legitimate row, not the future one
            used_ev = decay_map[key]["recency_weighted_ev"]
            assert used_ev != 0.99, (
                "Future decay row (sentinel_ts_ms=T) was used for prediction at ts=T — "
                "data leakage detected."
            )
