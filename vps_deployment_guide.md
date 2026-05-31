# VPS Architecture & Deployment Guide — V3

This is the canonical operational guide for the live trading system on the VPS. It covers how the system works, how to deploy changes, and what lives where.

> **Important context for AI agents:** The active codebase is `/home/johnny/ofi-lab-v3/`. Any directory named `ofi-lab-v2-ARCHIVED`, `polymarket-ofi-clone`, or similar is **historical only** — do not read from, edit, or deploy to those directories.

> **Last updated:** 2026-05-31 after the post-retrain hardening sprint (lock resilience, bg-leader election, predictions UI timing, Home page upgrade).

---

## 1. Server Access

The system runs on a Google Cloud VM (`vps-n2`), accessed via raw SSH.

- **IP Address:** `34.67.75.48`
- **Username:** `johnny`
- **SSH Key:** `~/.ssh/id_vps_n2`
- **Public hostname:** `bet.octavo.press` (HTTPS)

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48
```

---

## 2. Architecture Overview

```
                    ┌─────────────────────────────────────────────────┐
                    │              VPS (34.67.75.48)                  │
                    │                                                 │
  HTTPS :443 ──▶   │   Nginx (SSL termination, 300s read timeout)    │
                    │     ├── /             → /var/www/dps-dash/      │
                    │     ├── /api/kalshi/* → :8081 (dashboard proxy  │
                    │     │                        → :8080 trader)    │
                    │     └── /api/*        → :8081 (dashboard)       │
                    │                                                 │
                    │   systemd services:                             │
                    │     v3-ws-feed ───▶ Bybit L2 WebSocket          │
                    │     v3-paper-trader ─▶ 168 models, port 8080    │
                    │     v3-dashboard ──▶ uvicorn --workers 2, :8081 │
                    │     dps-retention.timer ─▶ 03:30 UTC daily      │
                    │                                                 │
                    │   Data:                                         │
                    │     /data/v3.db ── SQLite, WAL mode, ~1.1 GB    │
                    │     /data/models/fleet/ ── 168 LightGBM .lgb    │
                    │     /data/features_v3/ ── daily parquet         │
                    │     /data/parquet/ ──── 68GB historical         │
                    │                                                 │
                    │   Cron (johnny):                                │
                    │     00:30 UTC daily — daily features pipeline   │
                    │     02:00 UTC Thursday — weekly fleet retrain   │
                    │     08:00 UTC daily — snapshot_observe          │
                    └─────────────────────────────────────────────────┘
```

All three Python services run from `/home/johnny/ofi-lab-v3/` using the venv at `.venv/`.

The dashboard frontend is a Vite/React SPA in the `dashboard/` git submodule, built locally and served as static files by nginx.

---

## 3. Service Management

### Systemd Services

| Service | Port | What it does |
|---------|------|-------------|
| `v3-ws-feed` | — | Subscribes to Bybit L2 orderbook WebSocket, writes to shared memory + parquet |
| `v3-paper-trader` | 8080 | Loads ~168 active models, generates predictions every 5 min on contract boundaries, runs Kalshi live dispatch (when enabled) |
| `v3-dashboard` | 8081 | uvicorn (`--workers 2`) serving the FastAPI API. Hosts six background loops (refresh, precompute, rollup, tier scoring, governance ×2, WAL checkpoint) — but **only one worker runs them** via an advisory file lock |
| `dps-retention.timer` | — | Nightly DB retention sweep at 03:30 UTC. Prunes decay_evaluations / model_tier_score / decision_traces / analysis_cache rows past their retention window. Does **not** touch predictions or paper_trades. |

### Background-loop leader election

Both uvicorn workers run the FastAPI lifespan. Without coordination they would each spawn the heavy loops, doubling WAL contention. On startup each worker tries `fcntl.LOCK_EX | LOCK_NB` on `/tmp/v3-dashboard-bg.lock`. The first acquires it and runs:

- `_analysis_precompute_loop` (every 5 min)
- `_rollup_loop` (every 10 min)
- `_tier_scoring_loop` (every 60 min)
- `_governance_probation_loop` (every 15 min)
- `_governance_action_loop` (every 30 min)
- `_wal_checkpoint_loop` (every 5 min — runs `PRAGMA wal_checkpoint(PASSIVE)`)
- `_cutover_scheduler_loop` (every 60 s — promotes scheduled cutovers)

The other worker logs `worker N deferring background loops to leader (...)` and only serves foreground requests. Look for these lines in `journalctl -u v3-dashboard` after a restart to confirm only one leader exists.

### Common Commands

```bash
# Check status
systemctl status v3-paper-trader v3-dashboard v3-ws-feed dps-retention.timer

# View live logs
journalctl -u v3-paper-trader -f
journalctl -u v3-dashboard -f

# Restart a service
sudo systemctl restart v3-paper-trader
sudo systemctl restart v3-dashboard            # both workers; lock auto-releases
sudo systemctl restart v3-ws-feed v3-paper-trader v3-dashboard

# Check the kalshi runtime API directly (bypass dashboard proxy)
curl -H "Authorization: Bearer $DASHBOARD_PASS" http://127.0.0.1:8080/kalshi/status
```

> **The 30-Minute Warmup Rule:** After restarting `v3-paper-trader`, the WebSocket streams reset. The feature pipeline requires 30 minutes of live L2 data to calculate Moving Average Deviations (MAD). **The model will skip all predictions for 30 minutes after a restart.**

> **Resolution price buffer:** `resolution_checker.check_trades` uses the trader's in-memory `rows_1s` buffer (~40-min window). Any open paper_trade whose `ts_resolve_at_ms` is older than 45 min gets auto-abandoned (`pnl_method='abandoned_stale_no_price'`). After a trader restart, expect a one-time spike of abandons clearing trades whose resolution window predates the restart.

### Service Files

Located at `/etc/systemd/system/v3-*.service` and `/etc/systemd/system/dps-retention.{service,timer}`. After editing:

```bash
sudo systemctl daemon-reload
sudo systemctl restart <service-name>
```

---

## 4. Deploying Backend Changes

The VPS does **not** use `git pull`. Code is pushed from the local Mac via `rsync` or single-file `scp` for surgical hotfixes.

### Option A — Full sync (preferred for multi-file changes)

```bash
rsync -avz --delete \
  --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
  --exclude='data' --exclude='.pytest_cache' --exclude='*.pyc' \
  -e "ssh -o StrictHostKeyChecking=no -i ~/.ssh/id_vps_n2" \
  /Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3/ \
  johnny@34.67.75.48:/home/johnny/ofi-lab-v3/
```

### Option B — Single-file scp (for hotfixes)

```bash
scp -i ~/.ssh/id_vps_n2 \
  /Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3/trading/paper_trader.py \
  johnny@34.67.75.48:/tmp/paper_trader.py

ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
  "sudo cp /home/johnny/ofi-lab-v3/trading/paper_trader.py /home/johnny/ofi-lab-v3/trading/paper_trader.py.bak-\$(date +%Y%m%d_%H%M%S) && \
   sudo cp /tmp/paper_trader.py /home/johnny/ofi-lab-v3/trading/paper_trader.py && \
   sudo systemctl restart v3-paper-trader"
```

### Post-rsync hygiene

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 "
  chmod +x /home/johnny/ofi-lab-v3/deploy/cron/*.sh
  find /home/johnny/ofi-lab-v3 -name '__pycache__' -exec rm -rf {} + 2>/dev/null
"
```

### Restart + smoke

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 "
  sudo systemctl restart v3-paper-trader v3-dashboard
  sleep 12
  systemctl is-active v3-paper-trader v3-dashboard
  journalctl -u v3-dashboard --since '30 seconds ago' | grep -E 'bg_leader|ready'
"
```

> **Direction matters:** Always sync **local → VPS**, never VPS → local. Running rsync in reverse will overwrite un-deployed local changes.

---

## 5. Deploying Frontend Changes

The frontend lives in the `dashboard/` git submodule. Build locally, then rsync to nginx root.

### Step 1: Build

```bash
cd /Users/johnny/Code/DirectionPredictionSystem/dashboard
npm run build
```

Output: `dist/index.html` + `dist/assets/*`.

### Step 2: Deploy

```bash
rsync -e "ssh -i ~/.ssh/id_vps_n2" -avz \
  /Users/johnny/Code/DirectionPredictionSystem/dashboard/dist/ \
  johnny@34.67.75.48:/tmp/dash_dist/

ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 "
  sudo rsync -a --delete /tmp/dash_dist/ /var/www/dps-dash/
  sudo chown -R www-data:www-data /var/www/dps-dash/
"
```

Hard-refresh the browser (`Cmd+Shift+R`) after deploy to pick up the new hashed bundle.

### Submodule commit flow

```bash
# inside dashboard/
cd /Users/johnny/Code/DirectionPredictionSystem/dashboard
git add src/...
git commit -m "..."
# (push is best-effort; the submodule has no remote configured)

# bump pointer in parent + push
cd /Users/johnny/Code/DirectionPredictionSystem
git add dashboard
git commit -m "v3 dashboard: bump submodule pointer for ..."
git push origin v3-dashboard-upgrade
```

---

## 6. Environment & Secrets

The canonical environment file is `/etc/v3/env` (mode 640, owned root:johnny). All three systemd services load it via `EnvironmentFile=/etc/v3/env`.

Key variables (redacted):

```env
V3_ENV=prod
V3_DB_PATH=/data/v3.db
STORAGE_DB_PATH=/data/v3.db
V3_KILL_SWITCH_PATH=/data/v3_kill_switch.json
V3_ADMIN_SECRET=<redacted>
DASHBOARD_USER=admin
DASHBOARD_PASS=<redacted>            # Also the bearer token for the trader runtime API
DASHBOARD_PASSWORD=<redacted>         # Synonym kept for compatibility
PYTHONPATH=/home/johnny/ofi-lab-v3:/home/johnny/ofi-lab-v3/dashboard_api

# Background-loop kill switches (operator-toggled during retrain or incident)
V3_GOVERNANCE_ACTIONS_PAUSED=0        # 1 disables governance probation + action loops
V3_ANALYSIS_PRECOMPUTE_ENABLED=1      # 0 disables the 5-min compute_full_report warm-up
V3_ROLLUP_LOOP_ENABLED=1              # 0 disables the 10-min predictions_daily_rollup loop
V3_ANALYSIS_DEFAULT_HISTORY_DAYS=30   # bound for default since_ms on analysis endpoints
```

After editing `/etc/v3/env`, restart the affected services. The dashboard background-loop kill switches only take effect at process start (not reloaded on the fly).

---

## 7. Cron Jobs

The schedule lives in johnny's user crontab (`crontab -l`):

| Schedule | Script | What it does |
|----------|--------|-------------|
| `30 0 * * *` | `deploy/cron/v3-daily-features.sh` | Downloads yesterday's orderbook data, builds features through v1→v2→v3 pipeline. Includes smart backfill: fills gaps up to 7 days (or 30 days on cold start). |
| `0 2 * * 4` | `deploy/cron/v3-weekly-retrain.sh` | Weekly fleet retrain. Waits for `/data/training_ready.json`, then runs `scripts/train_fleet.py --parallel 1 --buffer-days 0`. ~25h end-to-end. |
| `0 8 * * *` | `scripts/snapshot_observe.sh` | Snapshots `/data/observe/` for the observation dashboard. |

Logs:

- Daily features → `/data/logs/daily_features.log`
- Weekly retrain → `/data/logs/weekly_retrain.log`
- Snapshot observe → `/data/observe/cron.log`

> **Weekly retrain is cron-driven, NOT a systemd timer.** `systemctl list-timers v3-weekly-retrain` will show nothing — that's expected.

---

## 8. Data Layout

### Active (do not delete)

| Path | What | Approx size |
|------|------|------|
| `/data/v3.db` | SQLite with WAL — predictions, paper_trades, model_registry, model_window_tier, predictions_daily_rollup, model_tier_score, governance_actions, decay_metrics, decay_evaluations, retrain_queue, analysis_cache, cell_governance, calibration_bins, model_selection | ~1.1 GB |
| `/data/v3.db-wal` | Write-ahead log. Auto-checkpoints at 200 pages + an explicit `PRAGMA wal_checkpoint(PASSIVE)` every 5 min from the dashboard. Typical 5–50 MB. | varies |
| `/data/models/fleet/` | ~168 LightGBM model files (`.lgb`) across the 2026-05-06 + 2026-05-12 fleets | ~420 MB |
| `/data/features_v3/` | Daily parquet feature files per symbol | ~700 MB |
| `/data/features_v2/` | Intermediate rolling features (v3 pipeline depends on these) | ~2 GB |
| `/data/features/` | Base features from L2 orderbook (v2 pipeline depends on these) | ~1.2 GB |
| `/data/parquet/` | Raw historical orderbook data | 68 GB |
| `/data/calibration*.json` + `/data/calibration_*_outcomes.jsonl` | Per-(model, symbol, window) calibration bins + raw outcomes for live trading | ~500 MB |
| `/data/kalshi_private_key.pem` | RSA key for Kalshi API signing | small |

### Key tables

| Table | What | Retention |
|-------|------|-----------|
| `predictions` | Every model prediction at every boundary, with resolve timing | indefinite (archive, never prune) |
| `paper_trades` | Synthetic trades for every dispatched prediction. `pnl_method='abandoned_stale_no_price'` marks unresolveable rows. | indefinite |
| `model_registry` | Per-model row with `tier`, `paper_active`, `live_eligible`, `fleet_version`, `probation_end_at`. Legacy scalar `tier` mirrored alongside per-window `model_window_tier`. | indefinite |
| `model_window_tier` | Per-(model, market_window_seconds) tier (gold/silver/watch/retired) + Kelly multiplier. Phase 57 per-window architecture. | indefinite |
| `predictions_daily_rollup` | Daily aggregates (n, n_correct, sum_pnl, etc.) per (model, symbol, window, date). Backs the leaderboard fast path. | indefinite |
| `model_tier_score` | Hourly tier-score snapshots for trend analysis | 30 days (retention cron) |
| `governance_actions` | Audit log for every promote / demote / retire / sync action | 30 days |
| `decay_metrics` + `decay_evaluations` | Per-boundary decay state + per-trigger evaluation rows | 14 days |
| `decision_traces` | Per-dispatch decision audit | 30 days |
| `analysis_cache` | Persistent cache of slow compute_full_report / leaderboard / skip_conditions results. 15–30 min TTL. | 24 hours |
| `retrain_queue` | Cells flagged by auto-demote logic as needing retrain | drained by weekly retrain |

### Deprecated (historical)

| Path | What |
|------|------|
| `/data/logs/` | V2-era JSONL predictions + paper trades + misc logs |
| `/data/logs_model_a/`, `logs_model_b/`, `logs_model_a_clone/` | V2 model JSONL logs |

---

## 9. Directory Layout on VPS

```
/home/johnny/
├── ofi-lab-v3/                ← ACTIVE — all services run from here
│   ├── trading/               ← paper_trader.py, resolution_checker.py, metric_writers.py
│   ├── dashboard_api/         ← FastAPI app, routers/, services/, main.py with bg loops
│   ├── storage/               ← db.py (open_database + PRAGMAs), schema.sql, decay_writer.py
│   ├── scripts/               ← train_fleet.py, promote_model.py, retention_cron.py, gold_miner_report.py
│   └── deploy/                ← systemd/ + cron/ shell scripts
├── ofi-lab-v3-staging/        ← staging files for testing
├── ofi-lab-v2-ARCHIVED/       ← V2 Docker codebase (see ARCHIVED.md)
├── backups/                   ← Kalshi orders backup
└── docs_artifacts/            ← design docs
```

---

## 10. Nginx Configuration

Location: `/etc/nginx/sites-enabled/dps-dash`.

```
server bet.octavo.press (HTTPS :443)
  proxy_read_timeout 300s         ← bumped from 30s for slow analysis endpoints
  proxy_buffering on

  / → /var/www/dps-dash/index.html (SPA — try_files $uri /index.html)
  /assets/ → /var/www/dps-dash/assets/ (1y immutable cache)
  /api/kalshi/* → 127.0.0.1:8081 (dashboard proxies to trader :8080 via httpx, 30s timeout)
  /api/kalshi-orders → 127.0.0.1:8081/api/kalshi/orders (legacy rewrite)
  /api/* → 127.0.0.1:8081 (v3-dashboard FastAPI)
```

SSL via Let's Encrypt (`certbot.timer` auto-renews).

After editing nginx:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## 11. Operational Knobs

### SQLite hardening (in `storage/db.py:open_database`)

```python
sqlite3.connect(db_path, timeout=30.0, isolation_level=None)
PRAGMA journal_mode = WAL
PRAGMA foreign_keys = ON
PRAGMA synchronous = NORMAL
PRAGMA busy_timeout = 30000          # 30 s lock-wait before OperationalError
PRAGMA wal_autocheckpoint = 200      # ~800 KB checkpoint trigger (default 1000)
```

Plus the explicit `PRAGMA wal_checkpoint(PASSIVE)` every 5 min from the dashboard leader.

### Trader lock resilience (in `trading/paper_trader.py:_contract_boundary_loop`)

Three callsites are wrapped in `try/except sqlite3.OperationalError` so a single locked tick logs a warning and continues instead of crashing the whole process:

- `self._run_predictions(...)`
- `self._check_trade_resolutions_v3(...)`
- `self._check_prediction_resolutions_v3(...)`

### Kalshi proxy timeout (in `dashboard_api/routers/kalshi_proxy.py`)

`httpx.AsyncClient(timeout=30.0)`. Distinguishes `ReadTimeout`/`ConnectTimeout`/`WriteTimeout` → HTTP 504 with descriptive body. Generic exceptions → HTTP 502 with class name + message.

### Analysis-cache TTLs (in `dashboard_api/services/analysis.py`)

- In-memory `_CACHE`: 5-min TTL, prune-on-put past 2× TTL, hard cap 256 entries.
- `_load_resolved_predictions` snaps default `since_ms` to a 5-min boundary so cache keys stabilize across precompute cycles (previously each ms-precision timestamp pinned a fresh ~50k-row payload).
- `compute_full_report` + `compute_skip_conditions` persistent-cache TTL = **30 min** (outlasts a slow precompute cycle).
- `recommend_premium_filter` persistent-cache TTL = **30 min**. Uses a compact 360-combo grid (vs. the 2880-combo `DEFAULT_GRID`) for ~8× faster cold compute.

---

## 12. Disabled Services (May 2026)

These were stopped and disabled because they had zero active workload:

| Service | Why disabled | RAM freed | Re-enable |
|---------|-------------|-----------|-----------|
| Docker + containerd | Zero containers running; V3 uses systemd | 127 MB | `sudo systemctl enable --now docker containerd` |
| PostgreSQL | Zero application databases | 72 MB | `sudo systemctl enable --now postgresql` |
| Redis | Zero keys stored | 13 MB | `sudo systemctl enable --now redis-server` |

Docker images are preserved on disk (~21 GB) as a V2 rollback option.

---

## 13. Common diagnostics

```bash
# Service health snapshot
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 '
  systemctl --no-pager --lines=0 status v3-dashboard v3-paper-trader v3-ws-feed | grep -E "Active:|Memory:|Main PID"
  free -h | head -3
  ls -lh /data/v3.db-wal
'

# Recent trader crashes / lock hits
journalctl -u v3-paper-trader --since "2 hours ago" | grep -iE "Fatal|sqlite locked|abandon"

# Recent dashboard loops
journalctl -u v3-dashboard --since "30 minutes ago" | grep -E "precompute|rollup|wal checkpoint|tier scor|govern"

# Bg-leader confirmation
journalctl -u v3-dashboard --since "5 minutes ago" | grep -E "bg_leader|leader lock|deferring"

# DB row write rates by table (last 15 min)
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'python3 << "PYEOF"
import sqlite3, time
db = sqlite3.connect("/data/v3.db")
now = int(time.time()*1000); since = now - 900000
for tbl, col, kind in [
    ("predictions", "ts_contract_open_ms", "ms"),
    ("paper_trades", "ts_contract_open_ms", "ms"),
    ("decay_evaluations", "ts", "iso"),
    ("decay_metrics", "ts", "iso"),
    ("model_tier_score", "ts", "iso"),
    ("governance_actions", "ts", "iso"),
]:
    if kind == "ms":
        n = db.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {col} > ?", (since,)).fetchone()[0]
    else:
        n = db.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {col} > datetime(\"now\",\"-15 minutes\")").fetchone()[0]
    print(f"  {tbl:25s} {n:>8d}")
PYEOF
'
```

### `governance_actions.ts` lexicographic-compare gotcha

`governance_actions.ts` stores ISO timestamps with a `T` separator (e.g. `2026-05-30T15:25:00Z`). `model_registry.probation_end_at` may have either `T` or space (`2026-05-30 11:39:18`). String comparisons like `ts > datetime('now','-1 hour')` will incorrectly include `T`-form rows because ASCII `T (84)` > space `(32)`. Use:

```sql
ts > strftime('%Y-%m-%dT%H:%M:%SZ', datetime('now','-1 hour'))
```

The cutover scheduler tolerates both formats; manual queries do not.

---

## Appendix: V2 Docker Reference (Historical)

The V2 system ran inside Docker container `h300-retrain-shifted-v2-clone` (image `golden-goose-v2:clone`). It was decommissioned in May 2026. The archived codebase is at `/home/johnny/ofi-lab-v2-ARCHIVED/`.

Key V2 facts (for reference only):

- Container had ~20 env vars injected via `docker run`
- Mounted `/data` → `/data` for persistent state
- Kill switch was memory-only, toggled via `curl` to port 8080
- Required `docker cp` + `docker restart` for hot-patch deploys
- Required full `docker build` + `docker run` for dependency changes
