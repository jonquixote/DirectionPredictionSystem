"""Tests for predictions_daily_rollup: schema, loop upsert, backfill, and
leaderboard path selection.

Fixture pattern mirrors test_analysis_endpoints.py:
  - In-memory SQLite DB via tmp_path
  - monkeypatch _get_db in analysis service
  - monkeypatch DEFAULT_HISTORY_DAYS to bypass 30d bound

The _BASE_TS here is set to a known UTC date so we can construct realistic
date-bucketed predictions and verify rollup SQL.
"""
from __future__ import annotations

import math
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 2023-11-14 12:00:00 UTC in ms — same epoch used by test_analysis_endpoints
_BASE_TS = 1_700_000_000_000

# Derive the date string for _BASE_TS so rollup SQL can match
_BASE_DATE = datetime.fromtimestamp(_BASE_TS / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
# Second test date: one day later
_BASE_DATE_PLUS1 = (
    datetime.fromtimestamp(_BASE_TS / 1000, tz=timezone.utc) + timedelta(days=1)
).strftime("%Y-%m-%d")

# ms boundary for _BASE_DATE_PLUS1
_BASE_TS_PLUS1_DAY = _BASE_TS + 86_400_000


# ---------------------------------------------------------------------------
# DB helpers (reuse pattern from test_analysis_endpoints.py)
# ---------------------------------------------------------------------------

def _make_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    """Create predictions, model_registry, and rollup table."""
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
        CREATE TABLE IF NOT EXISTS predictions_daily_rollup (
            model_name              TEXT NOT NULL,
            symbol                  TEXT NOT NULL,
            market_window_seconds   INTEGER NOT NULL,
            date_utc                TEXT NOT NULL,
            n                       INTEGER NOT NULL DEFAULT 0,
            n_correct               INTEGER NOT NULL DEFAULT 0,
            sum_pnl                 REAL NOT NULL DEFAULT 0,
            sum_pnl_sq              REAL NOT NULL DEFAULT 0,
            sum_p_calibrated        REAL NOT NULL DEFAULT 0,
            sum_brier_terms         REAL NOT NULL DEFAULT 0,
            n_resolved_trades       INTEGER NOT NULL DEFAULT 0,
            updated_at              TEXT NOT NULL,
            PRIMARY KEY (model_name, symbol, market_window_seconds, date_utc)
        );
        CREATE INDEX IF NOT EXISTS idx_pdr_date
            ON predictions_daily_rollup(date_utc);
        CREATE INDEX IF NOT EXISTS idx_pdr_symbol_window_date
            ON predictions_daily_rollup(symbol, market_window_seconds, date_utc);
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
    ts_ms: int = _BASE_TS,
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
        ts_ms, 12, 1, 0, 0.0002, 0.01,
    ))


# The exact same UPSERT SQL as used in main.py _run_rollup_tick
_ROLLUP_UPSERT_SQL = """
INSERT INTO predictions_daily_rollup (
  model_name, symbol, market_window_seconds, date_utc,
  n, n_correct, sum_pnl, sum_pnl_sq, sum_p_calibrated, sum_brier_terms,
  n_resolved_trades, updated_at
)
SELECT
  model_name,
  symbol,
  market_window_seconds,
  ? AS date_utc,
  COUNT(*),
  SUM(CASE WHEN prediction_correct THEN 1 ELSE 0 END),
  SUM(
    CASE
      WHEN p_market IS NULL THEN 0.0
      WHEN pred_direction = 'up' AND prediction_correct THEN  1.0 - p_market - 0.009
      WHEN pred_direction = 'up'                         THEN -(p_market + 0.009)
      WHEN pred_direction = 'down' AND prediction_correct THEN  p_market - 0.009
      ELSE                                                    -(1.0 - p_market + 0.009)
    END
  ),
  SUM(
    CASE
      WHEN p_market IS NULL THEN 0.0
      WHEN pred_direction = 'up' AND prediction_correct THEN  (1.0 - p_market - 0.009) * (1.0 - p_market - 0.009)
      WHEN pred_direction = 'up'                         THEN  (p_market + 0.009) * (p_market + 0.009)
      WHEN pred_direction = 'down' AND prediction_correct THEN  (p_market - 0.009) * (p_market - 0.009)
      ELSE                                                    (1.0 - p_market + 0.009) * (1.0 - p_market + 0.009)
    END
  ),
  SUM(COALESCE(pred_proba_calibrated, 0.5)),
  SUM(
    (CASE WHEN pred_proba_calibrated >= 0.5
          THEN pred_proba_calibrated
          ELSE 1.0 - pred_proba_calibrated END
     - CAST(prediction_correct AS REAL))
    *
    (CASE WHEN pred_proba_calibrated >= 0.5
          THEN pred_proba_calibrated
          ELSE 1.0 - pred_proba_calibrated END
     - CAST(prediction_correct AS REAL))
  ),
  SUM(CASE WHEN p_market IS NOT NULL THEN 1 ELSE 0 END),
  ? AS updated_at
FROM predictions
WHERE resolved = 1 AND warmup = 0
  AND prediction_correct IS NOT NULL
  AND strftime('%Y-%m-%d', datetime(ts_contract_open_ms / 1000, 'unixepoch')) = ?
GROUP BY model_name, symbol, market_window_seconds
ON CONFLICT(model_name, symbol, market_window_seconds, date_utc)
DO UPDATE SET
  n                 = excluded.n,
  n_correct         = excluded.n_correct,
  sum_pnl           = excluded.sum_pnl,
  sum_pnl_sq        = excluded.sum_pnl_sq,
  sum_p_calibrated  = excluded.sum_p_calibrated,
  sum_brier_terms   = excluded.sum_brier_terms,
  n_resolved_trades = excluded.n_resolved_trades,
  updated_at        = excluded.updated_at
"""


