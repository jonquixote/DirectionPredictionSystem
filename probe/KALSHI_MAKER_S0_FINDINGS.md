# Kalshi Maker §0 — Day-0 Verification Findings

**Date:** 2026-06-13. All probed against `api.elections.kalshi.com` (public, no auth).
Snapshot evidence — single sample mid-window; a full §0 would sample across window-life.

## Market universe (verified, not assumed)

Up/down 15-min markets exist for ALL FOUR symbols:
- `KXBTC15M`, `KXETH15M`, `KXSOL15M`, `KXXRP15M` (frequency = fifteen_min)
- **No 5-min up/down** (KXBTC5M etc. return empty). **No shorter.** Hourly exists only as
  *range*/*above-below* (KXBTC/KXETH/KXSOL/KXXRP, KXBTCD) — different instrument, not
  up/down, out of scope.
- **Universe = 4 markets × 15-min = 96 windows/day each = 384 windows/day total.**

## Fee — confirmed
Maker ≈ 0.0175·p(1−p) ($0.0175/contract at mid); taker 0.07·p(1−p). Matches registration.

## Placement — SOLVED (owner correct)
Orderbook is native resting limit orders (`yes_dollars`/`no_dollars` price-size ladders).
YES bid = best yes price; YES ask = 1 − best no bid. Two-sided resting is native. No build
needed for placement.

## Spread — MEASURED (revenue line), snapshot
```
KXBTC15M: spread=2.0c  mid=0.720  top-bid-queue=852 contracts
KXETH15M: spread=1.0c  mid=0.835  top-bid-queue=115
KXSOL15M: spread=0.5c  mid=0.917  top-bid-queue=53
KXXRP15M: spread=1.4c  mid=0.908  top-bid-queue=68
```
Spreads 0.5-2.0¢ — at the proxy's tight end. BUT two problems the snapshot exposes:
1. **Mids are 0.72-0.92, not 0.50** (sampled ~12min into window, post-move). Spread capture
   = half-spread = 0.25-1.0¢. At these off-mid prices the maker fee is small, but so is
   p(1−p) — and the informative move has already happened (adverse selection realized).
2. **Queue depth ahead: 53-852 contracts.** To earn the spread you JOIN the resting queue —
   behind everyone already there. Fill rate is gated by queue priority, not by willingness
   to quote. This is the hidden volume killer the §3 math didn't price.

## §4 cancel-on-move — STILL OPEN (the binding feasibility gate)
Not yet measured — requires the minimal watcher build to test cancel-ack latency vs the
≤1s target. Existing rails are place-and-wait-3s, no active cancel. This is the gate that
decides feasibility and it needs ≤2 build-days (§5) to measure honestly.

## Preliminary SCALE-UP read (the volume wall, now with real universe)
384 windows/day across 4 markets. Even quoting both sides every window, fills are gated by
deep queues (50-850 contracts ahead). To net $300/day at $0.010/contract needs ~30,000
profitable fills/day — not reachable on 4 thin 15-min markets with deep maker queues.
**The addressable market is structurally too small for the anchored scale-up bar**, before
cancel-on-move is even tested. This is a likely pre-quoting SCALE-UP failure (§3), recorded.

## Recommendation to owner (decision yours)
§0 already surfaces a probable SCALE-UP failure on volume grounds: 4 markets, deep queues,
$300/day bar unreachable at any plausible per-contract EV. Two honest paths:
- **(a) Day-0 KILL on breadth/volume now** — write `KALSHI_MAKER_VERDICT.md` (venue closed:
  insufficient addressable volume for the anchored bar), skip the build + quoting days,
  redirect the ~9 saved days. KILL-avoidance might pass (edge could be positive per contract)
  but SCALE-UP cannot, so the registered outcome is PARK/closed regardless.
- **(b) Spend ≤2 build-days to measure cancel-on-move + a full window-life spread
  distribution** before deciding — buys certainty on the feasibility gate, costs days the
  scale-up math suggests are already decided.

Registration says SCALE-UP-fail → PARK zero-touch. The volume wall points at (a). Awaiting
your call before any build or quote.
