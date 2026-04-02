# BACKEND_TASKS.md
# polymarket-ofi Dashboard API — Backend Implementation

**Service:** `dashboard_api` (FastAPI, Python 3.11+)  
**Repo path:** `polymarket-ofi/dashboard_api/`  
**Port:** `8765`  
**Mounts:** `/data` volume (read-only) — same volume as trading system  
**DB:** `/data/logs/candidate_trades.db` (SQLite)  
**Parquet:** `/data/parquet/` (OHLCV + features)  
**Models:** `/data/models/`  
**Logs:** `/data/logs/`  
**References:** `API_CONTRACT.md` for all request/response shapes

---

## Critical: Realized NE_t Formula

**Every endpoint, query, and metric that computes or returns NE_t must use the realized formula, not the theoretical EV formula.**

The theoretical formula `p_model − fee_t − spread_t − p_market` is wrong and was responsible for masking H60's losses. Do not use it anywhere.

**Correct realized NE_t per resolved trade:**

```python
def compute_realized_net(direction: str, correct: bool, p_market: float, fee: float) -> float:
    """
    direction: "up" or "down"
    correct:   whether the prediction was right
    p_market:  Polymarket implied probability at trade time
    fee:       platform fee (e.g. 0.02)
    """
    if direction == "up":
        return +(1 - p_market - fee) if correct else -(p_market + fee)
    else:  # down
        return +(p_market - fee) if correct else -((1 - p_market) + fee)
```

This function is the single source of truth. Import it from `services/metrics.py` everywhere — never inline the formula in a router or SQL query.

**For unresolved trades:** return `None` / `null`, never 0.

---

## 0. Project Scaffold

### 0.1 Directory Structure

```
dashboard_api/
├── main.py
├── requirements.txt
├── Dockerfile
├── routers/
│   ├── __init__.py
│   ├── status.py
│   ├── predictions.py
│   ├── trades.py
│   ├── performance.py
│   ├── features.py
│   ├── models_registry.py
│   ├── alerts.py
│   ├── logs.py
│   └── parquet.py
├── services/
│   ├── __init__.py
│   ├── auth.py
│   ├── db.py
│   ├── parquet_reader.py
│   ├── metrics.py
│   ├── psi.py
│   ├── alerts_engine.py
│   └── live_state.py
└── ws/
    ├── __init__.py
    └── broadcaster.py
```

### 0.2 requirements.txt

```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
aiosqlite>=0.20.0
pandas>=2.2.0
pyarrow>=16.0.0
numpy>=1.26.0
scipy>=1.13.0
python-dotenv>=1.0.0
websockets>=12.0
```

### 0.3 main.py

```python
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from services.db import init_db_pool, close_db_pool
from services.live_state import LiveState
from services.alerts_engine import start_alert_worker
from ws.broadcaster import ws_router
from routers import (
    status, predictions, trades, performance,
    features, models_registry, alerts, logs, parquet
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db_pool()
    LiveState.initialize()
    start_alert_worker()
    yield
    await close_db_pool()

app = FastAPI(title="polymarket-ofi Dashboard API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in [
    status.router, predictions.router, trades.router,
    performance.router, features.router, models_registry.router,
    alerts.router, logs.router, parquet.router
]:
    app.include_router(router, prefix="/api")

app.include_router(ws_router)
```

---

## 1. Authentication

**All endpoints require authentication. Implement in Phase 1 before any other work.**

### 1.1 services/auth.py

- HTTP Basic Auth via FastAPI `HTTPBasic` dependency
- Single shared credential: username + password read from environment variables `DASHBOARD_USER` and `DASHBOARD_PASS`
- If either env var is unset, log a warning and skip auth (development mode only)
- Return `401 Unauthorized` with `WWW-Authenticate: Basic` header on failure
- Add auth dependency to `app` globally via `dependencies=[Depends(verify_credentials)]` in `main.py`
- WebSocket auth: accept token as query param `?token=<base64(user:pass)>`, validate on connect

```python
# Environment variables required
DASHBOARD_USER=admin
DASHBOARD_PASS=<random 32-char string — generate on first deploy>
```

### 1.2 Docker environment

Add to `docker-compose.override.yml`:
```yaml
environment:
  - DASHBOARD_USER=${DASHBOARD_USER}
  - DASHBOARD_PASS=${DASHBOARD_PASS}
```

Store credentials in `.env` file at repo root. Ensure `.env` is in `.gitignore`.

---

## 2. Data Layer

### 2.1 services/db.py — SQLite Connection Pool

