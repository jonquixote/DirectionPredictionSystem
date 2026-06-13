# FAIR-VALUE DEVIATION PROBE — PRE-REGISTRATION

**Branch:** `probe/fairvalue`. **Drafted:** 2026-06-13. **Status:** AWAITING SIGN-OFF —
no Stage-1 run until committed AND owner-approved.
**Cost:** offline. 408-day L2 spot store + Polymarket real trade prints. Zero capital.

## GATE 0 — RESOLVED (2026-06-13, before any Stage-1 result)
- **Kalshi order-book history does NOT exist** (`/data/kalshi_orders.jsonl` = 465 of our own
  orders only; no book snapshots; no DB tables). Cannot test on Kalshi directly.
- **Polymarket real contract prices DO exist and are independent of Bybit spot:**
  `probe_track_a.db` = 53.4M timestamped trade prints (price + Up/Down token = real
  contract-implied P, set by independent traders). NOT spot-derived → no spot-vs-spot
  circularity. P_market(Polymarket print) vs P_fair(Bybit arithmetic) = two independent
  inputs.
- **Intra-window coverage CONFIRMED (the gate on whether this is the live test):** prints
  span the whole window life — 15m: 606 prints/window, all deciles 825k-1.36M; 5m:
  848/window. Stage 1 is fed the **trade prints** (intra-window), NOT the window-open-only
  `predictions.p_market` (which could test only the known-efficient open). This IS the live
  test of mid-window deviation.
- **Decision:** run on Polymarket prints, proxy-flagged for venue transfer (Polymarket fees/
  feed/mix ≠ Kalshi). Start Kalshi book logging in parallel as the venue-direct follow-up —
  not a blocker.

**The untested seat:** all three sealed probes modeled *price* (OBI state, OFI flow,
oscillation). This tests whether the *contract price* deviates from **model-free arithmetic
fair value** by more than cost, and whether the deviation is predictable. Not "can we predict
the move" — "does the market misprice the move it can already see."

---

## FAIR VALUE — model-free, the market's own arithmetic

At any instant in a window:
```
P_fair(up) = Φ( distance_from_open_bps / (sigma * sqrt(time_remaining_frac)) )
```
- `distance_from_open_bps` = (mid − window_open) / window_open × 1e4 (signed).
- `sigma` = EWMA realized vol of 1s log-returns from the 408-day spot store, scaled to the
  window horizon. NO learned features, NO LightGBM — pure arithmetic the market itself can do.
- `time_remaining_frac` = fraction of window left; as it → 0, Φ saturates to 0/1 (a small
  lead becomes near-certain). This is the standard barrier/digital-option intuition, not a fit.
- flat=UP (`>=`) parity with the contract at expiry.

The hypothesis under test: does `P_market` track `P_fair` to within cost, or are there
predictable, structural (non-latency) deviations a human-timescale strategy could reach?

---

## STAGE 1 — deviation distribution (decisive, offline)

For every instant with a contract price:
- **Primary source:** logged Kalshi book midpoint, if logged history is sufficient.
- **Proxy fallback (flagged):** if logged Kalshi history is thin, reconstruct contract-implied
  P from the spot-anchored binary (the same open-anchored mapping the markets resolve on) and
  **flag every proxy-derived conclusion as proxy** — a proxy deviation measures our-model vs
  arithmetic, not market vs arithmetic, and cannot by itself prove market mispricing.
- Measure `dev = P_market − P_fair`. Distribution of `|dev|`, bucketed by:
  (a) window-elapsed decile, (b) distance-from-open bucket.
- Report: fraction of instants where `|dev|` exceeds the maker-fee threshold
  `0.0175·p(1−p)`; whether excess-deviation concentrates in an identifiable, predictable
  bucket (post-spot-move, near-open, dead-zone); and the SIGN structure (is `dev` symmetric
  = noise, or biased = structural mispricing).

**STRUCTURE-EXISTS threshold (registered before running) — all conjuncts:**
- `|dev| > fee + half_spread` (half_spread = 0.5¢ proxy) in **≥ 20%** of instants, evaluated
  under BOTH fee schedules separately (Control 5): Polymarket 0.07·p(1−p) AND Kalshi
  0.0175·p(1−p). A deviation supports the Kalshi thesis only if it clears Kalshi cost.
