# ofi-lab Architecture

*Last updated: 2026-04-30*

---

## System Purpose

`ofi-lab` is a paper-trading system that predicts 5-minute crypto price direction
using order flow imbalance (OFI) features derived from Bybit spot L2 order books,
then simulates binary option trades on Polymarket "Up/Down" contracts.

The system does **not** execute real trades. It logs simulated P&L to append-only
JSONL ledgers for model evaluation and strategy refinement.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                    DATA INGESTION                         │
│                                                          │
│  Bybit S3 Archive ──► download_orderbook.py              │
│         │              (historical L2 snapshots)         │
│         ▼                                                │
│  /data/parquet/orderbook/{SYMBOL}/{date}_ob200.parquet   │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│                  FEATURE PIPELINE (offline)               │
│                                                          │
│  Step 1: build_features.py                               │
│     L2 orderbook → MLOFI, OFI, spread, VWAP, roll       │
│     Output: /data/features/                              │
│                                                          │
│  Step 2: add_rolling_features.py                         │
│     30s/60s rolling means and stds                       │
│     Output: /data/features_v2/                           │
│                                                          │
│  Step 3: build_features_v3.py                            │
│     VWAP enrichment, mlofi_momentum, cross-asset         │
│     Output: /data/features_v3/ (34 cols: 33 + cts)      │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│                  MODEL TRAINING (offline)                 │
│                                                          │
│  validation/run_training.py                              │
│    --horizon 300 --feature-dir /data/features_v3         │
│                                                          │
│  Algorithm:    LightGBM (LGBMClassifier)                 │
│  Features:     33 columns (see feature_names.json)       │
│  Output:       /data/models/run_YYYYMMDD_HHMMSS/         │
│                  ├── model.lgb                            │
│                  ├── config_snapshot.json                 │
│                  ├── feature_names.json                   │
│                  ├── metrics.json                         │
│                  ├── walk_forward_results.json            │
│                  ├── calibration.png                      │
│                  └── shap_importance.png                  │
│                                                          │
│  Go/no-go gate: AUC_contract >= 0.53                     │
│  Deterministic: random_state=42 → byte-identical output  │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│              LIVE PAPER TRADING (real-time)               │
│                                                          │
│  ┌─────────────┐    ┌──────────────┐                     │
│  │ Bybit WS    │───►│ LiveFeature  │                     │
│  │ L2 stream   │    │ Computer     │                     │
│  │ (10 levels) │    │              │                     │
│  └─────────────┘    └──────┬───────┘                     │
│                            │ feature bars                │
│                            ▼                             │
│                    ┌───────────────┐                      │
│                    │ LightGBM      │                      │
│                    │ model.predict │                      │
│                    └───────┬───────┘                      │
│                            │ pred_proba                  │
│                            ▼                             │
│                    ┌───────────────┐     ┌─────────────┐ │
│                    │ Confidence    │────►│ JSONL       │ │
│                    │ Gate (0.55)   │     │ Ledger      │ │
│                    └───────┬───────┘     └─────────────┘ │
│                            │                             │
│                            ▼                             │
│                    ┌───────────────┐                      │
│                    │ Polymarket    │                      │
│                    │ Discovery     │                      │
│                    │ (p_market)    │                      │
│                    └───────────────┘                      │
└──────────────────────────────────────────────────────────┘
```

---

## 6 Key Design Decisions

### 1. H300 on 900s Only (`H300_SUPPRESS_DURATIONS`)

The h300 model is trained on a 300-second (5-minute) horizon but is deployed
**exclusively** on 900-second (15-minute) Polymarket contracts. The 300s contract
duration is suppressed via:

```python
H300_SUPPRESS_DURATIONS = {"h300": {300}}
```

**Rationale:** The 5-minute directional signal detects moves that take 15 minutes
to fully materialize. The model's exceptional 70% win rate on 900s contracts is
an emergent property — it captures order flow imbalance that precedes larger moves.
This line must never be changed without a 48-hour paper trading comparison.

### 2. Bybit Data, Polymarket Execution

The model is trained entirely on **Bybit spot L2** order book data. It does not
use Polymarket data for training. Polymarket is used only for:
- Contract discovery (Gamma API → slug resolution)
- Market midpoint (CLOB `/midpoint` → p_market for divergence tracking)
- Fee rate queries (CLOB `/fee-rate`)

This separation means the prediction pipeline is completely independent of
Polymarket API availability. If Polymarket goes down, predictions continue;
only trade logging is affected.

### 3. Append-Only JSONL Ledgers

All predictions and trades are logged to append-only JSONL files. Records are
**never modified or deleted**. Each line is a self-contained JSON object with
a `record_type` field:

- `prediction` — logged for every model/symbol/boundary, even during warmup
- `trade_entry` — logged only when confidence gate is passed
- `trade_resolution` — logged when the contract expires
- `prediction_resolution` — logged for tracking accuracy

Trade entries include a `suppressed_reason` field (`null`, `"utc_blackout"`,
`"contract_mismatch"`) so that suppressed trades are still tracked for
counterfactual analysis.

### 4. 30-Minute MAD Warmup

The MLOFI normalisation uses Median Absolute Deviation (MAD) with a rolling
window of 1000 samples. On cold start, the MAD estimate is unstable, which
causes false triggers and unreliable predictions.

All paper traders suppress trades for the first 1800 seconds (30 minutes)
after first data arrives (`MAD_WARMUP_SECONDS = 1800`). Predictions are still
logged during warmup (with `warmup: true`) for analysis, but no trades fire.

### 5. Fee Model: `fee = stake × 0.072 × p × (1-p)`

Polymarket crypto taker fees follow a probability-nonlinear schedule:
- The fee rate for crypto markets is **0.072** (7.2%)
- Actual fee is `fee = stake × 0.072 × p_side × (1 - p_side)`
- At p=0.50: fee = 1.8% of stake (maximum)
- At p=0.95: fee = 0.34% of stake (minimum)

This coefficient is hardcoded in all paper traders as
`POLYMARKET_FEE_COEFFICIENT = 0.072`. It is NOT fetched from the API at
runtime because the API returns a different parameter (`base_fee` in bps),
not the category-specific taker rate.

### 6. UTC Blackout for H60 Models

H60 models (h60, h60_v2, h60_v3) have their trades suppressed during
21:00–03:59 UTC:

```python
H60_BLACKOUT_HOURS = set(range(21, 24)) | set(range(0, 4))
H60_BLACKOUT_MODELS = {"h60", "h60_v2", "h60_v3"}
```

During blackout, predictions are still logged but trades are written with
`suppressed_reason: "utc_blackout"`. This was added based on analysis showing
H60 models lose edge during low-liquidity overnight hours.

---

## Module Map

```
ofi-lab/
├── api/
│   ├── bybit.py              # Bybit WebSocket L2 manager
│   └── polymarket.py          # CLOB v2: /book, /fee-rate, parse_book()
│
├── data/
│   ├── download_orderbook.py  # Bybit S3 archive → parquet
│   ├── build_features.py      # L2 → base features
│   ├── add_rolling_features.py # → rolling features (v2)
│   └── build_features_v3.py   # → VWAP/cross-asset enrichment (v3)
│
├── feature_engineering/
│   └── mlofi.py               # MLOFI calculator (inverse-depth weighted)
│
├── trading/
│   ├── paper_trader.py         # Main multi-model paper trader
│   ├── btc_900s_paper_trader.py # BTC-only compounding strategy (RETIRED)
│   ├── h60_v2_paper_trader.py  # Dedicated h60_v2 compounder
│   ├── polymarket_discovery.py # Contract slug → token_id resolution
│   ├── live_features.py        # Real-time feature computation from WS
│   ├── ledger.py               # Append-only JSONL ledger
│   └── weekly_report.py        # Performance summary generator
│
├── validation/
│   ├── run_training.py         # Main training script (LightGBM)
│   ├── walk_forward.py         # Walk-forward cross-validation
│   ├── splitter.py             # Temporal train/val/test splits
│   └── leakage_check.py        # AUC > 62% → flag as suspect
│
├── monitoring/
│   ├── psi.py                  # Population Stability Index
│   ├── alerts.py               # Slack/email alerting
│   ├── daily_net.py            # Daily P&L summary
│   ├── h300_900s_eval.py       # h300 BTC 900s focused eval
│   └── perf_24h.py             # Rolling 24h performance
│
├── lag_measurement/
│   ├── run_pilot.py            # Coinbase WS → lag event collection
│   ├── collector.py            # OFI threshold → Polymarket depth monitoring
│   └── pilot_analysis.py       # sigma_pilot, n_required computation
│
├── config.py                   # Central configuration (all parameters)
├── CHANGE_LOG_AND_RULES.md     # Governance: Prime Directive, 8 rules
└── ARCHITECTURE.md             # This file
```

---

## Container Topology (VPS: polymarket-server)

```
┌─────────────────────────────────────────────────┐
│  polymarket-server (138.197.40.143)              │
│                                                  │
│  ┌──────────────────────────────────────┐        │
│  │ polymarket-ofi-paper-trader          │        │
│  │  Models: h300, h60, h60_v3           │        │
│  │  Logs: /data/logs/paper_trades_*.jsonl│       │
│  │  Status: UP (4 weeks)                │        │
│  └──────────────────────────────────────┘        │
│                                                  │
│  ┌──────────────────────────────────────┐        │
│  │ h60v2-trader                         │        │
│  │  Model: h60_v2 (BTC 900s only)      │        │
│  │  Logs: /data/logs/h60_v2_*.jsonl     │        │
│  │  Bankroll: $62.59 (+526%)            │        │
│  │  Status: UP (3 weeks)               │        │
│  └──────────────────────────────────────┘        │
│                                                  │
│  ┌──────────────────────────────────────┐        │
│  │ dps-backend / dps-frontend           │        │
│  │  Dashboard UI                        │        │
│  │  Status: UP                          │        │
│  └──────────────────────────────────────┘        │
│                                                  │
│  Storage:                                        │
│    /data/models/         — production models     │
│    /data/features_v3/    — pre-computed features │
│    /data/parquet/         — raw orderbook data   │
│    /data/logs/            — all JSONL ledgers    │
└─────────────────────────────────────────────────┘
```

---

## Data Flow Summary

1. **Historical**: Bybit S3 → parquet → features → features_v2 → features_v3 → LightGBM training
2. **Live**: Bybit WebSocket → LiveFeatureComputer → model.predict → confidence gate → JSONL ledger
3. **Market context**: Gamma API → contract discovery → CLOB midpoint → p_market (logged, not gating)

---

## Governance

All changes to this codebase are governed by `CHANGE_LOG_AND_RULES.md`.
The Prime Directive: **no changes to the 33-column feature schema, model
algorithm, or hyperparameters without explicit sign-off and a new experiment
branch.**

The frozen archive at `freeze-20260428/` contains the byte-verified original
`h300_v1` model and is **read-only**.
