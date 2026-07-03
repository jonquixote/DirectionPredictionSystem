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

## FORWARD EXECUTABLE CONFIRMATION — synthesis (2026-06-27, `probe/synthesis.py`, `results/synthesis_20260627.txt`)
~48h of forward executable data (book-mid + own Coinbase spot, both loggers) closes the
dialectic. The edge does **not** exist on executable prices on **either** venue:

- **A) PM book rarely goes rich.** Book-mid reached ≥0.55 in **21/5,355 windows (0.39%)** vs
  v3 `p_market`'s ~22% → the artifact inflated the fire-rate **~57×**. The "37.9 fires/day"
  was almost entirely fake-rich p_market.
- **B) PM executable fade is NEGATIVE.** Firing on book up≥0.55 & spot-flat, entry =
  `best_down_ask` (the real lift), spot outcome: down-win **25%** (n=8, EV −0.19);
  up≥0.58 → down-win **0%** (n=4, EV −0.40). Small n, but the sign is consistent and the
  mechanism is clear: when the book *genuinely* goes rich-up, **up tends to WIN** — the book
  is informative, so fading it loses. The opposite of the artifact's claim.
- **C) Live Gamma tracks the book** (|gamma_up − book| median 0.0000, p95 0.02, >0.05 only
  0.21%; gamma_up≥0.55 just 0.01%). So the v3 defect is specifically the **cached-stale**
  fallback in `discover_contract`, not live Gamma.
- **D) Kalshi is calibrated, not noisy.** n=**1,005** fires (good n), **tight 1c-median yes
  spread** (so the earlier "wide-book noise" guess was wrong — the book is tight and
  genuinely reaches 0.55), down-win **41%** at entry 41c → EV **−0.015 [−0.045,+0.016]**.
  Efficient; ~0 edge.

**Sealed synthesis:** there is no tradeable fade edge on executable prices on Polymarket
(negative — book is informative) or Kalshi (calibrated ~0). The historical +12pp was 100%
the v3 `p_market` stale-cache artifact. KILL confirmed on forward, executable, two-venue data.

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

## 8-DAY RERUN (2026-07-03 — `results/probe_suite_20260703.txt`)
Full-week executable data confirms the KILL and adds one nuance:
- PM fire-rate inflation confirmed at scale: book-mid ≥0.55 in **0.38%** of 19,404 windows
  (vs p_market ~22% → ~59×). Live gamma tracks book (p95 |diff| 0.02). PM executable fade
  still ≤0 at every threshold (largest-n cell X=0.53: n=121, EV −0.030).
- PM fade-entry depth at the rare rich moments is real: median **$620** in-band (n=56 fires).
- **Kalshi nuance:** at n=3,259 fires the point EV turned slightly positive and dose-responsive
  (X=0.55/0.58/0.60 → +0.5c/+1.4c/+1.8c net of taker fees; 6/7 coins positive). BUT
  cluster-robust bootstrap (fires grouped by boundary_ts; coins co-move) doubles the CI:
  X=0.60 EV +0.0179, clustered-95 **[−0.0154,+0.0528] — not significant** (609 clusters).
  A residual ~1-2c long-bias on Kalshi is *possible*; ~4 more weeks of passive logging
  would resolve it. It is in any case an order of magnitude below the artifact's +12pp.
