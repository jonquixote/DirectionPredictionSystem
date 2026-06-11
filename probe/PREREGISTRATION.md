# PRE-REGISTRATION — 10-Day Signal Hunt

**Registered:** 2026-06-10 (Day 0). **Clock ends:** Day 10 (2026-06-20, midnight PT).
**Branch:** `probe/10day-signal-hunt`.
**Rule:** This file is the contract. Day 9 arguments lose to Day 0 text. Thresholds below
are final. A failed gate is a finding, not a failure.

---

## 0. Day-0 Verification Results (evidence-backed, all probed 2026-06-10)

### 0.1 Fee schedule — VERIFIED from docs + live market objects

- Formula: `fee = shares × feeRate × p × (1 − p)`, **feeRate = 0.07**, taker-only.
  Source: https://docs.polymarket.com/polymarket-learn/trading/fees
  (100 sh @ 0.50 → $1.75; @ 0.30/0.70 → $1.47; symmetric around 0.50).
- Maker rebates: 20% of collected taker fees redistributed daily
  (`feeSchedule.rebateRate: 0.2`; `makerRebatesFeeShareBps: 10000`). Min payout $1.
  Source: https://docs.polymarket.com/polymarket-learn/trading/maker-rebates-program
- **Both 5m and 15m markets carry fees** (live market objects, both durations:
  `feesEnabled: true, feeType: crypto_fees_v2, rate 0.07, takerOnly: true`).
  This contradicts Jan-2026 press ("15-min only") — live object state governs.
- Fee regime history: 15m fees began ~2026-01-07 (press). Exact 5m fee start date
  UNKNOWN. **Registered handling:** forensics window (≤6 weeks back, all of May–June)
  assumes current schedule throughout; A1 reports a zero-fee sensitivity column so
  regime-boundary error is bounded and visible.

### 0.2 Resolution semantics — VERIFIED from live market descriptions

- "Resolve to 'Up' if the price at the end of the time range ... is **greater than or
  equal to** the price at the beginning. Otherwise 'Down'." → **flat resolves UP.**
  (Codebase corrected + backfilled 2026-06-10, commit `23a01e9`.)
