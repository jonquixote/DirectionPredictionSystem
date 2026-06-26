# Capacity, Sizing & Kelly — Answering the PARK Challenge

**Date:** 2026-06-24. **Context:** the owner challenged the Kalshi-fade PARK verdict
("what do you mean no venue pays it at a size worth my attention? what about compounding
and Kelly?"). The challenge was correct. The PARK rested on **two errors**, corrected here.

## The two errors in the PARK verdict
1. **Arbitrary $25/fill.** I carried Polymarket's thin depth ($14/window) over to Kalshi
   without measuring Kalshi. **Measured Kalshi 15-min books (2026-06-24, live):**
   total resting $4k–63k contracts; mid-band (15-85¢, excludes the 0.1¢ parking floor)
   **$344–$3,172 per market** even at late-window extreme prices. Per-fire fillable size
   at the down price is realistically **$100–$500+, not $25** — a 4–20× underestimate.
2. **A units error.** "$25/fill × 3¢ × 37.9 = $28/day" mixed cents-per-contract with
   dollars-of-stake. Correct: EV is **16.3% per dollar staked** (stake 40¢ to make 6.5¢),
   so $25 of stake → $4.08/fire → **$154/day even at the $25 assumption** — not $28.

## Per-trade economics (the part I undersold)
Buy DOWN at entry ~40¢ when up-price ≥0.55 & spot flat. Down-win 48.2% (s0 w900).
```
EV/contract = +$0.065   |   EV per $ STAKED = +16.3%   |   odds b = 1.5   |   ≤15-min resolution
```
A **+16% return on capital per trade, resolved in 15 minutes.** Not a thin edge.

## Kelly sizing
```
full Kelly f* = 13.7% of bankroll per fire   |   half-Kelly = 6.8%
half-Kelly log-growth = +1.04% per trade
avg concurrent positions = 37.9/day × 0.25h / 24h = 0.39  → mostly SEQUENTIAL → near-Kelly is valid
```
Because fires are spread through the day and resolve in 15 min, positions barely overlap —
you can size near-Kelly per fire without portfolio-correlation haircuts.

## Compounding: it works — until liquidity binds (this is the real answer)
Two regimes:
- **Capital-limited (small bankroll):** half-Kelly compounds at ~1%/trade × 37.9 trades/day.
  A small stake grows fast toward the liquidity ceiling.
- **Liquidity-limited (large bankroll):** once your half-Kelly bet exceeds per-fire fillable
  size, you can't bet more — growth stalls and you harvest a **flat daily $** = fires × fill × 16.3%.
```
per-fire fill | flat daily NET $ | bankroll where compounding stalls
   $25         |    $154/day      |   ~$370
   $100        |    $618/day      |   ~$1,500
   $300        |  $1,853/day      |   ~$4,400
   $500        |  $3,089/day      |   ~$7,300
```
**So:** Kelly + compounding take a small bankroll to the liquidity ceiling in days–weeks,
then it pays the capacity-limited rate. At measured Kalshi depth ($100–500/fire), that
steady rate is **$600–$3,000/day** — clearing the $300/day attention bar with room, the
opposite of the $28 PARK.

## Revised verdict: PARK → BUILD CANDIDATE (with one offline-unverifiable gate)
The economics are compelling **IF** the edge survives on **Kalshi prices.** That is the
single load-bearing assumption and it **cannot be checked offline:**
- The entire fade edge was measured on **Polymarket `p_market`** (what v3.db logs). Kalshi
  is a different book — possibly better-calibrated (deeper, more institutional), which could
  shrink or erase the +16%.
- We have **no logged Kalshi price history** (maker §0: only 465 of our own orders). So
  "does up-price≥0.55 + spot-flat predict down-win >breakeven **on Kalshi**" is unanswerable
  without live Kalshi data.

## Honest remaining risks (do not let the $3,000/day headline hide these)
1. **Kalshi-price edge unverified** — the big one. Polymarket-measured; could be venue-specific.
2. **Depth-at-fire-moment inferred** — snapshots were late-window; the at-the-money
   early-window down-side (35–45¢) depth is bounded by total book, not directly measured.
3. **Adverse selection** — buying down when up is rich; if Kalshi's richness reflects
   informed flow, we're the patsy. Spot-flat filter mitigates, doesn't eliminate.
4. **Fire-rate transfer** — 37.9/day is the Polymarket rate; Kalshi's may differ.
5. **Execution slippage** — ask 0.5–2¢ worse (maker §0); +6.5¢ margin survives it, but it
   eats the thin-threshold (up≥0.55) tail.

## Disposition
- **Supersedes the PARK in `KALSHI_FADE_VERDICT.md`.** The edge is real (s0: w900 EV
  +0.056, LB>0) AND, at measured Kalshi depth, **economically viable** ($600–3,000/day,
  Kelly-compoundable). It is a **BUILD candidate**, not PARK.
- **The one gate left is live Kalshi validation** (does the edge hold on Kalshi prices),
  which needs live Kalshi price+spot logging then re-derivation, or a live shadow.
- **Runway (~6 days) does not fit live validation to n≥500.** Registered path: stand up
  Kalshi price logging now (cheap, passive), defer the live shadow + re-derivation to a
  fresh server. This is the high-priority deferred build — a genuinely different status
  from the five sealed KILLs and the maker PARK.

**Bottom line for the owner's question:** "worth your attention" was my error — at real
Kalshi depth with Kelly compounding it's plausibly a few-hundred-to-few-thousand $/day
operation, *contingent on one thing we can only learn live: whether the edge that's real on
Polymarket is also real on Kalshi.* That single question is the whole game now.

---

## ROBUSTNESS ADDENDUM (2026-06-25 — `probe/freshmodel/robust.py`, `w24_regime.py`)
Four decision-relevant checks on existing data before committing to live collection:

- **Parameter robustness — GREEN.** Threshold sweep: every up-price X ∈ {0.55,0.58,0.60}
  × spot-flat T ∈ {3,5,10}bps cell is significantly +EV; monotonic in richness (X=0.55
  +0.083 → X=0.60 +0.150); insensitive to the spot-flat cutoff. NOT a knife-edge — the
  signature of real structure, not a fitted artifact. Deployment params: X≥0.55, T≤5bps,
  entry band 30-49¢ (the liquid core alone is +0.059).
- **Fire independence — GREEN.** 79% of fire-boundaries are a single symbol (18% two, 3%
  three) → the 37.9 fires/day are largely independent; capacity/Kelly independence holds.
- **Kalshi execution spread — GREEN (first live read).** From the logger's first hours:
  down-side executable spread at the fade prices is **1¢ median, 96% under the ~3¢ that the
  +6.5¢/share edge tolerates.** Execution is feasible at Kalshi spreads. (Depth at the
  immediate touch ~$63/2¢-band overnight → capacity ~$390/day even at this thin read;
  likely deeper in active hours.)
- **Temporal persistence — YELLOW (the one open risk).** Weekly structural gap (realized
  down-rate − implied down-price) was stable **+11 to +21pp for four weeks (W20-W23), then
  collapsed to +1.6pp in W24.** Ruled out: setup-thinning (same contracts), volatility
  (W23 was higher-vol and stayed strong), trend (drift ~48% throughout). W24 is the last
  PARTIAL week (n=345, ~3σ-low) — **either early decay or an unlucky partial week, and the
  offline data ends exactly at the ambiguous point.** Unresolvable offline; this is the
  precise question the live logger answers going forward.

**Revised confidence:** the edge is real, parameter-robust, executable at measured Kalshi
spreads, and on independent fires. The single unresolved risk is whether the +12pp gap
persists past W23 — the live forward collection is designed to settle exactly that, and
nothing offline can. Size any eventual live deployment for the possibility that W24 was the
start of decay (start small, gate on forward n, kill on sustained gap < breakeven).

---

## CROSS-DURATION ADDENDUM (2026-06-25 — `probe/freshmodel/xdur.py`, `xdur_weekly.py`)
Polymarket runs up/down (vs window-open) markets at **5m and 15m live**; gamma serves no
30m/60m up/down now (the only longer instruments are `*-above-on-*` STRIKE markets — a
different structure, same unusable family as Kalshi's KXBTCD ladder). But **v3.db holds
30m (1800s) history** (Polymarket served 30m during 05-13..06-26, since discontinued), so
the fade rule can be characterized across **three horizons** on existing data.

**The edge replicates independently at all three horizons** (rule up≥0.55, |spotDev|<5bps,
entry 30-49c → buy DOWN; BTC/ETH/SOL — XRP dropped from prod since the freshmodel era):

| dur | fires | down-win% | gap | EV/share | boot95 |
|-----|------:|----------:|----:|---------:|--------|
| 5m  | 2937  | 52.8% | +12.4pp | **+0.1075** | [+0.089,+0.125] |
| 15m | 1100  | 51.5% | +11.4pp | **+0.0969** | [+0.066,+0.126] |
| 30m |  594  | 53.5% | +13.5pp | **+0.1179** | [+0.075,+0.156] |

All three CIs exclude zero. The **gross calibration shows the same monotone richness
dose-response at every horizon** (up-px [0.55,0.60)→[0.70+): 5m −8.7→−42pp; 15m −7.7→−38;
30m −11.5→−31). Three independent horizons reproducing the identical monotone curve is hard
to dismiss as overfit — strong triangulation that the long-bias is structural, not
duration-specific.

**Operational consequence:** 5m is the **richest fire source** (2.7× the 15m fire count at
equal/better EV) and is live on Polymarket.

**Polymarket live-depth — CORRECTED.** A first depth probe (2026-06-25) appeared to show
~$0 resting in the 30-49c fade band and was written up as "thin ~50c MM quote." **That was
an instrumentation error, twice over:** (1) gamma's `order=startDate` listing returns the
NEXT-DAY pre-listed markets (boundaries +23h), not the live window — those untraded books
sit at a 50c seed; and (2) even on the live window, an at-the-money early-phase snapshot
shows nothing in 30-49c because down hasn't gone cheap yet. Targeting the **active** window
(slug = next dur-boundary close) shows the opposite: **real, liquid books** — down 0.49/0.50
**1c spread, 40-50 levels/side, $15k-61k total resting** (BTC 5m $61k, XRP $27k, even HYPE
$6k). So the prior "Polymarket too thin" claim is **not supported** by this data. Whether
there is fillable size specifically at the fade entry (down ~40c, mid-window rich moments)
is still unmeasured — a single snapshot can't see it — and is exactly what the new forward
logger settles.

`probe/polymarket_depth/logger.py` (deployed on VPS, daemon `pm_depth_daemon.sh` →
`/data/pm_depth.db`, 12s poll, 7 coins × 5m+15m): records best down-ask, 30-49c band depth,
and the full raw ladder at rich moments (down mid≤0.49). This both (a) measures whether
Polymarket-direct execution is viable at the fade entry — which, if yes, **sidesteps the
Kalshi transfer gate entirely for the richest (5m) slice** — and (b) captures doge/bnb/hype
Polymarket price paths forward (their history is in the disk-blocked 33GB trade harvest).

**W24 persistence, by horizon (attacks the YELLOW directly):** the W24 gap-collapse worry was
a 15m-only read. Across horizons: 5m **+3.3** (n=251), 15m **−2.8** (n=94), 30m **+14.4**
(n=48). **30m held fully intact** through W24 — if this were market-wide efficiency onset,
the longer/more-liquid horizon is where correction would show first, yet it's the one that
held. W21–W23 are a stable +11–14pp core at all three horizons (W20 was an early-window
hot-start outlier at +21). **Net: catastrophic decay is now unlikely; the 5m/15m W24
co-softening (small partial-week n) keeps a residual flag that only forward data settles.**
YELLOW → softened, not cleared. Production logs 5m/15m forward continuously; re-run
`xdur_weekly.py` as W25+ fill in.
