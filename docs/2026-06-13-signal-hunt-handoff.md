# DirectionPredictionSystem — Signal Hunt & Alignment Forensics Handoff

**Date:** 2026-06-13, last updated 2026-06-14. **Author:** working session (Claude + owner).
**Scope:** one long session covering (Phase 1) feature-contract alignment forensics, then
(Phase 2) a pre-registered 10-day signal-hunt probe and its offshoot mini-probes.

**STATUS BANNER (2026-06-14): SWEEP COMPLETE — SIX mechanisms tested, SIX kills.** Price-state
(OBI), price-flow (OFI), copy-the-winners, Kalshi maker seat, fair-value deviation, intra-window
inversion — all KILLed on sound, now-trusted instruments. Master verdict:
`probe/PROBE_SWEEP_VERDICT.md`. In 5-30min crypto binaries the price is efficient against
everything a solo operator computes from public data, and the 0.07·p(1−p) fee confiscates
whatever visible skill persists. **System → zero-touch shadow; owner reallocates to Branch B;
salvage = methodology + datasets.** Remaining: pull the two compressed probe DBs to cold storage
before server end (~2026-06-30).

---

## 0. THE ULTIMATE GOAL

Turn model predictions into profitable trades on short-horizon crypto up/down prediction
markets (Polymarket, later Kalshi) across BTC/ETH/SOL/XRP at 5/15/30-min windows. A LightGBM
fleet predicts direction; a gate turns confident predictions into trades. The system ran ~a
month and was **anti-predictive in aggregate** — fleet win rate 46-49%, consistently below
coin-flip across ~1M resolved predictions. The session's job: find and fix the corruption,
prove the fix on logged history, and only then resume trading decisions — and when that
failed, run a disciplined sweep of what else might carry edge before deciding the system's
fate.

---

## 1. PHASE 1 — FEATURE-CONTRACT ALIGNMENT FORENSICS

### Starting hypothesis (inherited, treated as known)
Train/serve feature-contract mismatch: models trained per-symbol on
`FEATURE_COLS_PER_SYMBOL` (32 cols, raw mid_price+spread, no symbol_cat); serving allegedly
used hardcoded lists matching neither. LightGBM reads a bare NumPy array by position →
wrong order = confident silent-wrong predictions. Leading suspect for systematic sub-50.

### What we actually found (the hypothesis was largely already fixed)
- **The predict-time alignment assertion was tautological** at HEAD: it hashed the sidecar
  list against itself. Rebuilt to compare *served order* (sidecar-resolved) vs *canonical
  training contract* (`feature_contract.FEATURE_COLS_PER_SYMBOL`, the module `run_training.py`
  imports) — two genuinely independent sources, refuses on dim+set+order mismatch, plus a
  columns-exist guard (no silent zero-fill). Commit `1648931`. Proven with a column-swap test
  that refuses (`scripts/test_vps_assertion.py`).
- **Production was ALREADY aligned.** VPS audit: 172/172 active model sidecars exactly equal
  the canonical 32-col contract, order included. Zero refusals across 42k+ predictions since
  restart was *genuine*, not a hollow instrument.
- **Forensic receipts (the decisive turn):** serving built vectors from `t.feature_names`
  (sidecar order) since the v3 birth commit `a4a1cf9` — the hardcoded 33-col list was a
  *fallback only for missing sidecars*. Of 1.28M logged predictions: 81% CANONICAL32 (correct
  contract), 19% FALLBACK33 (retired April fleet, served before sidecars existed). **The
  pipeline was clean end-to-end.** Train/serve mismatch was NOT the cause of sub-50.

### The four-check label/value audit (ruling out the next suspects)
1. **Flip test:** invert all predictions → 51.4% (directional-only 49.6%). Structured
   anti-correlation, concentrated in the highest-confidence band ([0.60+) = 48.4%, ~16σ).
   More-confident = more-wrong. Fingerprint of a distorted dominant feature, not noise.
