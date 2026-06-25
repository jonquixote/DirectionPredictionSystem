# DirectionPredictionSystem — Week-Long Session Log (2026-06-13 → 06-25)

**Author:** working session (Claude + owner). **Scope:** one continuous multi-day arc
covering (1) a pre-registered 10-day signal-hunt probe and its mini-probes, then (2) an
owner-driven re-examination that found the only real edge in the project.

> Read order for the full record: this log (narrative + index) →
> `probe/PROBE_SWEEP_VERDICT.md` (the six sealed kills) →
> `probe/FRESHMODEL_FINDINGS.md` (the re-examination) →
> `probe/CAPACITY_AND_KELLY.md` (the economics correction) →
> `probe/KALSHI_FADE_*` (the surviving build candidate).

---

## 0. THE GOAL (unchanged)
Turn model predictions into profitable trades on short-horizon crypto up/down prediction
markets (Polymarket, later Kalshi), BTC/ETH/SOL/XRP at 5/15/30-min windows. A LightGBM
fleet predicts direction; a gate turns confident predictions into trades. The system ran
~a month anti-predictive in aggregate (46-49% win rate). The week's job: determine whether
ANY capturable edge exists, with discipline, and stop fooling ourselves.

---

## 1. THE 10-DAY PROBE — six mechanisms, six kills (`probe/PROBE_SWEEP_VERDICT.md`)

Pre-registered (`probe/PREREGISTRATION.md`), all thresholds frozen before results. Verified
Day 0: fee = shares·0.07·p(1−p) taker on 5m AND 15m; flat resolves UP; **30m markets don't
exist** (universe = 8, not 12); resolution = Chainlink; Polymarket data-api works to ~5wk
depth. Three silent corruptions caught at Day 0 (flat-resolves-UP had been backwards;
old fee coef 0.072 vs 0.07; resolved markets vanish from default gamma queries).

| # | Mechanism | How it died | Doc |
|---|-----------|-------------|-----|
| 1 | Price-state (OBI/"MLOFI") | ~zero signal; offline features reproduce live sub-50 | Phase-1 forensics |
| 2 | Price-flow (true Cont-2014 OFI) | Gate B FAIL: fresh-slice EV CI crosses zero (50.84%, LB 50.24%) | `probe/VERDICT.md` |
| 3 | Copy-the-winners (taker) | Gross skill persists (Spearman 0.31, p≈10⁻⁷⁹) but 0.07·p(1−p) fee confiscates 100%+; cohort CIs overlap control | `probe/VERDICT.md` |
| 4 | Kalshi maker seat | §0 volume wall: 4 markets, deep queues, $300/day bar unreachable | `KALSHI_MAKER_VERDICT.md` |
| 5 | Fair-value deviation | Stage-1 KILL: 92% supra-cost deviation but symmetric/unbiased/own-model-error (concentration control) | `FAIRVALUE_VERDICT.md` |
| 6 | Intra-window inversion | Oscillation real (85% round-trips complete) but incomplete legs = adverse selection; EV −$0.064/window | `INVERSION_VERDICT.md` |

**Unifying finding at the time:** price is efficient vs everything a solo operator computes
from public data, and the fee structure confiscates persistent skill. Convergence across six
independent doors. Real, defensible, evidence-backed.

### Methodology that held (binding)
1. Every green light interrogated for what it INDEPENDENTLY compares.
2. Evidence verbatim, never "verified successfully."
3. Pre-registration is the contract; gates amendable only BEFORE results and only HARDER.
4. Gates are findings, not failures.
5. Controls always (random-control twin, zero-delta sanity, base rate).
6. Test set touched once.
7. Anchored bars ($300/day = owner attention opportunity cost) so thin edges fail honestly.
8. Model OUR execution at OUR fees.
9. Independence units (resolved fill / window / clustered).
10. Watch the staleness trap (stale prints manufacture latency edge).
11. Size memory before launching over the big store; guard long jobs with `ulimit -v`.

### Operational lessons (paid for in time)
- VPS heavy compute via `ssh -i ~/.ssh/id_vps_n2`, `nohup … & disown`, watched with
  Monitor `until <terminal-state>` loops. **`pgrep -f "[p]attern"`** to avoid the ssh shell
  matching its own command line (cost an hour misdiagnosed as a "disk crisis").