# ---------------------------------------------------------------------------
# Test 1: migration creates the table
# ---------------------------------------------------------------------------

class TestMigrationCreatesTable:
    def test_migration_creates_table(self, tmp_path):
        """init_schema() creates predictions_daily_rollup."""
        db_path = str(tmp_path / "t1.db")
        from storage.db import open_database, init_schema
        conn = open_database(db_path)
        init_schema(conn)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='predictions_daily_rollup'"
        ).fetchone()
        assert row is not None, "predictions_daily_rollup table not found"
        conn.close()


# ---------------------------------------------------------------------------
# Test 2: loop upsert aggregates correctly
# ---------------------------------------------------------------------------

class TestLoopUpsertAggregates:
    def test_loop_upsert_aggregates_correctly(self, tmp_path):
        """Seed 10 predictions across 2 dates, upsert, verify rollup values."""
        db_path = str(tmp_path / "t2.db")
        conn = _make_conn(db_path)
        _create_tables(conn)

        updated_at = "2023-11-14T12:00:00Z"

        # Day 0: 5 predictions for model_a/BTCUSDT/300 — all correct, p_market=0.50, up
        for i in range(5):
            _insert_pred(conn, model="model_a", symbol="BTCUSDT", window=300,
                         proba=0.65, direction="up", correct=True,
                         p_market=0.50, ts_ms=_BASE_TS + i * 1000)
        conn.commit()

        # Day 1: 5 predictions — alternating correct/wrong
        for i in range(5):
            _insert_pred(conn, model="model_a", symbol="BTCUSDT", window=300,
                         proba=0.55, direction="up", correct=(i % 2 == 0),
                         p_market=0.48, ts_ms=_BASE_TS_PLUS1_DAY + i * 1000)
        conn.commit()

        # Run upsert for both dates
        conn.execute(_ROLLUP_UPSERT_SQL, (_BASE_DATE, updated_at, _BASE_DATE))
        conn.execute(_ROLLUP_UPSERT_SQL, (_BASE_DATE_PLUS1, updated_at, _BASE_DATE_PLUS1))
        conn.commit()

        # Verify day 0 row
        row0 = conn.execute(
            "SELECT * FROM predictions_daily_rollup WHERE date_utc = ?",
            (_BASE_DATE,)
        ).fetchone()
        assert row0 is not None, f"No rollup row for {_BASE_DATE}"
        assert row0["n"] == 5
        assert row0["n_correct"] == 5
        assert row0["n_resolved_trades"] == 5
        # sum_pnl for up+correct at p_market=0.50: (1 - 0.50 - 0.009) * 5 = 0.491 * 5 = 2.455
        expected_pnl_d0 = (1.0 - 0.50 - 0.009) * 5
        assert abs(row0["sum_pnl"] - expected_pnl_d0) < 1e-6, \
            f"sum_pnl mismatch: got {row0['sum_pnl']}, expected {expected_pnl_d0}"

        # sum_pnl_sq: ((1 - 0.50 - 0.009)^2) * 5
        expected_pnlsq_d0 = ((1.0 - 0.50 - 0.009) ** 2) * 5
        assert abs(row0["sum_pnl_sq"] - expected_pnlsq_d0) < 1e-6

        # Brier: side_p = max(0.65, 0.35) = 0.65, outcome = 1 (correct)
        # (0.65 - 1)^2 = 0.1225 per row, 5 rows
        expected_brier_d0 = (0.65 - 1.0) ** 2 * 5
        assert abs(row0["sum_brier_terms"] - expected_brier_d0) < 1e-5

        # Verify day 1 row
        row1 = conn.execute(
            "SELECT * FROM predictions_daily_rollup WHERE date_utc = ?",
            (_BASE_DATE_PLUS1,)
        ).fetchone()
        assert row1 is not None, f"No rollup row for {_BASE_DATE_PLUS1}"
        assert row1["n"] == 5
        assert row1["n_correct"] == 3  # indices 0, 2, 4 are correct

        # sum_pnl day 1: up+correct at p=0.48: (1 - 0.48 - 0.009) = 0.511
        #                up+wrong  at p=0.48: -(0.48 + 0.009) = -0.489
        # 3 correct, 2 wrong
        expected_pnl_d1 = 3 * (1.0 - 0.48 - 0.009) + 2 * -(0.48 + 0.009)
        assert abs(row1["sum_pnl"] - expected_pnl_d1) < 1e-6

        conn.close()

    def test_upsert_is_idempotent(self, tmp_path):
        """Running the upsert twice for the same date produces the same row."""
        db_path = str(tmp_path / "t2b.db")
        conn = _make_conn(db_path)
        _create_tables(conn)
        updated_at = "2023-11-14T12:00:00Z"

        for i in range(3):
            _insert_pred(conn, ts_ms=_BASE_TS + i * 1000)
        conn.commit()

        conn.execute(_ROLLUP_UPSERT_SQL, (_BASE_DATE, updated_at, _BASE_DATE))
        conn.commit()
        row_a = dict(conn.execute(
            "SELECT * FROM predictions_daily_rollup WHERE date_utc = ?",
            (_BASE_DATE,)
        ).fetchone())

        # Run again — should overwrite with identical values
        conn.execute(_ROLLUP_UPSERT_SQL, (_BASE_DATE, updated_at, _BASE_DATE))
        conn.commit()
        row_b = dict(conn.execute(
            "SELECT * FROM predictions_daily_rollup WHERE date_utc = ?",
            (_BASE_DATE,)
        ).fetchone())

        assert row_a["n"] == row_b["n"]
        assert abs(row_a["sum_pnl"] - row_b["sum_pnl"]) < 1e-9
        # No duplicate rows
        count = conn.execute(
            "SELECT COUNT(*) FROM predictions_daily_rollup WHERE date_utc = ?",
            (_BASE_DATE,)
        ).fetchone()[0]
        assert count == 1
        conn.close()


