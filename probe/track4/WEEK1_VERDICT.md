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

---

# Validation appendix (2026-07-19, adversarial pass)

Ran validate_week1.py (full output /data/logs/track4_validate.log on prod).

**Verdict robustness — no estimator flips it:**
| estimator | 95% CI |
|---|---|
| cluster bootstrap (registered) | [−3.55c, +0.06c] |
| iid bootstrap | [−2.58c, +0.34c] |
| fire-weighted cluster | [−3.16c, +1.03c] |
| 5σ-trimmed cluster | identical (no outliers drive it) |

**Latency robustness:** re-pricing every fire at the NEXT poll's book (~30s later)
gives −1.20c vs −1.14c — fires were not stale-quote illusions; the loss is real
and not execution-window sensitive.

**Calibration (the mechanism of death):** model p is directionally informative
but compressed vs reality (p .86 → realized .95; p .06 → realized .03), while
the market mid is better calibrated overall (Brier .1074 vs model .1119).
Fires are the rows where the model disagrees with the market; conditional on
disagreement the market wins often enough that spread+fee eats the residual.
No-side fires lose more (−1.6c, n=1,059) than yes-side (−0.6c, n=827).

**Post-hoc pattern (NOT evidence, sampling-biased by construction):** claimed-EV
buckets are monotone: 2–4c → −2.1c, 4–6c → −1.6c, 6–8c → −1.4c, 8–10c → −0.8c,
≥10c → **+3.0c (n=239)**. A successor prereg could test EV≥10c as the fire gate
on fresh forward data. This subgroup was selected after seeing outcomes — it
would need to survive its own forward week.

**Range (B-type) extension — properly labeled, unregistered:** evaluated the
excluded range markets with correct range probabilities
(p = P(≥floor) − P(≥cap), same frozen model): 1,648 fires, mean **−1.9c**,
CI [−4.0c, −0.7c]. Pooled T+B: n=3,534, mean −1.5c, CI [−3.6c, −0.5c] —
strictly negative. The doubled sample confirms death; no resurrection story.

**Process notes:** (1) The week couldn't have been skipped — it was the forward
data. But model+evaluator should have been built at freeze time (07-11); that
would have caught the B-ticker contamination and the unreachable ≥200-cluster
bar before collection started. (2) Candle-fetch integrity: interior gaps ~50/yr
(exchange maintenance), skipped windows were future timestamps only.
