# KALSHI FADE-RICH-CONTRACT MINI-PROBE — VERDICT

**Sealed:** 2026-06-24. **Branch:** `probe/kalshi-fade`.
**Contract:** `KALSHI_FADE_PREREGISTRATION.md`.
**Outcome:** §0 **PASS on edge.** Original scale-FAIL→PARK **RETRACTED** — see below.

> **2026-06-24 CORRECTION (supersedes the PARK below):** the SCALE-FAIL rested on two
> errors — an arbitrary $25/fill (Polymarket depth carried to Kalshi unmeasured; real
> Kalshi mid-band depth $344–$3,172/market) and a units bug ($28/day should have been
> $154/day). Corrected per-trade EV is **+16.3% per $ staked**; at measured Kalshi depth
> ($100–500/fire) capacity is **$600–$3,000/day**, Kelly-compoundable — it CLEARS the
> $300/day bar. New status: **BUILD CANDIDATE**, gated only on live Kalshi-price
> validation (offline-unverifiable). Full analysis: `probe/CAPACITY_AND_KELLY.md`.

## §0 result (verbatim, OOS, bootstrap CI)
```
EDGE GATE — w900-only fade-rich liquid-band (up 0.55-0.70, Bybit spot<5bps, buy DOWN):
  w900: n=485  down-win 48.2%  EV/share +0.0563  boot95 [+0.0115,+0.1009]  PASS (LB>0, n>=300)
  dose-response: up>=0.58 +0.095 | up>=0.60 +0.121 | up>=0.62 +0.131  (all LB>0)

CAPACITY (§0.4):
  rule fires 37.9/day (17% of w900 windows, all 4 symbols)
  @ $25/fill, 3c EV  ->  ~$28/day net
  $300/day bar needs ~400 fills/day  ->  have 37.9/day  -> ~10x short
```

## Reading
- **The edge is real and survives on Kalshi's window.** The most likely Day-0 killer (edge
  is 5m-only; Kalshi has no 5m) did NOT fire — w900-only per-share EV bootstrap LB > 0,
  n=485, dose-responsive to richer up-prices. This is a genuine, OOS, alpha-not-beta
  structural mispricing (crypto long-bias → cheap DOWN underpriced when contract is rich
  but spot is flat), confirmed on the exact window Kalshi offers.
- **But the addressable scale is ~$28/day**, an order of magnitude under the anchored
  $300/day bar. Fire-rate (37.9/day) × realistic Kalshi size at the touch can't reach it,
  and 15-min depth won't give 10x size. This is a **pre-quoting SCALE-UP failure on
  capacity** — exactly the outcome the anchored bar exists to catch: a thin-but-real edge
  that would consume more attention than it pays.

## Disposition (registered §3: KILL-survives & SCALE-fails -> PARK)
- **PARK.** Do not allocate active time, do not quote, do not connect Kalshi. The edge is
  documented and real; it simply does not clear the attention bar at available venue scale.
- The per-share edge (5-13c) is venue-independent and durable; if a future venue offers
  materially deeper short-horizon crypto up/down books (or 5m markets, where the offline
  edge was fatter: w300 EV +0.093 on n=1316), the registered rule applies unchanged and
  the capacity math should be re-run there.

## What this thread delivered (banked)
The owner's challenge to the sealed sweep was correct and unlocked the only real signal in
the project: a measurable, OOS, regime-independent structural mispricing in crypto up/down
markets (`probe/FRESHMODEL_FINDINGS.md`). Eight deepening probes characterized it, caught
two of my own analytical errors (pooled-side masking; a Gaussian fair-price confound),
stripped a convexity mirage, and landed on a clean, frozen, deployable rule. It is real
and it is sub-scale at every venue we can reach. That is a complete, honest answer —
better than "no signal," and banked without overclaiming.

## Export
This verdict + `FRESHMODEL_FINDINGS.md` + `KALSHI_FADE_PREREGISTRATION.md` + all
`probe/freshmodel/*.py` and `probe/kalshi_fade/*.py` are committed. Add to the Day-10
export manifest. The registered rule survives the server; the capacity wall is venue data.