- Two OOM events on the 15GB VPS from fair-value Stage-1 (50M-row Python lists, then cached
  arrays + fetchall) → **one `gcloud compute reset`** (snapshot `vps-n2-pre-reset-20260613`
  taken first; all data + services recovered). Fix: streaming, chunked cursor→numpy, scipy
  vectorized, `ulimit -v` guard. Logged as lesson #11.

---

## 2. THE OWNER CHALLENGE — re-examining "no signal" (`probe/FRESHMODEL_FINDINGS.md`)

Owner pushed back: the analysis page shows models at 54-63% live accuracy — how is that "no
signal"? This forced the per-model / fresh-window / sibling / consensus / underdog / maker /
regime axes the sweep had skipped. ~14 analysis scripts (`probe/freshmodel/*.py`).

### The null results (the 54-63% explained)
- **analyze1-A decay:** flat 49-52% accuracy at every model age. No fresh-model edge.
- **analyze1-B sibling:** retrains of the same cell scatter ~52%, no persistence.
- **analyze2-D null:** fleet true mean ~50.3% — a whisper, far below the 53.5% fee breakeven.
- **analyze2-E consensus:** flat ~50.3% even when 100% of the fleet agrees (n=10k boundaries).
- **analyze2-F forward-OOS:** week-1 winners (≥53%) revert to 49.85% the next week. THE refutation
  of "trade the fresh winners."
- **analyze4 deployment-sim:** selecting cells profitable in H1 → −0.0149 OOS, WORSE than the
  −0.0098 all-cells baseline. Track-record selection is counterproductive.
- Conclusion: the 54-63% is right-tail-of-rank (170 models × 3 windows) + recency; it doesn't
  persist, aggregate, or beat fees.

### The signal that survived (the owner was right)
Conditioning revealed what pooling hid:
- **everything.py:** UNDERDOG side of fired predictions is +EV fleet-wide OOS (+0.0062 taker,
  +0.0188 maker-ub on 450k trades). Favorite side loses hard (drowned underdog in the pooled view).
- **deep3 regime split (decisive):** the edge is in DOWN-underdogs and is +EV in ALL regimes
  INCLUDING up-days (+0.0945 on up-days, CI excludes 0) → **ALPHA, not beta.** up-underdog is
  pure beta (positive only when market rises). The asymmetry proves a structural LONG-BIAS.
- **deep5 alignment:** `p_market` is aligned/real (spot cross-check: price deviates from 50¢
  only when spot moves) — NOT a stale-price artifact.
- **deep6-A:** intra-window spot MOMENTUM (early move continues) — model-free, huge n.
- **deep7 (real prices, no Gaussian):** the edge lives in the "contract priced rich while spot
  stayed FLAT" population = structural overpricing unsupported by underlying, fadeable.
- **deep8 liquid band + dollar-weighted:** convexity tail stripped → **+5–8% per-share EV in
  the liquid 40-50¢ band, OOS, n≥1.8k, CIs clear of zero.** Polymarket depth ($14/window)
  caps it at ~$70/day.

### Two of my OWN errors, on the record
- Pooled-aggregate reads initially dismissed the side-split signal (favorite losses masked
  underdog wins). The owner's challenge corrected it.
- **deep6-B used a Gaussian fair price** → Φ over-extremes on fat-tailed crypto → manufactured
  fake reversion EV. Discarded. Exactly the Control-2 trap the sealed fair-value probe was
  built around — walked into anyway, caught, retracted.

### The mechanism (one sentence)
Crypto up/down markets carry a **long-bias**: UP-prices exceed realized UP-rates. When the
contract reprices rich (≥55¢ UP) but spot HASN'T moved, that's structural overpricing; fading
it (buy DOWN, hold to resolution) is +5–13¢ per-share, all regimes, OOS.

---

## 3. THE SURVIVOR — Kalshi fade-rich-contract (`probe/KALSHI_FADE_*`, `CAPACITY_AND_KELLY.md`)

Pre-registered (`KALSHI_FADE_PREREGISTRATION.md`). Rule: **up-price ≥0.55 AND Bybit spot
dev-from-open <5bps → buy DOWN at the live ask, hold to resolution.** Taker.

- **§0 (offline, decisive):** does the edge survive on Kalshi's only window (15m=900s)?
  YES — w900-only per-share EV **+0.0563, boot95 [+0.0115,+0.1009], n=485**, dose-responsive
  to richer up-prices. The most likely Day-0 killer (5m-only) did NOT fire.