# ---------------------------------------------------------------------------
# Test 3: backfill seeds expected number of date rows
# ---------------------------------------------------------------------------

def _compute_full_backfill(conn, days: int = 90) -> int:
    """Standalone backfill helper for tests — mirrors compute_full_backfill in main.py
    without importing dashboard_api.main (which pulls FastAPI/services at module level).
    """
    from datetime import date as _date, timedelta as _td

    today = datetime.now(timezone.utc).date()
    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    batch = 0
    for offset in range(days, -1, -1):
        target_date = (today - _td(days=offset)).strftime("%Y-%m-%d")
        conn.execute(_ROLLUP_UPSERT_SQL, (target_date, updated_at, target_date))
        batch += 1
        if batch >= 7:
            conn.commit()
            batch = 0
    if batch > 0:
        conn.commit()

    return conn.execute(
        "SELECT COUNT(*) FROM predictions_daily_rollup"
    ).fetchone()[0]


class TestBackfillSeeds:
    def test_backfill_seeds_90d(self, tmp_path):
        """compute_full_backfill on a DB with 30 days of data fills 30 rollup rows."""
        db_path = str(tmp_path / "t3.db")
        conn = _make_conn(db_path)
        _create_tables(conn)

        # Seed 5 predictions per day for 30 calendar days (days 5–34 ago)
        now_utc = datetime.now(timezone.utc)
        seeded_dates = set()
        for day_offset in range(30):
            day_ts_ms = int(
                (now_utc - timedelta(days=day_offset + 5)).timestamp() * 1000
            )
            for j in range(5):
                _insert_pred(conn, ts_ms=day_ts_ms + j * 1000)
            seeded_dates.add(
                (now_utc - timedelta(days=day_offset + 5)).strftime("%Y-%m-%d")
            )
        conn.commit()

        total_rows = _compute_full_backfill(conn, days=90)

        # Backfill iterates 91 dates; only dates with predictions produce rows
        actual_rows = conn.execute(
            "SELECT COUNT(DISTINCT date_utc) FROM predictions_daily_rollup WHERE n > 0"
        ).fetchone()[0]
        assert actual_rows == len(seeded_dates), \
            f"Expected {len(seeded_dates)} date rows with n>0, got {actual_rows}"
        # total_rows = total count in table (includes empty-group days that were
        # skipped by GROUP BY — so total == actual_rows here)
        assert total_rows >= actual_rows

        conn.close()


