# Track 2 — Latency Structure Probe

**Mandate:** measure how long PM/Kalshi books take to reprice after Coinbase moves.
Mechanistic question behind the Kalshi residual; the verdict branches the build.

## Architecture / data flow
```
every ~1.25s (parallel threads, ms timestamps):
  Coinbase GET /ticker x7  ─┐
  Kalshi GET /orderbook x7 ─┼─> ticks(ts_ms, coin, cb_bid/ask, k_yes_bid/ask,
  PM CLOB POST /books x1  ─┘         pm_up_bid/ask, per-source latency, boundary_ts)
                                    └─> /data/track2_latency.db (WAL, ~60MB/day)
caches: Kalshi open ticker (60s TTL), PM up-token ids (per 15m boundary, 1 gamma req)
deadline exit + restart-wrapper until deadline
```
Rate budget @1.25s: Coinbase ≈5.6 req/s, Kalshi ≈5.6 req/s, PM ≈0.8 req/s — within
public limits. PM `POST /books` verified live (7 books, ~280ms, server `timestamp`).

## Components (in `probe/track2/`, deployed)
- **`latency_logger.py`** — as above; `--deadline` unix-ts; summary rows only.
- **`analyze_latency.py`** — event = |Δcb_mid| ≥ 5bps between consecutive polls;
  reprice = venue mid moved ≥1c IN the spot direction (2c sensitivity line);
  censored at 60s / window close. Outputs median/p75/p95 per venue + per coin.
  **Spec deviation, declared:** "50% of the Coinbase move" is ill-defined across
  bps-of-price vs probability space; ≥1-tick directional is the operational primary.
- Run: tmux `track2`, 50h deadline (48h data + margin), started 2026-07-03.

## Verdict (registered thresholds)
- median lag > 2.5s (≥2 polls) → **latency-taker viable**; write taker spec
  (websocket feed, direct-post order path, per-fire sizing from Track-1 economics).
- median ≤ ~1.25s (≤1 poll) → efficient at pollable timescales → **Track 3 primary**.
- 1–2 polls → ambiguous → escalate instrumentation (Kalshi websocket
  `orderbook_delta` channel — needs API auth from ofi-lab creds) before any build.
Quantization caveat: poll cadence floors resolvable lag at ~1.25s; "<1s" is not
distinguishable from instant — by design the verdict boundary sits at ≥2 polls.

## Timeline
Day 0 start → day 2 analysis + verdict (~1h of work).

## Kill criteria
- ≥20% failed polls on any venue over the run → data unusable, rerun before verdict.
- <200 spot events ≥5bps in 48h (dead tape) → extend run 48h once; if still thin, park.
- Track is self-terminating: it produces a verdict, not a strategy.
