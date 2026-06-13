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
- **Two-sided capability (build-gap check):** current rails place ONE maker order on the
  model's side and fall back to taker after 5 retries (`MAKER_WAIT_SECS=3.0`). Market-MAKING
  needs RESTING TWO-SIDED quotes (bid+ask) with active cancel-on-move. Determine: can the
  rails rest two-sided and cancel within ~1s of an adverse spot move? If not, the minimal
  build is scoped here and counted against the time budget — or it's a feasibility KILL (§4).
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
2. **achievable sustainable volume at that EV ≥ enough for net ≥ $100/day** with bounded
   attention (≤ the existing zero-touch automation; no new babysitting role).

**The volume math, stated honestly (this is likely where it dies):** BTC 15-min = 96
windows/day. Both sides every window at 10 contracts ≈ 1,920 fills/day. At $0.010/contract
net that is **~$19/day** — an order of magnitude below the $100/day bar. To clear $100/day
needs either net EV/contract ≫ $0.01, or many more verified markets/durations stacked, or
much larger size (which raises adverse selection and moves the book against us). The §0
universe-verification feeds directly here: total addressable windows/day across all verified
markets × realistic fill rate × net EV must reach $100/day. If the arithmetic can't reach it
even at the optimistic EV, that is a **pre-quoting SCALE-UP failure** worth recording before
spending quoting days.

**Outcomes:**
- KILL fails (LB > 0) **and** SCALE-UP clears → candidate for real attention; design the
  full deployment as its own next step.
- KILL fails **but** SCALE-UP fails → **PARK at zero-touch shadow; do NOT allocate active
  time.** The honest middle outcome and the most likely one.
- KILL triggers → done.

---

## 4. LATENCY-DEFENSE FEASIBILITY (pre-quoting; can be a KILL on its own)

Taxing takers did not remove latency players. A resting maker quote is the thing they pick
off. Determine, before quoting:
- Required quote-pull speed: how fast must we cancel a resting quote after a ≥Xbps spot move
  to avoid being the stale price a latency taker hits? (Estimate from the 100ms Bybit move
  cadence + Kalshi book-update latency.)
- Can the rails achieve it? Current path is place-and-wait-3s with taker fallback — that is
  NOT active cancel-on-move. If the rails structurally cannot cancel within the required
  window (target: ≤1s from spot move), and the minimal build to add it doesn't fit §5,
  that is a **feasibility KILL** — recorded before any capital is exposed.

---

## 5. SERVER / TIME BUDGET

Server runway: ~17 days from 2026-06-13 → hard stop ~2026-06-30, with export room.
- Day 0 (1 day): §0 verification + §4 feasibility. Either may KILL here.
- Build (≤2 days, only if §0/§4 pass): minimal two-sided quote + cancel-on-move on existing
  rails. Scoped minimal; if it can't be done in 2 days it's a feasibility KILL.
- Quoting window: **7 days** small-size live, demo/small-real per owner.
- Decision date: **2026-06-25** (verdict + export, ≥5 days server margin).

If §0 or §4 KILLs, the probe ends Day 0-1 with the verification as its finding (e.g.
"Kalshi spread measured at N bps, below break-even — KILL" is a complete, valuable result).

---

## GUARDRAILS (inherited, binding)

Evidence verbatim. Every gate can fail. Thresholds frozen before quoting — amendable only
BEFORE results exist and only in the harder direction (the standard that held 10 days).
Fair value and adverse selection MEASURED on Kalshi, not proxied. Independence = resolved
fill. No extending the window to reach n. PARK is a legitimate, pre-registered outcome and
the report states it without flinching.

**Blanks filled:** KILL n=500, LB≤$0. SCALE-UP LB≥$0.010/contract AND net≥$100/day at
sustainable volume/bounded attention. Latency target ≤1s cancel-on-move. Decision 2026-06-25.
Tighten before sign-off if any bar is too soft.
