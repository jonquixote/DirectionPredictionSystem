# MASTER CONSIDERATIONS & PRE-LIVE CHECKLIST
**Project:** `ofi-lab` — h300_v1 Retraining & Live Deployment
**Date:** 2026-04-28
**Status:** Pre-live planning — NO real money deployed yet

> This document captures every consideration, risk, and open question identified
> during the post-mortem and planning sessions of April 27-28, 2026.
> Work through every section before deploying real capital.

---

## SECTION 1: IMMEDIATE ACTIONS (Do Tonight / Before 11:00 UTC)

- [ ] **CLOB V2 Migration** — Polymarket V2 goes live at 11:00 UTC April 28
  - [ ] Update `requirements.txt`: remove `py-clob-client`, add `py-clob-client-v2>=1.0.0`
  - [ ] Update `api/polymarket.py`: new client init, `create_or_derive_api_key()`, `creds=` pattern
  - [ ] Update `trading/polymarket_discovery.py`: new imports + client instantiation
  - [ ] Manually wrap USDC.e → pUSD collateral before restart (all open orders will be cancelled)
  - [ ] Verify contract slug format `btc-updown-5m-{ts}` still valid post-V2

- [ ] **Secrets Scan Before git init**
  - [ ] Run: `grep -r "PRIVATE_KEY\|private_key\|pk=\|PK=\|api_key\|secret" ofi-lab/ --include="*.py" --include="*.env" --include="*.json"`
  - [ ] Add `.gitignore` entries: `.env`, `*.key`, `config/secrets*`, `*.pem` before first commit
  - [ ] Move all credentials to environment variables or Docker secrets — never in source files

- [ ] **Back Up 329 Days of Bybit Parquet (Irreplaceable)**
  - [ ] `rsync -avzP polymarket-server:/data/parquet/orderbook/ ~/Code/DirectionPredictionSystem/freeze-20260428/parquet/`
  - [ ] Verify Bybit archive still has data back to April 2025: `curl -I https://quote-saver.bycsi.com/orderbook/spot/BTCUSDT/...`
  - [ ] This data cannot be re-downloaded if the Bybit S3 endpoint changes or is deprecated

- [ ] **Kill `btc900s-trader` Container**
  - [ ] It is at $0.98 from $10.00 — no recovery path, only generating misleading log noise
  - [ ] `docker stop btc900s-trader && docker rm btc900s-trader`

---

## SECTION 2: FREEZE CHECKLIST (Golden Goose Collection)

- [ ] Run full freeze protocol from `GOLDEN_GOOSE_FREEZE_PROTOCOL.md`
  - [ ] STEP 0: Pre-flight — confirm container running, verify model MD5 = `8c25ce5c0e56fec14989a130094af0f9`
  - [ ] STEP 1: `docker commit polymarket-ofi-paper-trader golden-goose-h300v1:frozen-20260428`
  - [ ] STEP 2: `docker save ... | gzip -9 > /data/golden-goose-h300v1-frozen-20260428.tar.gz`
  - [ ] STEP 3: Archive model artifacts (`model.lgb`, `config_snapshot.json`, `feature_names.json`, logs)
  - [ ] STEP 4: Archive `features_v3/` (large — run in background)
  - [ ] STEP 5: `scp` / `rsync` frozen codebase to local
  - [ ] STEP 6: Pull all archives to local machine
  - [ ] STEP 7: **Verify restore** — load image, confirm model MD5 inside restored container
  - [ ] STEP 8: Write `FREEZE_RECEIPT.txt` with all MD5 hashes and operator name
  - [ ] Set freeze directory to read-only: `chmod -R a-w ~/Code/DirectionPredictionSystem/freeze-20260428/`

---

## SECTION 3: LAB SETUP CHECKLIST

- [ ] `cp -r freeze-20260428/polymarket-ofi-frozen-20260428/ ofi-lab/`
- [ ] `cd ofi-lab && git init && git add . && git commit -m "chore: initial commit from frozen polymarket-ofi 2026-04-28"`
- [ ] Drop governance docs into repo root and commit:
  - [ ] `GOLDEN_GOOSE_FREEZE_PROTOCOL.md`
  - [ ] `CHANGE_LOG_AND_RULES.md`
  - [ ] `OFI_LAB_SETUP_AND_TDD.md`
  - [ ] `MASTER_CONSIDERATIONS_CHECKLIST.md` (this file)