- Use `aiosqlite` for async SQLite access
- Create a module-level connection pool (single connection for SQLite is fine — WAL mode)
- Enable WAL mode on startup: `PRAGMA journal_mode=WAL`
- Set `PRAGMA query_only=ON` — dashboard never writes to the trading DB
- Expose a `get_db()` async context manager that yields a connection
- All queries use parameterized statements — no f-string SQL

```python
import aiosqlite
import os

DB_PATH = os.environ.get("DB_PATH", "/data/logs/candidate_trades.db")
_conn: aiosqlite.Connection | None = None

async def init_db_pool():
    global _conn
    _conn = await aiosqlite.connect(DB_PATH)
    _conn.row_factory = aiosqlite.Row
    await _conn.execute("PRAGMA journal_mode=WAL")
    await _conn.execute("PRAGMA query_only=ON")

from typing import AsyncGenerator

async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    yield _conn
```

### 2.2 services/parquet_reader.py — Parquet Reader

Do not expose raw parquet files to the browser. All parquet data is served through FastAPI endpoints.

**Functions to implement:**

```python
async def get_ohlcv(symbol: str, interval: str, from_ms: int, to_ms: int) -> list[dict]:
    """
    Load OHLCV candles from /data/parquet/{symbol}_{interval}.parquet
    Columns: timestamp_ms, open, high, low, close, volume
    Filter to [from_ms, to_ms] range
    Return list of dicts
    """

async def get_feature_timeseries(symbol: str, feature: str, from_ms: int, to_ms: int) -> list[dict]:
    """
    Load feature values from /data/parquet/features_{symbol}.parquet
    Return: [{timestamp_ms, value}]
    """

async def get_feature_distributions(symbol: str, model_version: str) -> dict:
    """
    Load training distribution (from /data/models/{model_version}/feature_stats.json or parquet)
    and live distribution (last 7 days from features parquet)
    Return: {feature_name: {training: [values], live: [values], psi: float}}
    """

async def get_all_features_latest(symbol: str) -> dict:
    """
    Return most recent row of features parquet for given symbol
    Return: {feature_name: value, timestamp_ms: int}
    """
```

- Use `pandas.read_parquet()` with `filters` parameter for predicate pushdown — do not load entire parquet into memory for every request
- Cache the most recent 1000 rows per symbol in memory, refresh every 60 seconds
- If a parquet file is missing, return `[]` with a `data_gap: true` flag in the response — never 500

---

## 3. Core Services

### 3.1 services/metrics.py — Accuracy, NE_t, CI, Calibration

**This is the most critical service. Get the formulas right.**

```python
# --- NE_t (see Critical section above) ---
def compute_realized_net(direction: str, correct: bool, p_market: float, fee: float = 0.02) -> float | None:
    # Implementation as specified above
    # Returns None for unresolved trades

# --- Wilson Score Confidence Interval ---
def wilson_ci(wins: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """
    Wilson score interval — correct behavior at small n and extreme p.
    Never use normal approximation for this dashboard.
    """
    from scipy import stats
    z = stats.norm.ppf((1 + confidence) / 2)
    p = wins / n if n > 0 else 0
    center = (p + z**2 / (2*n)) / (1 + z**2 / n)
    margin = (z * (p*(1-p)/n + z**2/(4*n**2))**0.5) / (1 + z**2/n)
    return (center - margin, center + margin)

# --- Rolling Accuracy Series ---
def rolling_accuracy_series(outcomes: list[bool], n: int = 50) -> list[dict]:
    """
    outcomes: list of bool (True=correct), chronological order
    Returns: [{index, accuracy, ci_low, ci_high}]
    Only includes positions where at least n trades have occurred.
    """

# --- Calibration Curve ---
def calibration_curve(proba_values: list[float], outcomes: list[bool], n_bins: int = 10) -> list[dict]:
    """
    Bucket p_model values into n_bins.
    For each bucket: mean_predicted_proba, actual_win_rate, count
    Returns: [{bucket_center, predicted, actual, n}]
    """

# --- Execution Funnel ---
def compute_execution_funnel(trades: list[dict]) -> dict:
    """
    Input: all candidate_trades rows (including suppressed/excluded)
    Returns: {
        predictions_fired: int,
        structural_pass: int,
        adverse_pass: int,
        net_pass: int,
        sanderink_pass: int,
        executed: int,
        drop_rates: {structural: float, adverse: float, net: float, sanderink: float}
    }
    """

# --- Divergence Bucket Stats ---
def divergence_bucket_stats(trades: list[dict]) -> list[dict]:
    """
    Buckets: 0–0.02, 0.02–0.05, 0.05–0.10, 0.10+
    Returns per bucket: {range, n, accuracy, ci_low, ci_high, avg_net, avg_net_theoretical}
    avg_net_theoretical uses p_model − fee − p_market (for comparison only, never as primary metric)
    """

# --- Z-test ---
def z_test(n: int, observed_accuracy: float, null_accuracy: float = 0.50) -> dict:
    """Returns: {z_score, p_value_one_tailed, significant_at_05: bool}"""
```

