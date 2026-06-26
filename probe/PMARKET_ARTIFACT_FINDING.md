# The fade edge is largely a `p_market` measurement artifact (2026-06-26)

**Status: BUILD CANDIDATE → effectively KILLED as previously measured.** The historical
Kalshi-fade edge (+12pp structural gap, +0.10 EV/share) was measured on v3.db `p_market`,
which at its *rich* values is **not the executable order-book price**. Reopen only if
forward executable (book-mid) data shows a real edge.

## What `p_market` actually is
`ofi-lab-v3/trading/polymarket_discovery.py::get_p_market`:
1. Try live CLOB `/midpoint` of the Up token (the real book mid).
2. **If that call fails (3s timeout / empty `mid` — common on these fast micro-markets),
   fall back to `contract["p_market_gamma"]` = `float(outcomePrices[0])` from Gamma,
   fetched ONCE at contract discovery and CACHED** (`discover_contract` is memoised).

So whenever the midpoint call fails, `p_market` = a **stale, cached Gamma outcomePrices
value** that can be extreme (≈0.99) while the live executable book sits at 0.50.

## The evidence (same markets, same hours)
Direct per-window match of v3.db `p_market` vs the `pm_depth` logger's executable
**book-mid** (`1 − down_mid` from CLOB `/book`), BTC/ETH/SOL 15m, 06-26, 111 matched windows:

```
windows where v3 p_market reached >=0.55:        25  (22.5%)
windows where pm_depth BOOK-mid reached >=0.55:   6  ( 5.4%)
v3 rich (>=0.55) WHILE book-mid stayed <0.52:    19   <-- non-executable
examples:  eth v3=0.995 book=0.515 | btc v3=0.995 book=0.495 | sol v3=0.995 book=0.515
```

Corroborating, the live book almost never goes rich: across 30,493 `pm_depth` obs
(phase≤0.8), up-mid ≥0.55 in **0.08%** (vs v3's ~11%/day). When the book *does* reach 0.55,
spot has usually moved (median dev 11.6 bps) — i.e. real, not a fade setup.

## Why this is decisive
The fade rule "buy DOWN at `1 − p_market` when up≥0.55 & spot flat" assumed you could buy
down at ~0.40 when `p_market`≈0.60. But at those moments the executable book down-ask was
~0.50 — **the 0.40 entry never existed.** The +12pp gap and +0.10 EV are properties of a
non-executable, partly-stale price series vs outcomes, not a tradeable edge.

## It also re-explains the Kalshi "non-transfer"
The Kalshi logger measures the **orderbook** mid (executable). Kalshi came out calibrated
(~0 EV) not because Kalshi is a different/efficient venue, but because it was **measured
correctly**. Polymarket measured on its glitchy stale fallback looked rich; measured on its
*book* (`pm_depth`) it is just as pinned at 0.50 as Kalshi. **Both venues are calibrated on
executable prices.** The cross-venue "+12pp PM vs +1.5pp Kalshi" in `xvenue_fade.py` is an
artifact of PM using `p_market` (v3.db) while Kalshi used a real book — not a venue effect.

## What still survives (and the one open thread)
- The book *does* occasionally reach the rich zone for real (~5% of windows), and at those
  moments there is genuine depth (`depth_report`: median ~$314 in the 30-49c band). Whether
  those **rare, real, executable** rich+flat moments resolve down above breakeven is
  **unmeasured** — `p_market` can't answer it; only book-mid + resolution can.
- Forward fix in place: `pm_depth` logs executable book-mid + spot outcome (→ the TRUE
  executable fade EV) and now also `gamma_up`/`gamma_last` (→ quantifies the artifact:
  how often the Gamma price diverges rich from the book). Decisive in a few days.

## Disposition
- **Supersedes the BUILD CANDIDATE in `CAPACITY_AND_KELLY.md` / `KALSHI_FADE_VERDICT.md`.**
  The edge as measured does not survive contact with executable prices.
- The remaining, much narrower question — is there a real edge at the rare executable
  book-mid rich moments — is now being collected forward and is the only thing that could
  resurrect any version of this. Size expectations accordingly: near-zero unless the
  forward book-mid data surprises.
- **Lesson:** every prior fade result built on v3.db `p_market` (deep4-8, freshmodel,
  xdur, s0, xvenue PM side) inherits this confound at the rich tail. Re-derive any
  survivor on executable book-mid before trusting it.
