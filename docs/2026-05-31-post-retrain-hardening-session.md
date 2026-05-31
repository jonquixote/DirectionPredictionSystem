# Post-Retrain Hardening Session — 2026-05-28 → 2026-05-31

Multi-day operations + hardening sprint that started the night the weekly retrain finished and ran through the next ~72 hours of check-ins. Every change here was driven by something we observed in prod: a crash log, a stuck row, a UI bug, a 502, a memory spike.

**Branch:** `v3-dashboard-upgrade`
**Commits:** `05b8a3b` → `f56f9f6` (parent), `523d971` → `dacfc96` (dashboard submodule)
**Outcome:** trader and dashboard now survive ordinary contention without crashing; the Home and Predictions pages match the v3 data model; analysis page loads sub-second on warm cache; the per-window tier architecture is consistent with the live trader.

---

## Phase A — Unfreeze knobs (2026-05-29)

The retrain had been gated behind three env knobs in `/etc/v3/env` to relieve sqlite contention. Once the retrain finished, those needed to come back on.

| Knob | Before | After |
|---|---|---|
| `V3_ANALYSIS_PRECOMPUTE_ENABLED` | 0 | 1 |
| `V3_ROLLUP_LOOP_ENABLED` | 0 | 1 |
| `V3_ANALYSIS_DEFAULT_HISTORY_DAYS` | 7 | 30 |

After `sudo systemctl restart v3-dashboard`, the rollup loop caught up within ~10 minutes (`predictions_daily_rollup.updated_at` advanced past the stale 5h gap).

**Recommend-premium** was retested under post-retrain load and still returned HTTP 000 after 240s. Deferred for a deeper structural fix (delivered in Phase B-equivalents below).

---

## Phase B — Tier split-brain + trader reload bug

Plan thought: "Tier scoring isn't promoting anyone — 645/645 rows in `model_window_tier` were `watch`."

Reality on inspection: tier scoring **was** producing real scores into `model_tier_score` (45,518 rows from 2026-05-25 onward, range 0.03 – 0.16 for XRP). The split-brain was elsewhere.

### Root cause

A one-shot SQL on 2026-05-27 promoted 26 models to gold in `model_registry.tier` (tagged `manual:opus_primary_window`) but never mirrored the change into `model_window_tier`. The probation evaluator filters `WHERE tier='watch'` on the **legacy** column, so:

- It kept skipping the 26 gold registry rows (which still showed `watch` in mwt).
- The legacy column never got the live trader's per-window state, so the trader's `kelly_by_window` lookup found `watch` (kelly = 0) for models the operator thought were trading at full size.

### Fixes

**1. One-shot mwt backfill (commit `05b8a3b`)** — synced 26 rows from `model_registry.tier` into `model_window_tier` for their primary window. Tagged `auto:backfill_2026_05_29_mwt_sync` + a `governance_actions` audit row per change. Tier distribution went from `645/watch` to `gold:26 / watch:619`.

**2. Trader `_reload_fleet` change-detection patch (commit `05b8a3b`)** — `_reload_fleet` was constructing the correct `new_meta` from a fresh mwt read, but classifying mwt-only updates as `unchanged` and dropping the result. Added `kelly_by_window` + `tier_by_window` to the `changed_fields` diff. Verified post-deploy: `h60_btc_v3_179d_20260512 kelly_by_window={300: 1.0, ...} tier_by_window={300: 'gold', ...}`.

### Cutover

Once tier signal was consistent and the new 2026-05-12 fleet's `probation_end_at` (2026-06-01) passed, the **probation evaluator** auto-promoted models without manual intervention. By the next check-in: 84 / 84 active on the new fleet, 84 / 84 still serving on 2026-05-06 (transitional), predictions balanced ~50/50 between the two.

---

## Phase C — Housekeeping (2026-05-29)

**Retrain queue drain.** `retrain_queue` had 100 unpicked rows from 2026-05-27, all flagged `auto:no_incumbent`. Cross-checked each `cell_key` against the 2026-05-12 model registry — every row had a matching trained model. Deleted all 100 with a `governance_actions` audit per row (`triggered_by='auto:post_retrain_queue_drain_2026_05_29'`).

