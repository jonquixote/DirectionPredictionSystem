"""CLI smoke tests for the 9 v2 modes added to scripts/run_analysis.py.

Each test:
  1. Seeds a minimal temp DB (10–20 resolved predictions)
  2. Runs the CLI via subprocess with STORAGE_DB_PATH pointing at the temp DB
  3. Asserts exit code 0 (or non-zero for the no-data edge case) + valid JSON stdout
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent  # ofi-lab-v3/
_CLI = str(_REPO_ROOT / "scripts" / "run_analysis.py")
_PYTHON = sys.executable
_BASE_TS = 1_700_000_000_000


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------

def _seed_test_db(db_path: str, n: int = 20) -> None:
    """Create minimal predictions + model_registry tables and seed n rows."""
    conn = sqlite3.connect(db_path)
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_overlap (
            ts_contract_open_ms    INTEGER NOT NULL,
            symbol                 TEXT NOT NULL,
            market_window_seconds  INTEGER NOT NULL,
            models_scored_json     TEXT NOT NULL DEFAULT '[]',
            directions_json        TEXT NOT NULL DEFAULT '[]',
            confidences_json       TEXT NOT NULL DEFAULT '[]',
            consensus              INTEGER NOT NULL DEFAULT 0,
            consensus_direction    TEXT,
            weighted_confidence    REAL,
            registry_load_generation INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ts_contract_open_ms, symbol, market_window_seconds, registry_load_generation)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decay_metrics (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                     TEXT NOT NULL DEFAULT '',
            model_name             TEXT NOT NULL,
            symbol                 TEXT NOT NULL,
            market_window_seconds  INTEGER NOT NULL,
            window_size            INTEGER NOT NULL DEFAULT 0,
            ts_ms                  INTEGER,
            computed_for_max_ts_ms INTEGER,
            recency_weighted_ev    REAL,
            rolling_win_rate       REAL,
            rolling_ev             REAL,
            brier_score            REAL,
            calibration_error      REAL,
            sample_count           INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
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
            computed_at            TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            PRIMARY KEY (symbol, market_window_seconds, objective)
        )
    """)

    conn.execute("INSERT OR IGNORE INTO model_registry (name) VALUES ('v2_model_a')")

    for i in range(n):
        correct = int(i < int(n * 0.65))
        # Vary proba slightly so grid-search sees a range
        proba = 0.55 + (i % 5) * 0.02
        conn.execute("""
            INSERT INTO predictions (
                prediction_id, model_name, symbol, market_window_seconds,
                pred_proba_calibrated, pred_proba_raw, pred_direction,
                prediction_correct, p_market, p_model_minus_market,
                ts_contract_open_ms, utc_hour, day_of_week, is_weekend,
                relative_spread, ev_estimate, resolved, warmup
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
        """, (
            str(uuid.uuid4()), "v2_model_a", "BTCUSDT", 300,
            proba, proba, "up",
            correct, 0.50, proba - 0.50,
            _BASE_TS + i * 60_000,
            i % 24, i % 7, 0,
            0.0002, 0.01,
        ))

    conn.commit()
    conn.close()


@pytest.fixture
def cli_db(tmp_path):
    """Seeded test DB with STORAGE_DB_PATH usable by env injection."""
    db_path = str(tmp_path / "cli_v2_test.db")
    _seed_test_db(db_path)
    return db_path


def _run_cli(args: list[str], db_path: str) -> tuple[int, str, str]:
    env = {**os.environ, "STORAGE_DB_PATH": db_path}
    result = subprocess.run(
        [_PYTHON, _CLI] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# v2 CLI smoke tests
# ---------------------------------------------------------------------------

class TestCliSimulate:
    def test_cli_simulate_runs(self, cli_db):
        """--simulate exits 0 and returns valid JSON."""
        rc, out, err = _run_cli(
            [
                "--simulate", '{"confidence_threshold": 0.55}',
                "--symbol", "BTCUSDT",
                "--window", "300",
            ],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}.\nstderr: {err}\nstdout: {out}"
        data = json.loads(out)
        assert isinstance(data, dict)


class TestCliGridSearch:
    def test_cli_grid_search_runs(self, cli_db):
        """--grid-search exits 0 and returns valid JSON."""
        rc, out, err = _run_cli(
            ["--grid-search", "--symbol", "BTCUSDT", "--window", "300"],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}.\nstderr: {err}\nstdout: {out}"
        data = json.loads(out)
        assert isinstance(data, dict)


class TestCliRegimeMatrix:
    def test_cli_regime_matrix_runs(self, cli_db):
        """--regime-matrix exits 0 and returns valid JSON."""
        rc, out, err = _run_cli(
            ["--regime-matrix", "--symbol", "BTCUSDT", "--window", "300"],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}.\nstderr: {err}\nstdout: {out}"
        data = json.loads(out)
        assert isinstance(data, dict)


class TestCliConsensus:
    def test_cli_consensus_runs(self, cli_db):
        """--consensus exits 0 and returns valid JSON."""
        rc, out, err = _run_cli(
            ["--consensus", "--symbol", "BTCUSDT", "--window", "300"],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}.\nstderr: {err}\nstdout: {out}"
        data = json.loads(out)
        assert isinstance(data, dict)


class TestCliDecayFilter:
    def test_cli_decay_filter_runs(self, cli_db):
        """--decay-filter exits 0 and returns valid JSON."""
        rc, out, err = _run_cli(
            ["--decay-filter", "--symbol", "BTCUSDT", "--window", "300"],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}.\nstderr: {err}\nstdout: {out}"
        data = json.loads(out)
        assert isinstance(data, dict)


class TestCliCommitteeWeights:
    def test_cli_committee_weights_runs(self, cli_db):
        """--committee-weights exits 0 and returns valid JSON."""
        rc, out, err = _run_cli(
            [
                "--committee-weights",
                "--symbol", "BTCUSDT",
                "--window", "300",
                "--objective", "sharpe",
            ],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}.\nstderr: {err}\nstdout: {out}"
        data = json.loads(out)
        assert isinstance(data, dict)


class TestCliWalkForward:
    def test_cli_walk_forward_runs(self, cli_db):
        """--walk-forward exits 0 and returns valid JSON."""
        rc, out, err = _run_cli(
            [
                "--walk-forward", '{"confidence_threshold": 0.55}',
                "--symbol", "BTCUSDT",
                "--window", "300",
            ],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}.\nstderr: {err}\nstdout: {out}"
        data = json.loads(out)
        assert isinstance(data, dict)


class TestCliTrainTest:
    def test_cli_train_test_runs(self, cli_db):
        """--train-test exits 0 and returns valid JSON."""
        rc, out, err = _run_cli(
            [
                "--train-test", '{"confidence_threshold": 0.55}',
                "--symbol", "BTCUSDT",
                "--window", "300",
            ],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}.\nstderr: {err}\nstdout: {out}"
        data = json.loads(out)
        assert isinstance(data, dict)


class TestCliRecommendPremium:
    def test_cli_recommend_premium_runs(self, cli_db):
        """--recommend-premium exits 0 and returns valid JSON."""
        rc, out, err = _run_cli(
            ["--recommend-premium", "--symbol", "BTCUSDT", "--window", "300"],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}.\nstderr: {err}\nstdout: {out}"
        data = json.loads(out)
        assert isinstance(data, dict)