2. **Label event vs scored event:** ruled out — all 21 horizon×window cells sub-50 including
   matched (h300@300 etc.).
3. **j-pointer / 4. units:** both sound (`build_targets` monotonic sweep correct, cts=ms).
4. **Scorer hand-verified 100.000%** faithful on 1,014,799 rows.

### Route-1/Route-3 value-parity audit (the real answer)
- **Route 1 (code diff):** live `live_features.py` vs offline `build_features*.py` —
  MLOFI/OFI sign, MAD norm, 1-min aggregation all MATCH. Minor mismatches: rolling-std ddof
  (1 vs 0), `spread_5m_pct` (min-max interp vs true percentile). Immaterial.
- **Route 3 (decisive):** ran offline-pipeline features through the same boosters on the
  live-era window, scored vs live outcomes. 24-model sample, n=129,399: **live 49.69% vs
  offline 49.44%, 90% direction agreement.** Offline features reproduce live results. **Value
  parity is NOT the bug.** The anti-predictiveness is real model behavior — the OBI feature
  set carries ~zero signal at these horizons. (MLOFI is mislabeled OBI — a static
  mean-reverting snapshot, not flow.)

### Scoring-semantics bug found + fixed
Flats (close==open) were scored `prediction_correct=0` for BOTH directions, dragging fleet
directional win rate ~1pp and feeding no-contest outcomes into calibration. Fixed (commits
`f0d8dfd`, `23a01e9`):
- **Model lens** (`predictions`): flat = no-contest, `prediction_correct=NULL`, excluded.
- **Trading lens** (`paper_trades`): **flat resolves UP** per verbatim Polymarket contract
  ("greater than or equal"). First shipped backwards (flat=DOWN), corrected same day.
- Idempotent backfill: 25,064 flat predictions → NULL, flat-DOWN trades → loss, flat-UP → win.

### Permanent observability added (Step 4)
`predictions.features_json` + `served_contract_json` — exact vector fed to the booster + served
order, on the canonical row of each prediction set. Idempotent migration, legacy NULL.
Commit `4d241c2`. Verified live (131,899 rows logged). This is what makes faithful replay
possible (an earlier replay matched production only 4.2% using parquet stand-ins).

### Governance landmine surfaced
**Training holdout AUC has zero correlation with live performance** (corr 0.072 across 172
models). Gate-passed (18) and gate-failed (154) models both live at ~49.5%. The retrain
evaluation harness is underpowered (n_contract 244-1442) and/or leaky — gate decisions are
coin flips. 154 gate-FAILED models were deployed and trading anyway. Recommendation:
replace the offline gate with live-shadow evaluation (gate on resolved live N), infra exists.

### Phase-1 bottom line
Serving infrastructure proven clean; observability permanent; scoring fixed; evaluation
harness diagnosed as uninformative. The feature set (OBI state) is the problem — and that set
back to its hypothesis. This set up Phase 2: test the one untested feature family (true flow)
and find out whether *anyone* wins these markets.

---

## 2. PHASE 2 — THE 10-DAY SIGNAL HUNT (pre-registered probe)

Branch `probe/10day-signal-hunt`. Two tracks, hard gates, all thresholds frozen Day 0
(`probe/PREREGISTRATION.md`) before any result.

### Day-0 verification (caught three silent corruptions before they propagated)
- Fee formula: `fee = shares × 0.07 × p(1−p)`, taker-only, 20% maker rebate. Both 5m AND 15m
  carry fees (live market objects; press said 15m-only).
- **Flat resolves UP** (verbatim contract) — yesterday's fix had it backwards; corrected.
- **30m markets do not exist** — universe is 8 (BTC/ETH/SOL/XRP × 5m/15m), not 12. The old
  fleet's least-bad horizon (1800s) has no tradeable market → probe tests only the 2 noisiest.
- Resolution = Chainlink data streams (not spot).
- Resolved markets vanish from default Gamma queries ~10min after close → need `closed=true`.