- Resolution source: **Chainlink data streams** per symbol (e.g.
  https://data.chain.link/streams/btc-usd), explicitly "not other sources or spot
  markets". Consequence: wallet PnL in Track A uses the market's own resolved outcome
  (gamma `outcomePrices`), never our Bybit feed. Track B labels use Bybit L2 mid
  (only historical option); A1 measures Bybit-vs-market outcome agreement (gate below).

### 0.3 Market universe — DEVIATION REGISTERED

- 5m and 15m markets exist for btc/eth/sol/xrp (8 markets), slug
  `{sym}-updown-{5m|15m}-{window_start_unix}`.
- **30m markets do not exist** (probed `30m`, `1h`, `hourly` slugs — none found).
  Universe is **8 markets, not 12**. All "12 markets" language inherited from the task
  is amended to 8.

### 0.4 Data access — VERIFIED

| Path | Status | Evidence |
|---|---|---|
| Gamma market lookup incl. resolved | WORKS with `closed=true` (resolved markets vanish from default queries ~10 min after close) | probed 5-week-old market, got conditionId + `outcomePrices` |
| data-api `/trades?market=<conditionId>` | WORKS, 500/page, `offset` pagination, week-old+ markets served | 500/500 matching rows |
| data-api `/trades?user=<wallet>`, `/positions?user=` (cashPnl) | WORKS | probed live wallet |
| Maker/taker attribution | Feed is taker-perspective; maker identity NOT in free API. Chain fallback (CTF `OrderFilled` via free RPC / subgraph) documented, probed only if Gate A reaches classification needs | registered limitation |
| Rate limits | Unmeasured. Registered: ≤5 req/s, exponential backoff on 429 | conservative |
| Bybit L2 store | 408 days × 4 symbols (2025-04-29..2026-06-10), 72 GB raw snapshot+delta | VPS `ls` |
| Tick resolution for true-OFI | **Δt ≈ 100 ms** (Bybit 100ms stream; p50=100ms, p99=209ms, 832k msgs/day BTC). True-OFI computed at 100ms ticks | VPS parquet probe |
| Spot latency reference for A3 | L2 mid @ 100ms (exceeds 1s requirement) | same store |

---

## A. TRACK A GATES (wallet forensics)

**Window:** longest available with ≥95% outcome-resolvable trades, target 5–6 weeks,
8 markets. **W1/W2 split:** W2 = most recent ~12 days, adjusted so W2 ≥ 25% of volume.

**A1 sanity gates (data quality, before any ranking):**
- Per-day volume within order-of-magnitude of public figures.
- ≥95% of trades joinable to a resolved outcome.
- Bybit-window-direction vs market `outcomePrices` agreement ≥ 98% on non-flat windows
  (measures Chainlink/Bybit divergence; failure = STOP and characterize before ranking).

**A2 persistence (the experiment):**
- Eligibility: ≥200 resolved W1 trades AND ≥50 W2 trades.
- Cohorts: top decile by W1 net PnL, top quartile, random control of equal size.
- Metrics: W2 net PnL + win rate with bootstrap 95% CI vs control; Spearman(W1 PnL, W2 PnL).

**A3 classification:** LATENCY = median entry latency < 3s after last ≥0.05% spot move
AND taker-heavy. MAKER / SLOW-ALPHA / TAIL-CONVERGER per task definitions.

**GATE A — proceed to build only if ALL:**
1. A cohort's W2 net PnL beats the random control with non-overlapping 95% CIs.
2. ≥1 surviving wallet classifies non-LATENCY.
3. Copy-with-lag at 30s: net PnL > 0 with Wilson 95% lower bound > 0 on n ≥ 300
   copyable trades, at current taker fees, with registered slippage proxy
   (next same-side print at/after entry + half contemporaneous spread; if book absent,
   +1¢ haircut).

Failure of any → Track A stops Day 3 with written finding.

## B. TRACK B GATES (one model attempt — true OFI)

**Features (list CLOSED at registration):**
1. Cont-2014 OFI, levels 1–5, computed from 100ms book deltas (exact branch conditions
   on best-level price/size changes).
2. Cumulative OFI lookbacks: 10s, 30s, 60s, 300s (per level-group: L1 and L1-5 weighted).
3. Depth-normalized OFI variants (divide by trailing same-window mean top-5 depth).
4. distance-from-window-open (price vs window open, bps).
5. EWMA realized vol (60s half-life on 1s returns).
6. time-remaining-in-window (fraction).
No additions after first results. No exceptions.

**Target:** window outcome (close ≥ open → UP, i.e. flat=UP) per 5m/15m market window,
Bybit L2 mid basis.

**Trade rule:** fire when |P_model − p_market| > θ where θ ≥ fee(p_market)/notional +
half-spread; θ registered per-market at evaluation start, before test touch.
**Metric:** EV per trade net of current fees. Accuracy secondary.

**Model + search budget (FIXED, 16 configs):**
LightGBM binary. Grid = num_leaves {31, 63} × learning_rate {0.05, 0.1} ×
min_child_samples {100, 500} × bagging_fraction {0.8, 1.0}; feature_fraction 0.8,
n_estimators 300, max_depth −1 fixed. Selection: walk-forward AUC **inside the training
span only**, embargo ≥ 1800s + 300s (longest lookback) at every split. Normalization/
calibration/weights fit on train folds only.

**Single-touch rule:** final test = the live-logged span with per-prediction `p_market`
(2026-05-24 onward, excluding any days used in training). Touched ONCE, after the grid
winner is frozen. Anyone "just checking" early = stop + disclose in the verdict.

**GATE B — success only if ALL, on the single touch:**
1. EV per trade net of fees > 0, bootstrap 95% lower bound > 0.
2. Wilson 95% lower bound on traded-subset directional accuracy ≥ 52%.
3. n ≥ 2,000 fired trades (aggregate across 8 markets allowed).
4. 48h live paper shadow directionally consistent with offline (no sign flip).

No second attempt. No added features. No test re-touch.

## DAY 10 DECISION TABLE (binding)

| Gate A | Gate B | Decision |
|---|---|---|
| PASS | FAIL | BUILD copier; treat edge as perishable; monthly persistence re-runs |
| FAIL | PASS | SHADOW→small-live for model with band-gating risk hygiene |
| PASS | PASS | BUILD copier (funds patience); model graduates as primary asset |
| FAIL | FAIL | **KILL**: zero-touch shadow mode; write-up is the salvage value; owner reallocates to Branch B |

## GUARDRAILS (binding, from the task)

Evidence verbatim; controls always; test touched once; gates are findings; no inversion
strategies; model OUR execution at OUR fees; the 10-day clock is real.