### 3.2 services/psi.py — Population Stability Index

PSI measures distribution shift between training and live feature distributions.

```python
def compute_psi(expected: list[float], actual: list[float], n_bins: int = 10) -> float:
    """
    PSI = sum((actual% - expected%) * ln(actual% / expected%))
    Bins: equal-frequency based on expected distribution
    Handle zero bins: add 0.0001 to avoid log(0)
    Returns: float
    Interpretation thresholds: < 0.10 stable, 0.10–0.25 monitor, > 0.25 shift
    """
```

Run PSI for every feature in the model's feature list on startup and every 6 hours. Cache results in `LiveState`. Emit `feature_drift` alert if any feature crosses a threshold.

### 3.3 services/live_state.py — In-Memory Live State

Singleton object holding fast-access state for WebSocket push and status endpoint. Updated by background tasks.

```python
class LiveState:
    last_prediction: dict         # most recent prediction per model+symbol key
    predictions_per_hour: dict    # {model: count} — rolling 60-min window
    trades_per_hour: dict         # {model: count}
    gate_status: dict             # {model: {"rolling_50": float, "pass": bool, "trend": str}}
    suppression_rules: list[dict] # active rules with status
    psi_results: dict             # {model: {feature: float}}
    ewm_state: dict               # {symbol: {mean, std, last_updated}}
    data_pipeline_health: dict    # {last_parquet_update, hours_since_ingest}
    pmarket_api_health: dict      # {last_fetch_ms, latency_ms, consecutive_failures}
    alert_queue: list[dict]       # last 500 alerts, newest first
    system_uptime: dict           # {model: uptime_seconds, started_at}

    @classmethod
    def initialize(cls): ...

    @classmethod
    def update_from_db(cls, conn): ...  # called every 5s by broadcaster

    @classmethod
    def snapshot(cls) -> dict: ...      # returns JSON-serializable dict for /api/status
```

### 3.4 services/alerts_engine.py — Alert Generation

Background async task. Runs every 60 seconds. Evaluates all alert conditions and writes to `alert_queue` in `LiveState`. Also writes to `/data/logs/dashboard_alerts.log`.

**Alert conditions to implement:**

```python
ALERT_RULES = [
    {
        "type": "GATE_STATUS_CHANGE",
        "severity": "WARN",
        "check": lambda state: rolling_50_crossed_threshold(state, threshold=0.515),
        "message": "Rolling-50 {direction} threshold for {model}: now {value:.1%}"
    },
    {
        "type": "NE_T_NEGATIVE",
        "severity": "WARN",
        "check": lambda state: last_50_net_negative(state),
        "message": "{model} NE_t/trade negative over last 50 resolved trades: {value:.3f}"
    },
    {
        "type": "ACCURACY_ALERT",
        "severity": "WARN",
        "check": lambda state: any_window_below_floor(state, floor=0.50, window=50),
        "message": "{model} rolling-50 at {value:.1%} — below floor"
    },
    {
        "type": "COVERAGE_DROP",
        "severity": "WARN",
        "check": lambda state: predictions_per_hour_below(state, floor=2),
        "message": "{model} predictions/hour dropped to {value} — pipeline may be stalled"
    },
    {
        "type": "FEATURE_DRIFT",
        "severity": "WARN",
        "check": lambda state: any_psi_above(state, threshold=0.10),
        "message": "PSI {value:.3f} for {feature} ({model}) — entering monitor zone"
    },
    {
        "type": "FEATURE_DRIFT",
        "severity": "CRITICAL",
        "check": lambda state: any_psi_above(state, threshold=0.25),
        "message": "PSI {value:.3f} for {feature} ({model}) — distribution shift detected"
    },
    {
        "type": "MODEL_AGREEMENT_FLIP",
        "severity": "INFO",
        "check": lambda state: models_flipped_agreement(state, min_agreement_hours=24),
        "message": "H60 and H300 prediction directions diverged after {hours}h of agreement"
    },
    {
        "type": "P_MARKET_ANOMALY",
        "severity": "WARN",
        "check": lambda state: pmarket_out_of_range(state, low=0.40, high=0.60, duration_min=30),
        "message": "{symbol} p_market at {value:.3f} — outside 0.40–0.60 for {duration} min"
    },
    {
        "type": "API_FAILURE",
        "severity": "CRITICAL",
        "check": lambda state: consecutive_api_failures(state, threshold=3),
        "message": "p_market API: {count} consecutive failures. Last success: {last_success}"
    },
    {
        "type": "SUPPRESSION_EFFECTIVENESS",
        "severity": "INFO",
        "check": lambda state: suppression_effectiveness_update(state, interval_hours=24),
        "message": "300s suppression saved {net:.2f} NE_t in last 24h ({n} trades avoided)"
    },
]
```

