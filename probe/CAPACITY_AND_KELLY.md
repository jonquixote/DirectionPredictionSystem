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
