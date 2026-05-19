"""Tests for recommend_premium_filter() discovery mode (Plan B3).

Four test cases:
1. strict rejects when FDR fails (walk-forward passes)
2. discovery accepts same candidate
3. discovery still requires walk-forward robust
4. n threshold difference: n=70 → strict rejects, discovery accepts
"""
from __future__ import annotations

import sqlite3
import uuid
from unittest.mock import MagicMock, patch

import pytest

_BASE_TS = 1_700_000_000_000


# ---------------------------------------------------------------------------
# Helpers (mirrors test_analysis_v2.py pattern)
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
            name TEXT PRIMARY KEY,
            filter_config_json TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS model_overlap (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            market_window_seconds INTEGER NOT NULL,
            ts_contract_open_ms INTEGER NOT NULL,
            consensus INTEGER DEFAULT 0,
            consensus_direction TEXT,
            weighted_confidence REAL,
            n_models INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS decay_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            market_window_seconds INTEGER NOT NULL,
            ts TEXT,
            ts_ms INTEGER,
            computed_for_max_ts_ms INTEGER,
            recency_weighted_ev REAL,
            rolling_win_rate REAL,
            rolling_ev REAL,
            brier_score REAL,
            calibration_error REAL,
            sample_count INTEGER
        );
        CREATE TABLE IF NOT EXISTS committee_weights (
            symbol TEXT NOT NULL,
            market_window_seconds INTEGER NOT NULL,
            objective TEXT NOT NULL,
            weights_json TEXT NOT NULL,
            n_boundaries INTEGER,
            expected_sharpe REAL,
            expected_win_rate REAL,
            expected_roi_pct REAL,
            converged INTEGER DEFAULT 1,
            computed_at TEXT,
            PRIMARY KEY (symbol, market_window_seconds, objective)
        );
    """)
    conn.commit()


def _insert_pred(conn, model="m", symbol="BTCUSDT", window=300,
                 proba=0.65, correct=True, p_market=0.50, ts_offset=0):
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
        proba, proba, "up",
        int(correct), p_market, proba - p_market,
        _BASE_TS + ts_offset,
        12, 1, 0, 0.0002, 0.01,
    ))


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------

def _make_wf_result(robust: bool, mean_wr: float = 0.62) -> dict:
    """Minimal walk_forward_validate return value."""
    return {
        "status": "ok",
        "result": {
            "robust": robust,
            "mean_win_rate": mean_wr,
            "std_win_rate": 0.02,
            "folds": [],
        },
    }


def _make_tt_result(test_wr: float) -> dict:
    """Minimal train_test_validate return value."""
    return {
        "status": "ok",
        "result": {
            "train": {"win_rate": test_wr + 0.02, "n": 80},
            "test": {"win_rate": test_wr, "n": 40},
        },
    }


def _make_grid_candidate(
    n_passed: int = 120,
    survives_fdr: bool = True,
    p_value_raw: float = 0.01,
) -> dict:
    """Synthetic grid_search candidate row."""
    return {
        "filter_config": {"proba_threshold": 0.62},
        "n_passed": n_passed,
        "win_rate": 0.65,
        "p_value": p_value_raw,
        "p_value_raw": p_value_raw,
        "p_value_bh_adjusted": p_value_raw * 5 if survives_fdr else 0.5,
        "survives_fdr_q05": survives_fdr,
        "roi_pct": 0.5,
        "ev_per_trade": 0.005,
    }


def _make_gs_ok(candidates: list) -> dict:
    return {
        "status": "ok",
        "metadata": {},
        "result": {"top": candidates},
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStrictVsDiscoveryMode:

    def test_strict_mode_rejects_non_fdr_survivor(self, tmp_path, monkeypatch):
        """Candidate passes walk-forward but fails FDR → strict returns no winner."""
        import dashboard_api.services.analysis as svc

        # Candidate that walks forward robustly but does NOT survive FDR
        cand = _make_grid_candidate(n_passed=120, survives_fdr=False, p_value_raw=0.04)
        gs_result = _make_gs_ok([cand])
        wf_result = _make_wf_result(robust=True)
        tt_result = _make_tt_result(test_wr=0.62)

        monkeypatch.setattr(svc, "grid_search", lambda *a, **kw: gs_result)
        monkeypatch.setattr(svc, "walk_forward_validate", lambda *a, **kw: wf_result)
        monkeypatch.setattr(svc, "train_test_validate", lambda *a, **kw: tt_result)

        result = svc.recommend_premium_filter("BTCUSDT", 300, mode="strict")

        assert result["status"] == "ok"
        assert result["result"]["winner"] is None, (
            "strict mode must require FDR survival; non-FDR candidate should not win"
        )
        gates = result["result"]["runners_up"][0]["gates_passed"]
        assert gates["survives_fdr_q05"] is False

    def test_discovery_mode_accepts_walk_forward_robust_without_fdr(self, tmp_path, monkeypatch):
        """Same candidate (no FDR, robust WF) → discovery returns winner."""
        import dashboard_api.services.analysis as svc

        cand = _make_grid_candidate(n_passed=120, survives_fdr=False, p_value_raw=0.03)
        gs_result = _make_gs_ok([cand])
        wf_result = _make_wf_result(robust=True)
        tt_result = _make_tt_result(test_wr=0.62)

        monkeypatch.setattr(svc, "grid_search", lambda *a, **kw: gs_result)
        monkeypatch.setattr(svc, "walk_forward_validate", lambda *a, **kw: wf_result)
        monkeypatch.setattr(svc, "train_test_validate", lambda *a, **kw: tt_result)

        result = svc.recommend_premium_filter("BTCUSDT", 300, mode="discovery")

        assert result["status"] == "ok"
        assert result["result"]["winner"] is not None, (
            "discovery mode should accept a walk-forward-robust candidate even without FDR"
        )
        w = result["result"]["winner"]
        assert w["mode_used"] == "discovery"
        # FDR status surfaced as informative
        assert "survives_fdr_q05_informative" in w["gates_passed"]
        assert w["gates_passed"]["survives_fdr_q05_informative"] is False

    def test_discovery_mode_still_requires_walk_forward_robust(self, tmp_path, monkeypatch):
        """Candidate fails walk-forward → discovery still returns no winner."""
        import dashboard_api.services.analysis as svc

        cand = _make_grid_candidate(n_passed=120, survives_fdr=False, p_value_raw=0.02)
        gs_result = _make_gs_ok([cand])
        wf_result = _make_wf_result(robust=False, mean_wr=0.48)  # NOT robust
        tt_result = _make_tt_result(test_wr=0.62)

        monkeypatch.setattr(svc, "grid_search", lambda *a, **kw: gs_result)
        monkeypatch.setattr(svc, "walk_forward_validate", lambda *a, **kw: wf_result)
        monkeypatch.setattr(svc, "train_test_validate", lambda *a, **kw: tt_result)

        result = svc.recommend_premium_filter("BTCUSDT", 300, mode="discovery")

        assert result["status"] == "ok"
        assert result["result"]["winner"] is None, (
            "discovery mode must still require walk-forward robustness"
        )
        gates = result["result"]["runners_up"][0]["gates_passed"]
        assert gates["wf_robust"] is False

    def test_discovery_mode_lower_n_threshold(self, tmp_path, monkeypatch):
        """n=70 candidate: strict rejects (needs 100), discovery accepts (needs 50)."""
        import dashboard_api.services.analysis as svc

        # n=70 — between 50 (discovery) and 100 (strict)
        cand = _make_grid_candidate(n_passed=70, survives_fdr=True, p_value_raw=0.02)
        gs_result = _make_gs_ok([cand])
        wf_result = _make_wf_result(robust=True)
        tt_result = _make_tt_result(test_wr=0.60)

        monkeypatch.setattr(svc, "grid_search", lambda *a, **kw: gs_result)
        monkeypatch.setattr(svc, "walk_forward_validate", lambda *a, **kw: wf_result)
        monkeypatch.setattr(svc, "train_test_validate", lambda *a, **kw: tt_result)

        strict_result = svc.recommend_premium_filter("BTCUSDT", 300, mode="strict")
        discovery_result = svc.recommend_premium_filter("BTCUSDT", 300, mode="discovery")

        assert strict_result["result"]["winner"] is None, (
            "strict mode requires n >= 100; n=70 should be rejected"
        )
        assert discovery_result["result"]["winner"] is not None, (
            "discovery mode requires n >= 50; n=70 should be accepted"
        )
        strict_gates = strict_result["result"]["runners_up"][0]["gates_passed"]
        assert strict_gates["n_passed_ge_100"] is False

        disc_gates = discovery_result["result"]["winner"]["gates_passed"]
        assert disc_gates["n_passed_ge_50"] is True