Deduplication: do not emit the same alert type for the same model more than once per 30 minutes unless severity escalates.

---

## 4. Routers

### 4.1 GET /api/status

Returns full `LiveState.snapshot()`. Response shape defined in `API_CONTRACT.md`.

Additionally compute and include:
- `model_metadata`: for each model version, load from `/data/models/{version}/metadata.json` — fields: `version`, `trained_date`, `feature_count`, `auc_train`, `auc_val`, `auc_test`, `feature_list`
- `suppression_rules`: read from config file or hardcoded rules list — each rule: `name`, `active`, `since_date`, `models_affected`, `description`
- `coverage_rate`: predictions fired / contracts available in last 1 hour (compute from DB, cache in LiveState)
- `system_fee`: platform fee used for NE_t backend calculations (read from config or constant 0.02)

### 4.2 GET /api/predictions

Paginated, filtered prediction records.

**Query params:** model, symbol, from_ms, to_ms, direction, suppressed (bool), warmup (bool), outcome (correct/incorrect/unresolved), divergence_min, divergence_max, pmodel_min, pmodel_max, prediction_id (exact), page, page_size (max 500), sort, order

**SQL skeleton:**
```sql
SELECT
    p.*,
    t.id           AS trade_id,
    t.correct      AS outcome,
    t.ne_t_computed AS realized_net
FROM predictions p
LEFT JOIN candidate_trades t ON t.prediction_id = p.prediction_id
WHERE 1=1
  AND (:model IS NULL OR p.model = :model)
  AND (:symbol IS NULL OR p.symbol = :symbol)
  AND (:from_ms IS NULL OR p.ts_model_ran_ms >= :from_ms)
  AND (:to_ms IS NULL OR p.ts_model_ran_ms <= :to_ms)
  AND (:direction IS NULL OR p.pred_direction = :direction)
  AND (:suppressed IS NULL OR (p.suppressed_reason IS NOT NULL) = :suppressed)
  AND (:warmup IS NULL OR p.warmup = :warmup)
  AND (:outcome IS NULL OR
       (:outcome = 'unresolved' AND t.id IS NULL) OR
       (:outcome = 'correct' AND t.correct = 1) OR
       (:outcome = 'incorrect' AND t.correct = 0))
ORDER BY p.ts_model_ran_ms DESC
LIMIT :page_size OFFSET :offset
```

Response includes: `total_count`, `page`, `page_size`, `items[]`

Each item includes all prediction fields plus resolved `outcome` and `realized_net` from the linked trade.

### 4.3 GET /api/trades

Paginated, filtered trade records.

**Additional query params beyond predictions:** contract_duration (300/900), net_sign (positive/negative), settled (bool)

**SQL includes realized NE_t computation:**
```sql
SELECT
    t.*,
    -- Inline computation for performance. This is the algebraically identical SQL
    -- implementation of compute_realized_net() from metrics.py.
    CASE
        WHEN t.resolved = 0 THEN NULL
        WHEN t.direction = 'up' AND t.correct = 1 THEN (1.0 - t.p_market - :fee)
        WHEN t.direction = 'up' AND t.correct = 0 THEN -(t.p_market + :fee)
        WHEN t.direction = 'down' AND t.correct = 1 THEN (t.p_market - :fee)
        WHEN t.direction = 'down' AND t.correct = 0 THEN -((1.0 - t.p_market) + :fee)
    END AS realized_net_computed
FROM candidate_trades t
WHERE t.executed = 1
  -- [filters]
ORDER BY t.timestamp_ms DESC
LIMIT :page_size OFFSET :offset
```

**Open trades sub-query (returned separately in response):**
```sql
SELECT *, (strftime('%s','now')*1000 - timestamp_ms) AS elapsed_ms
FROM candidate_trades
WHERE executed = 1 AND resolved = 0
ORDER BY timestamp_ms ASC
```

