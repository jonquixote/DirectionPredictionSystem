# Track 3 Verdict — Maker/spread-capture is NO-GO on Kalshi 15m books (2026-07-11)

**Sim:** `probe/track3/maker_sim.py` (selftest 9/9). Replay of 856,348 usable two-sided
books across **10,301 settled windows, 7 coins, 17 days** (archives + hot db, Jun 25 →
Jul 11). Registered fill model: strict trade-through only (opposing touch must cross
*through* our price between consecutive 10s snapshots), fills at our price, maker fee
0.0175·e·(1−e), quotes cancelled across >60s feed gaps, terminal inventory settles at
`windows.kalshi_result` (Track 1 settlement backfill). Artifacts:
`/data/logs/track3/track3_sweep_*.txt`.

## Result: every sweep cell loses ~$1,000/day at minimum size

| offset | cap | PnL/day | Sharpe_d | fills/day | adverse% (post-fill 2c/60s) |
|---|---|---|---|---|---|
| join | 5 | −1,110 | −3.66 | 18,288 | 66% |
| 1c | 5 | −1,040 | −3.64 | 16,736 | 66% |
| **1.5c** | **5** | **−956 (best)** | −3.66 | 15,225 | 67% |
| 1.5c | 20 | −1,002 | −3.62 | 16,172 | 67% |

(Full 3×3 table in the artifact; all 9 cells between −$956 and −$1,187/day at
size = 1 contract/side/coin.)

Per-fill economics: ≈ **−6c per fill** against ≤1.5c of earnable spread and ~0.4c fee.
Diagnostic with a quote filter (only quote when book spread ≤ 5c, i.e. exclude the
garbage-wide early-window books): best cell −$912/day — **the loss is not a wide-book
artifact; it is structural adverse selection.** 66% of fills see the mid continue ≥2c
against the position within 60s *after* the fill (measured from the fill snapshot, so
not tautological).

## Kill criteria (pre-registered in track3-maker-sim.md)
1. Best cell daily P&L ≥ $30/day → **FAIL** (−$956/day; not even the sign is right).
2. Stress-decile adverse ≤ 40% → **FAIL** (69%; full-sample 66%).
3. Fills ≥ 5/day/coin → pass (≈2,200/day/coin — fills are abundant; they are just toxic).

**NO-GO. No pilot.**

## Why (ties to Track 2)
Track 2 showed the Kalshi 15m mid does real, fast, spot-driven price discovery with no
exploitable lag. The flip side of "no lag for a taker" is "no shelter for a maker": a
passive quote near mid in a binary with a 15-minute fuse is run over every time spot
moves — ~16k trade-throughs/day across 7 coins. The wide, thin spreads resident makers
actually quote *are the market's answer*: quoting tighter than them (mid±1c) is the
winner's curse, and joining them still loses because at 10s requote latency you hold
stale quotes through every spot move.

## Fill-model honesty
Strict crossing UNDERSTATES fills (conservative on revenue) but the sim's 10s requote
cadence means quotes sit stale for up to 10s — a websocket maker requoting in
milliseconds would be picked off less. So the precise claim is: **a maker with ≥10s
quote latency loses ~$1k/day at minimum size; the sim cannot price a millisecond-latency
maker.** Given the loss is ~4× the entire earnable spread and 66% of flow is informed
(post-fill continuation), latency reduction would have to eliminate essentially all
pick-offs to reach breakeven — and that is an HFT infrastructure build (authenticated
websocket, colocation-grade requoting, trade feed), out of scope for this program.

## Program status after Track 3
- Track 1 (Kalshi residual): peek @ day 8 — 1,246/2,500 clusters, EV −4.4c,
  CI [−6.7, −2.2]. Dying on schedule; registered verdict 2026-07-28 (cron).
- Track 2 (latency-taker): KILLED 07-05.
- **Track 3 (maker): KILLED 07-11 (this doc).**
- Track 4 (signal discovery, vol/magnitude into Kalshi strike markets): the only open
  track. Requires a NEW collector (strike-market books are not logged today) — see
  README gating; start is a scope/infra decision (disk is at 98%).