- **Original verdict: PARK** (scale-fail) — **RETRACTED.** It rested on two errors:
  (1) arbitrary $25/fill (real live Kalshi mid-band depth measured $344–$3,172/market);
  (2) a units bug ($28/day should have been $154/day).
- **Corrected economics (`CAPACITY_AND_KELLY.md`):** EV = **+16.3% per $ staked**, ≤15-min
  resolution. Kelly f*≈13.7% (half 6.8%). Positions barely overlap (0.39 avg concurrent) →
  near-Kelly valid. Compounds to a liquidity ceiling, then flat at fires×fill×16.3%:
  ```
  fill $100/fire → $618/day (stalls ~$1.5k bankroll) | $300 → $1,853/day | $500 → $3,089/day
  ```
  At measured Kalshi depth this CLEARS the $300/day bar with room. **PARK → BUILD CANDIDATE.**
- **The one offline-unverifiable gate:** the edge was measured on POLYMARKET prices. Kalshi
  is a different, possibly-better-calibrated book — the +16% could shrink. We have no logged
  Kalshi price history, so this can only be settled with live Kalshi data.

### Action taken: live data collection started
`probe/kalshi_fade/logger.py` — daemon on the VPS (`/data/kalshi_fade.db`), every 10s logs
all 4 symbols' Kalshi 15-min raw orderbook + Coinbase spot + per-window open/close + dev-from-
open. Raw-capture (no parse assumption). Bybit REST is geo-blocked from GCP → Coinbase proxy
(sound for the intra-window dev-from-open filter). Restart-wrapped. Accumulates ~220 fires
over the remaining ~6 server-days — a directional first read on the Kalshi-price gate.

---

## 4. CURRENT STATE (2026-06-25)
- Six-mechanism sweep: SEALED (kill).
- Maker seat: SEALED (PARK, volume wall).
- Fresh-model re-examination: COMPLETE — found the real edge; documented my two errors.
- Kalshi fade: **BUILD CANDIDATE**, gated on live Kalshi-price validation.
- **Live:** Kalshi-fade logger daemon collecting; v3 services (paper-trader/dashboard/ws-feed)
  running; v3 fleet in zero-touch shadow (instrumentation only).
- **Server runway:** ~6 days. The live Kalshi shadow won't reach n≥500 here → registered path
  is collect-now, re-derive + live-shadow on a fresh server.

## 5. THE HONEST BOTTOM LINE
The owner's two challenges turned a disciplined, six-way "no signal" into a **real, named,
economically-viable edge candidate**: a structural long-bias mispricing in crypto up/down
markets, fadeable for +5–13¢/share, +16%/stake, Kelly-compoundable to $600–3,000/day at
measured Kalshi depth — **contingent on one thing we are now collecting data to learn:
whether the edge that is real on Polymarket is also real on Kalshi.** That single question is
the whole game.

## 6. ARTIFACT INDEX
- Sweep: `probe/PREREGISTRATION.md`, `probe/VERDICT.md`, `probe/PROBE_SWEEP_VERDICT.md`,
  `probe/track_a/*.py`, `probe/track_b/*.py`, `probe/inversion/*.py`, `probe/fairvalue/*.py`.
- Maker: `probe/KALSHI_MAKER_{PREREGISTRATION,S0_FINDINGS,VERDICT}.md`.
- Fresh-model: `probe/FRESHMODEL_FINDINGS.md`, `probe/freshmodel/{analyze,analyze2,analyze3,
  analyze4,drill,everything,verify,deep1..deep8}.py`.
- Kalshi fade: `probe/KALSHI_FADE_{PREREGISTRATION,VERDICT}.md`, `probe/CAPACITY_AND_KELLY.md`,
  `probe/kalshi_fade/{s0_w900,logger}.py`.
- Branches: `probe/10day-signal-hunt`, `probe/kalshi-maker`, `probe/inversion`,
  `probe/fairvalue`, `probe/freshmodel`, `probe/kalshi-fade` (current HEAD).
- Data on VPS: `/data/probe_track_a.db` (53.4M Polymarket trades), `/data/probe_ofi/`
  (408-day OFI features), `/data/kalshi_fade.db` (live, accumulating), `/data/v3.db`
  (predictions + features_json). Snapshot `vps-n2-pre-reset-20260613`.