### 4.4 GET /api/resolutions

```sql
SELECT r.*, t.direction, t.p_market, t.model
FROM resolutions r
JOIN candidate_trades t ON t.id = r.trade_id
WHERE [filters]
ORDER BY r.resolved_at_ms DESC
```

### 4.5 GET /api/performance/summary

Aggregated KPI metrics. Used by Section 4 KPI cards.

Returns per combination of `(model, symbol)`:
- `total_trades`, `wins`, `losses`, `unresolved`
- `accuracy` with `ci_low`, `ci_high` (Wilson)
- `realized_net_total`, `realized_net_per_trade`
- `gate_pass_rate` (executed / total candidates)
- `high_divergence_accuracy` (top 25% of `|p_model − p_market|` trades)
- `z_score`, `p_value`, `is_significant`

All NE_t computed via `compute_realized_net()` in Python — not SQL arithmetic.

### 4.6 GET /api/performance/rolling

Rolling accuracy time series. Used by Section 4 Rolling-N chart.

**Query params:** model, symbol, n (window size, default 50), from_ms, to_ms

Steps:
1. Pull all resolved trades for model+symbol ordered by timestamp_ms
2. Compute rolling accuracy using `rolling_accuracy_series()` from `metrics.py`
3. Return: `[{timestamp_ms, index, accuracy, ci_low, ci_high, gate_pass: bool}]`
4. Also return: `current_value`, `gate_threshold` (0.515), `gate_status`, `trend` (↑/↓/→ based on last 3 points), `variance_context` (p-value: is current value consistent with overall accuracy?)

**The `variance_context` field is required.** When rolling value fails gate, compute and return:
```json
{
  "gate_fail": true,
  "current_rolling": 0.50,
  "overall_accuracy": 0.584,
  "z_from_true": -1.13,
  "p_value": 0.258,
  "interpretation": "Within expected sampling variance for 58.4% true accuracy model. Not yet evidence of real edge erosion."
}
```

### 4.7 GET /api/performance/heatmap

24×7 accuracy grid. Used by Section 4 By Hour Heatmap.

**Query params:** model, symbol, from_ms, to_ms

```sql
SELECT
    CAST(strftime('%H', datetime(timestamp_ms/1000, 'unixepoch')) AS INTEGER) AS hour_utc,
    CAST(strftime('%w', datetime(timestamp_ms/1000, 'unixepoch')) AS INTEGER) AS day_of_week,
    COUNT(*) AS n,
    SUM(correct) AS wins,
    CAST(SUM(correct) AS FLOAT) / COUNT(*) AS accuracy
FROM candidate_trades
WHERE executed = 1 AND resolved = 1 [AND filters]
GROUP BY hour_utc, day_of_week
```

Returns: `{cells: [{hour, day, n, wins, accuracy}], daytime_accuracy, overnight_accuracy, best_hours, worst_hours}`

Daytime = UTC 04:00–21:00. Overnight = UTC 21:00–04:00.

### 4.8 GET /api/performance/by-divergence

Divergence bucket breakdown. Returns stats per bucket using `divergence_bucket_stats()` from `metrics.py`.

**Include per bucket:**
- `n`, `accuracy`, `ci_low`, `ci_high`
- `realized_net_per_trade` (from `compute_realized_net()`)
- `theoretical_net_per_trade` (for comparison: `p_model − fee − p_market` average — labeled clearly as theoretical/comparison only)
- `is_high_divergence` (true for `0.10+` bucket)

### 4.9 GET /api/performance/calibration

Calibration curve data for model and market calibration. Uses `calibration_curve()` from `metrics.py`.

Returns two arrays: `model_calibration` and `market_calibration`, each `[{bucket_center, predicted, actual, n}]`.

### 4.10 GET /api/performance/funnel

Execution funnel. Uses `compute_execution_funnel()` from `metrics.py`.

**Query params:** model, symbol, from_ms, to_ms

Requires access to all candidate rows including suppressed ones — filter by model/symbol/time, do not pre-filter by `executed = 1`.

Returns:
```json
{
  "predictions_fired": 450,
  "structural_pass": 380,
  "adverse_pass": 310,
  "net_pass": 290,
  "sanderink_pass": 270,
  "executed": 245,
  "drop_rates": {
    "structural": 0.156,
    "adverse": 0.184,
    "net": 0.065,
    "sanderink": 0.073
  }
}
```

