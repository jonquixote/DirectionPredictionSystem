"""CLI tests for scripts/run_analysis.py.

Uses subprocess so the CLI's path bootstrap runs correctly.
Each test verifies:
  1. Exit code 0
  2. Output is valid JSON
  3. Top-level structure is as expected
"""
from __future__ import annotations

import json
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
# DB seeding helper (same pattern as endpoint tests but standalone)
# ---------------------------------------------------------------------------

def _seed_test_db(db_path: str) -> None:
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

    # Seed 100 predictions for cli_model_a, BTCUSDT, 300
    for i in range(100):
        correct = i < 65  # 65% win rate
        conn.execute("""
            INSERT INTO predictions (
                prediction_id, model_name, symbol, market_window_seconds,
                pred_proba_calibrated, pred_proba_raw, pred_direction,
                prediction_correct, p_market, p_model_minus_market,
                ts_contract_open_ms, utc_hour, day_of_week, is_weekend,
                relative_spread, ev_estimate, resolved, warmup
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
        """, (
            str(uuid.uuid4()), "cli_model_a", "BTCUSDT", 300,
            0.62, 0.62, "up",
            int(correct), 0.50, 0.12,
            _BASE_TS + i * 1000,
            i % 24, i % 7, 0,
            0.0002, 0.01,
        ))

    conn.commit()
    conn.close()


@pytest.fixture
def cli_db(tmp_path):
    """Seeded test DB with STORAGE_DB_PATH set."""
    db_path = str(tmp_path / "cli_test.db")
    _seed_test_db(db_path)
    return db_path


def _run_cli(args: list[str], db_path: str) -> tuple[int, str, str]:
    """Run the CLI, return (returncode, stdout, stderr)."""
    env_extra = {"STORAGE_DB_PATH": db_path}
    import os
    env = {**os.environ, **env_extra}
    result = subprocess.run(
        [_PYTHON, _CLI] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCliLeaderboard:
    def test_cli_leaderboard_runs(self, cli_db):
        """--leaderboard exits 0 and outputs valid JSON list."""
        rc, out, err = _run_cli(
            ["--leaderboard", "--symbol", "BTCUSDT", "--window", "300", "--top", "10"],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}. stderr: {err}"
        data = json.loads(out)
        assert isinstance(data, list)

    def test_cli_leaderboard_metric_roi(self, cli_db):
        """--leaderboard --metric roi returns valid JSON list."""
        rc, out, err = _run_cli(
            ["--leaderboard", "--metric", "roi", "--top", "5"],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}. stderr: {err}"
        data = json.loads(out)
        assert isinstance(data, list)

    def test_cli_leaderboard_contains_model(self, cli_db):
        """Output should contain cli_model_a in the list."""
        rc, out, _ = _run_cli(
            ["--leaderboard", "--symbol", "BTCUSDT", "--window", "300"],
            cli_db,
        )
        assert rc == 0
        data = json.loads(out)
        names = [r["model_name"] for r in data]
        assert "cli_model_a" in names


class TestCliReport:
    def test_cli_report_runs(self, cli_db):
        """--report --symbol BTCUSDT --window 300 exits 0."""
        rc, out, err = _run_cli(
            ["--report", "--symbol", "BTCUSDT", "--window", "300"],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}. stderr: {err}"
        data = json.loads(out)
        assert "results" in data
        assert "recommended_configs" in data

    def test_cli_report_writes_json(self, cli_db, tmp_path):
        """--json-out writes a file and the file is valid JSON."""
        out_path = str(tmp_path / "report.json")
        rc, out, err = _run_cli(
            ["--report", "--symbol", "BTCUSDT", "--window", "300", "--json-out", out_path],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}. stderr: {err}"
        assert Path(out_path).exists(), "JSON output file was not created"
        data = json.loads(Path(out_path).read_text())
        assert "results" in data


class TestCliSkipConditions:
    def test_cli_skip_conditions_runs(self, cli_db):
        """--skip-conditions exits 0 and returns correct top-level structure."""
        rc, out, err = _run_cli(
            ["--skip-conditions", "--symbol", "BTCUSDT", "--window", "300"],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}. stderr: {err}"
        data = json.loads(out)
        assert "buckets" in data
        assert "skip_recommendations" in data

    def test_cli_skip_conditions_no_filter(self, cli_db):
        """--skip-conditions without --symbol or --window still returns structure."""
        rc, out, err = _run_cli(["--skip-conditions"], cli_db)
        assert rc == 0, f"CLI exited {rc}. stderr: {err}"
        data = json.loads(out)
        assert "skip_recommendations" in data


class TestCliThresholdGrid:
    def test_cli_threshold_grid_runs(self, cli_db):
        """--threshold-grid exits 0 and returns models + thresholds."""
        rc, out, err = _run_cli(
            ["--threshold-grid", "--symbol", "BTCUSDT", "--window", "300"],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}. stderr: {err}"
        data = json.loads(out)
        assert "models" in data
        assert "thresholds" in data

    def test_cli_threshold_grid_requires_symbol_and_window(self, cli_db):
        """--threshold-grid without --symbol exits non-zero."""
        rc, out, err = _run_cli(
            ["--threshold-grid", "--window", "300"],
            cli_db,
        )
        # Should exit 1 (missing required arg)
        assert rc != 0


class TestCliCommitteeSim:
    def test_cli_committee_sim_runs(self, cli_db):
        """--committee-sim exits 0 and returns expected structure."""
        rc, out, err = _run_cli(
            ["--committee-sim", "--symbol", "BTCUSDT", "--window", "300", "--strategy", "avg"],
            cli_db,
        )
        assert rc == 0, f"CLI exited {rc}. stderr: {err}"
        data = json.loads(out)
        assert "committee_win_rate" in data
        assert "recommendation" in data
