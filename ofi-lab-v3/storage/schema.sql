-- Canonical v3 schema. Applied by storage.db.init_schema().
-- Every CREATE uses IF NOT EXISTS so init_schema is idempotent.

-- =========================================================================
-- predictions: every model score, all evaluation rows.
-- =========================================================================
CREATE TABLE IF NOT EXISTS predictions (
    -- Identity
    prediction_id          TEXT PRIMARY KEY,

    -- Provenance: model identity
    model_name             TEXT NOT NULL,
    model_artifact_hash    TEXT NOT NULL,
    feature_names_hash     TEXT NOT NULL,
    feature_version        TEXT NOT NULL,
    training_horizon_seconds INTEGER NOT NULL,
    train_window_start     TEXT,
    train_window_end       TEXT,
    train_cutoff           TEXT,
    registry_load_generation INTEGER NOT NULL,

    -- Provenance: policy + calibration identity
    policy_config_hash     TEXT NOT NULL,
    decision_policy_version INTEGER NOT NULL,
    calibration_map_hash   TEXT NOT NULL,

    -- Symbol & evaluation window
    symbol                 TEXT NOT NULL,
    market_window_seconds  INTEGER NOT NULL,
    resolution_type        TEXT NOT NULL CHECK (resolution_type IN ('native','evaluation')),

    -- Timing
    ts_model_ran_ms        INTEGER NOT NULL,
    ts_contract_open_ms    INTEGER NOT NULL,
    ts_resolve_at_ms       INTEGER NOT NULL,

    -- Prediction
    pred_proba_raw         REAL NOT NULL,
    pred_proba_calibrated  REAL NOT NULL,
    pred_direction         TEXT NOT NULL CHECK (pred_direction IN ('up','down')),
    above_threshold        INTEGER NOT NULL,

    -- State flags
    warmup                 INTEGER NOT NULL DEFAULT 0,
    trade_eligible         INTEGER NOT NULL DEFAULT 1,
    platform               TEXT NOT NULL CHECK (platform IN ('paper','kalshi','polymarket')),

    -- Market context
    p_market               REAL,
    p_model_minus_market   REAL,

    -- Time context
    utc_hour               INTEGER,
    day_of_week            INTEGER,
    is_weekend             INTEGER,

    -- Regime tags (populated in Plan B; reserved here so schema is stable)
    regime_volatility      TEXT,
    regime_liquidity       TEXT,
    regime_trend           TEXT,
    relative_spread        REAL,

    -- Resolution
    price_at_open          REAL,
    price_at_close         REAL,
    contract_result        TEXT,
    prediction_correct     INTEGER,
    resolved               INTEGER NOT NULL DEFAULT 0,
    ts_resolved_ms         INTEGER,

    -- Compact decision trace (inline)
    decision_outcome       TEXT,
    decision_reason        TEXT,
    ev_estimate            REAL,
    kelly_fraction_capped  REAL,
    final_size_usdc        REAL,
    order_type             TEXT,

    -- Audit
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_pred_model_symbol      ON predictions(model_name, symbol);
CREATE INDEX IF NOT EXISTS idx_pred_ts                ON predictions(ts_contract_open_ms);
CREATE INDEX IF NOT EXISTS idx_pred_market_window     ON predictions(market_window_seconds);
CREATE INDEX IF NOT EXISTS idx_pred_unresolved        ON predictions(resolved) WHERE resolved = 0;
CREATE INDEX IF NOT EXISTS idx_pred_warmup            ON predictions(warmup);
CREATE INDEX IF NOT EXISTS idx_pred_resolution_type   ON predictions(resolution_type);
CREATE INDEX IF NOT EXISTS idx_pred_generation        ON predictions(model_name, registry_load_generation);
CREATE INDEX IF NOT EXISTS idx_pred_eval_for_decay ON predictions(model_name, symbol, resolution_type, ts_contract_open_ms)
WHERE resolution_type = 'evaluation' AND resolved = 1;

-- Idempotency: prevent duplicate scoring on hot reload re-entry.
CREATE UNIQUE INDEX IF NOT EXISTS idx_pred_idempotent ON predictions(
    model_name, symbol, ts_contract_open_ms, market_window_seconds, registry_load_generation
);

-- =========================================================================
-- paper_trades: predictions that pass paper-tier filters become rows here.
-- =========================================================================
CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id               TEXT PRIMARY KEY,
    prediction_id          TEXT NOT NULL REFERENCES predictions(prediction_id),

    -- Provenance copy (denormalized for fast queries; matches prediction row)
    model_name             TEXT NOT NULL,
    model_artifact_hash    TEXT NOT NULL,
    policy_config_hash     TEXT NOT NULL,
    decision_policy_version INTEGER NOT NULL,
    calibration_map_hash   TEXT NOT NULL,
    registry_load_generation INTEGER NOT NULL,
    feature_version        TEXT NOT NULL,
    training_horizon_seconds INTEGER NOT NULL,

    -- Symbol & window
    symbol                 TEXT NOT NULL,
    market_window_seconds  INTEGER NOT NULL,
    resolution_type        TEXT NOT NULL CHECK (resolution_type IN ('native','evaluation')),

    -- Timing
    ts_model_ran_ms        INTEGER NOT NULL,
    ts_contract_open_ms    INTEGER NOT NULL,
    ts_resolve_at_ms       INTEGER NOT NULL,

    -- Trade params
    pred_proba_raw         REAL NOT NULL,
    pred_proba_calibrated  REAL NOT NULL,
    pred_direction         TEXT NOT NULL,
    confidence_threshold_used REAL NOT NULL,
    simulated_stake_usdc   REAL,
    p_market               REAL,
    suppressed_reason      TEXT,
    filter_mode            TEXT,
    warmup                 INTEGER NOT NULL DEFAULT 0,
    platform               TEXT NOT NULL,

    -- Compact decision trace (inline)
    decision_outcome       TEXT NOT NULL,
    decision_reason        TEXT,
    ev_estimate            REAL,
    kelly_fraction_capped  REAL,
    final_size_usdc        REAL,
    order_type             TEXT,

    -- Resolution
    price_at_open          REAL,
    price_at_close         REAL,
    contract_result        TEXT,
    prediction_correct     INTEGER,
    gross_pnl              REAL,
    fee_paid               REAL,
    net_pnl                REAL,
    trade_result           TEXT,
    pnl_method             TEXT,
    resolved               INTEGER NOT NULL DEFAULT 0,
    ts_resolved_ms         INTEGER,

    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_trade_model            ON paper_trades(model_name, symbol);
CREATE INDEX IF NOT EXISTS idx_trade_unresolved       ON paper_trades(resolved) WHERE resolved = 0;
CREATE INDEX IF NOT EXISTS idx_trade_resolution_type  ON paper_trades(resolution_type);
CREATE INDEX IF NOT EXISTS idx_trade_eval_for_decay ON paper_trades(model_name, symbol, resolution_type, ts_contract_open_ms)
WHERE resolution_type = 'evaluation' AND resolved = 1;

-- =========================================================================
-- decision_traces: verbose forensic trace, append-only, linked by prediction_id.
-- =========================================================================
CREATE TABLE IF NOT EXISTS decision_traces (
    trace_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id          TEXT NOT NULL,
    ts                     TEXT NOT NULL,

    -- Filter chain serialized as JSON array
    --   [{"name": "...", "threshold": ..., "input_value": ..., "passed": 0|1}, ...]
    filters_json           TEXT NOT NULL,

    -- Kelly math breakdown
    kelly_raw              REAL,
    kelly_capped           REAL,
    bankroll_used          REAL,
    per_trade_cap_usdc     REAL,

    -- Fee model
    fee_model              TEXT NOT NULL,
    fee_amount             REAL,

    -- Platform-specific gating snapshot (JSON)
    platform_gate_json     TEXT,

    -- Context echoed for forensic completeness
    warmup                 INTEGER NOT NULL,
    consensus_data_json    TEXT,
    policy_config_hash     TEXT NOT NULL,
    calibration_map_hash   TEXT NOT NULL,
    registry_load_generation INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trace_pid ON decision_traces(prediction_id);
CREATE INDEX IF NOT EXISTS idx_trace_ts  ON decision_traces(ts);

-- =========================================================================
-- calibration_outcomes: one row per evaluation resolution, used for refit + decay.
-- =========================================================================
CREATE TABLE IF NOT EXISTS calibration_outcomes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                     TEXT NOT NULL,
    prediction_id          TEXT NOT NULL,
    model_name             TEXT NOT NULL,
    symbol                 TEXT NOT NULL,
    market_window_seconds  INTEGER NOT NULL,
    resolution_type        TEXT NOT NULL,
    side_conf              REAL NOT NULL,
    won                    INTEGER NOT NULL,
    warmup                 INTEGER NOT NULL DEFAULT 0,
    regime_volatility      TEXT,
    regime_liquidity       TEXT
);

CREATE INDEX IF NOT EXISTS idx_cal_model ON calibration_outcomes(model_name, symbol, market_window_seconds);
CREATE INDEX IF NOT EXISTS idx_cal_eval ON calibration_outcomes(model_name, resolution_type) WHERE resolution_type = 'evaluation';

-- =========================================================================
-- registry_audit: append-only log of registry generation increments.
--                 Plan A only writes the bootstrap row; Plan B writes on reload.
-- =========================================================================
CREATE TABLE IF NOT EXISTS registry_audit (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                     TEXT NOT NULL,
    generation             INTEGER NOT NULL,
    reason                 TEXT NOT NULL,
    detail_json            TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_generation ON registry_audit(generation);

-- =========================================================================
-- policy_audit: append-only log of policy version increments.
-- =========================================================================
CREATE TABLE IF NOT EXISTS policy_audit (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                     TEXT NOT NULL,
    decision_policy_version INTEGER NOT NULL,
    policy_config_hash     TEXT NOT NULL,
    snapshot_json          TEXT NOT NULL,
    initiated_by           TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_version ON policy_audit(decision_policy_version);

-- =========================================================================
-- model_overlap: per-boundary cross-model agreement record.
-- =========================================================================
CREATE TABLE IF NOT EXISTS model_overlap (
    ts_contract_open_ms    INTEGER NOT NULL,
    symbol                 TEXT NOT NULL,
    market_window_seconds  INTEGER NOT NULL,
    models_scored_json     TEXT NOT NULL,
    directions_json        TEXT NOT NULL,
    confidences_json       TEXT NOT NULL,
    consensus              INTEGER NOT NULL,
    consensus_direction    TEXT,
    weighted_confidence    REAL,
    registry_load_generation INTEGER NOT NULL,
    PRIMARY KEY (ts_contract_open_ms, symbol, market_window_seconds, registry_load_generation)
);

CREATE INDEX IF NOT EXISTS idx_overlap_symbol_window
    ON model_overlap(symbol, market_window_seconds);

-- =========================================================================
-- decay_metrics: rolling decay snapshots per (model, symbol, market_window).
-- =========================================================================
CREATE TABLE IF NOT EXISTS decay_metrics (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                     TEXT NOT NULL,
    model_name             TEXT NOT NULL,
    symbol                 TEXT NOT NULL,
    market_window_seconds  INTEGER NOT NULL,
    window_size            INTEGER NOT NULL,
    rolling_ev             REAL,
    recency_weighted_ev    REAL,
    rolling_win_rate       REAL,
    brier_score            REAL,
    calibration_error      REAL,
    sample_count           INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decay_model
    ON decay_metrics(model_name, symbol, market_window_seconds, ts);

-- =========================================================================
-- decay_evaluations: PSI / cliff / threshold check log.
-- =========================================================================
CREATE TABLE IF NOT EXISTS decay_evaluations (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                     TEXT NOT NULL,
    model_name             TEXT NOT NULL,
    symbol                 TEXT NOT NULL,
    market_window_seconds  INTEGER NOT NULL,
    eval_type              TEXT NOT NULL,
    metric_value           REAL,
    threshold              REAL,
    triggered              INTEGER NOT NULL,
    detail_json            TEXT
);

CREATE INDEX IF NOT EXISTS idx_decay_eval_model
    ON decay_evaluations(model_name, eval_type, ts);

-- T1.3 — composite index for governance demote queries
-- (model_name + market_window_seconds + eval_type + triggered + ts).
CREATE INDEX IF NOT EXISTS idx_decay_eval_full
    ON decay_evaluations(model_name, market_window_seconds, eval_type, triggered, ts DESC);

-- =========================================================================
-- regime_thresholds: per-symbol volatility/liquidity/trend quartiles.
-- =========================================================================
CREATE TABLE IF NOT EXISTS regime_thresholds (
    symbol                 TEXT PRIMARY KEY,
    thresholds_json        TEXT NOT NULL,
    updated_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- =========================================================================
-- regime_features_latest: latest feature snapshot per symbol for tagging.
-- =========================================================================
CREATE TABLE IF NOT EXISTS regime_features_latest (
    symbol                 TEXT PRIMARY KEY,
    vwap_dev_30s_std       REAL,
    mlofi_60s_std          REAL,
    relative_spread        REAL,
    spread_5m_pct          REAL,
    mlofi_momentum         REAL,
    vwap_2m_deviation      REAL,
    ts_updated_ms          INTEGER NOT NULL DEFAULT (cast(strftime('%s','now') as integer) * 1000),
    updated_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- =========================================================================
-- calibration_bins: histogram bins for model calibration curves.
-- =========================================================================
CREATE TABLE IF NOT EXISTS calibration_bins (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name             TEXT NOT NULL,
    bin_lo                 REAL NOT NULL,
    bin_hi                 REAL NOT NULL,
    observed_freq          REAL NOT NULL,
    n                      INTEGER NOT NULL,
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_cal_bins_model
    ON calibration_bins(model_name, bin_lo);

-- =========================================================================
-- calibration_summary: per-model calibration metrics.
-- =========================================================================
CREATE TABLE IF NOT EXISTS calibration_summary (
    model_name             TEXT PRIMARY KEY,
    brier                  REAL,
    log_loss               REAL,
    n_obs                  INTEGER,
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- =========================================================================
-- model_registry: runtime state of models (paper_active, live_eligible, etc).
-- =========================================================================
CREATE TABLE IF NOT EXISTS model_registry (
    name                   TEXT PRIMARY KEY,
    is_baseline            INTEGER NOT NULL DEFAULT 0,
    paper_active           INTEGER NOT NULL DEFAULT 1,
    live_eligible          INTEGER NOT NULL DEFAULT 0,
    lifecycle_state        TEXT NOT NULL DEFAULT 'prediction_only',
    symbol                 TEXT,
    training_horizon_seconds INTEGER,
    generation             INTEGER NOT NULL DEFAULT 0,
    artifact_path          TEXT,
    feature_names_path     TEXT,
    artifact_hash          TEXT,
    train_window_start     TEXT,
    train_window_end       TEXT,
    train_days             INTEGER,
    feature_version        TEXT DEFAULT 'v3',
    evaluation_windows TEXT DEFAULT '[300,900,1800]',
    filter_config_json TEXT DEFAULT '{}',
    platform_active_json TEXT DEFAULT '{"paper":true,"kalshi":false,"polymarket":false}',
    -- fleet_version (TEXT) — explicit fleet grouping, added via _run_migrations() ALTER TABLE.
    -- Backfilled from train_window_end for legacy rows. New registrations supply it directly.
    --
    -- Phase 5 scheduled-cutover columns. ALTER TABLE in _run_migrations() is the
    -- source of truth; declared here for documentation. New non-baseline models
    -- are inserted as paper_active=0 + cutover_state='scheduled' with
    -- cutover_scheduled_at = NOW + 24h. A background loop in dashboard_api
    -- flips them to paper_active=1 / cutover_state='cutover' when the time
    -- arrives. Legacy rows are backfilled with cutover_state='cutover',
    -- cutover_decided_by='auto', cutover_decided_at=created_at.
    -- Allowed cutover_state values: 'pending' | 'scheduled' | 'cutover' | 'skipped'
    cutover_scheduled_at TEXT,
    cutover_state TEXT DEFAULT 'cutover',
    cutover_decided_by TEXT,
    cutover_decided_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_reg_symbol_horizon ON model_registry(symbol, training_horizon_seconds);

-- =========================================================================
-- model_audit: audit trail of model state changes.
-- =========================================================================
CREATE TABLE IF NOT EXISTS model_audit (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name             TEXT NOT NULL,
    action                 TEXT NOT NULL,
    by_user                TEXT NOT NULL,
    detail                 TEXT,
    ts                     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_model_audit_name ON model_audit(model_name, ts);

-- =========================================================================
-- calibration_map: per-model isotonic regression coefficients.
-- Fitted from resolved evaluation predictions; supports provenance via map_hash.
-- =========================================================================
CREATE TABLE IF NOT EXISTS calibration_map (
    model_name      TEXT PRIMARY KEY,
    x_json          TEXT NOT NULL,
    y_json          TEXT NOT NULL,
    fit_at_ms       INTEGER NOT NULL,
    n_obs           INTEGER NOT NULL,
    map_hash        TEXT NOT NULL,
    fit_method      TEXT NOT NULL DEFAULT 'isotonic'
);

CREATE INDEX IF NOT EXISTS idx_calib_map_hash ON calibration_map(map_hash);

-- =========================================================================
-- model_selection: per-(symbol, window) strategy for committee/selection.
-- =========================================================================
CREATE TABLE IF NOT EXISTS model_selection (
    symbol TEXT NOT NULL,
    market_window_seconds INTEGER NOT NULL,
    strategy TEXT NOT NULL DEFAULT 'all',
    selected_model_name TEXT,
    committee_config_json TEXT DEFAULT '{}',
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_by TEXT,
    PRIMARY KEY (symbol, market_window_seconds)
);

-- =========================================================================
-- dashboard_settings: server-persisted operator/dashboard preferences.
-- =========================================================================
CREATE TABLE IF NOT EXISTS dashboard_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,  -- JSON-encoded
    updated_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_by  TEXT
);

-- =========================================================================
-- committee_weights: optimized per-(symbol, market_window) ensemble weights.
-- Written by analysis.optimize_committee_weights. Read by future ModelSelector
-- when strategy='committee_weighted'.
-- =========================================================================
CREATE TABLE IF NOT EXISTS committee_weights (
    symbol                 TEXT NOT NULL,
    market_window_seconds  INTEGER NOT NULL,
    objective              TEXT NOT NULL,        -- 'sharpe' | 'mean' | 'win_rate'
    weights_json           TEXT NOT NULL,        -- {"model_name": weight, ...}
    n_boundaries           INTEGER,
    expected_sharpe        REAL,
    expected_win_rate      REAL,
    expected_roi_pct       REAL,
    converged              INTEGER DEFAULT 1,    -- 0 if scipy fallback used
    computed_at            TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (symbol, market_window_seconds, objective)
);

-- =========================================================================
-- decay_metrics hardening columns (H2): denormalized ms timestamp + window
-- cutoff sentinel. Both required for leak-safe timestamp joins from analysis.
-- Pre-existing rows will have NULL until next refresh_decay_metrics() write.
-- =========================================================================
-- NOTE: SQLite has no IF NOT EXISTS for ALTER TABLE. The migration writer
-- (storage.db._migrate or analysis service init) must check + add.
-- See storage/db.py:_ensure_decay_metrics_hardening.

-- =========================================================================
-- analysis_cache (T1.2): persistent blob cache for slow /api/analysis/*
-- endpoints. Pre-populated every 5 min by the dashboard background loop so
-- warm reads return in <50 ms. Multi-worker safe via INSERT OR REPLACE
-- + WAL mode. payload_json is JSON-encoded (dumps with default=str).
-- =========================================================================
CREATE TABLE IF NOT EXISTS analysis_cache (
    key             TEXT PRIMARY KEY,
    payload_json    TEXT NOT NULL,
    computed_at_ms  INTEGER NOT NULL,
    duration_ms     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_analysis_cache_computed_at
    ON analysis_cache(computed_at_ms);

-- =========================================================================
-- predictions_daily_rollup (T2): materialized daily aggregates for fast
-- leaderboard queries over multi-week windows.
--
-- PnL (sum_pnl) is NE_t per $1 stake = compute_realized_net() inline:
--   up+correct:   1 - p_market - 0.009
--   up+wrong:   -(p_market + 0.009)
--   down+correct: p_market - 0.009
--   down+wrong: -(1 - p_market + 0.009)
-- Rows where p_market IS NULL contribute 0 to pnl sums; n_resolved_trades
-- counts only rows where p_market IS NOT NULL.
--
-- sum_pnl_sq enables Sharpe reconstruction: var = (n*sum_sq - sum^2)/(n*(n-1))
-- sum_brier_terms = SUM((side_p - outcome)^2) where side_p=max(pp, 1-pp)
-- =========================================================================
CREATE TABLE IF NOT EXISTS predictions_daily_rollup (
  model_name              TEXT NOT NULL,
  symbol                  TEXT NOT NULL,
  market_window_seconds   INTEGER NOT NULL,
  date_utc                TEXT NOT NULL,        -- YYYY-MM-DD
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
CREATE INDEX IF NOT EXISTS idx_pdr_date ON predictions_daily_rollup(date_utc);
CREATE INDEX IF NOT EXISTS idx_pdr_symbol_window_date ON predictions_daily_rollup(symbol, market_window_seconds, date_utc);

-- =========================================================================
-- training_data_snapshots (Tier 3 lineage): per-model fingerprint of the
-- feature distribution the model was trained on, plus the training-time
-- outcome stats. Computed at registration by scripts/register_model.py
-- (or by the backfill in scripts/backfill_training_snapshots.py for
-- existing fleets). One row per model.
--
-- feature_dist_json schema:
--   {
--     "symbol": "BTCUSDT",
--     "n_train_obs": 123456,
--     "features": {
--       "<feature_name>": {
--         "p01": ..., "p05": ..., "p25": ..., "p50": ...,
--         "p75": ..., "p95": ..., "p99": ...,
--         "mean": ..., "std": ..., "n_nulls": ...
--       },
--       ...
--     }
--   }
--
-- Drift between two snapshots = max KS over per-feature percentile
-- fingerprints (computed on demand by services/training_drift.py).
-- =========================================================================
CREATE TABLE IF NOT EXISTS training_data_snapshots (
    model_name             TEXT PRIMARY KEY,
    feature_names_hash     TEXT,
    feature_dist_json      TEXT,
    training_brier         REAL,
    training_log_loss      REAL,
    training_auc           REAL,
    n_train_obs            INTEGER,
    computed_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_training_snapshots_computed_at
    ON training_data_snapshots(computed_at);

-- =========================================================================
-- retrain_confidence_models (Tier 4 lineage): fitted linear-regression
-- coefficients that map predecessor metrics + training drift + cell history
-- into a predicted post-retrain metric band. Refit weekly after each
-- retrain finishes; older fits retained for audit.
--
-- coefs_json = {beta_0, beta_predecessor, beta_drift,
--               beta_cell_residual_sd, beta_brier_delta, beta_log_n_obs}
-- residual_sd = std of regression residuals (used as the 1-sigma band)
-- =========================================================================
CREATE TABLE IF NOT EXISTS retrain_confidence_models (
    fitted_at_ms              INTEGER NOT NULL,
    metric                    TEXT NOT NULL,
    predecessor_lookback_days INTEGER NOT NULL,
    successor_lookback_days   INTEGER NOT NULL,
    coefs_json                TEXT NOT NULL,
    n_train_pairs             INTEGER NOT NULL,
    r_squared                 REAL,
    residual_sd               REAL,
    feature_importance_json   TEXT,
    notes                     TEXT,
    PRIMARY KEY (fitted_at_ms, metric, predecessor_lookback_days, successor_lookback_days)
);

CREATE INDEX IF NOT EXISTS idx_retrain_conf_models_metric
    ON retrain_confidence_models(metric, fitted_at_ms DESC);

-- =========================================================================
-- retrain_confidence_predictions (Tier 4 lineage): per-(new model, metric)
-- predicted post-retrain band, written at register_model.py time. Compared
-- against realized values after the successor_lookback window elapses to
-- self-calibrate the predictor (see services/retrain_confidence.py).
-- =========================================================================
CREATE TABLE IF NOT EXISTS retrain_confidence_predictions (
    model_name        TEXT NOT NULL,
    metric            TEXT NOT NULL,
    predicted_value   REAL NOT NULL,
    predicted_lo      REAL,
    predicted_hi      REAL,
    inputs_json       TEXT,
    fitted_at_ms      INTEGER,
    predicted_at_ms   INTEGER NOT NULL,
    realized_value    REAL,
    realized_at_ms    INTEGER,
    PRIMARY KEY (model_name, metric, predicted_at_ms)
);

CREATE INDEX IF NOT EXISTS idx_retrain_conf_pred_model
    ON retrain_confidence_predictions(model_name);
CREATE INDEX IF NOT EXISTS idx_retrain_conf_pred_realized
    ON retrain_confidence_predictions(realized_at_ms);
