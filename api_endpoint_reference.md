# Paper Trading Monitoring API — Endpoint Reference

> Server runs on `0.0.0.0:8080` inside the trading container.  
> All responses are JSON. No authentication (internal network only).

---

## Control Endpoints

### `GET /config`
Returns the full current filter configuration dict.

```bash
curl http://localhost:8080/config
```

### `PATCH /config`
Merge partial JSON into live config. Takes effect at the next 5-min boundary.

```bash
# Lower confidence threshold for BTC only
curl -X PATCH http://localhost:8080/config \
  -H "Content-Type: application/json" \
  -d '{"per_symbol_confidence": {"BTCUSDT": 0.52}}'

# Enable Kelly sizing at quarter-Kelly
curl -X PATCH http://localhost:8080/config \
  -H "Content-Type: application/json" \
  -d '{"kelly_sizing_enabled": true, "kelly_fraction": 0.25}'
```

**Response:** `{"updated": {...changed keys...}, "config": {...full config...}}`

### `POST /pause`
Emergency kill switch — suppresses all trades immediately.

```bash
curl -X POST http://localhost:8080/pause
```

### `POST /resume`
Resume trading after a pause.

```bash
curl -X POST http://localhost:8080/resume
```

---

## Status & Market

### `GET /status`
Lightweight system overview. **No file reads** — all in-memory.

```bash
curl http://localhost:8080/status
```

**Response shape:**
```json
{
  "running": true,
  "paused": false,
  "predictions_total": 450,
  "trades_total": 38,
  "pending_trade_resolutions": 2,
  "pending_pred_resolutions": 6,
  "running_pnl": {"h300": -12.45},
  "models": ["h60", "h300"],
  "symbols": {
    "prediction": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "trade": ["BTCUSDT", "SOLUSDT"]
  },
  "uptime_seconds": 86420,
  "warmup_complete": true,
  "filters": { ...full filter config... }
}
```

### `GET /market`
Live market state from the in-memory feature computer.

```bash
curl http://localhost:8080/market
```

**Response shape:**
```json
{
  "BTCUSDT": {
    "mid_price": 94521.50,
    "spread": 0.50,
    "update_count": null,
    "mid_price_history_len": 362
  },
  "SOLUSDT": { ... }
}
```

---

## Data Feeds (Paginated)

### `GET /predictions`
All predictions merged with resolutions where available.

| Param | Type | Default | Description |
|:------|:-----|:--------|:------------|
| `model` | string | all | Filter by model name |
| `symbol` | string | all | Filter by symbol |
| `status` | string | `all` | `resolved`, `pending`, or `all` |
| `correct` | string | — | `true` or `false` |
| `limit` | int | 50 | Max records (cap 500) |
| `offset` | int | 0 | Pagination offset |

```bash
# Last 20 resolved BTC predictions for h300
curl "http://localhost:8080/predictions?model=h300&symbol=BTCUSDT&status=resolved&limit=20"
```

**Response shape:**
```json
{
  "total": 142,
  "offset": 0,
  "limit": 20,
  "count": 20,
  "predictions": [
    {
      "record_type": "prediction",
      "prediction_id": "abc-123",
      "model": "h300",
      "symbol": "BTCUSDT",
      "pred_proba": 0.623,
      "pred_direction": "up",
      "above_threshold": true,
      "price_at_contract_open": 94500.0,
      "p_market": 0.52,
      "prediction_correct": true,
      "contract_result": "up",
      "price_at_contract_close": 94680.0
    }
  ]
}
```

> **Note:** `features` dict is stripped from responses to save bandwidth.

### `GET /trades`
All trades merged with resolutions.

| Param | Type | Default | Description |
|:------|:-----|:--------|:------------|
| `model` | string | all | Filter by model name |
| `symbol` | string | all | Filter by symbol |
| `duration` | string | all | `300` or `900` |
| `status` | string | `all` | `resolved`, `pending`, `suppressed`, or `all` |
| `result` | string | — | `win` or `loss` |
| `limit` | int | 50 | Max records (cap 500) |
| `offset` | int | 0 | Pagination offset |

```bash
# All resolved h300 900s trades
curl "http://localhost:8080/trades?model=h300&duration=900&status=resolved"

# Just wins
curl "http://localhost:8080/trades?result=win"
```

### `GET /pending`
In-memory pending resolutions with countdown timers.

| Param | Type | Default | Description |
|:------|:-----|:--------|:------------|
| `type` | string | `all` | `trades`, `predictions`, or `all` |
| `model` | string | all | Filter by model name |

```bash
curl "http://localhost:8080/pending?type=trades"
```