### Track A — wallet forensics (does anyone win, is it copyable?)
- **Harvest:** 64,504 markets, **53.4M trade prints**, ~6 weeks, via `probe/track_a/harvest.py`
  (gamma 20-slug batch discovery + data-api pagination, 8 concurrent workers under a shared
  5 rps limiter, resumable). data-api offset cap (offset+limit>5000 → 400) handled as
  clip-with-flag.
- **A1 sanity (amended):** outcome join 100%; Bybit-vs-Chainlink mapping agreement 99.73% on
  |move|≥20bps (the 98% blanket threshold was misspecified — disagreement concentrates at
  near-flat windows = inter-feed bps noise, not wrong mapping; **amendment proposed before
  any downstream result, made the gate harder — the reference standard for amendments**).
- **A2 persistence (the experiment):** W1/W2 split, eligibility ≥200 W1/≥50 W2, binding clip
  rule (>15% W1 clip-exposure excluded). **Result: gross skill PERSISTS (Spearman rho 0.31
  net / 0.22 gross, p≈10⁻⁷⁹) but fees confiscate it.** Top decile W2: gross +$565,720, fees
  −$604,352, **net −$38,632.** Cohort CIs overlap control → **Gate A prong-1 FAILS.**
- **A3 fingerprint:** top-20 winners show **no latency signature at 1s resolution**, sit
  mid-odds/mid-window (quoter-like). Stated at that confidence — NOT "are makers" (1s
  timestamps can't distinguish fast taker from slow quoter).

### Track B — true-OFI model (the one untested feature hypothesis)
- **Feature build:** Cont-2014 OFI from 100ms L2 deltas (`probe/track_b/build_ofi_features.py`),
  levels 1-5, lookbacks 10/30/60/300s + normalized + 3 arithmetic features
  (dist-from-open, EWMA vol, time-remaining). Smoke: contemporaneous corr 0.726/0.734 vs the
  Cont-2014 fact — implementation correct. 408 days × 4 symbols.
- **Label:** window close ≥ open (flat=UP, contract-exact). Datasets ~880k rows/symbol/duration.
- **Grid:** 16 configs, 2100s-embargoed walk-forward, selection inside train span only
  (`window_start < 2026-05-24` hard filter — single-touch test span unreadable).
- **B4 single-touch (frozen pre-touch, staleness rule binding):** scored vs market
  outcomePrices; primary slice = market print ≤15s old. **Result: FAIL.** Fresh slice
  n=27,475, EV +$0.0035 CI crosses zero, accuracy 50.84% (Wilson LB 50.24%, need ≥52%).
  Latency mirage explicit: only the *stale* 15-60s print bucket shows CI-positive "EV";
  pure window-open OFI slice CI-**fully-negative**. **True flow carries no tradeable edge.**

### Phase-2 verdict (`probe/VERDICT.md`)
Both gates FAIL → decision-table KILL row. **Principal finding:** skill persists, the
0.07·p(1−p) fee confiscates 100%+ of it. Not "no signal" — "signal we cannot capture as a
taker." The January fee change priced out the visible taker alpha pool. (Sensitivity:
exclude-clipped-markets mode shows net +$174k in thin markets — flagged for next probe, NOT
a gate trigger.)

---

## 3. THE SEALED VERDICTS (one finding, five mechanisms; sixth running)

1. **Taker-copy** (`probe/VERDICT.md`) — gross skill real (Spearman rho 0.31, p≈10⁻⁷⁹),
   0.07·p(1−p) fee confiscates 100%+ of it. Net-negative. Gate A prong-1 FAIL.
2. **True-OFI model** (`probe/VERDICT.md`) — no edge vs market price; fresh-slice EV CI
   crosses zero (50.84%, Wilson LB 50.24%). Gate B FAIL.
