"""Database connection factory.

All v3 storage flows through a single SQLite database. WAL mode is
enabled so the API server can read concurrently with the paper trader's
writes. Foreign keys are enforced for prediction → trade integrity.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = "/data/v3.db"


def open_database(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (and create if needed) the v3 SQLite database.

    Returns a connection with WAL journaling, foreign keys enforced,
    and a 30-second busy timeout to handle multi-process contention
    between the API server (rollup/tier/governance loops) and the paper
    trader (high-volume prediction writes). 5s was crashing the trader
    on lock contention during retrain windows.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    # Auto-checkpoint more aggressively (default = 1000 pages ~ 4 MB).
    # Without this the WAL grows past 100 MB under our write rate
    # (predictions + tier_score + rollup + governance_actions) and
    # explicit checkpoints contend with active writers, producing
    # 'database is locked' crashes. 200 pages = ~800 KB threshold.
    conn.execute("PRAGMA wal_autocheckpoint = 200")
    return conn


_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_schema(conn) -> None:
    """Apply the canonical schema. Idempotent.

    Reads ``storage/schema.sql`` and executes its DDL. SQLite's
    ``CREATE TABLE`` and ``CREATE INDEX`` are not natively idempotent,
    so the SQL file uses ``CREATE TABLE IF NOT EXISTS`` and
    ``CREATE INDEX IF NOT EXISTS`` for safe re-application.

    Also runs in-place ALTER TABLE migrations for new columns on
    existing tables (safe to re-run — checks column existence first).
    """
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    _run_migrations(conn)


def _run_migrations(conn) -> None:
    _add_column_if_missing(conn, "model_registry", "filter_config_json", "TEXT DEFAULT '{}'")
    _add_column_if_missing(conn, "model_registry", "platform_active_json",
        "TEXT DEFAULT '{\"paper\":true,\"kalshi\":false,\"polymarket\":false}'")
    # T3.2 — explicit fleet grouping column. Backfilled from train_window_end for legacy rows.
    _add_column_if_missing(conn, "model_registry", "fleet_version", "TEXT")
    conn.execute(
        "UPDATE model_registry SET fleet_version = train_window_end WHERE fleet_version IS NULL"
    )
    # Phase 5 — scheduled cutover lifecycle. Newly-trained non-baseline models
    # land as cutover_state='scheduled' + paper_active=0 with cutover_scheduled_at
    # set to NOW+24h (configurable). A background loop in dashboard_api flips
    # them to paper_active=1 / cutover_state='cutover' when the time arrives.
    # Existing rows are backfilled with cutover_state='cutover' (already live).
    _add_column_if_missing(conn, "model_registry", "cutover_scheduled_at", "TEXT")
    _add_column_if_missing(
        conn, "model_registry", "cutover_state", "TEXT DEFAULT 'cutover'"
    )
    _add_column_if_missing(conn, "model_registry", "cutover_decided_by", "TEXT")
    _add_column_if_missing(conn, "model_registry", "cutover_decided_at", "TEXT")
    # Backfill legacy rows: they are already live, so state='cutover',
    # decided_by='auto' (assume past auto-promotion), decided_at=created_at.
    conn.execute(
        "UPDATE model_registry SET cutover_state = 'cutover' "
        "WHERE cutover_state IS NULL"
    )
    conn.execute(
        "UPDATE model_registry SET cutover_decided_by = 'auto' "
        "WHERE cutover_decided_by IS NULL AND cutover_state = 'cutover'"
    )
    conn.execute(
        "UPDATE model_registry SET cutover_decided_at = created_at "
        "WHERE cutover_decided_at IS NULL AND cutover_state = 'cutover'"
    )
    # H2 — decay-join leakage hardening. Denormalized ms timestamp + window
    # cutoff sentinel for strict-< joins from analysis service.
    _add_column_if_missing(conn, "decay_metrics", "ts_ms", "INTEGER")
    _add_column_if_missing(conn, "decay_metrics", "computed_for_max_ts_ms", "INTEGER")
    _migrate_native_to_eval_indexes(conn)
    # Phase 3 — adaptive governance. Tier lifecycle + composite tier scores +
    # action audit. All additive; legacy rows backfilled with tier='watch'
    # except baselines (kept gold) so existing fleet keeps trading.
    _add_column_if_missing(conn, "model_registry", "tier", "TEXT DEFAULT 'watch'")
    _add_column_if_missing(conn, "model_registry", "tier_assigned_at", "TEXT")
    _add_column_if_missing(conn, "model_registry", "tier_assigned_by", "TEXT")
    _add_column_if_missing(conn, "model_registry", "kelly_multiplier", "REAL DEFAULT 0.0")
    _add_column_if_missing(conn, "model_registry", "probation_start_at", "TEXT")
    _add_column_if_missing(conn, "model_registry", "probation_end_at", "TEXT")
    _add_column_if_missing(conn, "model_registry", "parent_model_name", "TEXT")
    # Patch B — demote tick should only count alerts from the model's primary
    # market_window (the contract window nearest its training horizon).
    # Models trained for h60-h300 -> primary 300s; h600-h900 -> 900s; h1200+ -> 1800s.
    _add_column_if_missing(
        conn, "model_registry", "primary_market_window_seconds", "INTEGER"
    )
    conn.execute(
        "UPDATE model_registry SET primary_market_window_seconds = "
        " CASE "
        "   WHEN training_horizon_seconds <= 300 THEN 300 "
        "   WHEN training_horizon_seconds <= 900 THEN 900 "
        "   ELSE 1800 "
        " END "
        " WHERE primary_market_window_seconds IS NULL "
        "   AND training_horizon_seconds IS NOT NULL"
    )
    # Backfill: paper_active=1 + cutover_state='cutover' rows ARE the current
    # gold incumbents (kelly=1.0). Baselines stay gold. Everything else watch.
    # CRITICAL: only backfill rows the governance loop hasn't touched yet
    # (tier_assigned_by IS NULL). Once governance demotes a model, migration
    # MUST NOT re-promote it on the next restart.
    conn.execute(
        "UPDATE model_registry "
        "   SET tier = 'gold', kelly_multiplier = 1.0, "
        "       tier_assigned_by = 'auto:migration_initial' "
        " WHERE paper_active = 1 AND cutover_state = 'cutover' "
        "   AND COALESCE(is_baseline, 0) = 0 "
        "   AND tier_assigned_by IS NULL"
    )
    conn.execute(
        "UPDATE model_registry "
        "   SET tier = 'watch', kelly_multiplier = 0.0, "
        "       tier_assigned_by = 'auto:migration_initial' "
        " WHERE tier IS NULL"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cell_governance (
            cell_key                TEXT PRIMARY KEY,
            symbol                  TEXT NOT NULL,
            horizon_seconds         INTEGER NOT NULL,
            training_days           INTEGER NOT NULL,
            incumbent_model_name    TEXT,
            challenger_model_name   TEXT,
            last_promotion_at       TEXT,
            last_demotion_at        TEXT,
            notes                   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_tier_score (
            id                              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                              TEXT NOT NULL,
            model_name                      TEXT NOT NULL,
            symbol                          TEXT NOT NULL,
            market_window_seconds           INTEGER NOT NULL,
            regime_label                    TEXT,
            composite_score                 REAL NOT NULL,
            component_live_rwev             REAL,
            component_paper_rwev            REAL,
            component_walk_forward_ev       REAL,
            component_calibration_drift     REAL,
            component_decay_slope           REAL,
            component_stability             REAL,
            weights_json                    TEXT,
            sample_count                    INTEGER NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mts_model ON model_tier_score(model_name, ts)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mts_cell "
        "ON model_tier_score(symbol, market_window_seconds, ts)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS governance_actions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              TEXT NOT NULL,
            model_name      TEXT NOT NULL,
            action          TEXT NOT NULL,
            from_tier       TEXT,
            to_tier         TEXT,
            triggered_by    TEXT NOT NULL,
            reason_json     TEXT NOT NULL,
            cell_key        TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gov_actions_model_ts "
        "ON governance_actions(model_name, ts)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gov_actions_cell_ts "
        "ON governance_actions(cell_key, ts)"
    )
    # Phase 6c — retrain queue. Auto-fills when a cell has no gold/silver incumbent.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS retrain_queue (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cell_key        TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            horizon_seconds INTEGER NOT NULL,
            training_days   INTEGER NOT NULL,
            requested_at    TEXT NOT NULL,
            triggered_by    TEXT NOT NULL,
            picked_up_at    TEXT,
            picked_up_by    TEXT,
            notes           TEXT,
            UNIQUE(cell_key, requested_at)
        )
    """)
    # Phase 57 — per-(model, market_window) tier. Signal lives at the
    # deployment context (window), so tier/kelly/dispatch should too.
    # Probation stays model-level on model_registry (challenger lifecycle).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_window_tier (
            model_name              TEXT NOT NULL,
            market_window_seconds   INTEGER NOT NULL,
            tier                    TEXT NOT NULL DEFAULT 'watch',
            kelly_multiplier        REAL NOT NULL DEFAULT 0.0,
            tier_assigned_at        TEXT,
            tier_assigned_by        TEXT,
            PRIMARY KEY (model_name, market_window_seconds)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mwt_tier "
        "ON model_window_tier(tier, market_window_seconds)"
    )
    # Backfill: 3 rows per existing non-baseline model (windows 300/900/1800).
    # Rows whose window matches model_registry.primary_market_window_seconds AND
    # model_registry.tier='gold' carry forward as gold. All others tier='watch'.
    # INSERT OR IGNORE keeps this idempotent across restarts.
    for win in (300, 900, 1800):
        conn.execute(
            "INSERT OR IGNORE INTO model_window_tier"
            " (model_name, market_window_seconds, tier, kelly_multiplier,"
            "  tier_assigned_at, tier_assigned_by)"
            " SELECT mr.name, ?,"
            "        CASE"
            "          WHEN mr.tier = 'gold' AND mr.primary_market_window_seconds = ?"
            "            THEN 'gold'"
            "          WHEN mr.tier = 'retired' THEN 'retired'"
            "          ELSE 'watch'"
            "        END,"
            "        CASE"
            "          WHEN mr.tier = 'gold' AND mr.primary_market_window_seconds = ?"
            "            THEN 1.0"
            "          ELSE 0.0"
            "        END,"
            "        COALESCE(mr.tier_assigned_at,"
            "                 strftime('%Y-%m-%dT%H:%M:%SZ','now')),"
            "        'auto:phase57_backfill'"
            "   FROM model_registry mr"
            "  WHERE COALESCE(mr.is_baseline, 0) = 0",
            (win, win, win),
        )

    # T1.3 — composite index on decay_evaluations covering governance demote
    # queries (dashboard_api/main.py gold→silver + silver→watch ticks). They
    # filter on model_name + market_window_seconds + eval_type + triggered + ts;
    # existing idx_decay_eval_model only covers (model_name, eval_type, ts).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decay_eval_full "
        "ON decay_evaluations(model_name, market_window_seconds, eval_type, triggered, ts DESC)"
    )
    # 2026-05-29 — model_selection.updated_at / updated_by were referenced
    # by routers/models_admin.py PUT /model-selection but never added to the
    # schema. SQLite ALTER TABLE ADD COLUMN rejects non-constant defaults
    # (no strftime); CREATE TABLE in schema.sql carries the default for
    # fresh DBs, and rows on existing DBs get NULL until the next write.
    _add_column_if_missing(conn, "model_selection", "updated_at", "TEXT")
    _add_column_if_missing(conn, "model_selection", "updated_by", "TEXT")
    # T2 — predictions_daily_rollup: materialized daily aggregates for fast
    # leaderboard queries.  The full DDL lives in schema.sql; we also create
    # it here so _run_migrations() stays the single migration entry point.
    conn.execute("""
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
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pdr_date "
        "ON predictions_daily_rollup(date_utc)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pdr_symbol_window_date "
        "ON predictions_daily_rollup(symbol, market_window_seconds, date_utc)"
    )
    # 2026-06-09 — per-prediction feature observability. The exact feature
    # vector fed to the booster + the served contract order, written on the
    # canonical (first) row of each prediction set. Enables faithful replay
    # and continuous alignment auditing; feature_names_hash alone proves the
    # name list, not the values.
    _add_column_if_missing(conn, "predictions", "features_json", "TEXT")
    _add_column_if_missing(conn, "predictions", "served_contract_json", "TEXT")
    # 2026-06-10 — flat-scoring separation. Model-quality lens: flats are
    # no-contest (prediction_correct=NULL, excluded from win rates). Trading
    # lens (paper_trades): flat resolves DOWN per contract semantics, so a
    # DOWN call on flat is a win with gross = stake*(1-p)/p. Historical flats
    # were scored as losses for both directions in both tables. Idempotent:
    # the WHERE clauses match only legacy-scored rows.
    conn.execute(
        "UPDATE predictions SET prediction_correct = NULL "
        "WHERE contract_result = 'flat' AND prediction_correct = 0"
    )
    # 2026-06-10 — CORRECTION: live Polymarket market descriptions say flat
    # resolves UP ("greater than or equal"), not DOWN as first assumed.
    # Flat-DOWN trades are losses; flat-UP trades are wins. Both statements
    # idempotent (state-matched WHERE clauses); the first also reverts the
    # short-lived flat-DOWN-wins backfill that shipped 2026-06-10 morning.
    conn.execute(
        "UPDATE paper_trades SET prediction_correct = 0, trade_result = 'loss', "
        "  gross_pnl = -simulated_stake_usdc, "
        "  net_pnl   = -simulated_stake_usdc - fee_paid "
        "WHERE contract_result = 'flat' AND pred_direction = 'down' "
        "  AND trade_result = 'win' AND pnl_method = 'binary_polymarket'"
    )
    conn.execute(
        "UPDATE paper_trades SET prediction_correct = 1, trade_result = 'win', "
        "  gross_pnl = simulated_stake_usdc * (1 - pred_proba_calibrated) / pred_proba_calibrated, "
        "  net_pnl   = simulated_stake_usdc * (1 - pred_proba_calibrated) / pred_proba_calibrated - fee_paid "
        "WHERE contract_result = 'flat' AND pred_direction = 'up' "
        "  AND trade_result = 'loss' AND pnl_method = 'binary_polymarket' "
        "  AND pred_proba_calibrated > 0"
    )


def _migrate_native_to_eval_indexes(conn) -> None:
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()}
    if "idx_pred_native_for_decay" in existing:
        conn.execute("DROP INDEX idx_pred_native_for_decay")
        conn.execute(
            "CREATE INDEX idx_pred_eval_for_decay"
            " ON predictions(model_name, symbol, resolution_type, ts_contract_open_ms)"
            " WHERE resolution_type = 'evaluation' AND resolved = 1"
        )
    if "idx_trade_native_for_decay" in existing:
        conn.execute("DROP INDEX idx_trade_native_for_decay")
        conn.execute(
            "CREATE INDEX idx_trade_eval_for_decay"
            " ON paper_trades(model_name, symbol, resolution_type, ts_contract_open_ms)"
            " WHERE resolution_type = 'evaluation' AND resolved = 1"
        )
    if "idx_cal_native" in existing:
        conn.execute("DROP INDEX idx_cal_native")
        conn.execute(
            "CREATE INDEX idx_cal_eval"
            " ON calibration_outcomes(model_name, resolution_type)"
            " WHERE resolution_type = 'evaluation'"
        )


def _add_column_if_missing(conn, table: str, column: str, col_type: str) -> None:
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
