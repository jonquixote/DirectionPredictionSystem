# Model Lineage Analytics + Predictive Retrain Confidence — Spec

**Date:** 2026-05-31
**Mode:** Spec / recon only — no code yet.
**Branch on build:** `v3-dashboard-upgrade` or successor.
**Supersedes:** All prior plan files in this slot.

---

## 1. Context

Three weekly retrains have produced three fleet generations on the same training-cell grid (4 symbols × 7 horizons × 3 train_days = 84 cells per fleet; ~336 cells total in the 3-fleet retention window). We can already see which CURRENT model performs best per `(symbol, market_window)`. What we cannot see yet:

- **Cell trends across fleet generations** — does `h300_btc_v3_179d` consistently win at the 5-min market across fleets, or does the winning horizon rotate?
- **Predecessor signal** — when a fresh model lands, can we predict its first-week performance from its predecessor's first-week performance? Are some cells "stable" (perf carries forward) and others "noisy" (predecessor is uninformative)?
- **Training-data correlation** — when the training window slides forward 7 days, what changes in the feature distribution, and does drift correlate with worse out-of-sample EV?
- **Retrain confidence** — when a new model goes live, can we give the operator a defensible "expected win-rate band" before they commit Kelly capital to it?

Intended outcome: an analytics layer that turns 3 fleets of historical data into a leading-indicator for retrain quality — and integrates the resulting signals back into the dispatch + tier decision loop. The intent is to spec the full thing now; build in phases as data accumulates.

> **Data thinness caveat:** each fleet has only ~14 days of resolved evaluation predictions per cell. Correlation analysis across only 3 retrains × 84 cells will have wide confidence intervals. Tier 1 (read-only) ships immediately; Tier 2-4 need more retrain cycles before the numbers stabilize. Every metric in this spec is rendered with explicit Wilson / bootstrap CI bands so a thin-data view is honest.

---

## 2. Vocabulary

| Term | Meaning |
|---|---|
| **Training cell** (`cell_key`) | A `(symbol, training_horizon_seconds, train_days)` triple. Already used in `cell_governance` + `retrain_queue`. E.g. `BTCUSDT_300_179` = "BTC, 300-second prediction horizon, 179-day training window". |
| **Fleet generation** | A `fleet_version` (e.g. `2026-05-12`). At weekly retrain cadence, each cell gets one new model per generation. |
| **Lineage** | The chronological chain of models in the same cell across fleets. E.g. `h300_btc_v3_179d_20260427` → `..._20260506` → `..._20260512`. Stored explicitly via `model_registry.parent_model_name`. |
| **Sibling** | Two models in the same cell from different fleets. The successor's parent. |
| **Market window** (`market_window_seconds`) | The Kalshi contract length the prediction is resolved against. Independent of training horizon. ANY model evaluates against ALL three: 300 / 900 / 1800. |
| **Best-per-cell** | "Best `(model_name, symbol, market_window)` row." NOT "best `(symbol, training_horizon)`". One model often has different perf at 5-min vs 30-min markets. |

---

## 3. Existing infrastructure (reusable as-is)

Already shipping these tables / endpoints / loops — the spec leans on them, doesn't rebuild them.

### Tables

| Table | Granularity | Useful for |
|---|---|---|
| `model_registry` | one row per model | lineage walk via `parent_model_name`, `fleet_version`, `train_window_start/end`, `train_days`, `training_horizon_seconds`, `symbol`, `primary_market_window_seconds`, `artifact_hash` |
| `cell_governance` | one row per `cell_key` | `incumbent_model_name`, `challenger_model_name`, `last_promotion_at` |
| `predictions_daily_rollup` | `(model, symbol, window, date_utc)` | daily aggregates: `n`, `n_correct`, `sum_pnl`, `sum_brier_terms`, `n_resolved_trades` — the rollup-loop already writes this every 10 min |
| `decay_metrics` | `(model, symbol, window, ts)` | rolling 100-sample snapshots: `rolling_ev`, `recency_weighted_ev`, `brier_score`, `calibration_error`, `sample_count` |
| `decay_evaluations` | `(model, symbol, window, eval_type, ts)` | per-trigger: `rwev_drop`, `brier_rise`, `calibration_drift`, `triggered (bool)` |
| `model_tier_score` | `(model, symbol, window, ts)` | the **composite_score** the user picked as canonical "best" metric. Already combines live RWEV, walk-forward EV, calibration drift, decay slope, stability. |
| `governance_actions` | per-action | promote / demote / retire audit |