3. **Kalshi maker seat** (`probe/kalshi-maker`, `KALSHI_MAKER_VERDICT.md`) — KILLed at §0 on
   the **volume wall**: only 4 markets (BTC/ETH/SOL/XRP 15-min up/down; no 5m, no up/down
   hourly), 384 windows/day, deep maker queues (53-852 contracts). $300/day anchored
   scale-up bar structurally unreachable before cancel-on-move feasibility even tested.
4. **Fair-value deviation** (`probe/fairvalue`, `FAIRVALUE_VERDICT.md`) — Stage-1 KILL on
   n=50.8M prints. 92% of prints deviate from arithmetic fair value by more than cost — a
   **screaming false positive** strangled by four controls: deviation is symmetric (signed
   bias ≈0), uniform across the window (~10%/decile), and partly our own model error
   (mean|dev_emp| 0.114 > gaussian 0.084). Killed by the concentration control (11% vs ≥60%
   needed). Best demonstration in the arc of why the controls were worth the care.
5. **Intra-window inversion** (`probe/inversion`, `INVERSION_VERDICT.md`) — Stage 1 PASS
   (oscillation real, 85% round-trips complete) → Stage 2 KILL: incomplete legs = adverse
   selection; EV −$0.064/window, bootstrap LB −$0.065, n=63k, on the most-favorable early
   slice + best δ. The oscillation pays pennies on reversion, costs dollars on continuation.

**The consistent lesson:** the fee + microstructure of these markets are engineered to leave
no room for an outside, non-latency, human-timescale strategy at meaningful size. The house
priced out exactly the seats an outsider can reach. SIX structurally different ways in, six
hits on the same wall — the convergence IS the finding. Master: `probe/PROBE_SWEEP_VERDICT.md`.

---

## 4. THE LAST GATE — inversion Stage 2 (SEALED KILL)

### Inversion (`probe/inversion`, `INVERSION_VERDICT.md`)
Hypothesis: price oscillates around window-open, flips dampen late.
- **Stage 1 PASSED** (`stage1_flips.py`): flip histogram 4/4 symbols + pooled (5.18
  flips/window in first-66%, dampening 0.37 ≤ 0.5). Physics real.
- **Stage 2 KILL** (`stage2_roundtrip.py`): round-trip backtest on real Polymarket prints,
  through-level fill realism, maker fee + 30/69bps adverse haircut, completed-vs-incomplete-leg
  cost. Pointed at the early-window slice (deciles 0-1, the fair-value flicker). Result:
  EV −$0.064/window, bootstrap LB −$0.065, n=63,104, ALL δ×haircut cells negative, on the
  most-favorable ground. 78-85% of round-trips complete (oscillation real) but the 15-22%
  incomplete legs carry to resolution as adverse-selected directional losses that swamp the
  pennies of captured spread. Kill earned, not by neglect.

### Fair-value deviation — SEALED KILL (see §3.4 and `FAIRVALUE_VERDICT.md`)
GATE 0 resolved (Kalshi book absent → ran on 53.4M independent Polymarket prints,
intra-window coverage confirmed 606/window). Five controls all reported verbatim;
concentration control killed it. The seat is closed; its one residual flicker (early-decile
bias) was handed to inversion Stage 2 — which also died.

---

## 5. METHODOLOGY / WORKFLOW / DISCIPLINE (the part worth keeping)

These emerged across the session and became binding:

1. **Every green light is interrogated for what it INDEPENDENTLY compares.** Two instruments
   in this project logged "all clear" while comparing something to itself (the
   feature_names_hash; the tautological assertion). A passing check proves nothing until shown
   capable of failing on real bad input.
2. **Evidence verbatim, conclusions second.** Every gate decision ships with the command and
   its output. "Verified successfully" is not a result.
