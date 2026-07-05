# Track 2 Verdict — Latency-taker is DEAD; Kalshi discovers, PM is frozen (2026-07-05)

**Dataset:** `/data/track2_latency.db`, 1,006,453 rows, 50.0h (07-03 09:10 → 07-05 11:10Z),
143,779 polls @1.25s, **zero gaps >10s**, Coinbase/PM 100% non-null, Kalshi 94.6%
(boundary empty-books). Clean. Artifacts: `probe/results/track2_analysis_*.txt` (v1),
`track2_v2_*.txt` (controls + divergence).

## Verdict: no exploitable spot→contract repricing lag on either venue
The v1 detector (time-to-first-1c-move IN the spot direction) said "median 5s → viable" —
**an artifact of having no control.** Adding controls (`analyze_latency_v2.py`) settles it:

| venue | SAME dir | OPP dir | PLACEBO | directional hit-rate |
|---|---|---|---|---|
| Kalshi | 60% / 5.0s | 59% / 5.0s | 61% / 6.25s | **50%** (1230 vs 1236) |
| PM | 4% / 26s | 11% / 17.5s | 7% | **24%** (moves *against* spot) |

Kalshi SAME ≈ OPP ≈ PLACEBO and a coin-flip hit-rate ⇒ the "5s lag" is **mid volatility,
not causal repricing** — nothing to take. PM actually moves *against* spot more than with
(sticky-mid mean reversion around 0.50) — also nothing. Holds at 10bps events too.
(Caveat: 1.25s cadence can't resolve sub-1.25s lag — but a poller/taker at that cadence
couldn't exploit sub-second lag anyway, and the 5–60s band we *can* see shows zero signal.)

## The structural finding (ties the whole program together)
Mid by window phase:

```
phase        Kalshi mid med(std)     PM up-mid med(std)   %extreme(<.15|>.85)
0.00-0.25    0.495 (0.180)           0.500 (0.013)        K 39%   PM 0%
0.25-0.50    0.505 (0.269)           0.500 (0.014)
0.50-0.75    0.515 (0.359)           0.500 (0.014)
0.75-1.00    0.570 (0.431)           0.500 (0.017)
```

- **Kalshi** does real, calibrated price discovery: median unbiased at 0.50, variance grows
  toward resolution, 39% of mids reach extremes. Efficient — no lag, no fade edge (Track 1).
- **PM 15m up/down book is FROZEN at 0.50** — std 0.013–0.017 at *every* phase, 0% extreme.
  It carries essentially no information. This is the root of the entire `p_market` saga: the
  real PM book never moves, so `get_p_market`'s stale-Gamma excursions were the only thing
  that looked like signal — and they were non-executable.
- The "cross-venue divergence" (all-ticks median gap 0.28) is just Kalshi fanning to [0,1]
  while PM stays pinned (per-phase gap 0.005 early → 0.070 late). Not an arb: the PM side is
  a frozen, thin, non-executable quote.

## Branch decision (updates the gating in README.md)
- **Latency-taker: KILLED.** Do not spec it.
- **Track 3 (maker sim) → PRIMARY, but Kalshi-only.** PM has no flow and a frozen mid — no
  spread-capture business there. Kalshi has real 2-sided flow + discovery → the only venue
  where making could earn the spread. Re-scope Track 3 to Kalshi books exclusively.
- **Track 4 hypothesis #1 (cross-venue arb): deprioritized** — the divergence is
  PM-is-frozen, not a tradeable gap; PM leg is non-executable. Prefer Track 4's vol/magnitude
  hypothesis into Kalshi strike markets instead.

## What would change this verdict
Only sub-1.25s structure could hide a taker edge; that needs a Kalshi websocket
`orderbook_delta` probe (auth required). Given the controls show nothing at 5–60s AND the
hit-rate is a clean coin flip, this is low-priority — the efficient-market read is strongly
favored. Recommend NOT building the websocket probe unless Track 3 also fails.
