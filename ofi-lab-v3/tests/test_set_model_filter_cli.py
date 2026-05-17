"""Tests for scripts/set_model_filter.py CLI tool.

Uses a temp SQLite DB seeded with the minimal model_registry + model_audit schema.
Calls the script's main() directly (same process) rather than via subprocess so
coverage is captured and errors surface cleanly.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Add scripts/ to path so we can import the module directly.
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import set_model_filter as smf  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal schema (only the tables the CLI touches)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_registry (
    name                   TEXT PRIMARY KEY,
    symbol                 TEXT,
    training_horizon_seconds INTEGER,
    filter_config_json     TEXT DEFAULT '{}',
    updated_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS model_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name TEXT NOT NULL,
    action     TEXT NOT NULL,
    by_user    TEXT NOT NULL,
    detail     TEXT,
    ts         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""


@pytest.fixture()
def tmp_db(tmp_path) -> Path:
    """Create a temp SQLite DB with minimal schema and return its path."""
    db_path = tmp_path / "test_v3.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


def _insert_model(db_path: Path, name: str, symbol: str = "BTC",
                  horizon: int = 60, filter_json: str = "{}") -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO model_registry "
        "(name, symbol, training_horizon_seconds, filter_config_json) "
        "VALUES (?, ?, ?, ?)",
        (name, symbol, horizon, filter_json),
    )
    conn.commit()
    conn.close()


def _get_filter(db_path: Path, name: str) -> dict:
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT filter_config_json FROM model_registry WHERE name = ?", (name,)
    ).fetchone()
    conn.close()
    if row is None:
        raise KeyError(f"Model {name!r} not found")
    return json.loads(row[0] or "{}")


def _run(argv: list) -> int:
    """Run main() and return exit code."""
    return smf.main(argv)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPatternMatch:
    def test_pattern_match_h60_models(self, tmp_db):
        """Pattern h60_* updates only matching models, leaves others alone."""
        _insert_model(tmp_db, "h60_btc_30d", symbol="BTC", horizon=60)
        _insert_model(tmp_db, "h60_eth_30d", symbol="ETH", horizon=60)
        _insert_model(tmp_db, "h300_btc_30d", symbol="BTC", horizon=300)

        rc = _run([
            "--db", str(tmp_db),
            "--model-pattern", "h60_*",
            "--confidence-threshold", "0.58",
        ])
        assert rc == 0

        assert _get_filter(tmp_db, "h60_btc_30d")["confidence_threshold"] == pytest.approx(0.58)
        assert _get_filter(tmp_db, "h60_eth_30d")["confidence_threshold"] == pytest.approx(0.58)
        # h300 must NOT be touched
        assert "confidence_threshold" not in _get_filter(tmp_db, "h300_btc_30d")

    def test_no_match_returns_exit_1(self, tmp_db):
        _insert_model(tmp_db, "h300_btc_30d")
        rc = _run(["--db", str(tmp_db), "--model-pattern", "h9999_*",
                   "--confidence-threshold", "0.5"])
        assert rc == 1


class TestExactModelMatch:
    def test_exact_model_match(self, tmp_db):
        """--model exact match updates only that model."""
        _insert_model(tmp_db, "h300_btc_30d")
        _insert_model(tmp_db, "h300_eth_30d")

        rc = _run([
            "--db", str(tmp_db),
            "--model", "h300_btc_30d",
            "--warmup-seconds", "600",
        ])
        assert rc == 0

        assert _get_filter(tmp_db, "h300_btc_30d")["warmup_seconds"] == 600
        assert "warmup_seconds" not in _get_filter(tmp_db, "h300_eth_30d")

    def test_exact_no_match_returns_exit_1(self, tmp_db):
        rc = _run(["--db", str(tmp_db), "--model", "nonexistent",
                   "--warmup-seconds", "300"])
        assert rc == 1


class TestMergePreservesUnsuppliedKeys:
    def test_merge_preserves_unsupplied_keys(self, tmp_db):
        """Supplying --confidence-threshold must not erase ev_threshold."""
        initial = json.dumps({"confidence_threshold": 0.5, "ev_threshold": 0.01})
        _insert_model(tmp_db, "h60_btc_30d", filter_json=initial)

        rc = _run([
            "--db", str(tmp_db),
            "--model", "h60_btc_30d",
            "--confidence-threshold", "0.60",
        ])
        assert rc == 0

        result = _get_filter(tmp_db, "h60_btc_30d")
        assert result["confidence_threshold"] == pytest.approx(0.60)
        assert result["ev_threshold"] == pytest.approx(0.01)  # preserved


class TestClearKeys:
    def test_clear_keys_removes_keys(self, tmp_db):
        """--clear-keys removes specified keys, leaves others intact."""
        initial = json.dumps({
            "confidence_threshold": 0.55,
            "ev_threshold": 0.003,
            "blackout_hours": [21, 22, 23],
            "warmup_seconds": 1800,
        })
        _insert_model(tmp_db, "h1800_btc_30d", filter_json=initial)

        rc = _run([
            "--db", str(tmp_db),
            "--model", "h1800_btc_30d",
            "--clear-keys", "blackout_hours,warmup_seconds",
        ])
        assert rc == 0

        result = _get_filter(tmp_db, "h1800_btc_30d")
        assert "blackout_hours" not in result
        assert "warmup_seconds" not in result
        assert result["confidence_threshold"] == pytest.approx(0.55)
        assert result["ev_threshold"] == pytest.approx(0.003)


class TestDryRun:
    def test_dry_run_writes_nothing(self, tmp_db):
        """--dry-run must not modify the DB."""
        initial = json.dumps({"confidence_threshold": 0.5})
        _insert_model(tmp_db, "h60_btc_30d", filter_json=initial)

        rc = _run([
            "--db", str(tmp_db),
            "--model", "h60_btc_30d",
            "--confidence-threshold", "0.99",
            "--dry-run",
        ])
        assert rc == 0

        # DB must be unchanged
        result = _get_filter(tmp_db, "h60_btc_30d")
        assert result["confidence_threshold"] == pytest.approx(0.5)


class TestValidation:
    def test_validation_rejects_confidence_above_1(self, tmp_db):
        with pytest.raises(SystemExit) as exc_info:
            smf.main([
                "--db", str(tmp_db),
                "--model", "abc",
                "--confidence-threshold", "1.5",
            ])
        assert exc_info.value.code == 2

    def test_validation_rejects_confidence_below_0(self, tmp_db):
        with pytest.raises(SystemExit) as exc_info:
            smf.main([
                "--db", str(tmp_db),
                "--model", "abc",
                "--confidence-threshold", "-0.1",
            ])
        assert exc_info.value.code == 2

    def test_validation_rejects_ev_out_of_range(self, tmp_db):
        with pytest.raises(SystemExit) as exc_info:
            smf.main([
                "--db", str(tmp_db),
                "--model", "abc",
                "--ev-threshold", "2.0",
            ])
        assert exc_info.value.code == 2

    def test_validation_rejects_negative_warmup(self, tmp_db):
        with pytest.raises(SystemExit) as exc_info:
            smf.main([
                "--db", str(tmp_db),
                "--model", "abc",
                "--warmup-seconds", "-1",
            ])
        assert exc_info.value.code == 2

    def test_validation_rejects_blackout_hour_out_of_range(self, tmp_db):
        with pytest.raises(SystemExit) as exc_info:
            smf.main([
                "--db", str(tmp_db),
                "--model", "abc",
                "--blackout-hours", "24",
            ])
        assert exc_info.value.code == 2

    def test_missing_target_returns_exit_2(self, tmp_db):
        """No --model, --model-pattern, or --list → exit 2."""
        with pytest.raises(SystemExit) as exc_info:
            smf.main(["--db", str(tmp_db), "--confidence-threshold", "0.5"])
        assert exc_info.value.code == 2


class TestListMode:
    def test_list_mode_outputs_all_models(self, tmp_db, capsys):
        """--list prints a table containing all registered model names."""
        _insert_model(tmp_db, "h60_btc_30d", symbol="BTC", horizon=60,
                      filter_json='{"confidence_threshold":0.58}')
        _insert_model(tmp_db, "h300_eth_30d", symbol="ETH", horizon=300)
        _insert_model(tmp_db, "h1800_sol_30d", symbol="SOL", horizon=1800)

        rc = _run(["--db", str(tmp_db), "--list"])
        assert rc == 0

        captured = capsys.readouterr()
        assert "h60_btc_30d" in captured.out
        assert "h300_eth_30d" in captured.out
        assert "h1800_sol_30d" in captured.out
        # Config value visible
        assert "0.58" in captured.out


class TestBlackoutHoursParsing:
    def test_blackout_hours_deduped_and_sorted(self, tmp_db):
        _insert_model(tmp_db, "h60_btc_30d")

        rc = _run([
            "--db", str(tmp_db),
            "--model", "h60_btc_30d",
            "--blackout-hours", "23,0,1,23,22",  # duplicate 23
        ])
        assert rc == 0

        result = _get_filter(tmp_db, "h60_btc_30d")
        assert result["blackout_hours"] == [0, 1, 22, 23]  # sorted, deduped


class TestAuditRow:
    def test_audit_row_written_on_change(self, tmp_db):
        _insert_model(tmp_db, "h60_btc_30d")

        _run([
            "--db", str(tmp_db),
            "--model", "h60_btc_30d",
            "--confidence-threshold", "0.58",
        ])

        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute(
            "SELECT * FROM model_audit WHERE model_name = ?", ("h60_btc_30d",)
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][2] == "set_model_filter"  # action column
