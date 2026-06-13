# FAIR-VALUE DEVIATION PROBE — PRE-REGISTRATION

**Branch:** `probe/fairvalue`. **Drafted:** 2026-06-13. **Status:** AWAITING SIGN-OFF —
no Stage-1 run until committed AND owner-approved.
**Cost:** offline. 408-day L2 spot store + any logged Kalshi book history. Zero capital.

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

**STRUCTURE-EXISTS threshold (registered before running):**
- `|dev| > fee + half_spread` (half_spread = 0.5¢ proxy, the §0-measured tight end) in
  **≥ 20%** of instants, AND
- that excess concentrates: **≥ 60%** of the excess-deviation instants fall in a SINGLE
  identifiable, ex-ante-predictable bucket (one elapsed-decile band or one distance band),
  AND
- the deviation is **directionally biased** within that bucket (mean signed dev ≥ half its
  own std — i.e. a real lean, not symmetric chop).

All three required. If deviations are random/symmetric/sub-cost, or excess is smeared with no
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
