# KALSHI MAKER-VIABILITY MINI-PROBE — PRE-REGISTRATION

**Branch:** `probe/kalshi-maker`. **Drafted:** 2026-06-13. **Status:** AWAITING SIGN-OFF —
no quoting, no building until committed AND owner-approved.
**Predecessor:** `probe/VERDICT.md` (taker-copy + OFI-model both KILLED; maker seat the one
structurally-subsidized survivor, at break-even ± measurement error, Polymarket-proxy only).

**Purpose in one line:** replace the proxy adverse-selection bound (~30-69 bps) with a
Kalshi-MEASURED net-EV-per-contract number, and decide KILL / PARK / SCALE on pre-set bars.

---

## 0. PRE-QUOTING VERIFICATION (Day 0 — gates the rest; commit findings before quoting)

All assumed numbers below are Polymarket-proxy or doc-derived until this step replaces them.

- **Market universe (verify, don't assume):** which crypto up/down markets Kalshi actually
  lists and at which durations. Known: series `KXBTC15M` (BTC 15-min) is wired in
  `execution/kalshi_live_trader.py`. VERIFY via authenticated API: do ETH/SOL/XRP series
  exist? Any 5-min or hourly? Record exact series tickers + durations offered. The probe
  quotes ONLY verified-live markets.
- **Fee (confirm from Kalshi account/API, not press):** maker ≈ 0.0175·p(1−p) ($0.0175/contract
  at mid), taker 0.07·p(1−p). Confirm maker rebate/fee booking on a real fill.
- **Two-sided placement — SOLVED (owner clarification).** Kalshi orders ARE resting limit
  orders: set a price, fills only when a taker hits it, and a buy and a sell can rest
  simultaneously. The venue natively supports resting bid+ask. Placement is NOT the gap.
  The binding feasibility question narrows to the CANCEL side — see §4.
- **Window dead-zone:** markets may be untradeable / price-setting-only for the first ~40s
  of each window. Fold into the quoting-window model: effective quoting life per 15-min
  window ≈ (900 − 40)s, and the §3 windows/day fill math uses the reduced life.
- **Spread reality:** sample live Kalshi order books on the verified markets — actual
  bid/ask spread distribution at mid vs tails. The proxy assumed 1-2¢ (100-200 bps); MEASURE
  it. Spread is the entire revenue line; an assumed spread is not a result.

---

## 1. WHAT IS MEASURED (on Kalshi directly, small size, live resolved markets)

Per filled contract, booked to resolution:
- **spread capture** = (our resting quote price) − (contemporaneous fair mid at fill instant).
  Fair mid from the live Kalshi book midpoint at fill time (NOT our model, NOT Bybit).
- **adverse selection** = realized: did the market move through our fill before resolution?
  Attributed as (resolution outcome value − fill price), i.e. the actual P&L of the filled
  contract net of spread already counted. Booked per resolved contract, not modeled.
- **maker fee** = 0.0175·p(1−p) at fill price, charged per fill.
- **net EV per filled contract** = spread_capture − adverse_selection_loss − maker_fee,
  realized, per resolved contract.

**Quoting policy:** rest small two-sided quotes around the live book mid (NOT a directional
model — the OFI model is dead; we are providing liquidity, not predicting). Size = minimum
viable (1 contract/side, scale only if §3 cleared). One quoting bot, verified markets only.
**Independence unit:** one resolved filled contract per (market-window, side). Fills within a
window on the same side telescope to one observation (the trading-system clustering lesson).

---

## 2. KILL CRITERION (ends the probe)

On n ≥ **500** independent resolved fills:
> realized net-EV-per-contract bootstrap 95% **lower bound ≤ $0** → **KILL.**

Marginal-positive-with-LB-crossing-zero is a KILL, NOT "run it longer." If 500 fills are
unreachable in the quoting window (§5), that is an **insufficient-liquidity KILL** —
reported as such, the staleness-window lesson: do not extend to manufacture n.

---

## 3. SCALE-UP CRITERION (the binding bar — written now, before any marginal-positive appears)

The trading-system pattern that just consumed four months: a thin edge that needs constant
quote-pulling, uptime babysitting, and capital to mean anything — it eats the time it was
meant to free. SCALE-UP must clear a bar high enough that such an edge does NOT qualify.

**SCALE-UP requires BOTH:**
1. realized net-EV-per-contract bootstrap 95% **lower bound ≥ $0.010** (clears break-even
   with room — roughly double the maker fee at mid — not the marginal $0.001-0.005 band), AND
2. **achievable sustainable volume at that EV ≥ enough for net ≥ $300/day** with bounded
   attention (≤ the existing zero-touch automation; no new babysitting role).

   **Bar justification (anchored, not arbitrary):** $300/day ≈ the opportunity cost of
   pulling the owner's active attention off Branch B. A part-day of focused senior eng/quant
   time is worth ~$300-500; a strategy that demands ongoing attention must clear at least the
   low end of that or it is net-negative on the only scarce resource (time, not capital).
   Plus it must cover its own running cost (server + data). Below $300/day net at bounded
   attention, the correct action is PARK zero-touch — the edge does not earn the attention it
   consumes. The bar's whole job is to make thin-but-positive fail honestly; tying it to a
   defensible comparison is what stops it being lowered the moment a marginal-positive appears.

**The volume math, stated honestly (this is likely where it dies):** BTC 15-min = 96
windows/day. Both sides every window at 10 contracts ≈ 1,920 fills/day. At $0.010/contract
net that is **~$19/day** — over an order of magnitude below the $300/day bar. To clear
$300/day needs net EV/contract ≫ $0.01, OR many verified markets/durations stacked, OR much
larger size (which raises adverse selection and moves the book against us). The §0
universe-verification feeds directly here: addressable windows/day across all verified
markets × realistic fill rate × net EV must reach $300/day. If the arithmetic can't reach it
even at optimistic EV, that is a **pre-quoting SCALE-UP failure** worth recording before
spending quoting days.

**Outcomes:**
- KILL fails (LB > 0) **and** SCALE-UP clears → candidate for real attention; design the
  full deployment as its own next step.
- KILL fails **but** SCALE-UP fails → **PARK at zero-touch shadow; do NOT allocate active
  time.** The honest middle outcome and the most likely one.
- KILL triggers → done.

---

## 4. LATENCY-DEFENSE FEASIBILITY — CANCEL-ON-MOVE is the binding gate

Placement is solved (§0: Kalshi natively rests two-sided limit orders). The open question is
the CANCEL side: taxing takers did not remove latency players, and a resting quote is the
thing they pick off when spot moves and our price goes stale. Determine, before quoting:
- Required cancel-and-reprice speed: how fast must we cancel after a ≥Xbps spot move to avoid
  being the stale price a latency taker hits? (Estimate from 100ms Bybit move cadence +
  measured Kalshi cancel-ack latency.)
- Can the rails achieve it? Existing path is place-and-wait-3s — NO active cancel-on-move.
  Minimal build = a spot-move watcher that cancels/reprices resting quotes. If the rails +
  minimal build cannot cancel within target **≤1s from spot move** (and the build doesn't fit
  §5), that is a **feasibility KILL** — recorded before any capital is exposed.

---

## 5. SERVER / TIME BUDGET

Server runway: ~17 days from 2026-06-13 → hard stop ~2026-06-30, with export room.
- Day 0 (1 day): §0 verification + §4 feasibility. Either may KILL here.
- Build (≤2 days, only if §0/§4 pass): minimal two-sided quote + cancel-on-move on existing
  rails. Scoped minimal; if it can't be done in 2 days it's a feasibility KILL.
- Quoting window: **7 days** small-size live, demo/small-real per owner.
- Decision date: **2026-06-25** (verdict + export, ≥5 days server margin).

**Mandatory verdict + export on ANY outcome, including a Day-0 KILL.** If this dies at §0
(only BTC-15m wired; no ETH/SOL/XRP breadth) or §4 (rails can't cancel sub-1s), that is a
RECORDED result — "Kalshi maker infeasible on free rails / insufficient breadth, venue
closed" — written to a verdict doc and exported, not a quiet stop. Closing a venue with
evidence is a deliverable. A Day-0 KILL still produces `probe/KALSHI_MAKER_VERDICT.md`.

---

## GUARDRAILS (inherited, binding)

Evidence verbatim. Every gate can fail. Thresholds frozen before quoting — amendable only
BEFORE results exist and only in the harder direction (the standard that held 10 days).
Fair value and adverse selection MEASURED on Kalshi, not proxied. Independence = resolved
fill. No extending the window to reach n. PARK is a legitimate, pre-registered outcome and
the report states it without flinching.

**Blanks filled:** KILL n=500, LB≤$0. SCALE-UP LB≥$0.010/contract AND net≥$300/day
(anchored to attention opportunity cost) at sustainable volume/bounded attention. Latency
target ≤1s cancel-on-move (binding feasibility gate; placement solved). Decision 2026-06-25.
Verdict+export mandatory on any outcome incl. Day-0 KILL.