### Code paths

| Function / file | What it gives us |
|---|---|
| `services/analysis.py:compute_leaderboard` | already rollup-backed, fast. Returns per-`(model, symbol, window)` win_rate / ROI / Sharpe / Brier / p-value over a window. |
| `services/analysis.py:compute_full_report` | composite report per `(symbol, window)`. Cache TTL 30 min. |
| `services/tier_scorer.py:compute_all_tier_scores` | composite scoring loop. Hourly. |
| `services/analysis.py:walk_forward_validate` + `train_test_validate` | per-model backtests on resolved paper_trades. **Can be retargeted** to score a candidate fleet member pre-promotion (Tier 4). |
| `trading/metric_writers.py:refresh_decay_metrics` | the rolling-100 EWMA writer. Already writes peer-aware decay triggers. |
| `dashboard_api/main.py:_governance_probation_loop` | reads composite scores during probation window. Already lineage-aware (peer baselines, 72h grace). |
| `scripts/gold_miner_report.py` | one-shot longitudinal report. Closest existing precedent for this feature. |

### UI surfaces

| Page / component | What's there today |
|---|---|
| `dashboard/src/pages/Analysis.tsx` | sub-pages: Leaderboard, ThresholdHeatmap, SkipPanel, CommitteeCard, RegimeMatrix, DecayExplorer, CommitteeWeights, FilterSimulator. Per-`(symbol, window)` filter selector at top. |
| `dashboard/src/pages/Models.tsx` | model list with `fleet_version` filter + tier badges. |
| `dashboard/src/pages/ModelDetail.tsx` | one model: platform status, audit trail, perf summary, calibration, threshold sweep. **No predecessor view.** |

---

## 4. Gap analysis — what's missing

| Need | Current state | Gap |
|---|---|---|
| Pivot data on `cell_key` (not just `model_name`) | `cell_governance` keys on it; no API or UI surfaces it | No endpoint returns per-cell longitudinal data |
| Lineage walk | `parent_model_name` is set but never followed by any code path | No "show predecessor chain" function |
| Predecessor → successor metric correlation | Not computed anywhere | No table, no job |
| Training-data drift snapshot per model | Not captured. `feature_version` is a text tag only. | No `feature_names_hash`, no distribution stats, no train-window KS/PSI vs predecessor |
| Pre-promotion confidence interval | Probation evaluator gates on composite_score during probation, but does not estimate the *expected* post-probation score | No prior-based confidence band |
| "Best per cell" matrix view | Operator can scroll Leaderboard with filters; no 4×3 at-a-glance grid | No matrix component |

---

## 5. Tier 1 — Read-only analytics (build first)

**Goal:** answer the user's literal question — "for BTC at 5-min markets, which model performs best, and how have the cell's predecessors performed?" — without changing any training pipeline behavior.

### 5.1 New backend endpoints (additive only)

All endpoints in `dashboard_api/routers/analysis.py`. All composite reads from `model_tier_score` + `predictions_daily_rollup` (no raw prediction scans).

**`GET /api/analysis/best-per-cell`**

Query params: `metric=composite` (default) | `roi` | `win_rate` | `sharpe`; `since_ms` (optional, default `now - 14d`); `min_n_samples=50`.

Response:

```json
{
  "cells": [
    {
      "symbol": "BTCUSDT",
      "market_window_seconds": 300,
      "best_model": "h300_btc_v3_179d_20260512",
      "training_horizon_seconds": 300,
      "train_days": 179,
      "fleet_version": "2026-05-12",
      "composite_score": 0.142,
      "win_rate": 0.582,
      "win_rate_ci_lo": 0.541,
      "win_rate_ci_hi": 0.621,
      "roi_pct": 8.10,
      "n_samples": 240,
      "n_resolved_trades": 239,
      "runners_up": [
        {"model": "h300_btc_v3_89d_20260506", "composite_score": 0.118, ...},
        {"model": "h300_btc_v3_329d_20260512", "composite_score": 0.094, ...}
      ]
    },
    ...
  ],
  "metadata": {"computed_at": "...", "metric": "composite", "since_ms": ..., "min_n_samples": 50}
}
```

