# Track 3 — Maker / Spread-Capture Simulation

**Mandate:** price the spread-capture business offline from logged Kalshi books before
any live order. Both venues calibrated ⇒ the spread is the only durable revenue.

## Data dependency (resolved)
Input = full book history: `/data/archives/kalshi_books_*.jsonl.gz` (complete, from
archive-then-prune) + hot 7-day `obs.orderbook_json`. ~8+ days × 7 coins × 10s books.

## Architecture
```
loader: archives+db -> per (coin, window) time-ordered book states {yes_bids, no_bids}
     -> replay clock at obs cadence (10s)
strategy: quote BOTH sides at mid ± offset, size=1 unit, requote each tick;
          inventory cap I: stop quoting the side that would exceed |I|
fill model (conservative, registered):
  - our quote fills when the OPPOSING side's touch crosses it between consecutive
    snapshots (trade-through), NOT merely touches — 10s snapshots can't see queue
    position; crossing is the defensible lower bound
  - fills at our price; maker fee 0.0175*e*(1-e); forced unwind at window close:
    settle at contract result (settlement labels from Track 1 backfill)
accounting: per-fill PnL, inventory path, adverse-selection flag
  (mid moves >=2c against within 60s of fill), per-window and per-day rollups
```

## Parameter sweep
offset ∈ {0.5c, 1c, 1.5c} × inventory cap ∈ {5, 10, 20} × 7 coins
(0.5c offset only where tick grid allows; Kalshi tick = 1c ⇒ 0.5c ≈ join-the-touch).

## Outputs
Per cell: expected daily P&L, max drawdown, Sharpe (daily), fill rate, adverse-selection
rate, required capital (cap × worst entry × concurrent windows), capacity ceiling
(fills/day × size before self-competition — bounded by observed traded volume/day,
`obs.volume` deltas). Stress: P&L on the top-10%-realized-vol windows separately.
Deliverable: sweep table + go/no-go with pilot capital requirement.

## Honest limitations (state in the report)
10s snapshots understate fills (no intra-snapshot flow) and overstate queue priority;
both biases push conservative except adverse selection, which snapshots UNDERSTATE —
the stress cut + trade-through fill rule are the mitigations. A live pilot at minimum
size is the only true fill-model calibration; the sim gates whether that pilot is worth it.

## Implementation steps & timeline
1. Loader + replay skeleton (day 1–2)
2. Fill model + accounting + unit tests on synthetic books (day 2–4)
3. Sweep + stress + report (day 4–7)
Effort: ~3–5 focused days, all offline. Start after Track 2 verdict (day 2) unless
verdict says latency-taker, in which case still run — maker vs taker compete on numbers.

## Kill criteria (pre-registered)
- Best sweep cell daily P&L < $30/day at realistic capacity → no-go (not worth ops risk).
- Adverse-selection rate > 40% of fills in the stress decile with no offset/cap cell
  fixing it → no-go.
- Fill rate so low (<5 fills/day/coin at 1c offset) that revenue is noise → no-go.