3. **Pre-registration is the contract.** Thresholds frozen before any result. **Gates are
   amendable ONLY before downstream results exist AND only in the harder direction.** The
   Gate-3 and B4-staleness amendments are the reference standard; the Day-9 temptation to
   "restate" a gate after seeing results has to meet that bar (it can't).
4. **Gates are findings, not failures.** A failed gate / STOP that fires is the system
   working. Report it plainly; never tune until green.
5. **Controls always.** No cohort without its random-control twin; no backtest without a
   zero-delta sanity case; no calibration claim without the base rate.
6. **The test set is touched once.** Selection inside training spans, single-touch eval.
7. **Anchored bars, not arbitrary.** Scale-up criteria tied to a defensible number ($300/day
   = attention opportunity cost off Branch B) so they can't be lowered when a marginal-positive
   appears. The scale-up bar's whole job is to make thin-but-positive FAIL honestly — a thin
   edge that needs babysitting consumes the time it was meant to free.
8. **Model OUR execution, never theirs.** Copy/maker backtests price our lagged/adverse fills
   at our fees.
9. **Independence units.** One resolved fill / one window / clustered — fires sharing an
   outcome are not independent observations.
10. **Watch the staleness trap.** A 120s-old print is a price nobody offers — scoring against
    it manufactures backtest-only latency edge. Primary populations use fresh data; stale
    buckets are reported as the latency-mirage diagnostic.
11. **Size memory before launching over the big store; guard with `ulimit -v`.** The
    fair-value Stage 1 OOM'd the 15GB VPS TWICE (v1: ~50M prints × 12 fields in Python lists
    ≈18GB; v2: cached all 4 symbols' 1s arrays + fetchall'd 12M-row cells). Both wedged sshd
    so hard the OOM-killer couldn't recover it — required a `gcloud compute reset` (snapshot
    taken first: `vps-n2-pre-reset-20260613`; all data + services recovered clean). The fix
    (v3): per-symbol processing freed between symbols, chunked cursor `fetchmany`→numpy (never
    fetchall), vectorized `scipy.ndtr` + `np.searchsorted`, **launched under `ulimit -v` so a
    runaway dies with MemoryError instead of wedging the box.** Ran in minutes at ~3GB peak.

### Operational pattern
- Heavy compute runs on the VPS (`ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48`), launched
  `nohup … & disown`, watched via Monitor with `until <terminal-state> ; do sleep N; done`
  loops (one notification per completion). **Use `pgrep -f "[p]attern"`** to avoid the ssh
  shell matching its own command line (cost an hour misdiagnosed as a "disk crisis").
- Every script: syntax-check local → scp to VPS → smoke on one unit → launch full → monitor.
- Commits are frequent, scoped, evidence-bearing; each probe artifact (registration, script,
  findings, verdict) is its own commit on its own branch.

---

## 6. CURRENT STATE (as of 2026-06-14 ~06:00 UTC)

- **Phase 1:** complete, deployed, sealed. Fleet → zero-touch shadow; predictions +
  feature_json logging continue as instrumentation. Gate-failed-models / uninformative-harness
  finding documented; live-shadow-gate is the recommended fix (not yet built).
- **Phase 2 probe (taker-copy + OFI):** sealed (`VERDICT.md`). Big artifacts compressed on VPS
  for export: `probe_track_a.db.gz` 8.4G, `probe_ofi.tar.gz` 5.2G, `feature_log.parquet` 4.3M
  (pulled local). Export non-urgent (server ~17 days runway from 2026-06-13).
