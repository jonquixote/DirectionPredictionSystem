# Track 4 week-1 EV checkpoint — VERDICT: DEAD

Prereg: `track4-vol-magnitude-into-kalshi-strikes` (frozen 2026-07-11T08:10Z).
Checkpoint instant: 2026-07-18T08:00Z. Computed 2026-07-19 (late compute, legal:
forward-only design — model trained exclusively on pre-collector Coinbase 1m
candles, evaluated exclusively on books logged before the checkpoint instant).

## Registered result (threshold markets, venue-settled)

| metric | value | rule |
|---|---|---|
| fires | 1,886 | ≥50 ✓ (no extension clause) |
| mean P&L/contract | **−0.0114** | must be >0 ✗ |
| cluster-95 CI | **[−0.0355, +0.0006]** | CI-low must be >0 ✗ |
| effective clusters | 165 | ≥200 ✗ (note: unreachable — week 1 contains only 165 close_ts) |
| win rate | 21.5% | — |
| median fired spread | 1.0c | kill if >3c ✓ pass |
| collector uptime | 99.9% | kill if <80% ✓ pass |
| model EV at fire (claimed) | +0.0562 | realized −0.0114 → miscalibrated at tails |

**VERDICT: DEAD — week-1 CI-low ≤ 0.** Registered kill criterion fires; prereg
forbids "one more week". The vol-clustering signal is real (MI screen passed) but
does not translate into executable edge over Kalshi threshold prices at EV≥2c:
the model systematically overestimates tail probabilities relative to the market
(21.5% win rate on fires with mean claimed edge +5.6c).

## Label-artifact incident (methods note, important for any successor prereg)

First computation produced **+18.8c/contract, CI [+16.9, +19.4]** — a spurious
PASS-magnitude result. Cause: `strike_logger.discover()` collected both T-type
(threshold) and B-type (range) tickers (`floor_strike or cap_strike`), and the
label `1{spot_close ≥ strike}` is meaningless for ranges — 51% of fires were
range markets. The prereg's mandated venue-settlement verification caught it:
26% label disagreement on a 250-fire sample, venue-settled P&L −0.9c.

Corrected run: threshold tickers only (`-T<strike>$`), payouts from actual
Kalshi settlement results (all 1,886 fires settled), Coinbase-label agreement
98.1% (residual = settlement-index vs Coinbase-spot differences; fine for
threshold labels, and irrelevant once venue results are used for P&L).

Lesson frozen into any Track-5 prereg: instrument filters must be enforced at
the collector AND the evaluator; range (B-type) markets need range labels.

## Program state

- T1: decision cron fires 2026-07-28 04:30Z on Linode (peek EV −4.2c → expect NO-GO).
- T2, T3: dead (prior verdicts).
- T4: **dead** (this document).
- Strike collector left running (0.5 GB/mo archives) pending decision on a
  successor hypothesis; stop `track4-strike-logger` if none within ~a week.

Artifacts: /data/logs/week1_run.log (invalid first run), week1_run2.log
(corrected), track4_week1_fires.json, track4_settlements.json,
/data/models/track4_week1.joblib.