- excess concentrates: **≥ 60%** of excess-deviation instants in a SINGLE ex-ante-predictable
  bucket (one elapsed-decile or one distance band).
- directionally biased within that bucket (mean signed dev ≥ 0.5 · its own std).
- **Control 1 (σ):** survives under all 3 σ estimators incl. same-window oracle.
- **Control 2 (empirical):** `|P_market − P_empirical|` (not just Gaussian Φ) supra-cost,
  where P_empirical = realized P(up) binned by (dist-from-open, time-remaining) over 408 days.
- **Control 3 (OOS):** the concentrated bucket found on the early span holds on the held-out
  later span (time split registered before running: train = first 70% of dates, test = last 30%).
- **Control 4 (capturable):** at deviation instants, some is capturable not queue-blocked —
  proxy (no book history): deviation persists ≥1 subsequent print / converges within-window
  (a fill on the fade side becomes available), reported as proxy.

All required. Each control reports its own verbatim breakdown; lead with whichever kills it. If deviations are random/symmetric/sub-cost, or excess is smeared with no
predictable concentration → **Stage-1 KILL: market efficiently priced vs the arithmetic,
venue-question closed.** A biased, concentrated, supra-cost deviation is the only thing that
survives to Stage 2.

Rationale: random supra-cost deviation you cannot time is unharvestable (you'd pay the
spread crossing in and out for nothing). Harvestable mispricing must be predictable (you know
WHEN to look) and biased (you know WHICH WAY) and bigger than the cost of acting.

---

## STAGE 2 — deviation backtest (only if Stage 1 clears)

Enter when `|P_market − P_fair| > fee + half_spread`, in the predicted direction (fade the
deviation toward fair value); hold to resolution OR to convergence (`|dev| < fee`), whichever
first. Cost: maker fee 0.0175·p(1−p) per leg + adverse-selection haircut **30bps / 69bps**
(sealed-probe bound, both as sensitivity) + through-level fill realism.

**STAGE-2 KILL:** per-instant-attempted net EV bootstrap 95% **lower bound ≤ 0** on
n ≥ **2,000** independent instants (one per window-bucket to keep independence), at 30bps
haircut → KILL.

---

## SCALE-UP CRITERION (same anchored bar)
BOTH: net EV/attempt bootstrap LB ≥ **$0.010** AND sustainable **net ≥ $300/day** at bounded
attention (opportunity cost of attention off Branch B + running cost). Volume ceiling: same 4
Kalshi 15-min markets, 384 windows/day — flagged as the probable absolute-dollar wall the
prior probes hit. SCALE-UP-fail → PARK zero-touch.

---

## HONEST PRIOR (registered)
If the crowd priced fair value perfectly, deviation is sub-cost everywhere. Latency bots were
explicitly taxed because they harvested spot-vs-contract lag — so the FAST version of this
deviation is already gone, priced out by the fee change. Stage 1 asks whether any SLOWER,
structural deviation survives that a human-timescale strategy could reach. Likely small,
likely also walled by the same volume ceiling. One histogram decides whether it exists at all.

## OUTCOMES (binding)
- Stage-1 KILL (efficient/sub-cost/unpredictable) → verdict, done. Cheapest exit.
- Stage-1 pass, Stage-2 KILL → "deviation real, not harvestable post-cost."
- Stage-2 survives, SCALE-UP fail → PARK zero-touch.
- Stage-2 survives, SCALE-UP clears → candidate; design separately.

## GUARDRAILS (inherited)
Evidence verbatim. Gates fail. Thresholds frozen before results, amendable only before and
only harder. Proxy contract-price clearly flagged everywhere it's used. Adverse-selection
haircut is a sealed-probe bound, not a Kalshi measurement — Stage-2 inherits the caveat.
Separate from inversion + maker probes: no shared gates.

**Blanks filled:** Stage-1 structure = |dev|>fee+0.5¢ in ≥20% instants AND ≥60% concentrated
in one predictable bucket AND directional bias ≥0.5·std. Stage-2 KILL = net EV/attempt LB≤0
on n≥2000 at 30bps. SCALE-UP = LB≥$0.010 AND net≥$300/day. Offline, zero marginal cost.
