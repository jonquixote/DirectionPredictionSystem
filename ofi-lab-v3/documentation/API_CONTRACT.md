# API_CONTRACT.md
# polymarket-ofi Dashboard API — Interface Contract

**Version:** 1.0  
**Base URL:** `http://localhost:8765`  
**Auth:** HTTP Basic (all endpoints)  
**Timestamps:** Unix milliseconds (int64) unless noted  
**Null policy:** Missing or unresolved values are `null` — never `0`, never `""`  
**Owned by:** Neither frontend nor backend. Both teams read this.  
**Change process:** Both sides must agree before any field is added, removed,
or renamed. Bump the version header on every change.

---

## Contents

1. [Authentication](#1-authentication)
2. [Shared Types](#2-shared-types)
3. [Response Envelope](#3-response-envelope)
4. [Error Shapes](#4-error-shapes)
5. [REST Endpoints](#5-rest-endpoints)
   - 5.1 Status
   - 5.2 Predictions
   - 5.3 Trades
   - 5.4 Resolutions
   - 5.5 Performance — Summary
   - 5.6 Performance — Rolling
   - 5.7 Performance — Heatmap
   - 5.8 Performance — By Divergence
   - 5.9 Performance — Calibration
   - 5.10 Performance — Funnel
   - 5.11 Performance — Suppression Effectiveness
   - 5.12 Performance — By Symbol
   - 5.13 Performance — By Contract
   - 5.14 Performance — Cross-Symbol Correlation
   - 5.15 Features — History
   - 5.16 Features — Importance
   - 5.17 Features — Live Values
   - 5.18 Features — Distributions
   - 5.19 Features — Missingness
   - 5.20 Models Registry
   - 5.21 Models Diff
   - 5.22 Alerts
   - 5.23 Raw Log
   - 5.24 Parquet — Price
   - 5.25 Parquet — Features
6. [WebSocket Contract](#6-websocket-contract)
7. [Shared Query Parameters](#7-shared-query-parameters)
8. [NE_t Formula Reference](#8-net-formula-reference)
9. [TypeScript Types](#9-typescript-types)

---

## 1. Authentication

All REST endpoints and the WebSocket require authentication.

**REST:** HTTP Basic Auth header on every request.

```
Authorization: Basic <base64(username:password)>
```

**WebSocket:** Pass credentials as a query param on connect:

```
WS /ws/live?token=<base64(username:password)>
```

**On failure:** `401 Unauthorized` with header `WWW-Authenticate: Basic`

```json
{ "detail": "Invalid credentials" }
```

---

## 2. Shared Types

### ModelVersion
```
"h60_v1" | "h60_v3" | "h300"
```

### Symbol
```
"BTCUSDT" | "SOLUSDT" | "ETHUSDT"
```

### Direction
```
"up" | "down"
```

### Outcome
```
"correct" | "incorrect" | "unresolved"
```

### ContractDuration
```
300 | 900   (seconds as integer)
```

### SuppressedReason
```
"contract_mismatch" | "utc_blackout" | null
```

### AlertSeverity
```
"INFO" | "WARN" | "CRITICAL"
```

### AlertType
```
"GATE_STATUS_CHANGE"
| "NE_T_DAILY"
| "NE_T_NEGATIVE"
| "ACCURACY_ALERT"
| "COVERAGE_DROP"
| "FEATURE_DRIFT"
| "MODEL_AGREEMENT_FLIP"
| "P_MARKET_ANOMALY"
| "API_FAILURE"
| "SUPPRESSION_EFFECTIVENESS"
```

### GateTrend
```
"up" | "down" | "stable"
```

### PSIStatus
```
"stable" | "monitor" | "shift"
```

### StalenessStatus
```
"fresh" | "stale" | "missing"
```

---

## 3. Response Envelope

All list endpoints wrap their payload in this envelope:

```typescript
interface ListResponse<T> {
  data: T[];
  meta: {
    total: number;          // total matching records (before pagination)
    page: number;
    page_size: number;
    from_ms: number | null;
    to_ms: number | null;
    filters_applied: Record<string, unknown>;
  };
  warnings: string[];       // soft errors: e.g. "data_gap", "psi_unavailable"
}
```

Single-object endpoints return the object directly (no envelope).

---

## 4. Error Shapes

```typescript
// All non-2xx responses
interface ErrorResponse {
  detail: string;           // human-readable message
  code: string;             // machine-readable: "db_unavailable", "not_found", etc.
  timestamp_ms: number;
}
```

| HTTP Status | When |
|-------------|------|
| `200 OK` | Success, including empty result sets |
| `400 Bad Request` | Invalid query params (bad timestamp, unknown model name) |
| `401 Unauthorized` | Missing or wrong credentials |
| `404 Not Found` | Record ID does not exist |
| `503 Service Unavailable` | DB locked or unreachable |

**Never return `500` to the frontend.** All unexpected errors are caught and returned as `503` with `code: "internal_error"` and a sanitized message.

---

## 5. REST Endpoints

---

### 5.1 GET /api/status

Real-time system health snapshot. Not paginated.

**Query params:** none

**Response: `StatusResponse`**

```typescript
interface StatusResponse {
  ts: number;                           // snapshot timestamp ms

  containers: ContainerStatus[];
  data_pipeline: DataPipelineHealth;
  pmarket_api: PmarketApiHealth;
  ewm_state: Record<Symbol, EWMState>;  // key: "BTCUSDT" etc.
  model_meta: ModelMetadata[];
  suppression_rules: SuppressionRule[];
  coverage_rate_1h: CoverageRate;
  gate_status: Record<ModelVersion, GateStatusMini>;
  predictions_per_hour: Record<ModelVersion, number>;
  trades_per_hour: Record<ModelVersion, number>;
  open_trades_count: number;
  system_fee: number;                   // platform fee used for NE_t backend calculations (e.g., 0.02)
}

interface ContainerStatus {
  model: ModelVersion;
  healthy: boolean;                     // false = stopped or unhealthy
  warmup: boolean;
  uptime_seconds: number;
  started_at_ms: number;
  cpu_pct: number;
  ram_used_mb: number;
  ram_total_mb: number;
  last_prediction_ms: number | null;    // null if no predictions yet
  last_prediction_age_seconds: number | null;
}

interface DataPipelineHealth {
  last_parquet_update_ms: number | null;
  hours_since_ingest: number | null;    // null if never updated
  parquet_file_sizes: Record<string, number>;  // bytes per file
  parquet_row_counts: Record<string, number>;
  stale: boolean;                       // true if hours_since_ingest > 2
}

interface PmarketApiHealth {
  last_fetch_ms: number | null;
  latency_ms: number | null;
  consecutive_failures: number;
  degraded: boolean;                    // true if consecutive_failures >= 3
}

interface EWMState {
  symbol: Symbol;
  rows_loaded: number;
  mean: number | null;
  std: number | null;
  last_updated_ms: number | null;
  stale: boolean;
}

interface ModelMetadata {
  version: ModelVersion;
  trained_date: string;                 // ISO date "2026-01-15"
  feature_count: number;
  feature_list: string[];
  auc_train: number | null;
  auc_val: number | null;
  auc_test: number | null;
  file_path: string;
  lgbm_params: Record<string, unknown>;
  training_window: {
    start_date: string;
    end_date: string;
    row_count_per_symbol: Record<Symbol, number>;
  };
  ewm_params: {
    span: number;
    update_frequency_seconds: number;
    initialization_method: string;
  };
}

interface SuppressionRule {
  name: string;                         // "UTC_BLACKOUT" | "CONTRACT_MISMATCH"
  active: boolean;
  models_affected: ModelVersion[];
  description: string;
  since_date: string;                   // ISO date
  params: Record<string, unknown>;      // e.g. {start_hour: 21, end_hour: 4}
}

interface CoverageRate {
  predictions_fired: number;
  contracts_available: number;
  rate: number;                         // 0–1
}

interface GateStatusMini {
  model: ModelVersion;
  rolling_n: number;                    // 50
  rolling_value: number | null;
  gate_threshold: number;               // 0.515
  pass: boolean;
  trend: GateTrend;
  distance_to_threshold: number | null; // rolling_value - gate_threshold
}
```

---

### 5.2 GET /api/predictions

Paginated, filtered prediction records.

**Query params:** see [Section 7 — Shared Query Parameters](#7-shared-query-parameters), plus:

| Param | Type | Description |
|-------|------|-------------|
| `divergence_min` | float | `\|p_model − p_market\|` minimum |
| `divergence_max` | float | `\|p_model − p_market\|` maximum |
| `pmodel_min` | float | p_model minimum |
| `pmodel_max` | float | p_model maximum |
| `prediction_id` | string | Exact match |

**Response: `ListResponse<PredictionRecord>`**

```typescript
interface PredictionRecord {
  prediction_id: string;
  ts_model_ran_ms: number;
  symbol: Symbol;
  model: ModelVersion;
  pred_direction: Direction;
  pred_proba: number;                   // 4 decimal precision
  p_market: number | null;             // null if unavailable at prediction time
  divergence: number | null;           // |pred_proba - p_market|, null if p_market null
  signed_divergence: number | null;    // pred_proba - p_market (signed)
  warmup: boolean;
  suppressed_reason: SuppressedReason;
  features: Record<string, number | null>;  // full feature dict
  
  // Resolved from linked trade (null if no trade)
  trade_id: number | null;
  outcome: Outcome;
  realized_net: number | null;         // null if unresolved or no trade
}
```

---

### 5.3 GET /api/trades

Paginated, filtered paper trade records.

**Additional query params:**

| Param | Type | Description |
|-------|------|-------------|
| `contract_duration` | int | `300` or `900` |
| `net_sign` | string | `"positive"` or `"negative"` |
| `settled` | bool | `true` = resolved only |

**Response: `ListResponse<TradeRecord>` plus `open_trades: OpenTrade[]`**

```typescript
interface TradeRecord {
  id: number;
  prediction_id: string | null;
  timestamp_ms: number;
  symbol: Symbol;
  model: ModelVersion;
  contract_duration: ContractDuration;
  direction: Direction;
  simulated_stake_usdc: number;        // 10.00
  p_market: number | null;
  price_at_open: number | null;
  price_at_close: number | null;
  outcome: Outcome;
  correct: boolean | null;             // null if unresolved
  realized_net: number | null;         // from compute_realized_net(), null if unresolved
  realized_net_breakdown: RealizedNetBreakdown | null;
  suppressed_reason: SuppressedReason;
  gate_structural_reason: string | null;
  gate_adverse_pass: boolean | null;
  resolved: boolean;
  resolved_at_ms: number | null;

  // Links
  prediction_id: string | null;
  resolution_id: number | null;
}

interface RealizedNetBreakdown {
  // Full decomposition shown in NE_t tooltip
  direction: Direction;
  correct: boolean;
  p_market: number;
  fee: number;
  formula: string;     // human-readable: "+(1 − 0.623 − 0.02) = +0.357"
  result: number;
}

interface OpenTrade {
  id: number;
  symbol: Symbol;
  model: ModelVersion;
  contract_duration: ContractDuration;
  direction: Direction;
  timestamp_ms: number;
  elapsed_ms: number;
  p_market: number | null;
}
```

---

### 5.4 GET /api/resolutions

```typescript
interface ResolutionRecord {
  id: number;
  trade_id: number;
  resolved_at_ms: number;
  settlement_price: number | null;
  correct: boolean;
  model: ModelVersion;
  symbol: Symbol;
  direction: Direction;
  p_market: number | null;
}
```

---

### 5.5 GET /api/performance/summary

Aggregated KPIs. Used by Section 4 Overview cards.

**Query params:** model, symbol, from_ms, to_ms (all optional)

**Response: `PerformanceSummary[]`** (one per model×symbol combination)

```typescript
interface PerformanceSummary {
  model: ModelVersion;
  symbol: Symbol | "ALL";
  contract_duration: ContractDuration | "ALL";
  
  // Counts
  total_trades: number;
  wins: number;
  losses: number;
  unresolved: number;
  
  // Accuracy
  accuracy: number | null;             // null if no resolved trades
  ci_low: number | null;               // Wilson score 95% CI
  ci_high: number | null;
  z_score: number | null;
  p_value: number | null;              // one-tailed vs 50%
  is_significant: boolean | null;      // p < 0.05
  
  // NE_t (ALL from compute_realized_net() — never theoretical formula)
  realized_net_total: number | null;
  realized_net_per_trade: number | null;
  
  // Gate
  candidates_total: number;            // including suppressed
  gate_pass_rate: number | null;       // executed / candidates
  
  // High-divergence subset (top 25% |p_model − p_market|)
  high_divergence_n: number;
  high_divergence_accuracy: number | null;
  high_divergence_ci_low: number | null;
  high_divergence_ci_high: number | null;
  
  // Direction breakdown
  up_bets_n: number;
  up_bets_accuracy: number | null;
  down_bets_n: number;
  down_bets_accuracy: number | null;
}
```

---

### 5.6 GET /api/performance/rolling

Rolling accuracy time series. Used by Section 4 Rolling-N chart.

**Query params:** model (required), symbol, n (default 50), from_ms, to_ms

**Response: `RollingAccuracyResponse`**

```typescript
interface RollingAccuracyResponse {
  model: ModelVersion;
  symbol: Symbol | "ALL";
  window_n: number;
  gate_threshold: number;              // 0.515
  
  series: RollingPoint[];
  
  // Current state
  current_value: number | null;
  gate_status: "pass" | "fail" | "insufficient_data";
  trend: GateTrend;
  trend_basis: string;                 // "last 3 windows"
  
  // Variance context — always present when gate_status = "fail"
  variance_context: VarianceContext | null;
}

interface RollingPoint {
  index: number;                       // trade sequence number
  timestamp_ms: number;                // timestamp of the Nth trade in this window
  accuracy: number;
  ci_low: number;
  ci_high: number;
  gate_pass: boolean;
  n_in_window: number;                 // usually == window_n, less at start
}

interface VarianceContext {
  // Answers: "Is this gate failure real signal or just variance?"
  overall_accuracy: number;            // accuracy across all trades
  current_rolling: number;
  z_from_true: number;                 // z-score of current rolling vs overall
  p_value: number;                     // two-tailed p-value
  interpretation: string;
  // e.g. "50.0% rolling is within expected variance for a 58.4% true
  //       accuracy model (p=0.258). Not yet evidence of edge erosion."
}
```

---

### 5.7 GET /api/performance/heatmap

24×7 UTC accuracy grid. Used by Section 4 By Hour Heatmap.

**Query params:** model, symbol, from_ms, to_ms

**Response: `HeatmapResponse`**

```typescript
interface HeatmapResponse {
  model: ModelVersion | "ALL";
  symbol: Symbol | "ALL";
  
  cells: HeatmapCell[];
  
  // Aggregated periods
  daytime_accuracy: number | null;     // UTC 04:00–21:00
  daytime_n: number;
  overnight_accuracy: number | null;   // UTC 21:00–04:00
  overnight_n: number;
  
  best_hours: HourSummary[];           // hours with n >= 5, sorted by accuracy desc
  worst_hours: HourSummary[];
}

interface HeatmapCell {
  hour_utc: number;                    // 0–23
  day_of_week: number;                 // 0=Sunday … 6=Saturday
  n: number;
  wins: number;
  accuracy: number | null;
}

interface HourSummary {
  hour_utc: number;
  n: number;
  accuracy: number;
}
```

---

### 5.8 GET /api/performance/by-divergence

Accuracy and NE_t bucketed by |p_model − p_market|.

**Query params:** model, symbol, from_ms, to_ms

**Response: `DivergenceBucketResponse`**

```typescript
interface DivergenceBucketResponse {
  model: ModelVersion | "ALL";
  symbol: Symbol | "ALL";
  buckets: DivergenceBucket[];
  high_divergence_threshold: number;   // 0.10 — the top bucket cutoff
}

interface DivergenceBucket {
  label: string;                       // "0.00–0.02" | "0.02–0.05" | "0.05–0.10" | "0.10+"
  range_low: number;
  range_high: number | null;           // null for open-ended top bucket
  is_high_divergence: boolean;
  n: number;
  wins: number;
  accuracy: number | null;
  ci_low: number | null;
  ci_high: number | null;
  realized_net_per_trade: number | null;  // from compute_realized_net()
  // Theoretical shown for comparison only — clearly labeled
  theoretical_net_per_trade: number | null;  // p_model − fee − p_market average
}
```

---

### 5.9 GET /api/performance/calibration

Reliability diagram data for model and market calibration curves.

**Query params:** model, symbol, from_ms, to_ms, n_bins (default 10)

**Response: `CalibrationResponse`**

```typescript
interface CalibrationResponse {
  model_calibration: CalibrationBucket[];   // X=p_model, Y=actual win rate
  market_calibration: CalibrationBucket[];  // X=p_market, Y=actual win rate
  // Perfect calibration = diagonal line (X=Y)
}

interface CalibrationBucket {
  bin_center: number;                  // midpoint of the probability bin
  bin_low: number;
  bin_high: number;
  predicted: number;                   // mean predicted probability in this bin
  actual: number | null;               // actual win rate in this bin
  n: number;
  deviation: number | null;            // actual - predicted (positive = underconfident)
}
```

---

### 5.10 GET /api/performance/funnel

Execution gate funnel. Shows drop-off at each gate stage.

**Query params:** model, symbol, from_ms, to_ms

**Response: `FunnelResponse`**

```typescript
interface FunnelResponse {
  model: ModelVersion | "ALL";
  symbol: Symbol | "ALL";
  period: { from_ms: number; to_ms: number };
  
  stages: FunnelStage[];
  // Ordered: predictions_fired → structural → adverse → net → sanderink → executed
}

interface FunnelStage {
  name: string;
  // "predictions_fired" | "structural_pass" | "adverse_pass"
  // | "net_pass" | "sanderink_pass" | "executed"
  label: string;                       // human-readable display label
  n: number;
  drop_from_previous: number;          // absolute count dropped at this stage
  drop_rate_from_previous: number | null;  // 0–1, null for first stage
  cumulative_pass_rate: number | null; // n / predictions_fired, null for first stage
}
```

---

### 5.11 GET /api/performance/suppression-effectiveness

Validates active suppression rules by computing hypothetical P&L on suppressed trades.

**Query params:** model, symbol, from_ms, to_ms

**Response: `SuppressionEffectivenessResponse[]`** (one per active rule)

```typescript
interface SuppressionEffectivenessResponse {
  rule: SuppressedReason;
  rule_label: string;                  // "300s Contract Mismatch" | "UTC Overnight Blackout"
  period: { from_ms: number; to_ms: number };
  
  suppressed_n: number;                // total trades suppressed by this rule
  resolved_n: number;                  // of those, how many have since resolved
  
  hypothetical_accuracy: number | null;
  hypothetical_net_per_trade: number | null;  // from compute_realized_net()
  hypothetical_net_total: number | null;
  
  net_saved: number | null;            // abs(hypothetical_net_total) if negative
  decision: "CORRECT" | "INCORRECT" | "NEUTRAL" | "INSUFFICIENT_DATA";
  // CORRECT = suppression avoided negative NE_t
  // INCORRECT = suppression blocked positive NE_t
  // NEUTRAL = hypothetical near-zero NE_t
  // INSUFFICIENT_DATA = resolved_n < 20
  
  note: string;                        // e.g. "53 suppressed trades resolved; 300s
                                       //  accuracy was 34.7% (-$0.83/trade). Suppression
                                       //  saved $59.76 NE_t in this period."
}
```

---

### 5.12 GET /api/performance/by-symbol

Returns `PerformanceSummary[]` grouped by symbol, for a given model.

**Query params:** model (required), from_ms, to_ms

Same shape as 5.5, one record per symbol (`symbol` field is the symbol, not "ALL").

---

### 5.13 GET /api/performance/by-contract

Returns `PerformanceSummary[]` grouped by contract_duration (300 vs 900), for a given model.

**Query params:** model (required), symbol, from_ms, to_ms

Same shape as 5.5. Also includes the **300s vs 900s agreement analysis:**

```typescript
interface ByContractResponse {
  summaries: PerformanceSummary[];
  agreement_analysis: ContractAgreementAnalysis;
}

interface ContractAgreementAnalysis {
  // When both 300s and 900s fired in same prediction window
  matched_events_n: number;
  same_direction_n: number;
  same_direction_rate: number | null;
  
  both_correct_n: number;
  both_wrong_n: number;
  s300_wrong_s900_right_n: number;
  s300_right_s900_wrong_n: number;
  
  // When they disagree, which is right more often?
  s900_wins_on_disagreement_rate: number | null;
  // e.g. 0.631 → "900s right 63% of the time when they disagree"
  
  note: string;
}
```

---

### 5.14 GET /api/performance/cross-symbol-correlation

Pairwise outcome correlation between symbols within matched prediction windows.

**Query params:** model (required), from_ms, to_ms, match_window_seconds (default 60)

**Response: `CrossSymbolCorrelationResponse`**

```typescript
interface CrossSymbolCorrelationResponse {
  model: ModelVersion;
  match_window_seconds: number;
  pairs: CorrelationPair[];
}

interface CorrelationPair {
  symbol_a: Symbol;
  symbol_b: Symbol;
  n_matched: number;
  pearson_r: number | null;
  p_value: number | null;
  interpretation: string;
  // e.g. "Weak positive correlation (r=0.14, p=0.23) — outcomes largely independent"
}
```

---

### 5.15 GET /api/features/history

Feature value time series from parquet.

**Query params:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `feature` | string | ✓ | Feature name (e.g. `"mid_price_dev_30d"`) |
| `symbol` | Symbol | ✓ | |
| `from_ms` | int | | |
| `to_ms` | int | | |

**Response: `FeatureHistoryResponse`**

```typescript
interface FeatureHistoryResponse {
  feature: string;
  symbol: Symbol;
  series: FeaturePoint[];
  ewm_mean: number | null;
  ewm_std: number | null;
  last_updated_ms: number | null;
  data_gap: boolean;
}

interface FeaturePoint {
  timestamp_ms: number;
  value: number | null;
}
```

---

### 5.16 GET /api/features/importance

Feature importances for a model version.

**Query params:** model_version (required)

**Response: `FeatureImportanceResponse`**

```typescript
interface FeatureImportanceResponse {
  model_version: ModelVersion;
  gain: FeatureScore[];   // sorted descending by score
  split: FeatureScore[];
  shap_available: boolean;
  shap_values: FeatureScore[] | null;  // null if not available
}

interface FeatureScore {
  feature: string;
  score: number;
  rank: number;
}
```

---

### 5.17 GET /api/features/live-values

Most recent feature values per model+symbol.

**Query params:** model (required), symbol (required)

**Response: `LiveFeatureValuesResponse`**

```typescript
interface LiveFeatureValuesResponse {
  model: ModelVersion;
  symbol: Symbol;
  snapshot_ms: number;
  
  features: LiveFeatureEntry[];
}

interface LiveFeatureEntry {
  feature: string;
  value: number | null;
  training_mean: number | null;
  training_std: number | null;
  z_score: number | null;           // (value - training_mean) / training_std
  staleness_seconds: number | null; // seconds since last computed
  staleness_status: StalenessStatus;
  importance_rank: number | null;   // from gain importance
}
```

---

### 5.18 GET /api/features/distributions

PSI and distribution comparison for all features.

**Query params:** model (required), symbol, live_window_days (default 7)

**Response: `FeatureDistributionsResponse`**

Sorted by `psi` descending (most shifted first).

```typescript
interface FeatureDistributionsResponse {
  model: ModelVersion;
  symbol: Symbol | "ALL";
  live_window_days: number;
  computed_at_ms: number;
  features: FeatureDistribution[];
}

interface FeatureDistribution {
  feature: string;
  psi: number | null;
  psi_status: PSIStatus;
  
  // Histogram data for both distributions
  training_histogram: HistogramBin[];
  live_histogram: HistogramBin[];
  
  training_n: number;
  live_n: number;
  
  training_mean: number | null;
  training_std: number | null;
  live_mean: number | null;
  live_std: number | null;
}

interface HistogramBin {
  bin_low: number;
  bin_high: number;
  count: number;
  pct: number;       // 0–1
}
```

---

### 5.19 GET /api/features/missingness

Null rates per feature.

**Query params:** model, symbol, from_ms, to_ms

**Response: `FeatureMissingnessResponse`**

Sorted by null_pct descending.

```typescript
interface FeatureMissingnessResponse {
  features: FeatureMissingness[];
}

interface FeatureMissingness {
  feature: string;
  total: number;
  non_null: number;
  null_count: number;
  null_pct: number;           // 0–1
  last_seen_non_null_ms: number | null;
  flagged: boolean;           // true if null_pct > 0.05
}
```

---

### 5.20 GET /api/models

Model version registry. All versions.

**Response: `ModelRegistryResponse`**

```typescript
interface ModelRegistryResponse {
  versions: ModelRegistryEntry[];  // sorted by trained_date desc
}

interface ModelRegistryEntry extends ModelMetadata {
  // All fields from ModelMetadata (see 5.1), plus:
  gate_config: GateConfig;
  suppression_rules_active_at_training: SuppressionRule[];
  performance_at_training: {
    accuracy_train: number | null;
    accuracy_val: number | null;
    accuracy_test: number | null;
    up_class_pct: number | null;
    down_class_pct: number | null;
  };
  leakage_flag: boolean;            // true if accuracy_test > 0.62
  diff_from_previous: ModelDiff | null;  // null for first version
}

interface GateConfig {
  rolling_n: number;               // 50
  gate_threshold: number;          // 0.515
  sanderink_threshold: number | null;
  ne_t_gate_active: boolean;
  adverse_gate_active: boolean;
}

interface ModelDiff {
  added_features: string[];
  removed_features: string[];
  changed_params: ParamChange[];
  same_params: string[];
}

interface ParamChange {
  key: string;
  value_old: unknown;
  value_new: unknown;
}
```

---

### 5.21 GET /api/models/diff

Side-by-side diff of two model versions.

**Query params:** version_a (required), version_b (required)

**Response: `ModelDiffResponse`**

```typescript
interface ModelDiffResponse {
  version_a: ModelVersion;
  version_b: ModelVersion;
  
  feature_diff: {
    added: string[];      // in b, not in a
    removed: string[];    // in a, not in b
    unchanged: string[];
  };
  
  param_diff: ParamChange[];
  
  performance_diff: {
    accuracy_val_a: number | null;
    accuracy_val_b: number | null;
    auc_val_a: number | null;
    auc_val_b: number | null;
  };
}
```

---

### 5.22 GET /api/alerts

Paginated alert log.

**Query params:** severity, type, model, symbol, from_ms, to_ms, resolved (bool), page, page_size

**Response: `ListResponse<AlertRecord>`**

```typescript
interface AlertRecord {
  id: string;               // UUID
  timestamp_ms: number;
  type: AlertType;
  severity: AlertSeverity;
  message: string;
  model: ModelVersion | null;
  symbol: Symbol | null;
  resolved: boolean;
  resolved_at_ms: number | null;
  meta: Record<string, unknown>;  // type-specific data for UI rendering
}
```

---

### 5.23 GET /api/logs/raw

Single raw record or full event chain.

**Query params:**

| Param | Type | Description |
|-------|------|-------------|
| `id` | string or int | Record ID |
| `type` | string | `"prediction"` \| `"trade"` \| `"resolution"` |
| `prediction_id` | string | Returns full chain (overrides id + type) |

**Response: `RawRecordResponse`**

```typescript
// When type is specified (single record):
interface RawRecordResponse {
  type: "prediction" | "trade" | "resolution";
  record: Record<string, unknown>;    // full raw DB row, no field filtering
}

// When prediction_id is specified (event chain):
interface EventChainResponse {
  prediction_id: string;
  prediction: Record<string, unknown> | null;
  trade: Record<string, unknown> | null;
  resolution: Record<string, unknown> | null;
  // null means "not found", not an error
}
```

---

### 5.24 GET /api/parquet/price

OHLCV candle data.

**Query params:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `symbol` | Symbol | ✓ | |
| `interval` | string | ✓ | `"1m"` \| `"5m"` \| `"15m"` \| `"1h"` |
| `from_ms` | int | | |
| `to_ms` | int | | |

**Response: `PriceDataResponse`**

```typescript
interface PriceDataResponse {
  symbol: Symbol;
  interval: string;
  candles: OHLCV[];
  data_gap: boolean;
  gap_ranges: Array<{ from_ms: number; to_ms: number }>;
  // gap_ranges empty when data_gap = false
}

interface OHLCV {
  timestamp_ms: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}
```

---

### 5.25 GET /api/parquet/features

Feature values aligned to timestamps (for price chart overlay).

**Query params:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `symbol` | Symbol | ✓ | |
| `features` | string | ✓ | Comma-separated feature names |
| `from_ms` | int | | |
| `to_ms` | int | | |

**Response: `ParquetFeaturesResponse`**

```typescript
interface ParquetFeaturesResponse {
  symbol: Symbol;
  requested_features: string[];
  missing_features: string[];        // features not found in parquet
  rows: Array<{
    timestamp_ms: number;
    [feature: string]: number | null;
  }>;
  data_gap: boolean;
}
```

---

## 6. WebSocket Contract

### Connection

```
WS ws://localhost:8765/ws/live?token=<base64(user:pass)>
```

Client must reconnect on disconnect with exponential backoff starting at 1s, max 30s.

Backend does not hold any per-client state. On reconnect, client receives a fresh full snapshot immediately.

### Message Format

All messages from server:

```typescript
interface WSMessage {
  type: "status" | "predictions" | "trades" | "alerts";
  ts: number;        // server timestamp ms
  payload: unknown;  // typed by `type` field
}
```

### Message Types

**`type: "status"`** — sent every 5 seconds, always

```typescript
// payload: StatusResponse (same as GET /api/status)
```

**`type: "predictions"`** — sent when new predictions exist since last push

```typescript
// payload: PredictionRecord[]  (new predictions since last ws push)
```

**`type: "trades"`** — sent when trades are newly resolved since last push

```typescript
// payload: TradeRecord[]  (newly resolved trades since last ws push)
```

**`type: "alerts"`** — sent when new alerts have been generated since last push

```typescript
// payload: AlertRecord[]  (new alerts since last ws push)
```

### Client → Server Messages

The server accepts one message type from client:

```typescript
interface WSClientMessage {
  type: "ping";
}
// Server responds with:
interface WSPongMessage {
  type: "pong";
  ts: number;
}
```

No other client-to-server messages are defined. Filtering happens via REST, not WebSocket.

---

## 7. Shared Query Parameters

All list endpoints accept these parameters unless noted otherwise.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | ModelVersion | null (all) | Filter to one model version |
| `symbol` | Symbol | null (all) | Filter to one symbol |
| `from_ms` | int | null | Start timestamp (inclusive) |
| `to_ms` | int | null | End timestamp (inclusive) |
| `direction` | Direction | null (both) | |
| `suppressed` | bool | null (all) | `true` = suppressed only, `false` = non-suppressed only |
| `warmup` | bool | null (all) | `true` = warmup only |
| `outcome` | Outcome | null (all) | |
| `page` | int | 1 | 1-indexed |
| `page_size` | int | 50 | Max 500 |
| `sort` | string | default per endpoint | Field name to sort by |
| `order` | string | `"desc"` | `"asc"` or `"desc"` |

---

## 8. NE_t Formula Reference

**This formula is the single source of truth for all NE_t values in the system.**  
Both backend (`services/metrics.py`) and frontend (tooltip rendering, mock data) must use this formula.  
The theoretical formula `p_model − fee − p_market` must never be used as a primary metric.

```
Given:
  direction  ∈ {"up", "down"}
  correct    ∈ {true, false}
  p_market   ∈    — Polymarket implied probability at trade time[10]
  fee        = 0.02      — platform fee

For UP bets:
  Win  → realized_net = +(1 − p_market − fee)
  Loss → realized_net = −(p_market + fee)

For DOWN bets:
  Win  → realized_net = +(p_market − fee)
  Loss → realized_net = −((1 − p_market) + fee)

Unresolved trades → realized_net = null
```

**Break-even accuracy** (for reference in Statistical Tools):

```
For UP bet:  p_break_even = (1 − fee) / (1 − fee + fee) = (1 − fee) / 1 = 1 − fee
             Simplified:  need accuracy > p_market + fee  to be NE_t-positive in expectation

For DOWN bet: need accuracy > (1 − p_market) + fee
```

---

## 9. TypeScript Types

The following types are consumed exclusively by the frontend and are not part of the API response shapes. They are included here so backend and frontend use consistent terminology.

```typescript
// Convenience union for filter state
type AllOrValue<T> = T | "ALL";

// Used in filterStore.ts
interface FilterState {
  model: ModelVersion | null;
  symbol: Symbol | null;
  from_ms: number | null;
  to_ms: number | null;
  direction: Direction | null;
  suppressed: boolean | null;
  warmup: boolean | null;
  outcome: Outcome | null;
  contract_duration: ContractDuration | null;
  divergence_min: number | null;
  divergence_max: number | null;
  pmodel_min: number | null;
  pmodel_max: number | null;
  net_sign: "positive" | "negative" | null;
  settled: boolean | null;
}

// Used by RollingAccuracyChart.tsx
interface GateStatusDisplay {
  model: ModelVersion;
  rolling_value: number | null;
  gate_threshold: number;
  pass: boolean;
  trend: GateTrend;
  trend_icon: "↑" | "↓" | "→";
  distance_label: string;  // e.g. "+3.5pp above threshold" or "1.5pp below threshold"
  variance_note: string | null;  // shown when failing gate; from variance_context
}

// Used by CandlestickChart.tsx prediction markers
interface PredictionMarker {
  timestamp_ms: number;
  direction: Direction;
  outcome: Outcome;
  suppressed: boolean;
  model: ModelVersion;
  pred_proba: number;
  p_market: number | null;
  trade_id: number | null;
}

// Used by ContractWindowSpan in CandlestickChart.tsx
interface ContractWindow {
  open_ms: number;
  close_ms: number;
  direction: Direction;
  outcome: Outcome;
  realized_net: number | null;
  symbol: Symbol;
  duration: ContractDuration;
}
```

---

*End of API_CONTRACT.md — v1.0*
```