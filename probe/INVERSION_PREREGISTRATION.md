# INTRA-WINDOW INVERSION PROBE — PRE-REGISTRATION

**Branch:** `probe/inversion`. **Drafted:** 2026-06-13. **Status:** AWAITING SIGN-OFF —
no Stage-1 run until committed AND owner-approved.
**Cost:** offline only. 408-day L2 spot store. No Kalshi, no quoting, no capital, no server
competition with the maker probe.

**Kept cleanly separate from the maker probe:** this is MEAN-REVERSION around the window-open
price, NOT spread-capture. Different mechanism, different gates, no shared criteria. The two
probes do not contaminate each other's conclusions. (They MIGHT turn out to be one edge in
two outfits — Stage 2 decides that; until then, separate.)

---

## HYPOTHESIS (owner observation)

These up/down markets oscillate around the window-open price: the sign of (price − open)
flips frequently in roughly the first 66-80% of the window and dampens near the end, because
early on a small spot move flips the binary. If the oscillation has exploitable STRUCTURE, a
registered round-trip (rest bid at X below open, sell at Y above) might harvest it regardless
of final direction.

## REGISTERED HONEST PRIOR

Visible oscillation is visible to everyone. Buy-low/sell-high loops are the single
most-attempted strategy in any oscillating market. If round-trips were free, competing loops
would already have compressed the spread that makes them pay. Stage 2's real job is whether
oscillation clears costs AFTER everyone else's loops have arbitraged it — which may be the
same wall the maker probe hits. Prior: more likely PARK/KILL than SCALE. The data decides.

---

## STAGE 1 — flip-rate structure (cheap, decisive, offline)

For each 15-min window in the 408-day store, per symbol (btc/eth/sol/xrp):
- **window open** = first 1s mid at/after window start (mirror Kalshi resolution semantics:
  the level the binary is measured against; flat = UP per contract, `>=`).
- Track `sign(mid − open)` across the window's 1s life.
- **Flip** = sign change between consecutive 1s samples (excluding the zero-crossing dwell at
  exactly open; count a flip when sign goes + → − or − → +).
- Bucket flips by **window-elapsed decile** (0-10%, ..., 90-100%).

**Output:** flip-rate histogram (mean flips per window per decile), per symbol + pooled, with
the count of windows. Plus: terminal-dampening ratio = mean flip-rate(last 2 deciles) /
mean flip-rate(first 6 deciles).

**STRUCTURE-EXISTS threshold (registered before running):** BOTH
1. flip-rate in deciles 1-6 ≥ **2× pooled mean of the most-common single decile** is too
   loose — instead: mean flips/window in the first 66% (deciles 1-7) ≥ **3.0** AND
2. terminal-dampening ratio ≤ **0.5** (last-20% flip-rate at most half the first-66% rate).

Both must hold pooled AND in ≥3 of 4 symbols. If not → **Stage-1 KILL: no exploitable
oscillation structure**, write verdict, stop. (A market that drifts rather than oscillates,
or oscillates uniformly with no terminal dampening, offers no timed round-trip.)

Rationale for the numbers: a round-trip needs the price to leave AND return within the
tradeable window. <3 flips in the first two-thirds means too few there-and-back trips to
harvest; dampening ratio >0.5 means the late window is as choppy as the early — no timing
edge from "enter early, the oscillation will revisit your level."

---

## STAGE 2 — round-trip backtest (only if Stage 1 shows structure)

Registered round-trip rule on spot, then re-priced as a Kalshi contract round-trip:
- Rule: at window start, rest a BUY at open−δ and a SELL at open+δ (in contract-price terms
  via the open-anchored binary mapping), δ registered as the grid {1¢, 2¢, 3¢} of contract
  price. A round-trip completes when both legs fill within the window.
- **Cost stack (all applied):** Kalshi maker fee 0.0175·p(1−p) per leg + an
  adverse-selection haircut drawn from the sealed probe's bound (**30 bps primary / 69 bps
  thin** — run both as sensitivity) + queue-fill realism: a leg fills only if spot traded
  through the level by ≥1 tick (not merely touched), the conservative proxy for queue
  priority on a real book.
- Outcome: net round-trip EV per completed round-trip, AND per window attempted (incomplete
  round-trips carry the cost of the filled leg to resolution — you're left holding a
  directional position, scored at the contract's resolved value).

**STAGE-2 KILL:** net EV bootstrap 95% **lower bound ≤ 0** per window-attempted, on
n ≥ **2,000** windows, at the 30bps haircut → KILL. (Per-window, not per-completed-round-trip:
the incomplete-leg risk is part of the strategy's real cost and must be in the denominator.)

---

## SCALE-UP CRITERION (same anchored bar as the maker probe)

BOTH:
1. net EV/window bootstrap 95% LB ≥ a positive figure clearing costs with room (set at
   **$0.010/window** equivalent, parallel to the maker $0.010/contract), AND
2. sustainable absolute dollars ≥ **net $300/day** at bounded attention — same justification:
   the opportunity cost of pulling active attention off Branch B (~$300-500/part-day) plus
   running cost. Below it → **PARK zero-touch, no active time.**

Volume ceiling (stated now): if this trades the same 4 Kalshi 15-min markets = 384
windows/day, then $300/day needs ~$0.78 net/window — far above the $0.010 floor. Like the
maker probe, the absolute-dollar wall is the likely binding constraint, and the registration
says so before any result.

---

## OUTCOMES (binding)
- Stage-1 KILL (no structure) → verdict, done. Cheapest exit.
- Stage-1 passes, Stage-2 KILL (structure exists but costs eat it) → verdict: "oscillation
  real, not harvestable post-cost" — the registered honest-prior outcome.
- Stage-2 survives KILL, SCALE-UP fails → PARK zero-touch.
- Stage-2 survives, SCALE-UP clears → candidate; design deployment separately.

## GUARDRAILS (inherited)
Evidence verbatim. Gates fail. Thresholds frozen before results, amendable only before
results and only harder. Open price + flat=UP mirror the contract exactly (the label-vs-
scored-event lesson). Adverse-selection haircut from the sealed probe is a bound, not a
Kalshi measurement — Stage 2 conclusions inherit that caveat. Separate from the maker probe:
no shared gates.

**Blanks filled:** Stage-1 structure = ≥3.0 flips/window in first 66% AND dampening ≤0.5,
in ≥3/4 symbols. Stage-2 KILL = net EV/window LB≤0 on n≥2000 at 30bps haircut. SCALE-UP =
LB≥$0.010/window AND net≥$300/day. δ grid {1,2,3}¢. Offline, zero marginal cost.