- **Kalshi maker:** sealed KILL (`KALSHI_MAKER_VERDICT.md`).
- **Fair-value deviation:** sealed KILL (`FAIRVALUE_VERDICT.md`), Stage-1, n=50.8M prints,
  concentration control. (Cost: 2× OOM + 1 VPS reset; lesson #11.)
- **Inversion:** sealed KILL (`INVERSION_VERDICT.md`), Stage 2, EV −$0.064/window on n=63k.
- **Master:** `probe/PROBE_SWEEP_VERDICT.md` — six mechanisms, six kills. SWEEP COMPLETE.
- **VPS:** reset-recovered, healthy, all 3 services active, snapshot rollback exists.

### IMMEDIATE NEXT ACTION (sweep done — only cleanup remains)
1. **Pull the two compressed probe DBs to cold storage** before server end (~2026-06-30):
   `/data/probe_exports/probe_track_a.db.gz` (8.4G), `probe_ofi.tar.gz` (5.2G). Non-urgent
   but the one open task. (feature_log.parquet already local; code/docs in git.)
2. Decide on snapshot `vps-n2-pre-reset-20260613` — retain or delete.
3. (Optional, only if ever revisiting Kalshi) stand up Kalshi book logging to convert the
   maker/fair-value Polymarket-proxy bounds into venue-measured numbers.
4. System stays zero-touch shadow; owner's active time → Branch B.

### Decision-table endgame — REACHED
Six mechanisms KILLed: price-state, price-flow, copy, maker, fair-value-deviation, inversion.
No capturable edge for a solo operator from public data; fees confiscate persistent skill.
**Outcome: full zero-touch shadow; owner reallocates to Branch B; methodology + datasets are
the salvage.** A real, defensible, evidence-backed answer to the question the project asked.

---

## 7. KEY ASSETS / ACCESS

- **VPS:** `ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48` (GCP vps-n2). Canonical ops doc:
  `vps_deployment_guide.md`. Services: v3-paper-trader :8080, v3-dashboard :8081, v3-ws-feed.
  Deploy = rsync/scp local→VPS, never git pull. **Deployed code can be ahead of git HEAD.**
- **Data:** `/data/v3.db` (predictions, paper_trades, registry, +features_json span);
  `/data/parquet/orderbook/` (408 days × 4 symbols, 100ms L2 deltas, 72G);
  `/data/probe_track_a.db` (53.4M Polymarket trades + 64,504 markets);
  `/data/probe_ofi/` (408-day Cont-2014 OFI features + datasets);
  `/data/probe_exports/` (compressed for cold storage).
- **Branches:** `probe/10day-signal-hunt` (sealed), `probe/kalshi-maker` (sealed KILL),
  `probe/fairvalue` (sealed KILL), `probe/inversion` (Stage 1 done, Stage 2 running — current
  HEAD). Verdicts: `probe/VERDICT.md`, `KALSHI_MAKER_VERDICT.md`, `FAIRVALUE_VERDICT.md`;
  registrations: `PREREGISTRATION.md`, `KALSHI_MAKER_PREREGISTRATION.md`,
  `INVERSION_PREREGISTRATION.md`, `FAIRVALUE_PREREGISTRATION.md`.
- **Probe scripts:** `probe/track_a/{harvest,a1_sanity,a2_persistence}.py`,
  `probe/track_b/{build_ofi_features,build_dataset,train,b4_eval}.py`,
  `probe/inversion/{stage1_flips,stage2_roundtrip}.py`,
  `probe/fairvalue/stage1_deviation.py` (bounded-memory v3).

## 8. KEY NUMBERS (reference)
- Fleet: ~49.6% directional win rate; high-conf band 48.4% (anti-predictive).
- Fee: Polymarket/Kalshi taker 0.07·p(1−p) (~3.5% at mid); Kalshi maker 0.0175·p(1−p) (~44bps).
- Persistence: Spearman rho 0.31 net (p≈10⁻⁷⁹); top decile gross +$566k, net −$39k (fees win).
- Adverse-selection bound: 30bps (primary) / 69bps (thin) — a floor, from realized winner edge.
- Maker break-even: spread capture must exceed ~74-113bps; measured Kalshi spread 0.5-2.0¢.
- Anchored scale-up bar: net $300/day at bounded attention.
- Intra-window print coverage: 15m 606/window (all deciles 825k-1.36M), 5m 848/window.
- Fair-value Stage 1: n=50.8M prints, 92% supra-cost deviation but symmetric/uniform/own-model-
  error → KILL by concentration (11% vs ≥60%). Kalshi universe: 4 markets ×15-min, spread
  0.5-2.0¢, queues 53-852 contracts.
