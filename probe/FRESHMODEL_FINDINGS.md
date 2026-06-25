# Fresh-Model Thread — Findings

**Date:** 2026-06-24. **Branch:** `probe/freshmodel`.
**Origin:** owner's challenge to the sealed sweep — "many models on the analysis page
show 54-63% accuracy, yet you say no signal." Forced a per-model / fresh-window /
sibling-consistency / consensus / underdog / maker / regime / mechanism investigation.

## Summary
**A real, named, structural mispricing exists** — but at Polymarket liquidity it pays
~$70/day in capacity. Worth a deliberate Kalshi-only mini-probe; nothing else changes.

| Question | Answer |
|---|---|
| Is the 54-63% on the analysis page deployable? | No. Right-tail-of-rank + week-1 winners revert to 49.85% next week (analyze1-2). |
| Per-model fresh accuracy decay? | Flat 49-52% across days 0-20 (analyze1-A). |
| Consensus across fleet? | Flat ~50.3% even when 100% agree (analyze2-E). |
| Sibling retrains? | Scatter ~52%; no cell-level persistence (analyze1-B). |
| Track-record selection (deploy past-profitable cells)? | Counterproductive: −0.0149 OOS vs −0.0098 baseline (analyze4). |
| Underdog-side of fired predictions? | **+0.0062 taker, +0.0188 maker-upper-bound, OOS, fleet-wide on 450k trades** (everything.py). |
| Alpha or beta? | **Alpha. Down-underdog +EV on up-days too** (deep3). |
| `p_market` stale/misaligned? | No, aligned and spot-tracking (deep5). |
| Real mechanism? | **Contract priced rich while spot stayed flat → structural overpricing** (deep7). |
| Survives convexity + liquid band? | Yes: **+5–8% per-share EV, OOS, n≥1.8k, CIs clear of zero** (deep8 Part 1). |
| Polymarket dollar-weighted? | Collapses to ~+0.4%/$ on ~$70/day total capacity (deep8 Part 3). |

## The mechanism (one sentence)
Crypto retail/sentiment markets carry a **long-bias** — UP-prices systematically exceed
realized UP-rates. Inside short windows, when the contract reprices rich (≥55¢ UP) **but
spot hasn't actually moved**, that's *structural overpricing* unsupported by underlying;
fading it (buy DOWN at the real price, hold to close) is +5–8% per-share, in all market
regimes including up-days, OOS, real-price, n=1.8–5.4k.

## What I got wrong, on the record
- Initial pooled-aggregate reads dismissed the signal that survived a side-split (favorite
  losses drowned underdog wins). The owner's challenge corrected this.
- deep6-B used a Gaussian fair-price; Φ over-extremes on fat-tailed crypto manufactured
  apparent reversion EV. Discarded — exactly the Control 2 trap the sealed fair-value
  probe was built around, walked into anyway.
- deep4 headline (+44% EV at extreme prices) was the convexity tail — per-share EV
  inflated by ultra-cheap-side variance, not deployable. deep8 strips it to ~+5–8% liquid.

## Why this isn't deployable on Polymarket
deep8 Part 3 measured `p_market`-band liquidity from the 53M-trade probe DB: median $14.75
of capacity at entry±1¢ on the Down side during these windows; 44% of opportunities have
<$10. Dollar-weighted simulation (cap min($25, band/4)): **+0.4%/$ on $16,489 across 28
OOS days ≈ $70/day net, ~$0.30/day at this size**. The edge is real; Polymarket can't
absorb it.

## The path that survives — Kalshi-only fade-rich-contract probe
**Why Kalshi:** the sealed §0 measured Kalshi top-of-book at hundreds-to-thousands of
contracts vs Polymarket's $15 in the band. The per-share edge is venue-independent
(contract calibration vs spot); the dollar-pay-off depends on venue depth. Kalshi is the
one venue where this could plausibly clear the $300/day anchored bar from the maker probe.

**Rule (frozen here):** at fire on a Kalshi up/down contract whose up-price ≥0.55 (down
≤0.45) AND simultaneous Bybit spot deviation from window-open <5bps, buy DOWN at real
quoted ask, hold to resolution. Taker fees as registered.

**Pre-registered gate (mini-probe):** realized per-fill net EV bootstrap LB > 0¢ on
n ≥ 500 fills over ≤10 quoting days. Anchored $300/day bar inherits.
Built as own one-pager next, before any quote.

## Artifacts
- `probe/freshmodel/{analyze,analyze2,analyze3,analyze4,drill,everything,verify,deep1..deep8}.py`
- All committed to `probe/freshmodel`.
- This findings doc + the Kalshi-fade pre-registration close the thread.