12 cells total (4 symbols × 3 market windows). Backed by `model_tier_score` for composite; falls through to `compute_leaderboard` for the other metrics. 30-min `_persistent_cache` TTL (same pattern as full_report).

**`GET /api/analysis/cell-history`**

Query params: `cell_key` (required, format `SYMBOL_HORIZON_TRAININGDAYS`); `since_ms` (default = oldest fleet's `train_window_end`).

Response:

```json
{
  "cell_key": "BTCUSDT_300_179",
  "symbol": "BTCUSDT",
  "training_horizon_seconds": 300,
  "train_days": 179,
  "fleets": [
    {
      "fleet_version": "2026-04-27",
      "model_name": "h300_btc_v3_179d_20260427",
      "train_window_start": "...",
      "train_window_end": "2026-04-27",
      "retired_at": "2026-05-12T...Z",
      "lifetime_metrics": {
        "n_samples": 1820, "win_rate": 0.554, "roi_pct": 4.2,
        "composite_score_final": 0.082,
        "n_resolved_trades": 1812
      },
      "daily_series": [
        {"date": "2026-04-28", "n": 96, "win_rate": 0.563, "roi_pct": 5.1, "composite_score": 0.091},
        ...
      ],
      "decay_events": [
        {"ts": "...", "eval_type": "rwev_drop", "triggered": true, "metric_value": -0.18}
      ]
    },
    {"fleet_version": "2026-05-06", "model_name": "h300_btc_v3_179d_20260506", ...},
    {"fleet_version": "2026-05-12", "model_name": "h300_btc_v3_179d_20260512", ...}
  ]
}
```

Per-market-window breakdown is folded in inside `lifetime_metrics` + `daily_series` as `by_market_window: {300: {...}, 900: {...}, 1800: {...}}`. Same 30-min cache TTL.

**`GET /api/analysis/cell-list`**

Returns the 84 active cells with their current incumbent + last-promotion timestamp. Pure index for the matrix grid + dropdown.

### 5.2 New frontend surfaces

Two new components, mounted in `dashboard/src/pages/Analysis.tsx` as additional collapsible sections (consistent with existing T2-fe-layout pattern). Each reuses `Skeleton`, `SectionErrorBoundary`, `useUrlState`, the existing `Segment`, `DataFreshness`.

**A. `Analysis/BestPerCell.tsx`** — 4×3 matrix grid

```
                 5-min market    15-min market    30-min market
BTCUSDT          h300_btc        h900_btc         h1800_btc
                 ..._179d         ..._329d        ..._89d
                 wr 58.2% ± 4    wr 54.4% ± 5    wr 51.0% ± 4
                 composite 0.14   composite 0.11  composite 0.07
ETHUSDT          ...              ...              ...
SOLUSDT          ...              ...              ...
XRPUSDT          ...              ...              ...
```

Each cell links to ModelDetail. Tap-to-expand shows the runner-up list.

State: `metric` (composite | roi | win_rate | sharpe) via `useUrlState`; `?focus_cell=` deep-link.

**B. `Analysis/CellLineage.tsx`** — fleet-generation history for a chosen cell

Cell picker: dropdown of all 84 cell_keys (groups by symbol).

Renders three sub-views:

1. **Fleet ribbon** — horizontal timeline, one box per fleet generation, colored by composite score (green → red). Tap a box → drills into that model's ModelDetail.
2. **Per-fleet metric table** — one row per fleet, columns: name · paper_active · lifetime n · win_rate · ROI% · composite · decay_event_count · promoted_at · retired_at.
3. **Per-fleet daily series chart** — three small Recharts line plots stacked (one per market_window). Each plot overlays a colored line per fleet generation, x-axis = days-since-promotion (so all fleets align at day 0).

URL state: `?cell_key=BTCUSDT_300_179`. Defaults to the cell of the currently-incumbent best-performer.

### 5.3 Build cost (Tier 1)

- 1 backend file (3 endpoints, ~250 lines): `dashboard_api/routers/analysis.py` extensions + helpers in `services/analysis.py`.
- 2 frontend components (~400 lines combined).
- ~1 day end-to-end. Zero schema changes.

---

## 6. Tier 2 — Fleet succession correlation engine

**Goal:** quantify how predictive a predecessor's metrics are of its successor's metrics, per cell + per lookback-window. Output: "predecessor's first-7-day win-rate explains 0.62 of variance in successor's first-7-day win-rate, n=84 cells." Plus per-cell residuals, so the operator can spot cells where the relationship is unusually noisy.

### 6.1 Schema deltas (additive)

```sql
CREATE TABLE IF NOT EXISTS fleet_succession_correlation (
  computed_at_ms        INTEGER NOT NULL,
  metric                TEXT NOT NULL,            -- composite | win_rate | roi | brier
  predecessor_lookback_days  INTEGER NOT NULL,    -- 7 | 14 | lifetime
  successor_lookback_days    INTEGER NOT NULL,
  n_pairs               INTEGER NOT NULL,         -- count of (predecessor, successor) tuples
  pearson_r             REAL,
  pearson_r_ci_lo       REAL,                     -- bootstrap 95%
  pearson_r_ci_hi       REAL,
  spearman_rho          REAL,
  slope                 REAL,                     -- linear regression slope
  intercept             REAL,
  residual_sd           REAL,                     -- for confidence-band rendering in Tier 4
  PRIMARY KEY (computed_at_ms, metric, predecessor_lookback_days, successor_lookback_days)
);

CREATE TABLE IF NOT EXISTS fleet_succession_residuals (
  computed_at_ms        INTEGER NOT NULL,
  metric                TEXT NOT NULL,
  predecessor_lookback_days INTEGER NOT NULL,
  cell_key              TEXT NOT NULL,
  predecessor_model_name TEXT NOT NULL,
  successor_model_name  TEXT NOT NULL,
  predecessor_value     REAL,
  successor_value       REAL,
  residual              REAL,                     -- successor_value - (slope*predecessor_value + intercept)
  PRIMARY KEY (computed_at_ms, metric, predecessor_lookback_days, cell_key)
);
```

### 6.2 Job — `services/lineage_correlation.py`

Pure-Python module called from a new `_lineage_correlation_loop` in `dashboard_api/main.py`. Runs once per hour on the bg-leader worker.

Algorithm (per `metric` × `lookback_days_pair`):

1. Enumerate all `(predecessor_model_name, successor_model_name)` pairs where `parent_model_name` links them and BOTH have ≥`lookback_days` of resolved data.
2. For each pair, compute `predecessor_value` = aggregate metric over predecessor's first `predecessor_lookback_days` of live evaluation, and `successor_value` = same for successor.
3. Compute Pearson r, Spearman ρ, OLS slope/intercept across all pairs.
4. Bootstrap 95% CI on r (1000 resamples).
5. Compute per-pair residuals.
6. Write one summary row + N residual rows.

Retention: `model_tier_score` retention cron extension prunes `fleet_succession_*` older than 90 days.

### 6.3 New endpoint — `GET /api/analysis/succession-correlation`

Returns the latest snapshot per metric + lookback. Powers a new frontend chart.

### 6.4 New frontend section — `Analysis/SuccessionCorrelation.tsx`

Scatter plot: x = predecessor metric, y = successor metric. One dot per `(cell_key)`. Hover → cell name + both values. Fitted regression line with residual band.

Filter: metric (composite | win_rate | roi); lookback (7d | 14d | lifetime).

Header text: "predecessor 14d composite_score explains r=0.42 (CI 0.31–0.53, n=84) of successor 14d composite_score".

### 6.5 Build cost (Tier 2)

- 1 schema migration.
- 1 new pure-Python module (`services/lineage_correlation.py`, ~200 lines).
- 1 new bg loop in main.py (~30 lines).
- 1 new endpoint (~40 lines).
- 1 new frontend component (~250 lines).
- Need ≥3 retrain cycles of clean data before numbers stabilize. **Wait 3 weeks post-Tier 1** before building.

---

## 7. Tier 3 — Training-data drift instrumentation

**Goal:** capture, at training time, a fingerprint of the feature distribution each model was trained on. Then expose `drift(predecessor → successor)` as a per-cell signal.

### 7.1 What to capture per model

Augment `register_model.py` to compute + persist, alongside the existing `artifact_hash`:

- `feature_names_hash` (sha256 of sorted feature names — already file-based, not hashed in DB)
- **Per-feature distribution snapshot**: for each of the ~32 v3 features, percentiles `[1, 5, 25, 50, 75, 95, 99]` + mean + std + n_nulls, computed over the training window. Stored as JSON.
- **Training outcome stats**: training brier, log_loss, AUC, n_train_obs (already in `calibration_summary` but never linked to registry).

### 7.2 Schema delta

```sql
CREATE TABLE IF NOT EXISTS training_data_snapshots (
  model_name             TEXT PRIMARY KEY REFERENCES model_registry(name),
  feature_names_hash     TEXT,
  feature_dist_json      TEXT,     -- {feature: {p01,p05,...,mean,std,n_nulls}}
  training_brier         REAL,
  training_log_loss      REAL,
  training_auc           REAL,
  n_train_obs            INTEGER,
  computed_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
```

Backfill: best-effort for the current 3 fleets by re-reading `feature_names.json` from each artifact dir + reading the stored training metrics from `metrics.json` (already written by `retrain.py`). Where data is missing, leave NULL — older fleets degrade gracefully.

### 7.3 Drift metric

Per `(predecessor, successor)` pair within a cell:

- **Per-feature drift** = `KS_statistic(predecessor_dist, successor_dist)` using the stored percentiles + means as a 9-point empirical CDF approximation. (No raw training data needed at scoring time — that's the point.)
- **Cell drift score** = max per-feature KS across the feature set. Robust to "one feature changed a lot while others didn't."

Add to `cell-history` response: `drift_from_predecessor` per fleet.

### 7.4 New job + endpoint

`services/training_drift.py` computes `cell_drift` on demand (cheap given the percentile-summary representation). Triggered by `cell-history` endpoint and on registration via `register_model.py`.

### 7.5 UI integration

CellLineage view gains a "drift" badge on each fleet box: green (KS < 0.10), amber (0.10–0.25), red (>0.25). Clickable → drift breakdown chart per feature.

### 7.6 Build cost (Tier 3)

- 1 schema migration + backfill script (~120 lines).
- `register_model.py` augmentation (~80 lines).
- `services/training_drift.py` (~150 lines).
- CellLineage drift badge + breakdown view (~100 lines).
- Requires a retrain pass to fully populate — backfill handles the current 3 fleets.

---

## 8. Tier 4 — Predictive retrain confidence

**Goal:** when a new model gets registered, render a confidence band for its expected first-week composite_score on the dashboard, **before** any live data exists. Operator can see "h300_btc_v3_179d_20260519 — predicted composite 0.10 (±0.04 1σ band) based on predecessor + drift signal" and decide whether to let Kalshi dispatch start at full Kelly.

### 8.1 The model

Linear regression — deliberately simple, lets a thin-data audit by eye:

```
successor_composite_7d = β₀
                       + β₁ · predecessor_composite_14d
                       + β₂ · cell_drift
                       + β₃ · cell_residual_sd_history   -- per-cell historical residual std
                       + β₄ · training_brier_delta       -- (successor_train_brier - predecessor_train_brier)
                       + β₅ · n_train_obs_log
                       + ε
```

β coefficients are fit per metric (`composite`, `win_rate`, `roi`) from `fleet_succession_residuals` (built by Tier 2). Refit weekly after each retrain finishes.

### 8.2 Schema delta

```sql
CREATE TABLE IF NOT EXISTS retrain_confidence_models (
  fitted_at_ms     INTEGER NOT NULL,
  metric           TEXT NOT NULL,
  predecessor_lookback_days INTEGER NOT NULL,
  successor_lookback_days   INTEGER NOT NULL,
  coefs_json       TEXT NOT NULL,           -- {beta_0..beta_5}
  n_train_pairs    INTEGER NOT NULL,
  r_squared        REAL,
  residual_sd      REAL,                    -- for CI band rendering
  feature_importance_json TEXT,             -- which inputs explained the most variance
  PRIMARY KEY (fitted_at_ms, metric, predecessor_lookback_days, successor_lookback_days)
);

CREATE TABLE IF NOT EXISTS retrain_confidence_predictions (
  model_name       TEXT NOT NULL,            -- the *successor* model
  metric           TEXT NOT NULL,
  predicted_value  REAL NOT NULL,
  predicted_lo     REAL,                    -- 1σ band lower (predicted - residual_sd)
  predicted_hi     REAL,
  inputs_json      TEXT,                    -- {predecessor_value, drift, ...} snapshot
  predicted_at_ms  INTEGER NOT NULL,
  PRIMARY KEY (model_name, metric, predicted_at_ms)
);
```

### 8.3 Wiring

- `register_model.py` calls `services/retrain_confidence.py:predict(model_name)` at registration; writes a row to `retrain_confidence_predictions`.
- After 7 days of live data, a job compares predicted_value vs realized_value and writes calibration stats. The model self-evaluates over time.

### 8.4 UI integration

- ModelDetail.tsx gains a "expected performance" chip when the model is < 14d old. Shows predicted band + the inputs that drove it.
- BestPerCell matrix can render a tiny "newly retrained" badge on cells where the new model's predicted band overlaps the predecessor's lifetime range vs sharply diverges.
- New "Calibration of our own predictor" chart on a (future) Lineage page: predicted vs realized scatter, refreshed after each retrain.

### 8.5 Build cost (Tier 4)

- 2 schema migrations.
- `services/retrain_confidence.py` (~200 lines) — fit + predict.
- Hook in `register_model.py` (~40 lines).
- UI surfaces (~150 lines).
- **Strictly gated on Tier 2 + Tier 3** producing several months of data first. Likely 8-12 weeks out.

---

## 9. Integration touch points

| Existing surface | Tier where it changes |
|---|---|
| `dashboard_api/routers/analysis.py` | T1 (new endpoints) |
| `dashboard_api/services/analysis.py` | T1 (helpers) |
| `dashboard_api/main.py` background loops | T2 (new `_lineage_correlation_loop`) |
| `scripts/register_model.py` | T3 (capture drift snapshot), T4 (write predicted band) |
| `scripts/train_fleet.py` | T3 indirectly (must surface training brier / log_loss into the metadata it hands `register_model.py`) |
| `storage/schema.sql` + `storage/db.py:_run_migrations` | T2, T3, T4 (new tables) |
| `dashboard/src/pages/Analysis.tsx` | T1, T2 (new collapsible sections) |
| `dashboard/src/pages/ModelDetail.tsx` | T3, T4 (drift badge, predicted band chip) |
| `scripts/retention_cron.py` | T2, T4 (extend retention for new tables) |
| `dashboard_api/services/tier_scorer.py` | T4 only (optional: feed predicted band into early-life kelly multiplier) |

The architecture stays compatible with the bg-leader file lock + 30s busy_timeout + WAL checkpoint regime from the post-retrain hardening sprint.

---

## 10. Phasing

| Phase | Build | Trigger | Risk |
|---|---|---|---|
| **T1** | Best-per-cell matrix + Cell lineage view + 3 endpoints | Build now. Pure read. | Low. New endpoints behind `_persistent_cache`, won't load-spike the trader. |
| **T2** | Fleet succession correlation table + scatter view | After ≥3 retrain cycles produce enough `(pred, succ)` pairs in clean data (≥6 weeks). | Low. Hourly bg loop on the leader worker. |
| **T3** | Training-data drift snapshots + drift badges | Triggered by a retrain pass after we agree the percentile fingerprint is the right shape. | Medium. Touches `register_model.py` + `train_fleet.py`. Backfill script handles current 3 fleets. |
| **T4** | Predictive retrain confidence model + predicted band on ModelDetail | After T2+T3 have stable history (likely 8-12 weeks). | Medium. Math is simple; data is the gate. Self-evaluating calibration chart keeps us honest. |

---

## 11. Critical files (when build starts)

### New

- `dashboard_api/services/lineage.py` (T1) — helpers for cell walks + per-cell history aggregation
- `dashboard_api/services/lineage_correlation.py` (T2)
- `dashboard_api/services/training_drift.py` (T3)
- `dashboard_api/services/retrain_confidence.py` (T4)
- `dashboard/src/pages/Analysis/BestPerCell.tsx` (T1)
- `dashboard/src/pages/Analysis/CellLineage.tsx` (T1)
- `dashboard/src/pages/Analysis/SuccessionCorrelation.tsx` (T2)
- `dashboard/src/lib/api.ts` — new helpers per endpoint

### Modified

- `dashboard_api/routers/analysis.py`
- `dashboard_api/services/analysis.py`
- `dashboard_api/main.py` (T2 loop, T4 hook)
- `storage/schema.sql` + `storage/db.py:_run_migrations`
- `scripts/register_model.py` (T3, T4)
- `scripts/train_fleet.py` (T3)
- `scripts/retention_cron.py` (T2, T4)
- `dashboard/src/pages/Analysis.tsx`
- `dashboard/src/pages/ModelDetail.tsx`

---

## 12. Verification (when build starts)

| What | Test | When |
|---|---|---|
| Best-per-cell endpoint | `curl /api/analysis/best-per-cell` returns 12 cells in <1s warm | T1 deploy |
| Cell-history correctness | Pick a cell with ≥2 fleets, confirm `daily_series` per market_window matches a manual `predictions_daily_rollup` query | T1 deploy |
| Matrix UI renders | Open `/analysis`, expand BestPerCell, verify 4×3 grid with metric switcher | T1 deploy |
| Lineage view URL state | `?cell_key=BTCUSDT_300_179` deep-link survives page refresh | T1 deploy |
| Succession correlation snapshot | After first hourly run, `SELECT * FROM fleet_succession_correlation` shows ≥1 row per metric × lookback | T2 first tick |
| Drift snapshot on next retrain | `SELECT * FROM training_data_snapshots WHERE model_name = <new model>` has non-NULL `feature_dist_json` | T3 first retrain |
| Predicted band rendered | New model registration writes a `retrain_confidence_predictions` row; ModelDetail shows the band chip | T4 first new model |
| Predictor calibration | After 6 retrain cycles, predicted-vs-realized scatter shows residuals within ±2σ band | T4 + 6 weeks |
| Pytest | `pytest ofi-lab-v3/tests/` green | every commit |

---

## 13. Open questions (decide before building T1)

1. **Per-market-window in best-per-cell**: do we show the same model winning across all 3 markets (current behavior in the leaderboard) or pick a unique winner per `(symbol, market_window)`? Spec assumes unique winner per market — confirm.
2. **"Group of models" interpretation**: the user said "which model OR group of models." Do we want a "committee" view that picks the top-k by composite and shows a virtual ensemble's metrics? Probably belongs on the Committee sub-page, not here. Confirm out of scope.
3. **Retired fleets**: do we include the 2026-04-27 fleet (0 paper_active but has historical data) in the cell-history view? Spec assumes yes — historical data is the whole point.
4. **Calibration of cell_drift signal**: KS over percentile fingerprints is approximate. If feature distributions are very heavy-tailed, the 9-point approximation will underweight tail differences. Acceptable for v1; revisit if T4 residuals correlate strongly with feature kurtosis.
5. **Naming**: should the new Analysis section be called "Lineage" or "Cell history" or "Fleet timeline"? Spec uses "Lineage."

---

## 14. Math appendix

### Wilson lower-bound CI on win_rate

Already used in `services/analysis.py`. Reuse for all win-rate bands.

### Bootstrap CI on Pearson r (T2)

```python
def bootstrap_pearson_ci(xs, ys, n_resamples=1000, alpha=0.05):
    n = len(xs)
    rs = []
    for _ in range(n_resamples):
        idx = np.random.randint(0, n, n)
        r = np.corrcoef(xs[idx], ys[idx])[0, 1]
        rs.append(r)
    rs.sort()
    return rs[int(alpha/2 * n_resamples)], rs[int((1-alpha/2) * n_resamples)]
```

### KS over percentile fingerprints (T3)

Given `pred_pct = {p01, p05, p25, p50, p75, p95, p99}` and same for `succ_pct`, treat each as a 9-point empirical CDF over its support and compute `max(|F_pred(x) - F_succ(x)|)` at the union of grid points. Linear interpolation between known percentiles. Sufficient for screening; not a hypothesis test.

### Predicted band rendering (T4)

```
predicted_lo = β·x - residual_sd
predicted_hi = β·x + residual_sd
```

Rendered as a 1σ band (~68% coverage) — operator sees a "likely range," not a hard bound.