**Orphan predictions (Task #34).** 702 predictions referenced `900s_btc_v3_20260315`, a model that didn't exist in `model_registry`. Inserted a synthetic registry row with `tier='retired', lifecycle_state='retired', fleet_version='2026-03-15'` + audit row. Dashboard joins now resolve cleanly; the 702 historical predictions are preserved as raw evidence.

**Weekly retrain timer audit.** `systemctl list-timers v3-weekly-retrain` returned nothing — but the retrain *had* run successfully (`/data/logs/weekly_retrain.log` rc=0). Discovered weekly retrain is **cron-driven**, not systemd: `0 2 * * 4 /home/johnny/ofi-lab-v3/deploy/cron/v3-weekly-retrain.sh`. Task #62 (which marked the timer "complete") was misleading. Updated `vps_deployment_guide.md` to call this out.

---

## Memory leak: `_CACHE` unbounded growth (commit `1f1c92e`)

**Observed:** dashboard workers grew from ~340 MB at restart to **6.7 GB each** in 3 hours after re-enabling precompute. Combined ~13.4 GB out of 15 GB — minutes from OOM.

**Root cause:** `_cache_put` in `dashboard_api/services/analysis.py` never evicted anything. `_load_resolved_predictions` set the default `since_ms = now - 30d` to **millisecond** precision, so every call produced a fresh key that pinned a ~50k-row payload. Combined with the precompute loop hitting 13 (sym, win) pairs every 5 min, memory grew ~4 GB/hour.

**Fixes:**

- `_cache_put` now prunes entries past `2 × _CACHE_TTL_SECS` on every write and applies a hard cap of 256 entries (oldest-first eviction).
- `_load_resolved_predictions` snaps the default `since_ms` to a **5-min boundary** so consecutive precompute cycles share a key.

**Verified:** workers stable at ~430 MB combined after several precompute cycles with PRECOMPUTE re-enabled.

---

## Recommend-premium 504 → 68s cold / 0.5s warm (commit `1f1c92e`)

Three layers:

1. **Compact grid.** Added `_RECOMMEND_PREMIUM_GRID` with **360 combos** (vs. 2,880 in `DEFAULT_GRID`). Coarser confidence/EV sweeps, single blackout/RWEV variant. ~8× fewer Python iterations.

2. **Persistent cache, 30-min TTL.** `recommend_premium_filter` now snaps `since_ms` to a 30-min bucket and reads/writes `_CACHE` + `analysis_cache`. All three return paths (winner / no_winner / grid_empty) are cached so the no-data shape doesn't recompute on retry.

3. **In-memory cache snap from Phase memory-leak.** Sub-calls within a single `recommend_premium_filter` invocation share predictions via the snapped `_load_resolved_predictions` cache key.

**Verified:** discovery cold 68s, warm 0.5s (138× speedup). Strict cold 130s. Both well within the bumped 300s nginx timeout.

---

## Trader lock-crash → tick-level resilience (commit `0e17fc1`)

**Observed:** trader crashed three times across a 24-hour window with `sqlite3.OperationalError: database is locked` even after we'd bumped `busy_timeout` to 30 s. Each crash → systemd auto-restart → ~30-60 s of lost predictions during cold-start.

**Root cause:** the dashboard's 5-min tier-scoring tick performs batched INSERTs over hundreds of (model, window) triples. Under that pressure a concurrent trader INSERT can exceed even 30 s of busy-wait. The trader's main `try/except Exception` re-raised, exiting the process.

**Fix:** wrap the three boundary-loop callsites that touch the DB in `try / except sqlite3.OperationalError`:

- `self._run_predictions(...)`
- `self._check_trade_resolutions_v3(...)`
- `self._check_prediction_resolutions_v3(...)`

Lock errors now log `WARNING: <call> sqlite locked, skipping tick` and the loop continues to the next boundary. Catch generic `Exception` too so any other unexpected fault inside one tick doesn't tear down the whole process.

Verified across the next 24 hours: lock warnings appeared a handful of times but **zero process restarts** caused by them.

---

## Background-loop leader election (commit `b8bbb9f`)

**Observed:** with `uvicorn --workers 2`, both workers ran the FastAPI lifespan, doubling WAL pressure: 2× precompute, 2× rollup writes, 2× governance ticks, 2× tier scoring.

**Fix:** advisory file lock `/tmp/v3-dashboard-bg.lock` via `fcntl.LOCK_EX | LOCK_NB`. First worker becomes `bg_leader=True` and spawns the loops; the other logs `deferring background loops to leader` and only serves foreground requests.

Verified:

```
worker 389914 acquired background-loop leader lock
worker 389913 deferring background loops to leader ([Errno 11] Resource temporarily unavailable)
```

Side effects:

- WAL growth halved.
- `tier_score` tick now writes 504 rows once an hour instead of 466 + 466 within 2 s.
- Probation evaluator no longer double-processes (which had been producing the "70× re-promotion per model" spam).

---

## WAL bloat → automatic checkpoint loop (commit `9b96670` + `b8bbb9f`)

**Observed:** `/data/v3.db-wal` peaked at **114 MB** between sessions. Default `wal_autocheckpoint = 1000` (≈4 MB) couldn't keep up with combined predictions + tier_score + rollup + decay write rate. Long WAL = long lock-wait = more "database is locked" crashes.

**Fixes:**

1. `PRAGMA wal_autocheckpoint = 200` in `storage/db.py:open_database` — 5× more frequent triggers (~800 KB threshold).
2. New `_wal_checkpoint_loop` in `dashboard_api/main.py` (bg-leader only) runs `PRAGMA wal_checkpoint(PASSIVE)` every 5 min. PASSIVE never blocks active writers, so it's safe alongside the trader's write path.

Verified post-deploy: WAL 114 MB → 133 KB after explicit `wal_checkpoint(TRUNCATE)` + first auto cycle. Steady state since: 5-50 MB peaks between checkpoints, then truncates.

---

## Probation re-promotion loop (commit `9b96670`)

**Observed:** `governance_actions` showed **630 promote actions / 9 distinct models** in 12 hours. The same 9 models were getting "promoted" every 15-min tick — ~36 redundant writes per hour, each producing a `governance_actions` audit row + an idempotent `model_window_tier` UPDATE.

**Root cause:** `_update_mwt_primary` (in `dashboard_api/main.py`) updated `model_window_tier.tier` + `kelly_multiplier` and set `model_registry.tier_assigned_at` as a breadcrumb — but **never wrote `model_registry.tier` itself**. The probation eligibility query was `WHERE tier = 'watch'` on `model_registry`, so the same 9 watch-tagged rows kept being re-selected forever even though their mwt was already silver/retired.

**Fix:** mirror the new tier + Kelly into `model_registry` alongside the mwt update:

```python
conn.execute(
    "UPDATE model_registry SET tier = ?, kelly_multiplier = ?, "
    "tier_assigned_at = ?, tier_assigned_by = 'auto:probation_evaluator' "
    "WHERE name = ?",
    (new_tier, _TIER_KELLY[new_tier], now_iso, model),
)
```

Verified next tick (15:25 UTC): 9 distinct promote actions on **9 distinct models** (1:1). Subsequent 8h: **zero** probation actions because no eligible challengers remained.

---

## Trades resolution stall + auto-abandon (commits `6f5b240`, `2fbd767`, `ba46707`, `f56f9f6`)

**Observed sequence:**

1. Late-night spot check: 25,107 `paper_trades` with `resolved=0` and `ts_resolve_at_ms` deep in the past. Oldest from 2026-05-17 (13 days stale).
2. Trader log was silent — no errors, no warnings.

**Root cause:** `resolution_checker.check_trades` calls `feature_computer.price_at(symbol, ts_resolve_at_ms)`, which scans an in-memory `rows_1s` buffer (~40-min window). Anything older than that raises `KeyError`. The original code silently `continue`d on `KeyError | AttributeError`, accumulating an invisible backlog. After every trader restart, all trades whose resolve_at predated the restart became permanently unresolveable.

**Fixes (incremental):**

1. **Visibility (commit `6f5b240`).** Count `skipped_no_price` per call. Emit `WARNING` when ≥50 skipped or when any skip happens with zero resolves.

2. **One-shot DB cleanup.** Marked 24,919 rows older than 40 min as `resolved=1, pnl_method='abandoned_stale_no_price', net_pnl=0`. CHECK constraint forced `resolution_type='evaluation'` (couldn't use a synthetic value).

3. **Auto-abandon inside the resolution path (commit `2fbd767`).** `check_trades` now does an `UPDATE … WHERE resolved=0 AND ts_resolve_at_ms < now_ms - cutoff` at the top of every call. Prevents the backlog from re-accumulating after every restart.

4. **Cutoff tightening (commits `ba46707`, `f56f9f6`).** First cut was 60 min — but the buffer is only ~40 min, leaving a 20-min window where trades got skipped every second but didn't get abandoned for another 20 min (saw 386 stuck rows). Tightened to **45 min** (buffer + 5 min clock-skew margin). Log message updated to match.

5. **Warning rate-limit (commit `2fbd767`).** Added `self._last_skip_warn_ts` so the no_price warning fires at most once per 5 min. Otherwise a cold trader spammed the journal for the 40 min it took the buffer to fill.

Verified post-fixes: 233 + 153 stale rows auto-abandoned on the first ticks after restart, then stable at zero. Skip warnings stopped spamming.

---

## Predictions page — market-window timing display (commits `523d971`, `2af89f0`)

**User report:** "predictions with 4:40 (2340 UTC) next to them and they say resolved but it's 4:42. They shouldn't resolve until the market ends which is a minimum of 5 minutes."

**Root cause (display-only):** `PredRow` in `dashboard/src/pages/Predictions.tsx` rendered `fmtTime(ts_model_ran_ms)` next to a `✓ / ✗` outcome badge driven solely by `prediction_correct`. It ignored `ts_resolve_at_ms` (when the contract actually closes) and never checked whether the market window had elapsed. DB itself was correct — zero rows with `ts_resolve_at_ms > now AND resolved = 1`.

Compounding: the backend's `_enrich_prediction` in `dashboard_api/routers/predictions.py` was **dropping** `ts_contract_open_ms`, `ts_resolve_at_ms`, `ts_resolved_ms`, `resolved`, and `prediction_correct` from the response — so the frontend couldn't have rendered the timing even if it wanted to.

**Fixes:**

1. **Backend (commit `2af89f0`).** Expose the missing timing fields from `_enrich_prediction`.

2. **Frontend (commit `523d971`).** `PredRow` now renders three lines:

   ```
   model · SYMBOL · 300s
   opened HH:MM:SS · direction · conf X.XXX
   closes HH:MM:SS · Nm    ← or "resolved HH:MM:SS"
   ```

   Outcome badge (`✓ / ✗ / realized_net`) only renders once `now >= ts_resolve_at_ms AND resolved = true`. Until then the badge is `⋯`.

3. **Pending filter.** Wired `ActivityFilters`' `status` segment (resolved / pending / all) into Predictions.tsx with URL state. `status=pending` maps to `outcome='unresolved'` on the API.

Verified: `/api/predictions/count?outcome=unresolved` → 32,020 pending. `/api/predictions` now returns the timing fields. Rows show "closes 23:45:00 · 3m" while in-flight, then "resolved 23:45:00 +$0.0734" after the contract closes.

---

## Home page upgrade — hybrid perf + system (commits `0da1848`, `dacfc96`, `998608b`)

The Home page hadn't been touched since v2. It still rendered a 2x2 stats grid + 5 recent trades, none of which surfaced the v3 fleet, tier, or governance state.

**Direction (chosen via AskUserQuestion):** Hybrid — top half performance, bottom half system.

**Sections:**

- Hero (kept): Kalshi balance + delta + since timestamp + DataFreshness + source/range filters.
- **Stats grid 2×3** (expanded): kalshi delta · paper p&l · resolved trades · win rate · pending (→ Predictions filtered) · active models (→ Models).
- **Top performers**: top 5 by `total_pnl_usdc` from `/api/analysis/leaderboard`, deduplicated by `model_name` (best (symbol, window) per model), with `n_resolved_trades ≥ 5`. Each row links to ModelDetail.
- **System**: fleet badges, tier totals (g/s/w/r summed across windows), 24-h governance summary (↑promotes · ↓demotes · ×retires) + last 4 inline gov_actions, retrain queue pending, last retrain ts.
- Recent trades (kept).

### Bug found post-deploy

Top performers initially showed "no resolved trades in window" even with active trading. `api.performance` was hitting `/performance` (no trailing path), which v3 had **removed** (404). The handler silently returned null, so every dependent stat showed `—`.

**Fix (commit `998608b`):**

- Source stats grid and top performers from `/api/analysis/leaderboard` (rollup-backed, fast).
- Add `api.predictionsCount({outcome:'unresolved'})` helper hitting `/predictions/count` for the pending stat.
- Default Home source to `paper` (kalshi balance had a separate 502 issue, see next item).

Verified: top 3 paper performers right after deploy — `h900_sol_v3 SOL 30m +$28.21 57%`, `h1200_sol_v3 SOL 15m +$19.76 54%`, `h600_eth_v3 ETH 30m +$19.36 59%`.

---

## Kalshi proxy 502 → real balance (commit `998608b`)

**Observed:** `/api/kalshi/balance` returned HTTP 502 with body `{"error":"proxy error: "}` (empty exception message).

**Root cause:** `dashboard_api/routers/kalshi_proxy.py` used `httpx.AsyncClient(timeout=10.0)`. The Kalshi REST balance call takes 7-15 s under normal load, regularly tipping into proxy timeout. The generic `except Exception` clause's `str(e)` was empty for httpx timeouts, producing the cryptic body.

**Fix:**

- Bump httpx client timeout 10 s → **30 s**.
- Catch `httpx.ReadTimeout` / `ConnectTimeout` / `WriteTimeout` separately → HTTP **504** with body `{"error":"proxy timeout after 30s: ReadTimeout"}` so the frontend can render an actionable message.
- Generic `Exception` body now includes class name + message.

Verified: `/api/kalshi/balance` → 200 in 12.8 s with real `$0.07` balance. Home hero now populates.

---

## Analysis page (already shipped earlier in sprint)

Several layers landed in commits `b8bbb9f`, `c34e627`, `1f1c92e` that I want to call out explicitly because they were major:

- **`compute_leaderboard` rollup default.** When the caller didn't pass `since_ms`, the function fell through to the slow 50k-row live aggregate instead of the rollup fast path. Now defaults `since_ms` to `now - 30d` snapped to 5 min, engaging the rollup.
- **Persistent cache TTL** for `full_report` / `skip_conditions` bumped from 5 min to **30 min**. The precompute cycle's ALL/ALL view itself iterates every pair, so a full cycle takes 10-15 min on a 78k-row XRP/300 set; 5-min TTL was racing the cycle and serving stale-as-miss on the heaviest pair.

Verified post-deploy:

| Endpoint | Before | After |
|---|---|---|
| `full-report` BTC/300 | 30 s timeout | 0.35 s |
| `full-report` ETH/900 | 30 s timeout | 0.15 s |
| `full-report` SOL/1800 | 30 s timeout | 0.24 s |
| `full-report` XRP/300 | 30 s timeout | 0.10 s |
| `leaderboard` | 30 s timeout | 1.12 s |
| `skip-conditions` BTC/300 | 30 s timeout | 0.12 s |

---

## Inventory of changes (concise)

### Code (`ofi-lab-v3/`)

| File | Change |
|---|---|
| `storage/db.py` | `timeout=30`, `busy_timeout=30000`, `wal_autocheckpoint=200` |
| `dashboard_api/main.py` | bg-leader file lock, `_wal_checkpoint_loop`, probation `_update_mwt_primary` mirror to `model_registry.tier`, shutdown handles None tasks |
| `dashboard_api/services/analysis.py` | `_CACHE` prune-on-put + 256-entry cap, `_load_resolved_predictions` snap to 5-min boundary, `compute_leaderboard` default `since_ms`, full_report/skip_conditions persistent cache 30 min, `recommend_premium_filter` compact grid + 30-min cache |
| `dashboard_api/routers/kalshi_proxy.py` | httpx timeout 10 s → 30 s, separate ReadTimeout/etc. → 504 |
| `dashboard_api/routers/predictions.py` | `_enrich_prediction` exposes `ts_contract_open_ms`, `ts_resolve_at_ms`, `ts_resolved_ms`, `resolved`, `prediction_correct`, `contract_duration_seconds` |
| `trading/paper_trader.py` | `import sqlite3`, `_reload_fleet` change-detection adds `kelly_by_window`/`tier_by_window`, `_contract_boundary_loop` try/except OperationalError on three callsites |
| `trading/resolution_checker.py` | skipped_no_price counter + 5-min rate-limited warn, auto-abandon trades older than 45 min |

### Dashboard submodule (`dashboard/`)

| File | Change |
|---|---|
| `src/lib/api.ts` | added `predictionsCount` helper |
| `src/pages/Home.tsx` | full rewrite: hybrid perf + system, sourced from leaderboard / predictionsCount / modelsSummary / governanceActions / trainingQueue |
| `src/pages/Predictions.tsx` | `PredRow` renders market-window timing, pending status filter wired |

### DB operations (one-shot)

| Op | Rows | When |
|---|---|---|
| Backfill mwt from registry gold | 26 | 2026-05-29 |
| Drain stale `retrain_queue` | 100 | 2026-05-29 |
| Synthetic registry row for `900s_btc_v3_20260315` | 1 | 2026-05-29 |
| Clean stale unresolved paper_trades | ~25,000 | 2026-05-30 |
| Manual `wal_checkpoint(TRUNCATE)` | — | 2026-05-30 |

All ops audited via `governance_actions` rows.

### Ops knobs touched

- `/etc/v3/env` — `V3_ANALYSIS_PRECOMPUTE_ENABLED=1`, `V3_ROLLUP_LOOP_ENABLED=1`, `V3_ANALYSIS_DEFAULT_HISTORY_DAYS=30`.
- Nginx `proxy_read_timeout 30s` → `300s` (already in place from earlier work; reconfirmed).

---

## What's still pending

- **Recommend-premium cold** is 68-130 s. Acceptable now that the result is cached for 30 min, but if we ever need it faster the next layer is moving the compute to a background task with a poll endpoint.
- **Trader resilience covers boundary loop**, not every codepath. Hot reload + decay refresh + reconcile_lifecycle still have their own narrower `try/except` blocks. If any of those raises something other than `OperationalError`, the trader can still exit. Worth a future audit pass.
- **Per-window tier promotion semantics** — probation evaluator promotes only the *primary* window. Models that perform well on a non-primary window are not auto-promoted there. Plan tracked as a separate ticket.
- **WS-feed crash recovery.** The feed has been up 2 weeks straight; if it dies during retrain we don't have a redundant ingestion path. Lower priority.

---

## How to read the system after a restart

A clean restart of `v3-dashboard` should produce, within ~30 s:

```
worker N1 acquired background-loop leader lock
Dashboard API ready — background refresh + ... loops started (bg_leader=True)
worker N2 deferring background loops to leader ([Errno 11] Resource temporarily unavailable)
Dashboard API ready — ... (bg_leader=False)
```

Within ~5 min: `wal checkpoint: busy=0 log_pages=... ckpt_pages=...`

Within ~10 min: `rollup loop: upserted N rows across 2 dates`

A clean trader restart produces, within ~10 s:

```
Paper Trader starting
fleet_hot_reload: added model ... (or unchanged=N)
phase57 sample: <model> kelly_by_window=... tier_by_window=...
Kalshi live trader initialized
Runtime API server listening on 0.0.0.0:8080
First L2 data received — MAD warmup starts (30 min)
```

Within ~1 s of being able to resolve trades: `check_trades: auto-abandoned N trades older than 45min` (one-time cleanup), then the loop goes quiet.

If you see `Fatal error: database is locked` in the trader journal at any point after these changes — that's a bug in `_contract_boundary_loop`, not expected behavior.
