# KALSHI FADE-RICH-CONTRACT MINI-PROBE — PRE-REGISTRATION

**Branch:** `probe/kalshi-fade`. **Drafted:** 2026-06-24. **Status:** AWAITING SIGN-OFF —
no quoting/trading until committed AND owner-approved.
**Origin:** `probe/FRESHMODEL_FINDINGS.md` — a real, OOS, alpha-not-beta structural
mispricing (~+5–8% per-share EV) that Polymarket's depth ($14/window) can't pay, but
Kalshi's deeper book might.

**Purpose in one line:** convert the offline-measured per-share fade edge into a
Kalshi-MEASURED net dollar EV at small size, on the one venue with depth.

---

## THE RULE (frozen — no tuning after results)
At a Kalshi crypto up/down window: **IF** the live up-price ≥ **0.55** (down ≤ 0.45)
**AND** simultaneous Bybit spot deviation from window-open < **5 bps** → **buy DOWN (NO)
at the live ask, hold to resolution.** Taker. One position per window. Size = minimum,
scale only if §3 clears.
- Rationale: contract priced rich (≥55¢ UP) while spot hasn't moved = structural
  overpricing unsupported by underlying; fade it. (deep7/deep8, real prices, OOS.)

---

## §0 — PRE-QUOTING VERIFICATION (gates everything; can KILL on Day 0)

1. **Window-restriction re-confirm (offline, decisive, FIRST):** the edge was measured
   pooled on 5m+15m Polymarket. **Kalshi has only 15-min** crypto up/down (maker §0:
   KXBTC15M/KXETH15M/KXSOL15M/KXXRP15M; no 5m). Re-run deep8 fade-rich liquid-band EV on
   **w900 ONLY**. If the edge is 5m-only or insignificant at 900s → **Day-0 KILL** (nothing
   to trade on Kalshi). Frozen threshold: w900-only per-share EV bootstrap LB > 0 on
   n ≥ 300 at up≥0.55/spot-flat.
2. **Kalshi real ask at the down price (~40–45¢):** sample live books. Edge needs the ask
   within the per-share margin. If the down-side ask sits >~3¢ above the fair down price,
   the +5–8% is eaten → KILL or maker-only re-think.
3. **Signal feasibility:** can we read Bybit spot-dev + Kalshi up-price and evaluate the
   rule within the window's tradeable life (≈900−40s dead-zone)? Taker (no cancel race),
   so latency risk is low — but confirm the up≥0.55/spot-flat condition persists long
   enough to place. If it's a sub-second flicker, it's the latency game → KILL.
4. **Fire-rate / capacity:** from the offline data, how many w900 windows/day satisfy the
   rule across BTC/ETH/SOL/XRP? × per-fill $ at small size = the achievable daily $.
   Feeds §3 directly.

---

## §1 — WHAT IS MEASURED (Kalshi-direct, small size)
Per filled DOWN position, booked to resolution:
**realized net EV per fill = (resolution payoff − entry ask) − Kalshi taker fee**
(0.07·p(1−p) at entry). NO Bybit reference for PnL — Kalshi entry + Kalshi resolution.
Independence unit = one resolved fill. Bootstrap 95% CI on per-fill EV.

---

## §2 — KILL CRITERION
On n ≥ **500** independent resolved fills (≤ quoting-window days):
> realized per-fill net-EV bootstrap 95% **lower bound ≤ 0** → **KILL.**
Marginal-positive-LB-crosses-zero = KILL, not "run longer." If 500 fills unreachable in
the window → **insufficient-liquidity/fire-rate KILL**, reported as such (no window extension).

## §3 — SCALE-UP CRITERION (binding, anchored)
BOTH:
1. per-fill net-EV bootstrap 95% **LB ≥ $0.03** (3¢/contract — clears taker fee + slip
   with room; the offline liquid edge was 5–8¢ per-share, so 3¢ realized is the bar), AND
2. sustainable **net ≥ $300/day** at bounded attention (anchored: opportunity cost of
   owner attention off Branch B + running cost — inherited from the maker probe).
**Capacity math, stated now:** 4 markets × 96 windows/day = 384 w900 windows/day; the
rule fires on a fraction (up≥0.55 & spot-flat — offline ≈ low-single-digit % of windows).
If addressable fills/day × per-fill $ can't reach $300/day even at the optimistic 3¢ and
realistic Kalshi size, that's a **pre-quoting SCALE-UP failure** — record before spending
quoting days. KILL-survives-but-SCALE-fails → **PARK** (edge real, sub-scale).

## §4 — SERVER/TIME BUDGET (TIGHT — read first)
Runway: server reset 2026-06-13, ~17-day estimate → **~6 days left (~2026-06-30).**
- Day 0 (now): §0 offline re-confirm + Kalshi book/feasibility. **May KILL here at zero cost.**
- If §0 passes and ≥4 quoting days fit before server end: quote small, gate on n≥500.
- **If the quoting window does NOT fit runway:** the registered, offline-validated rule +
  §0 Kalshi book evidence is the deliverable — **deferred to a fresh server for live
  validation, not rushed.** A forced 2-day quote to n<500 is a registered no-go.
- Decision date: **2026-06-29** (margin for export).

## §5 — MANDATORY VERDICT + EXPORT (any outcome, incl. Day-0 KILL)
A Day-0 KILL (edge 5m-only, or Kalshi ask too wide, or sub-scale capacity) is a recorded
result → `probe/KALSHI_FADE_VERDICT.md` + export. Closing the venue with evidence is a
deliverable.

## GUARDRAILS (inherited, binding)
Evidence verbatim. Gates fail. Thresholds frozen pre-result, amendable only BEFORE results
exist and only harder (the Gate-3/B4 standard). Model OUR execution at OUR fees. Kalshi-
direct measurement, no Polymarket-proxy in the PnL. Independence = resolved fill. No
window extension to reach n. PARK is a legitimate registered outcome.

**Blanks filled:** rule up≥0.55 & spot<5bps → buy DOWN taker hold-to-resolution.
§0 w900-only LB>0 on n≥300. KILL per-fill EV LB≤0 on n≥500. SCALE LB≥$0.03 AND ≥$300/day.
Decision 2026-06-29. Runway ~6 days → likely offline-validate-and-defer.