### 4.11 GET /api/performance/suppression-effectiveness

Suppression validation endpoint. Shows what would have happened if suppressed trades had been executed.

**For each active suppression rule (300s contract_mismatch, UTC blackout):**

```sql
-- 300s suppressed trades that have since resolved
SELECT
    direction,
    p_market,
    correct,  -- we know this because the market resolved anyway
    COUNT(*) AS n
FROM candidate_trades
WHERE suppressed_reason = 'contract_mismatch'
  AND resolved = 1
  [AND time filter]
GROUP BY direction, correct
```

Compute hypothetical NE_t using `compute_realized_net()` for each suppressed-but-resolved trade. Return:
```json
{
  "rule": "contract_mismatch_300s",
  "period": {"from_ms": ..., "to_ms": ...},
  "suppressed_n": 89,
  "resolved_n": 72,
  "hypothetical_accuracy": 0.347,
  "hypothetical_net_per_trade": -0.83,
  "hypothetical_net_total": -59.76,
  "net_saved": 59.76,
  "decision": "CORRECT"
}
```

### 4.12 GET /api/performance/by-symbol and /api/performance/by-contract

These are convenience endpoints that call `/api/performance/summary` with pre-set grouping dimensions. Return arrays grouped by symbol or contract_duration with full metric sets per group.

### 4.13 GET /api/performance/cross-symbol-correlation

For a given model and time range: pairwise outcome correlation between symbols.

```python
# For each pair (BTC, SOL), (BTC, ETH), (SOL, ETH):
# Find trades where both symbols fired in same prediction window (within 60s)
# Compute Pearson correlation of outcome (1/0) vectors
# Return correlation matrix
```

Returns: `{pairs: [{sym_a, sym_b, n_matched, correlation, p_value}]}`

### 4.14 GET /api/features/history

Feature time series. Delegates to `parquet_reader.get_feature_timeseries()`.

**Query params:** feature (required), symbol (required), from_ms, to_ms

Returns: `[{timestamp_ms, value}]` plus `{ewm_mean, ewm_std, last_updated}` from LiveState.

### 4.15 GET /api/features/importance

Feature importances for a model version.

Load from `/data/models/{model_version}/feature_importance.json`. File should contain:
```json
{
  "gain": {"feature_name": value, ...},
  "split": {"feature_name": value, ...},
  "shap_available": false
}
```

If file missing: return `404` with `detail: "Feature importance not available for this model version"`.

### 4.16 GET /api/features/live-values

Most recent feature values per model+symbol. Delegates to `parquet_reader.get_all_features_latest()`.

Augments with per-feature: `training_mean`, `training_std`, `z_score`, `staleness_seconds`, `staleness_status` (fresh/stale/missing).

### 4.17 GET /api/features/distributions

PSI and distribution comparison. Delegates to `parquet_reader.get_feature_distributions()`.

Augments each feature with `psi`, `psi_status` (stable/monitor/shift), `training_n`, `live_n`.

Returns sorted by `psi` descending.

### 4.18 GET /api/features/missingness

Feature null rate analysis.

```sql
SELECT
    feature_name,
    COUNT(*) AS total,
    SUM(CASE WHEN value IS NULL THEN 1 ELSE 0 END) AS null_count,
    MAX(timestamp_ms) AS last_seen_non_null
FROM feature_log  -- or from parquet
GROUP BY feature_name
ORDER BY null_count DESC
```

If feature data is in parquet, compute via pandas. Return: `[{feature, total, non_null, null_count, null_pct, last_seen_ms, flagged}]`. Flagged = `null_pct > 0.05`.

### 4.19 GET /api/models

Model version registry. For each version in `/data/models/`:
- Load `metadata.json`: version, trained_date, feature_list, lgbm_params, training_window, ewm_params, auc_train, auc_val, auc_test
- Load `gate_config.json`: gate thresholds, suppression rules active at training time
- Compute diff from previous version automatically

Returns: `[{...metadata, diff_from_previous: {...}}]` sorted by trained_date descending.

### 4.20 GET /api/models/diff

Side-by-side diff of any two model versions.

**Query params:** version_a, version_b

Returns: `{added_features, removed_features, changed_params, same_params}` — each is a list of `{key, value_a, value_b}` objects.

### 4.21 GET /api/alerts

Paginated alert log from `LiveState.alert_queue`.

**Query params:** severity (INFO/WARN/CRITICAL), type (alert type string), from_ms, to_ms, resolved (bool), page, page_size

Returns: `{total, items: [{id, timestamp_ms, type, severity, message, resolved, model, symbol}]}`

