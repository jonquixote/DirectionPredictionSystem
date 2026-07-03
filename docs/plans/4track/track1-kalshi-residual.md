# Track 1 — Close the Kalshi Residual

**Mandate:** passively resolve the +1.8c-point / not-yet-significant Kalshi long-bias
(X=0.60, 8d: EV +0.0179, clustered-95 [−0.0154,+0.0528], 609 clusters). No new
infrastructure; discipline + data fixes only.

## Architecture / data flow
```
kalshi_fade_logger (existing, 10s poll) ──> /data/kalshi_fade.db
                                              ├─ obs (books; blobs archived+pruned daily)
                                              └─ windows (ticker, spot_open/close, kalshi_result)
Kalshi GET /markets?status=settled ──daily──> backfill_settlements.py ──> windows.kalshi_result
prereg_track1.json (FROZEN) ──> decide_track1.py ──2026-07-28──> PASS/FAIL verdict
```

## Components (all in `probe/track1/`, deployed)
1. **`prereg_track1.json`** — the decision rule as config, frozen 2026-07-03. Rule:
   first-fire per window, up_mid ≥ 0.60, |dev| < 5bps, phase ≤ 0.8, entry = 1−yes_bid,
   taker fee; outcome = settlement only (`result=='no'` ⇒ down wins; unsettled excluded);
   cluster bootstrap by boundary_ts, 5000 resamples; **PASS iff CI-low > 0 at ≥2,500
   clusters**. Amendments prohibited — a change is a new file.
2. **`backfill_settlements.py`** — paginates settled markets per series (verified:
   `result=yes` ⇒ UP won), joins on `windows.ticker`, idempotent, daily cron 03:40Z.
   Also prints spot-proxy vs settlement agreement (diagnostic only).
3. **`archive_prune_books.py`** — archive-then-prune (see README conflict note).
   Watermark-incremental gzip JSONL to `/data/archives/`; NULLs blobs > 7d; freed
   pages are reused by inserts so the db plateaus (~600MB) without VACUUM. Daily 03:30Z.
4. **`decide_track1.py`** — end-to-end registered test. Refuses a verdict below
   2,500 clusters (exit 2 = peek/diagnostics only; no early stopping). Cron one-shot
   2026-07-28 04:30Z writes verdict to `/data/logs/track1_decision.log`.

## Timeline & effort
Deployed today (~0 ongoing effort). Clusters accrue ~76/day → ≥2,500 ≈ 2026-07-28.

## Kill criteria
- Registered FAIL (CI includes 0 at ≥2,500 clusters) → close thread, stop kalshi logger.
- Data-integrity kill: settlement backfill match rate < 95% of closed windows, or
  spot-proxy/settlement disagreement > 5% (would indicate a resolution-basis bug —
  halt and investigate before the verdict date).
- Logger uptime < 90% over the accrual window → extend the date, don't lower the bar.

## Dependency out
PASS escalates the Track 2 latency-taker branch from "spec" to "build with sizing".

## Day-0 peek (2026-07-03, diagnostics only — no verdict per prereg)
Settlement backfill: **5,122/5,122 windows matched.** Spot-proxy disagrees with
settlement on **11.0%** of windows (561) — the proxy's `spot_open` is polled up to
10s AFTER the boundary, a mechanical bias that inflated fade EV. With settlement
labels the registered rule reads **EV −0.0384/share, clustered-95 [−0.0692, −0.0061]
(611 clusters, 2,599 fires)** — the "+1.8c residual" inverts and the CI excludes
zero on the NEGATIVE side. The registered test still runs to 2,500 clusters on
2026-07-28 (cost: zero), but PASS would now require a ~+5c swing — treat the thread
as near-certainly dead. Fourth instance of the program's one lesson: label/price
provenance decides the sign (p_market → spot-proxy → same failure class).