- [ ] Create `ARCHITECTURE.md` — document every non-obvious design decision (see Section 7)
- [ ] Create `tests/` directory with TDD stubs (5 gates from `OFI_LAB_SETUP_AND_TDD.md`)
- [ ] Create `/data/lab/models_lab/` on VPS — all new model runs write here, NEVER to `/data/models/`
- [ ] Snapshot `features_v3/` file timestamps before any pipeline run:
  - `ssh polymarket-server "stat /data/features_v3/BTCUSDT/*.parquet > /tmp/features_v3_manifest_pre_backfill.txt"`

---

## SECTION 4: DATA BACKFILL CHECKLIST

- [ ] Download 36 missing days (March 24 – April 27):
  ```bash
  python -m data.download_orderbook     --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT     --start-date 2026-03-24 --end-date 2026-04-27
  ```
- [ ] Run 3-step feature pipeline on new data only (do NOT reprocess existing files):
  - [ ] Step 1: `build_features.py`
  - [ ] Step 2: `add_rolling_features.py`
  - [ ] Step 3: `build_features_v3.py`
- [ ] Verify output schema after each step: exactly 34 columns (33 features + `cts`)
- [ ] Verify no NaN values in feature columns for new dates
- [ ] Verify existing `features_v3/` files were NOT modified (check timestamps vs. manifest)

---

## SECTION 5: DATA PIPELINE HARDENING (Prevent the 36-Day Gap From Happening Again)