**Response shape:**
```json
{
  "pending_trades": [
    {
      "resolve_at": "2026-05-01T05:15:00+00:00",
      "resolve_in_seconds": 412,
      "trade_id": "def-456",
      "model": "h300",
      "symbol": "BTCUSDT",
      "pred_direction": "up",
      "pred_proba": 0.612,
      "price_at_open": 94500.0,
      "duration": 900,
      "stake": 10.0,
      "p_market": 0.51
    }
  ],
  "pending_predictions": [ ... ]
}
```

---

## Performance (Server-Side Aggregates)

### `GET /performance`
Comprehensive stats broken down by model, symbol, and duration.

| Param | Type | Default | Description |
|:------|:-----|:--------|:------------|
| `model` | string | all | Filter by model name |
| `hours` | float | — | Lookback window (e.g. `24` for last 24h) |

```bash
# Full performance, all time
curl http://localhost:8080/performance

# Last 24 hours for h300 only
curl "http://localhost:8080/performance?model=h300&hours=24"
```

**Response shape:**
```json
{
  "h300": {
    "predictions": {
      "total": 450,
      "resolved": 380,
      "correct": 210,
      "incorrect": 170,
      "accuracy": 0.5526
    },
    "predictions_by_symbol": {
      "BTCUSDT": {"total": 150, "resolved": 130, "correct": 74, "accuracy": 0.5692},
      "SOLUSDT": { ... }
    },
    "trades": {
      "total_entries": 52,
      "suppressed": 8,
      "active": 44,
      "resolved": 38,
      "pending": 6,
      "wins": 22,
      "losses": 16,
      "win_rate": 0.5789,
      "gross_pnl": 18.42,
      "total_fees": 3.21,
      "net_pnl": 15.21,
      "avg_pnl": 0.40,
      "best_trade": 8.52,
      "worst_trade": -10.0
    },
    "trades_by_symbol": {
      "BTCUSDT": {"active": 22, "resolved": 18, "wins": 11, "losses": 7, "net_pnl": 9.82, "win_rate": 0.6111}
    },
    "trades_by_duration": {
      "900": {"active": 20, "resolved": 16, "wins": 10, "losses": 6, "net_pnl": 12.50, "win_rate": 0.625}
    }
  }
}
```

### `GET /performance/pnl_series`
Cumulative P&L time series for charting.

| Param | Type | Required | Description |
|:------|:-----|:---------|:------------|
| `model` | string | **yes** | Model name |

```bash
curl "http://localhost:8080/performance/pnl_series?model=h300"
```

**Response:** Array of `{ts, net_pnl, cumulative_pnl, trade_result, symbol, duration}`

### `GET /performance/accuracy_series`
Rolling prediction accuracy for charting.

| Param | Type | Default | Description |
|:------|:-----|:--------|:------------|
| `model` | string | **required** | Model name |
| `window` | int | 20 | Rolling window size |

```bash
curl "http://localhost:8080/performance/accuracy_series?model=h300&window=50"
```

**Response:** Array of `{ts, prediction_correct, rolling_accuracy, cumulative_accuracy, symbol}`

---

## Filter Analysis

### `GET /suppression_log`
All suppressed trades grouped by reason. Useful for tuning filters.

| Param | Type | Default | Description |
|:------|:-----|:--------|:------------|
| `model` | string | all | Filter by model |
| `reason` | string | all | Filter by reason (e.g. `paused`, `circuit_breaker`) |
| `limit` | int | 100 | Max records (cap 500) |

```bash
curl "http://localhost:8080/suppression_log?reason=clob_divergence"
```

**Response:**
```json
{
  "total": 24,
  "reason_counts": {
    "utc_blackout": 12,
    "contract_mismatch": 8,
    "clob_divergence": 4
  },
  "suppressions": [ ...trade_entry records... ]
}
```

---

## Endpoint Summary

| # | Method | Path | Purpose | Reads Disk? |
|:--|:-------|:-----|:--------|:------------|
| 1 | GET | `/config` | Filter config | No |
| 2 | PATCH | `/config` | Runtime tuning | No |
| 3 | POST | `/pause` | Kill switch | No |
| 4 | POST | `/resume` | Resume | No |
| 5 | GET | `/status` | System overview | No |
| 6 | GET | `/market` | Live prices | No |
| 7 | GET | `/predictions` | Prediction feed | Yes |
| 8 | GET | `/trades` | Trade feed | Yes |
| 9 | GET | `/pending` | Pending items | No |
| 10 | GET | `/performance` | Aggregated stats | Yes |
| 11 | GET | `/performance/pnl_series` | P&L chart data | Yes |
| 12 | GET | `/performance/accuracy_series` | Accuracy chart data | Yes |
| 13 | GET | `/suppression_log` | Filter analysis | Yes |