### 4.22 GET /api/logs/raw

Single raw JSON record by ID and type.

**Query params:** id (required), type (prediction/trade/resolution, required)

Routes to appropriate table. Returns the full raw row as a dict. No field filtering — return everything.

Also accepts `prediction_id` to return the full chain:
```json
{
  "prediction": {...},
  "trade": {...},
  "resolution": {...}
}
```

If any chain element is missing, return `null` for that field (not 404).

### 4.23 GET /api/parquet/price

OHLCV candle data. Delegates to `parquet_reader.get_ohlcv()`.

**Query params:** symbol (required), interval (1m/5m/15m/1h, required), from_ms, to_ms

Returns: `[{timestamp_ms, open, high, low, close, volume}]` plus `{data_gap: bool, gap_ranges: [...]}`

`data_gap: true` if there are missing candles in the requested range. `gap_ranges` lists the missing intervals.

### 4.24 GET /api/parquet/features

Feature data for price overlay. Returns feature values aligned to price timestamps.

**Query params:** symbol (required), features (comma-separated list), from_ms, to_ms

Returns: `[{timestamp_ms, ofi, mlofi, mid_price_dev_30d, ...}]` — only requested features.

---

## 5. WebSocket

### 5.1 WS /ws/live

Server-push WebSocket. Sends updates every 5 seconds.

```python
# ws/broadcaster.py

async def broadcast_loop(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            snapshot = LiveState.snapshot()
            await websocket.send_json({
                "type": "status",
                "ts": int(time.time() * 1000),
                "payload": snapshot
            })
            # Push any new predictions since last send
            new_preds = LiveState.pop_new_predictions()
            if new_preds:
                await websocket.send_json({
                    "type": "predictions",
                    "ts": int(time.time() * 1000),
                    "payload": new_preds
                })
            # Push any new alerts since last send
            new_alerts = LiveState.pop_new_alerts()
            if new_alerts:
                await websocket.send_json({
                    "type": "alerts",
                    "ts": int(time.time() * 1000),
                    "payload": new_alerts
                })
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
```

Message types: `status`, `predictions`, `trades`, `alerts`

**LiveState must track which predictions/alerts have been pushed** to avoid re-sending. Use a `last_pushed_ts` cursor per message type.

**Reconnection:** Client must handle disconnects and reconnect with exponential backoff. Backend does not maintain any client-side state.

---

## 6. Docker

### 6.1 Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1"]
```

Single worker — SQLite does not support concurrent writes, and this service is read-only. Single worker avoids shared state issues.

### 6.2 docker-compose.override.yml

```yaml
version: "3.8"
services:
  dashboard:
    build:
      context: ./dashboard_api
      dockerfile: Dockerfile
    container_name: polymarket-ofi-dashboard
    ports:
      - "8765:8765"
    volumes:
      - /data:/data:ro
    environment:
      - DB_PATH=/data/logs/candidate_trades.db
      - PARQUET_DIR=/data/parquet
      - MODEL_DIR=/data/models
      - LOG_DIR=/data/logs
      - DASHBOARD_USER=${DASHBOARD_USER}
      - DASHBOARD_PASS=${DASHBOARD_PASS}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8765/api/status"]
      interval: 30s
      timeout: 10s
      retries: 3
    depends_on: []  # No dependency on trading containers — dashboard is read-only
    networks:
      - polymarket-net
```

---

## 7. Background Tasks

### 7.1 LiveState Refresh Loop

Runs every 5 seconds. Updates `LiveState` from DB and parquet.

```python
async def refresh_loop():
    while True:
        async with get_db() as db:
            await LiveState.update_from_db(db)
        await asyncio.sleep(5)