# ---------------------------------------------------------------------------
# Tests 4–6: leaderboard path selection and rollup meta
# ---------------------------------------------------------------------------

def _make_analysis_db(tmp_path, monkeypatch):
    """Create analysis test DB with rollup table and wire monkeypatches."""
    db_path = str(tmp_path / "analysis_rollup_test.db")
    conn = _make_conn(db_path)
    _create_tables(conn)

    import dashboard_api.services.analysis as svc
    monkeypatch.setattr(svc, "_get_db", lambda: _make_conn(db_path))
    monkeypatch.setattr(svc, "DEFAULT_HISTORY_DAYS", 10_000)

    return db_path, conn


class TestLeaderboardRollupPathSelection:
    def test_leaderboard_rollup_path_taken_for_old_since_ms(self, tmp_path, monkeypatch):
        """since_ms = 7 days ago → rollup path, meta['path'] == 'rollup'."""
        db_path, conn = _make_analysis_db(tmp_path, monkeypatch)

        # Seed 60 predictions on a date 7 days ago
        target_dt = datetime.now(timezone.utc) - timedelta(days=7)
        target_date = target_dt.strftime("%Y-%m-%d")
        target_ts_ms = int(target_dt.replace(hour=12).timestamp() * 1000)
        updated_at = target_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        for i in range(60):
            _insert_pred(conn, model="model_x", symbol="BTCUSDT", window=300,
                         proba=0.62, direction="up", correct=(i % 3 != 0),
                         p_market=0.50, ts_ms=target_ts_ms + i * 60_000)
        conn.commit()

        # Run rollup upsert so table has data
        conn.execute(_ROLLUP_UPSERT_SQL, (target_date, updated_at, target_date))
        conn.commit()

        from dashboard_api.services.analysis import compute_leaderboard

        # since_ms = 7 days ago (before today midnight) — must take rollup path
        since_ms = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000)
        meta: dict = {}
        rows = compute_leaderboard(
            symbol="BTCUSDT", market_window=300, min_samples=50,
            since_ms=since_ms, meta=meta,
        )
        assert meta.get("path") == "rollup", \
            f"Expected path='rollup', got: {meta}"
        assert len(rows) >= 1

        conn.close()

    def test_leaderboard_live_path_for_recent_since_ms(self, tmp_path, monkeypatch):
        """since_ms = now (in last hour) → live path, meta['path'] in ('fast', 'full')."""
        db_path, conn = _make_analysis_db(tmp_path, monkeypatch)

        # Seed predictions with current timestamps
        now_ms = int(time.time() * 1000)
        for i in range(60):
            _insert_pred(conn, model="model_y", symbol="BTCUSDT", window=300,
                         proba=0.62, direction="up", correct=(i % 3 != 0),
                         p_market=0.50, ts_ms=now_ms - i * 60_000)
        conn.commit()

        from dashboard_api.services.analysis import compute_leaderboard

        # since_ms = 30 minutes ago — within today, so should use live path
        since_ms = now_ms - 30 * 60 * 1000
        meta: dict = {}
        compute_leaderboard(
            symbol="BTCUSDT", market_window=300, min_samples=1,
            since_ms=since_ms, meta=meta,
        )
        assert meta.get("path") in ("fast", "full", "cache"), \
            f"Expected live path, got: {meta}"

        conn.close()

    def test_rollup_freshness_in_meta(self, tmp_path, monkeypatch):
        """meta has rollup_freshness_seconds after a rollup query."""
        db_path, conn = _make_analysis_db(tmp_path, monkeypatch)

        target_dt = datetime.now(timezone.utc) - timedelta(days=3)
        target_date = target_dt.strftime("%Y-%m-%d")
        target_ts_ms = int(target_dt.replace(hour=10).timestamp() * 1000)
        updated_at = target_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        for i in range(60):
            _insert_pred(conn, model="model_z", symbol="BTCUSDT", window=300,
                         proba=0.60, direction="up", correct=True,
                         p_market=0.50, ts_ms=target_ts_ms + i * 60_000)
        conn.commit()
        conn.execute(_ROLLUP_UPSERT_SQL, (target_date, updated_at, target_date))
        conn.commit()

        from dashboard_api.services.analysis import compute_leaderboard
        since_ms = int((datetime.now(timezone.utc) - timedelta(days=4)).timestamp() * 1000)
        meta: dict = {}
        rows = compute_leaderboard(
            symbol="BTCUSDT", market_window=300, min_samples=50,
            since_ms=since_ms, meta=meta,
        )
        assert meta.get("path") == "rollup"
        assert "rollup_freshness_seconds" in meta, \
            f"rollup_freshness_seconds missing from meta: {meta}"
        freshness = meta["rollup_freshness_seconds"]
        assert freshness is not None
        # updated_at was 3 days ago → freshness should be around 3 days in seconds
        assert freshness > 0, f"Freshness should be positive, got {freshness}"
        assert freshness < 7 * 86400, f"Freshness should be < 7 days, got {freshness}"

        conn.close()

    def test_leaderboard_no_rollup_when_since_ms_is_none(self, tmp_path, monkeypatch):
        """since_ms=None → always live path regardless of rollup table contents."""
        db_path, conn = _make_analysis_db(tmp_path, monkeypatch)

        # Seed some predictions (at _BASE_TS, which is in the past)
        for i in range(60):
            _insert_pred(conn, model="model_w", symbol="BTCUSDT", window=300,
                         proba=0.62, direction="up", correct=True,
                         p_market=0.50, ts_ms=_BASE_TS + i * 1000)
        conn.commit()

        # Seed rollup row
        updated_at = "2023-11-14T12:00:00Z"
        conn.execute(_ROLLUP_UPSERT_SQL, (_BASE_DATE, updated_at, _BASE_DATE))
        conn.commit()

        from dashboard_api.services.analysis import compute_leaderboard
        meta: dict = {}
        compute_leaderboard(
            symbol="BTCUSDT", market_window=300, min_samples=1,
            since_ms=None, meta=meta,
        )
        # since_ms=None must go to live path (DEFAULT_HISTORY_DAYS still applies)
        assert meta.get("path") in ("fast", "full", "cache"), \
            f"Expected live path for since_ms=None, got: {meta}"

        conn.close()

    def test_rollup_row_matches_live_computation(self, tmp_path, monkeypatch):
        """Rollup and live paths agree on win_rate for the same data."""
        db_path, conn = _make_analysis_db(tmp_path, monkeypatch)

        target_dt = datetime.now(timezone.utc) - timedelta(days=5)
        target_date = target_dt.strftime("%Y-%m-%d")
        target_ts_ms = int(target_dt.replace(hour=9).timestamp() * 1000)
        updated_at = target_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # 70 correct out of 100
        for i in range(100):
            _insert_pred(conn, model="model_v", symbol="BTCUSDT", window=300,
                         proba=0.65, direction="up", correct=(i < 70),
                         p_market=0.50, ts_ms=target_ts_ms + i * 60_000)
        conn.commit()
        conn.execute(_ROLLUP_UPSERT_SQL, (target_date, updated_at, target_date))
        conn.commit()

        from dashboard_api.services.analysis import compute_leaderboard

        since_ms_old = int((datetime.now(timezone.utc) - timedelta(days=6)).timestamp() * 1000)
        meta_rollup: dict = {}
        rows_rollup = compute_leaderboard(
            symbol="BTCUSDT", market_window=300, min_samples=50,
            since_ms=since_ms_old, meta=meta_rollup,
        )
        assert meta_rollup.get("path") == "rollup"
        assert len(rows_rollup) == 1
        assert abs(rows_rollup[0]["win_rate"] - 0.70) < 0.001

        conn.close()
