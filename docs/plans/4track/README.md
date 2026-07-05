# 4-Track Research Program — Overview & Cross-Track Rules (2026-07-03)

Closing the crypto-prediction program (PM fade = p_market artifact, KILLED on 1.4M
executable obs; see `probe/PMARKET_ARTIFACT_FINDING.md`) and pivoting to four tracks.
Governing lesson: **measurement infrastructure before model infrastructure; EV-gate
against executable prices before any claim counts.**

## Tracks & gating

```
Track 1 (Kalshi residual)  RESULT: settlement labels INVERT it to -3.8c [-6.9,-0.6];
        near-certainly dead (registered test still runs free to 2026-07-28).
Track 2 (latency probe)    VERDICT 2026-07-05: NO causal repricing lag (controls show
        SAME==OPP==PLACEBO, Kalshi hit-rate 50%). Latency-taker KILLED.
        Structural: Kalshi discovers price; PM 15m book is FROZEN at 0.50.
        -> Track 3 PRIMARY, re-scoped KALSHI-ONLY (PM has no flow to make on).
Track 3 (maker sim, offline, KALSHI books) — PRIMARY build; depends on BOOK ARCHIVES
Track 4 (signal discovery) — cross-venue arb DEPRIORITIZED (PM frozen/non-executable);
        lead with vol/magnitude into Kalshi strike markets
```
Both Track 1 & 2 returned negative. See `TRACK2_VERDICT.md`.

## Resolved dependency conflict (important)
The instructed disk prune ("drop book_json older than 7 days") would have **destroyed
Track 3's replay input** — the 713MB of Kalshi books IS `obs.orderbook_json`.
Resolution implemented in `probe/track1/archive_prune_books.py`: **archive-then-prune** —
all blobs append to compressed JSONL in `/data/archives/` (full history, ~10:1
compression) before any NULLing. Track 3 reads archives + the 7-day hot window.

## Cross-track rules (all four)
1. **One provenance-tagged executable price per prediction.** No silent fallbacks —
   the `get_p_market` stale-cache fallback manufactured a fake +12pp edge. If the
   executable price is unavailable, the prediction does not fire; fail loud.
2. **Labels from venue settlement** (`windows.kalshi_result`, backfilled daily), not
   derived spot, wherever available. Spot-proxy only as a reported diagnostic.
3. **Dedup at write time** — one row per window; warmup excluded at schema level.
4. **Any positive-EV claim must state:** n, effective clusters (not raw fires),
   cluster-robust CI (cluster = boundary_ts; coins co-move), executable price source.

## State (2026-07-03)
- Loggers UP: kalshi_fade (195h, 97.7% uptime), pm_depth (173h, 100%).
- Disk 97% (6.5G free): mitigated by daily archive+prune cron (see Track 1).
- Track 1 prereg frozen: `probe/track1/prereg_track1.json` (X=0.60, settlement
  outcomes, cluster bootstrap, verdict at >=2,500 clusters, est. 2026-07-28).
- **Day-0 peek:** settlement labels flip the residual to **−3.8c, CI [−6.9c,−0.6c]**
  (spot-proxy had 11% label error). Thread near-certainly dead; registered test
  still runs (free). Track 2 verdict now decides the program's primary build.
- Track 2 logger deployed, 48h clock started (see Track 2 doc for run details).

## Timeline
| when | what |
|---|---|
| day 0 (today) | archive+prune live; settlements backfilled; Track 2 logger started |
| day 2 | Track 2 analysis + verdict -> branch decision (taker spec vs Track 3 primary) |
| day 2-10 | Track 3 replay engine + sweep (offline) ; Track 4 MI screen + prereg |
| day 10-17 | Track 4 week-1 kill-or-continue checkpoint |
| ~day 25 (2026-07-28) | Track 1 registered decision at >=2,500 clusters |