- [ ] **Dead Man's Switch on Data Collection**
  - [ ] Set up a cron job or supercronic inside the data container:
    ```
    0 2 * * * python -m data.download_orderbook --date yesterday && curl -fsS https://hc-ping.com/{YOUR_KEY} > /dev/null
    ```
  - [ ] Register a free monitor at [healthchecks.io](https://healthchecks.io) — alert if no ping for 25 hours
  - [ ] Configure alert to email/SMS so a silent data stop is caught within 1 day, not 36

- [ ] **Schema Validation at Ingest**
  - [ ] Add assertion to end of `build_features_v3.py`:
    ```python
    assert df.shape[1] == 34, f"Schema error: expected 34 cols, got {df.shape[1]}"
    assert set(FEATURE_COLS).issubset(df.columns), "Missing feature columns"
    ```
  - [ ] If assertion fails, raise exception and write to `/data/alerts/schema_error_{date}.txt`

- [ ] **Verify Bybit S3 Archive Longevity**
  - [ ] Confirm `quote-saver.bycsi.com` has data going back to at least 2025-04-29 (training start)
  - [ ] Document the URL pattern and any known retention policy
  - [ ] Consider whether a secondary data source (Tardis, Kaiko) should be set up as backup

---

## SECTION 6: PRE-LIVE TRADING REQUIREMENTS (ALL MUST BE MET)

### 6A. Probability Calibration — REVIEW BEFORE SIZING ANY BET

- [ ] Pull `calibration.png` from the server: `scp polymarket-server:/data/models/latest_h300/calibration.png .`
- [ ] Inspect the calibration curve:
  - If the curve is well-calibrated (close to diagonal): proceed with current gate
  - If sigmoidal / probabilities cluster near 0.5: the 0.56 gate is tighter than you think — raise to 0.58-0.60
  - If convex / model is overconfident: apply Platt scaling or isotonic regression on val set before deploying
- [ ] **This step is mandatory before Kelly sizing**. Uncalibrated probabilities = incorrect bet sizes.

### 6B. Kelly Fraction Safety

- [ ] **Use fractional Kelly — not full Kelly**
  - Full Kelly at 33% is catastrophic for a model with unverified out-of-sample variance
  - Formula: \( f^* = p - rac{(1-p)}{b} \), then use 25-50% of that value
  - **Start at 5-10% Kelly regardless of what the simulation says**
  - A 5-loss streak at 33% Kelly = 87% bankroll loss. At 10% Kelly = 41% loss (survivable)
  - Only scale Kelly upward after 100+ out-of-sample trades confirm the win rate holds

### 6C. Circuit Breaker / Kill Switch

- [ ] Implement hard stops in `paper_trader.py` before live deployment:
  - [ ] **Daily loss limit**: halt if bankroll drops > 15% in a calendar day, require manual restart
  - [ ] **Consecutive loss streak**: pause + alert after 6 consecutive losses
  - [ ] **Confidence distribution watchdog**: if model outputs > 0.90 confidence on > 20% of trades
    in a 1-hour window, halt — this indicates feature pipeline malfunction (NaN propagation, stale data)
  - [ ] **Zero-bankroll guard**: if bankroll <= 10% of starting capital, halt permanently

### 6D. Drift Detection Watchdog

- [ ] Implement `drift_watchdog.py` as a daily cron job:
  ```python
  # Read last 7 days of closed trades from JSONL log
  # Compute rolling win rate at gate = 0.56
  # If win_rate_7d < 0.55 for 3 consecutive days: write /data/alerts/drift_{date}.txt
  # If win_rate_3d < 0.52: halt paper trading container, require manual review
  ```
- [ ] This should have caught the W17 collapse 7-10 days earlier than manual discovery

### 6E. Model Price vs. CLOB Price Divergence Gate

- [ ] Add to `paper_trader.py` trade logic:
  - Fetch current Polymarket CLOB midpoint for the contract
  - Only place order if `abs(model_prob - clob_midpoint) >= MIN_EDGE` (suggest 0.04 minimum)
  - If CLOB is already pricing your signal, there is no edge — skip the trade
  - This is the difference between finding alpha and confirming the crowd

### 6F. Volatility Regime Filter

- [ ] Add to `paper_trader.py`:
  - Compute BTC 24h realized volatility from the live Bybit feed
  - If 24h realized vol < threshold (suggest: 1.5% daily), widen gate to 0.60 or pause
  - MLOFI signals are weakest during low-volatility consolidation — this is when the model bleeds

### 6G. Rollback SOP

- [ ] Document and test the one-command restore before promoting any new model:
  ```bash
  docker load < freeze-20260428/golden-goose-h300v1-frozen-20260428.tar.gz
  docker run -d --name golden-goose-restored -v /data:/data golden-goose-h300v1:frozen-20260428 python trading/paper_trader.py
  ```
- [ ] Confirm this works on a test basis before the retrained model goes live
- [ ] Define paper-trading kill threshold: if new model win rate < 0.52 after 20+ trades → auto-revert

---

## SECTION 7: ARCHITECTURE.md — KEY DECISIONS TO DOCUMENT

Write a short explanation for each of these in `ARCHITECTURE.md`:

- [ ] **Why `H300_SUPPRESS_DURATIONS = {"h300": {300}}`** — the 5-min model is deployed on 15-min contracts because MLOFI detects moves that take 15 min to materialize; 300s contracts are too noisy
- [ ] **Why `features_v3/` and not `features_1m/`** — confirmed by 33-column schema match; `features_1m` has only 29 columns and wrong feature set
- [ ] **Why models were deployed despite failing AUC >= 0.53 gate** — gate is miscalibrated; real-world edge is at high-confidence predictions, not overall AUC
- [ ] **Why the retrain model is called `h300_v1_retrain_{date}` not v2 or v3** — v2 never existed, v3 failed its gate and was never deployed; naming must be unambiguous
- [ ] **Why the paper trader uses flat $10 stake** — measures raw model accuracy, not compounding behavior; separate from Kelly deployment
- [ ] **Why `random_state=42` is non-negotiable** — LightGBM with same data + params + seed is byte-deterministic; any seed change breaks reproducibility verification

---

## SECTION 8: RETRAINING ACCEPTANCE CRITERIA

Define success before you retrain — not after.

**A retrained model is promotable to live trading if and only if:**

- [ ] Passes all 6 TDD gates (feature schema, MD5 determinism, gate behavior, suppress durations, pipeline integrity, CLOB v2 smoke)
- [ ] Win rate at gate 0.56 on held-out test set >= **58%** (vs. h300_v1's 67.44% in-sample — conservative out-of-sample target)
- [ ] Minimum **30 trades** fired at gate >= 0.56 in the test period (sample size requirement)
- [ ] SHAP top-5 features overlap >= 3 with h300_v1's top-5 (continuity check — if the model learned completely different features, it's fitting noise)
- [ ] Calibration curve reviewed and passes (not convex/overconfident)
- [ ] Minimum **72 hours of paper trading** with >= 20 trades at gate >= 0.56
- [ ] Paper trading win rate >= 55% during the 72-hour window
- [ ] Circuit breaker and kill switch implemented and tested before live deployment
- [ ] Fractional Kelly (5-10%) confirmed as starting sizing

---

## SECTION 9: REAL MONEY RISK FRAMEWORK

**Resolve ALL of these before deploying real capital:**

- [ ] **Legal / ToS / Regulatory**
  - Verify Polymarket Terms of Service permit automated API trading from your account
  - Verify Polymarket is legally accessible and tradeable from California / the US in 2026
  - Consult a tax professional: prediction market winnings may be ordinary income, gambling income, or capital gains — each has different treatment and reporting requirements
  - Confirm bot-generated trade volume reporting obligations (hundreds of trades/month)

- [ ] **Position Sizing vs. Liquidity**
  - Check Polymarket BTC Up/Down 900s contract liquidity depth at the CLOB
  - Determine the maximum order size that can be filled at midpoint without moving the market
  - Set a hard maximum bet size in `paper_trader.py` regardless of Kelly output

- [ ] **Fee Verification**
  - Confirm current Polymarket V2 fee schedule (the simulation used `POLYMARKET_FEE = 0.072`)
  - Verify this is still accurate post-V2 migration; fees may have changed

- [ ] **Realistic Return Expectations**
  - The $13,986 from $10 simulation result is in-sample overfitting — do not use it as a target
  - Realistic live target at 5-10% Kelly with 58%+ win rate: **$50–$200 from $10 in 30 days**
  - Set expectations before deployment to prevent emotional decision-making during drawdowns

---

## SECTION 10: CONTAINER HYGIENE

- [ ] Maximum 2 concurrent live trading containers at any time (1 control + 1 experiment)
- [ ] All lab containers follow naming convention: `ofi-lab-paper-trader-{model_name}-{date}`
- [ ] All lab logs write to `/data/lab/logs_lab/` — never to `/data/logs/` (original)
- [ ] All lab models write to `/data/lab/models_lab/` — never to `/data/models/` (original)
- [ ] `h60v2-trader`: decide whether to keep (up 191% but model is fundamentally broken at low confidence — one bad streak ends it) — recommended: let it run but do not add capital
- [ ] `btc900s-trader`: KILL immediately (at $0.98, no recovery path)

---

## SECTION 11: MONITORING STACK (Build This, Don't Skip It)

| Monitor | What It Watches | Alert Condition | Tool |
|:--------|:----------------|:----------------|:-----|
| Data heartbeat | `download_orderbook.py` daily run | No ping for 25h | healthchecks.io |
| Schema watchdog | `build_features_v3.py` output | != 34 columns | Custom script → alert file |
| Drift watchdog | 7-day rolling win rate at gate 0.56 | < 0.55 for 3 days | `drift_watchdog.py` cron |
| Circuit breaker | Daily bankroll drawdown | > 15% in 1 day | In `paper_trader.py` |
| Consecutive losses | Per-trade loss counter | 6 in a row | In `paper_trader.py` |
| Confidence spike | Model output distribution | > 90% conf on > 20% of trades | In `paper_trader.py` |
| CLOB divergence | Model prob vs. CLOB midpoint | < 0.04 divergence | In `paper_trader.py` |

---

## MINI PLAN: PHASES TO LIVE DEPLOYMENT

```
PHASE 0 — TONIGHT (Before 11:00 UTC)
  ├── Secrets scan + .gitignore
  ├── Kill btc900s-trader
  ├── CLOB V2 patch (3 files)
  └── Wrap USDC.e → pUSD

PHASE 1 — FREEZE (Tonight, run in background)
  ├── docker commit + docker save + gzip
  ├── rsync 329 days of parquet to local
  └── Verify restore works (model MD5 check)

PHASE 2 — LAB BOOTSTRAP (Tomorrow)
  ├── cp frozen codebase → ofi-lab/
  ├── git init + commit governance docs
  ├── Implement 6 TDD stubs in tests/
  └── Write ARCHITECTURE.md

PHASE 3 — DATA BACKFILL (Tomorrow, ~4 hrs)
  ├── download_orderbook.py (Mar 24 – Apr 27)
  ├── Run 3-step feature pipeline (new dates only)
  └── Validate schema: 34 cols, zero NaN

PHASE 4 — RETRAIN (Day 3)
  ├── Pull calibration.png — review before anything
  ├── Train Model A (Shifted: TRAIN_END=2026-02-28)
  ├── Train Model B (Extended: TRAIN_END=2026-03-15)
  ├── Run all 6 TDD gates on both models
  └── Check SHAP top-5 overlap vs. h300_v1

PHASE 5 — PAPER TRADING VALIDATION (Days 4-7, min 72h)
  ├── Deploy best model as ofi-lab-paper-trader-h300v1_retrain_{date}
  ├── Run drift watchdog + circuit breaker live
  ├── Compare win rate vs. acceptance criteria (>= 55%, >= 20 trades)
  └── Test rollback procedure manually

PHASE 6 — LIVE DEPLOYMENT DECISION (Day 7+)
  ├── All acceptance criteria met? → Proceed
  ├── Legal/ToS/tax resolved? → Proceed
  ├── Circuit breaker tested? → Proceed
  ├── Calibration reviewed, fractional Kelly set? → Proceed
  └── Start at minimum viable stake, scale only after 100 live trades
```

---

*Last updated: 2026-04-28. Every item must be checked before Phase 6.*
*Maintainer: update this file whenever a new consideration is identified.*