```

Updates to compute on each tick:
- Last prediction per model+symbol (most recent `ts_model_ran_ms`)
- predictions_per_hour and trades_per_hour (count rows in last 3600 seconds)
- Open trade count
- p_market API health (read from trading system's last fetch log)
- EWM state (read from trading system's EWM state file if available)

### 7.2 Rolling Metrics Refresh Loop

Runs every 60 seconds (slower — heavier query).

Updates to compute on each tick:
- Rolling-50 per model+symbol (last 50 resolved trades)
- Gate status per model
- Trend direction (compare rolling-50 now vs 15 min ago)
- Variance context (p-value vs overall accuracy)

### 7.3 PSI Refresh Loop

Runs every 6 hours. Matches cron schedule of trading system's EWM update.

- Load training distributions from model metadata
- Load live distributions from feature parquet (last 7 days)
- Compute PSI per feature per model
- Update `LiveState.psi_results`
- Generate alerts if thresholds crossed

### 7.4 Daily NE_t Log Ingestion

Reads `/data/logs/daily_net.log` on startup and every 6 hours. Parses cron log output, stores in `LiveState.daily_net_history`. Exposes via `/api/alerts` with type `NE_T_DAILY`.

---

## 8. Error Handling & Edge Cases

**All endpoints must handle these cases gracefully — never return 500 to the frontend:**

- DB file missing or locked → `503 Service Unavailable` with `detail: "Database unavailable"`
- Parquet file missing → `200 OK` with `data: []` and `data_gap: true`
- Model metadata file missing → `404` with detail, not 500
- Zero trades matching filters → `200 OK` with empty `items: []` and `total: 0`
- Division by zero in accuracy calculation → return `null` for accuracy fields
- Unresolved trade NE_t → return `null`, never 0
- SQLite read during WAL checkpoint → retry up to 3 times with 100ms backoff

**Response envelope (all list endpoints):**
```json
{
  "data": [...],
  "meta": {
    "total": 1234,
    "page": 1,
    "page_size": 50,
    "from_ms": ...,
    "to_ms": ...,
    "filters_applied": {...}
  },
  "warnings": []
}
```

`warnings` array is used for soft errors like `data_gap: true` or `psi_unavailable`.

---

## 9. Database Schema Assumptions

The backend is read-only against the existing `candidate_trades.db`. Before writing any queries, confirm the exact column names and types:

```sql
-- Run this and paste output into a comment at top of db.py
SELECT sql FROM sqlite_master WHERE type='table';
```

Map confirmed column names to the parameter names used throughout this document. If any assumed column (`prediction_id`, `p_market`, `direction`, `correct`, `suppressed_reason`, `gate_structural_reason`, `gate_adverse_pass`, `executed`, `resolved`, `ne_t_computed`) does not exist under that name, update all queries accordingly and note the mapping.

---

## 10. Implementation Order

**Phase 1 (do first):**
1. Project scaffold — directory structure, `main.py`, `requirements.txt`, `Dockerfile`
2. Authentication (`services/auth.py`) — required before anything is accessible
3. DB layer (`services/db.py`) — confirm schema, map column names
4. `services/metrics.py` — all formulas, especially `compute_realized_net()`
5. `GET /api/status` (static version, no LiveState yet)
6. `GET /api/predictions` — table + filters
7. `GET /api/trades` — table + filters + NE_t computation
8. `GET /api/resolutions`
9. Docker setup — confirm container starts, mounts, reaches DB
10. `LiveState` skeleton + 5s refresh loop
11. `WS /ws/live` — basic status push

**Phase 2:**
12. `/api/performance/summary`
13. `/api/performance/rolling` with variance context
14. `/api/performance/heatmap`
15. `/api/performance/by-divergence`
16. `/api/performance/calibration`
17. `/api/performance/funnel`
18. `/api/performance/suppression-effectiveness`

**Phase 3:**
19. `services/parquet_reader.py` — OHLCV + features
20. `/api/parquet/price` and `/api/parquet/features`
21. `/api/features/*` (all feature endpoints)
22. `services/psi.py` + PSI refresh loop

**Phase 4:**
23. `/api/models` and `/api/models/diff`
24. `services/alerts_engine.py` — all alert rules
25. `/api/alerts`
26. `/api/logs/raw` with chain view
27. `/api/performance/cross-symbol-correlation`
28. Daily NE_t log ingestion

---

## 11. Testing Checklist

For each endpoint, verify:

- [ ] Returns correct data shape (matches `API_CONTRACT.md`)
- [ ] All filter combinations work (including empty result sets — never 500)
- [ ] Pagination works at boundaries (page 1, last page, beyond last page)
- [ ] NE_t values match `compute_realized_net()` output — spot-check 10 resolved trades manually
- [ ] Rolling-50 matches manual calculation — verify one series by hand
- [ ] Wilson CI bounds are correct — compare against scipy `proportion_confint`
- [ ] Suppressed trades excluded from accuracy/NE_t where expected
- [ ] Unresolved trades excluded from NE_t (return null, not 0)
- [ ] Auth returns 401 with wrong credentials, 200 with correct
- [ ] WebSocket connects, sends status every 5s, handles disconnect cleanly
- [ ] Docker container starts, mounts `/data` read-only, passes healthcheck
- [ ] PSI computation runs without error when training stats are present
- [ ] All alert conditions fire correctly on synthetic test data

