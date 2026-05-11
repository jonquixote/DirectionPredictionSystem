# v3 Production Deployment — Hard Cutover Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for sequenced manual execution. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Hard cutover from v2 (`ofi-lab/`) to v3 (`ofi-lab-v3/`) on the production server. No paper-shadow phase — system is not currently live-trading real money, so simpler cutover is acceptable.

## VPS environment (per `docs_artifacts/vps_deployment_guide.md` or repo root `vps_deployment_guide.md`)

- **Host:** `34.67.75.48` (Google Cloud VM, "vps-n2")
- **SSH:** `ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48`
- **User:** `johnny` (existing — do NOT create new `v3` user; ignore deploy plan steps that create one)
- **v2 layout:**
  - Code on host: `/home/johnny/ofi-lab/`
  - Runs inside Docker container `h300-retrain-shifted-v2-clone` (image `golden-goose-v2:clone`)
  - Container has Kalshi env vars baked in via `docker run --env-file` — do NOT `docker rm`
  - Container mounts host `/data` → container `/data`
  - v2 kill switch is **in-memory only** — disable via API before `docker stop` (or env var `KALSHI_LIVE_ENABLED=0` on boot)
  - 30-min warmup after any restart (no predictions)
- **v3 layout (new, bare-metal alongside docker v2):**
  - Code on host: `/home/johnny/ofi-lab-v3/` (parallel to v2)
  - Runs bare metal via systemd, NOT in docker (avoid env-var rebuild complexity)
  - DB: `/data/v3.db` — separate file, coexists with v2 artifacts in `/data`
  - Port: dashboard on **8081** (v2 holds 8080)
  - Sync method: `rsync` (same pattern as v2), or `git clone` of the v3 repo. Recommend `git clone` for v3 because we have proper tags.
  - Do NOT touch `/data/models/`, `/data/kalshi_orders.jsonl`, `/data/kalshi_private_key.pem` — those are v2 state. v3 has its own `/data/models/v3/` if needed.

**Architecture:** Stop v2 cleanly. Take a backup. Init v3 schema on prod server. Run backfills. Bring v3 services up via systemd. Verify dashboard + paper trader healthy. Keep v2 code on disk for 30 days for rollback insurance.

**Tech Stack:** systemd, nginx, sqlite3, Python 3.13 venv, journald logs.

**Pre-deploy state (verified 2026-05-10):**
- Plan A complete (tag `v3-plan-a-foundation-complete`)
- Plan B complete (C1–C9 wiring closed; C3–C8 + calibrator wiring landing via background haiku — see Phase 0)
- Plan C complete (dashboard, API, kill switch, rollback, audit; 320+ tests green)
- Hardening complete: persistent `V3_ADMIN_SECRET`, HTTP Basic Auth on POST routes, systemd units in `deploy/systemd/`, nginx config in `deploy/nginx/`, backup script in `deploy/cron/`
- Backfill scripts ready: `scripts/backfill_regime.py`, `scripts/backfill_calibration.py`, `scripts/preflight_v3.py`

---

# Phase 0 — Pre-cutover blockers

These must clear before the cutover window.

## Task D0.1: Close pyright diagnostics on agent-#1 test files

Background haiku is finishing C3–C8 + calibrator wiring. New diagnostics flagged real bugs in its generated test files:

- `tests/test_lifecycle_loop.py:31,63` — `evaluate_lifecycle_transitions` import path wrong.
- `tests/test_dynamic_range_integration.py:25,35,52` — `lookback_days` kwarg name mismatch; `_tracked_symbols` attr doesn't exist on `PaperTrader`.
- `tests/test_decay_refresh_wiring.py:21,31` — cosmetic (unused asyncio, unused loop var).
- `tests/test_backfill_calibration.py` — `from scripts.backfill_calibration` fails because `scripts/` lacks `__init__.py`. Add it or use `importlib`.

- [ ] **Step 1: Wait for agent #1 to return.** When it does, run:

```bash
cd ofi-lab-v3 && source .venv/bin/activate
pytest tests/test_lifecycle_loop.py tests/test_dynamic_range_integration.py tests/test_decay_refresh_wiring.py tests/test_backfill_calibration.py -v
```
If any fail, fix per diagnostics above. If all pass, pyright noise is IDE-only and can be dismissed.

- [ ] **Step 2: Full suite green**

```bash
pytest tests/ -q --ignore=tests/test_gmadl.py 2>&1 | tail -5
```
Expected: 0 failures, 0 errors. Pass count ≥ 327.

- [ ] **Step 3: Tag**

```bash
git tag v3-deploy-ready
git push --tags
```

## Task D0.2: Server prerequisites

- [ ] **Step 1: SSH access + sudo**

Confirm `ssh server` works and the operator account has sudo.

- [ ] **Step 2: Install OS packages**

```bash
ssh server "sudo apt-get update && sudo apt-get install -y \
  python3.13 python3.13-venv python3-pip \
  sqlite3 libsqlite3-dev \
  libomp-dev \
  nginx certbot python3-certbot-nginx \
  systemd"
```
(Adjust for Debian/Ubuntu vs RHEL.)

- [ ] **Step 3: Create dedicated user**

```bash
ssh server "sudo useradd -r -m -d /opt/v3-home -s /bin/bash v3"
```

- [ ] **Step 4: Create dirs + perms**

```bash
ssh server "sudo mkdir -p /opt/ofi-lab-v3 /etc/v3 /data /backups /var/log/v3 && \
            sudo chown -R v3:v3 /opt/ofi-lab-v3 /data /backups /var/log/v3 && \
            sudo chmod 750 /etc/v3"
```

## Task D0.3: Secrets prepared

- [ ] **Step 1: Generate `V3_ADMIN_SECRET`**

Locally:
```bash
cd ofi-lab-v3 && ./scripts/generate_admin_secret.sh
```
Copy the 64-char hex output. Do not commit, do not paste into Slack.

- [ ] **Step 2: Generate dashboard Basic Auth creds**

Pick a strong password for `DASHBOARD_USER=admin`. Use `pwgen -s 32 1` or similar.

- [ ] **Step 3: Populate `/etc/v3/env` on server**

```bash
ssh server "sudo -u v3 tee /etc/v3/env" <<EOF
V3_ENV=prod
V3_ADMIN_SECRET=<paste-from-step-1>
V3_KILL_SWITCH_PATH=/data/kill_switch.json
V3_DB_PATH=/data/v3.db

DASHBOARD_USER=admin
DASHBOARD_PASS=<paste-from-step-2>

BYBIT_API_KEY=<from-1password>
BYBIT_API_SECRET=<from-1password>
KALSHI_EMAIL=<from-1password>
KALSHI_PASSWORD=<from-1password>
EOF
ssh server "sudo chmod 600 /etc/v3/env && sudo chown v3:v3 /etc/v3/env"
```

- [ ] **Step 4: Verify perms**

```bash
ssh server "ls -la /etc/v3/env"
```
Expected: `-rw------- 1 v3 v3 ...`

---

# Phase 1 — Cutover window

**Estimated wall-clock: 30–45 min.** Schedule during a low-volatility window. Announce in any relevant channel ("v3 cutover starting, paper trader down for ~30 min").

## Task D1.1: Stop v2

- [ ] **Step 1: Capture v2 state for forensics**

```bash
ssh server "ps aux | grep -E 'paper_trader|ofi-lab' | grep -v grep > /tmp/v2-procs-pre-stop.txt"
ssh server "sqlite3 /data/v2.db '.tables' > /tmp/v2-tables.txt"
```

- [ ] **Step 2: Stop v2 cleanly**

If running under systemd:
```bash
ssh server "sudo systemctl stop v2-paper-trader v2-dashboard"
```

If running under tmux/screen, attach and Ctrl-C, then confirm:
```bash
ssh server "ps aux | grep paper_trader | grep -v grep"
```
Expected: empty.

- [ ] **Step 3: Final v2 DB backup**

```bash
ssh server "sqlite3 /data/v2.db '.backup /backups/v2-final-$(date +%Y%m%d-%H%M).db' && \
            gzip /backups/v2-final-*.db"
```

## Task D1.2: Deploy v3 code

- [ ] **Step 1: Push tag to remote**

Locally:
```bash
cd ofi-lab-v3 && git push origin v3-deploy-ready
```

- [ ] **Step 2: Clone on server**

```bash
ssh server "sudo -u v3 git clone https://github.com/<org>/<repo>.git /opt/ofi-lab-v3 && \
            cd /opt/ofi-lab-v3 && \
            sudo -u v3 git checkout v3-deploy-ready"
```

If repo private, use deploy key or token. If repo already exists from prior dry run:
```bash
ssh server "cd /opt/ofi-lab-v3 && sudo -u v3 git fetch --tags && sudo -u v3 git checkout v3-deploy-ready"
```

- [ ] **Step 3: Build venv**

```bash
ssh server "cd /opt/ofi-lab-v3/ofi-lab-v3 && \
            sudo -u v3 python3.13 -m venv .venv && \
            sudo -u v3 .venv/bin/pip install --upgrade pip && \
            sudo -u v3 .venv/bin/pip install -r requirements.txt"
```

If `requirements.txt` missing or stale, pin from local dev:
```bash
# locally
cd ofi-lab-v3 && pip freeze > /tmp/req.txt
scp /tmp/req.txt server:/tmp/
ssh server "sudo -u v3 cp /tmp/req.txt /opt/ofi-lab-v3/ofi-lab-v3/requirements.txt && \
            cd /opt/ofi-lab-v3/ofi-lab-v3 && \
            sudo -u v3 .venv/bin/pip install -r requirements.txt"
```

- [ ] **Step 4: Smoke import test**

```bash
ssh server "cd /opt/ofi-lab-v3/ofi-lab-v3 && \
            sudo -u v3 .venv/bin/python -c \
            'from trading.paper_trader import PaperTrader; from dashboard_api.main import app; print(\"OK\")'"
```
Expected: `OK`. Any import error blocks cutover — fix here, do not proceed.

## Task D1.3: Init v3 DB

- [ ] **Step 1: Create fresh v3 DB**

```bash
ssh server "cd /opt/ofi-lab-v3/ofi-lab-v3 && \
            sudo -u v3 .venv/bin/python -c \
            'from storage.db import open_database, init_schema; \
             c = open_database(\"/data/v3.db\"); init_schema(c); c.close(); print(\"schema ready\")'"
```

- [ ] **Step 2: Seed model registry from v2**

If v2 has a model registry or equivalent metadata, migrate it. Otherwise seed manually:

```bash
ssh server "sudo -u v3 sqlite3 /data/v3.db <<SQL
INSERT INTO model_registry (name, symbol, horizon, is_baseline, lifecycle_state,
                            paper_active, live_eligible, generation, created_at)
VALUES ('h300_btc', 'BTCUSDT', 900, 1, 'active', 1, 0, 0, strftime('%s','now')*1000);
SQL"
```
Add rows for any additional models you intend to run on day 1. Baseline first.

- [ ] **Step 3: Copy model artifacts**

The LightGBM `.txt` / `.pkl` / `.json` files from v2 must land at the path `model_registry.artifact_path` references.

```bash
ssh server "sudo -u v3 mkdir -p /data/models && \
            sudo -u v3 cp /opt/ofi-lab/models/h300_btc.txt /data/models/"
```
Adapt to actual artifact filenames.

- [ ] **Step 4: Run preflight**

```bash
ssh server "cd /opt/ofi-lab-v3/ofi-lab-v3 && \
            sudo -u v3 .venv/bin/python scripts/preflight_v3.py --db /data/v3.db"
```
Expected: `SUCCESS: All preflight checks passed.` Cold-start warnings about non-baseline models OK. Hard errors block cutover.

## Task D1.4: Install systemd units + nginx

- [ ] **Step 1: Copy unit files**

```bash
ssh server "sudo cp /opt/ofi-lab-v3/ofi-lab-v3/deploy/systemd/v3-paper-trader.service /etc/systemd/system/ && \
            sudo cp /opt/ofi-lab-v3/ofi-lab-v3/deploy/systemd/v3-dashboard.service /etc/systemd/system/ && \
            sudo systemctl daemon-reload"
```

- [ ] **Step 2: Install nginx site**

```bash
ssh server "sudo cp /opt/ofi-lab-v3/ofi-lab-v3/deploy/nginx/v3-dashboard.conf /etc/nginx/sites-available/v3-dashboard && \
            sudo ln -sf /etc/nginx/sites-available/v3-dashboard /etc/nginx/sites-enabled/v3-dashboard && \
            sudo nginx -t && \
            sudo systemctl reload nginx"
```

- [ ] **Step 3: Issue TLS cert (first time only)**

```bash
ssh server "sudo certbot --nginx -d v3-dashboard.<your-domain> --non-interactive --agree-tos -m <your-email>"
```
Skip if cert already in place.

- [ ] **Step 4: Install backup cron**

```bash
ssh server "sudo -u v3 crontab -l 2>/dev/null > /tmp/v3-cron; \
            echo '0 */6 * * * /opt/ofi-lab-v3/ofi-lab-v3/deploy/cron/v3-db-backup.sh' >> /tmp/v3-cron; \
            sudo -u v3 crontab /tmp/v3-cron && rm /tmp/v3-cron"
ssh server "sudo -u v3 crontab -l"
```
Verify the line is present.

## Task D1.5: Start v3 services

- [ ] **Step 1: Engage kill switch BEFORE starting paper trader**

Defensive default — first boot in killed state. Operator manually resumes after verifying dashboard.

```bash
ssh server "sudo -u v3 .venv/bin/python -c \
            'from dashboard_api.services import kill_switch_state as ks; \
             ks.engage(reason=\"first_boot_safety\", by=\"deploy\")'"
ssh server "ls -la /data/kill_switch.json"
```

- [ ] **Step 2: Start dashboard**

```bash
ssh server "sudo systemctl enable v3-dashboard && sudo systemctl start v3-dashboard"
sleep 3
ssh server "sudo systemctl status v3-dashboard --no-pager | head -20"
ssh server "sudo journalctl -u v3-dashboard --since '1 min ago' --no-pager | tail -30"
```
Expected: `active (running)`. No tracebacks in journal.

- [ ] **Step 3: Dashboard smoke test**

```bash
curl -fsS -u admin:<dashboard-pass> https://v3-dashboard.<your-domain>/api/kill_switch | python -m json.tool
```
Expected: `{"engaged": true, "state": {"reason": "first_boot_safety", ...}}`

```bash
curl -fsS -u admin:<dashboard-pass> https://v3-dashboard.<your-domain>/api/models/list | python -m json.tool
```
Expected: at least one model row, baseline first.

- [ ] **Step 4: Start paper trader (still kill-switched)**

```bash
ssh server "sudo systemctl enable v3-paper-trader && sudo systemctl start v3-paper-trader"
sleep 5
ssh server "sudo journalctl -u v3-paper-trader --since '1 min ago' --no-pager | tail -50"
```
Expected: trader starts, immediately logs `kill_switch_engaged_skipping_boundary` on each tick. No crashes.

- [ ] **Step 5: Resume from kill switch via UI**

Open `https://v3-dashboard.<your-domain>/models` in browser. Log in. Banner should show kill switch engaged. Click "Resume…", type `I CONFIRM`. Verify banner clears. Verify `/api/kill_switch` now returns `{"engaged": false}`.

Equivalent agent-native curl:
```bash
TOK=$(curl -fsS -u admin:<pass> -X POST -H 'Content-Type: application/json' \
  https://v3-dashboard.<your-domain>/api/admin/confirm_intent \
  -d '{"action":"kill_switch_resume","target":"global","by":"deploy"}' | jq -r .token)
curl -fsS -u admin:<pass> -X POST -H 'Content-Type: application/json' \
  https://v3-dashboard.<your-domain>/api/kill_switch/confirm_resume \
  -d "{\"by\":\"deploy\",\"confirmation_token\":\"$TOK\"}"
```

## Task D1.6: First-trade verification

- [ ] **Step 1: Wait for first boundary tick**

```bash
ssh server "sudo journalctl -u v3-paper-trader -f --since '0 sec ago'"
```
Watch for prediction emission. At 900s boundary cadence, first tick within 15 min of resume.

- [ ] **Step 2: Confirm a prediction landed in DB**

```bash
ssh server "sqlite3 /data/v3.db 'SELECT COUNT(*) as n, MAX(boundary_ms) as last FROM predictions'"
```
Expected: `n >= 1`, `last` within last 16 min.

- [ ] **Step 3: Confirm regime tags populated**

```bash
ssh server "sqlite3 /data/v3.db 'SELECT symbol, vol_bucket, liq_bucket FROM predictions ORDER BY ts_model_ran_ms DESC LIMIT 5'"
```
Expected: non-null buckets.

- [ ] **Step 4: Confirm `/api/models/list` reports activity**

```bash
curl -fsS -u admin:<pass> https://v3-dashboard.<your-domain>/api/models/h300_btc | python -m json.tool | head -30
```
Expected: `lifecycle_state="active"`, `paper_active=True`, generation = 0.

- [ ] **Step 5: Observe for 1 hour**

Leave `journalctl -f` running for at least one full decay-refresh cycle (4 boundaries × 900s = 1 hour). Confirm:
- `decay_refresh_failed` does not appear.
- `decay_metrics` rows accumulating: `sqlite3 /data/v3.db 'SELECT COUNT(*) FROM decay_metrics'` increases.
- `model_overlap` rows accumulating (if ≥2 models active).

## Task D1.7: Announce cutover complete

- [ ] **Step 1: Update `MEMORY.md`**

```
- v3 hard cutover 2026-05-<DD> — v2 stopped, v3 live on /data/v3.db. v2 code retained at /opt/ofi-lab for 30-day rollback window.
```

- [ ] **Step 2: Notify channel**

"v3 cutover complete. Paper trader running, dashboard at https://v3-dashboard.<domain>. v2 retired but disk-preserved through <date+30d> for rollback."

---

# Phase 2 — Post-cutover (first 7 days)

## Task D2.1: Daily smoke (day 1, 3, 7)

- [ ] `sudo systemctl status v3-paper-trader v3-dashboard`
- [ ] `sqlite3 /data/v3.db 'SELECT COUNT(*) FROM predictions WHERE ts_model_ran_ms > strftime("%s","now","-24 hours")*1000'` — non-zero
- [ ] `sudo journalctl -u v3-paper-trader --since '24 hours ago' | grep -E 'ERROR|CRITICAL|Traceback' | head -20` — empty
- [ ] Check `/api/audit` for unexpected enable_live or rollback actions
- [ ] Verify nightly backup at `/backups/v3-*.db.gz` is fresh

## Task D2.2: Lifecycle FSM first run verification

After 16 boundaries × 900s = 4 hours, FSM runs first time.

- [ ] `sudo journalctl -u v3-paper-trader | grep lifecycle_transition` — review any transitions. None expected on day 1 with only baseline active.
- [ ] `sqlite3 /data/v3.db 'SELECT name, lifecycle_state FROM model_registry'` — baseline must remain `active`.

## Task D2.3: Add second model (optional, day 3+)

Once baseline stable, promote a second model to paper.

- [ ] Insert new row in `model_registry` (or load via `/api/models/<name>/reload` after staging artifact).
- [ ] `POST /api/models/<name>/enable_paper` via dashboard or curl.
- [ ] Watch `/api/overlap` populate as predictions overlap between models.

---

# Phase 3 — Rollback procedure (break-glass only)

Use only if v3 produces clearly wrong predictions, persistent crashes, or DB corruption.

- [ ] **Step 1: Engage kill switch immediately**

```bash
curl -fsS -u admin:<pass> -X POST -H 'Content-Type: application/json' \
  https://v3-dashboard.<your-domain>/api/kill_switch \
  -d '{"reason":"rollback_to_v2","by":"<your-name>"}'
```

- [ ] **Step 2: Stop v3**

```bash
ssh server "sudo systemctl stop v3-paper-trader v3-dashboard"
```

- [ ] **Step 3: Restore v2 from `/backups/v2-final-*.db.gz`**

```bash
ssh server "gunzip -c /backups/v2-final-<timestamp>.db.gz > /data/v2.db"
```

- [ ] **Step 4: Start v2 services**

```bash
ssh server "sudo systemctl start v2-paper-trader v2-dashboard"
```

- [ ] **Step 5: File post-mortem**

Capture v3 logs from journald, snapshot the bad v3 DB, write incident note. Do NOT delete v3 artifacts — needed for root-cause analysis.

---

# Self-Review

**Spec coverage:**
- Hard cutover (no shadow phase) → Phase 1 ✓
- Phase 0 covers prerequisites + secrets + outstanding wiring ✓
- Cutover window includes stop-v2, deploy-v3, init-db, start-services, verify ✓
- Kill switch engaged on first boot, manual resume gates real activity ✓
- Rollback procedure preserves v2 binaries + DB backup ✓
- Day 1/3/7 smoke checklist ✓

**Open dependency:** Phase 0.1 waits on the background haiku finishing C3–C8 + calibrator wiring. If it returns with NEEDS_CONTEXT, defer cutover and re-dispatch.

**Risk notes:**
- First boot kill-switch-engaged is intentional — prevents prediction emission until operator manually verifies the dashboard is reachable.
- Lifecycle FSM runs every 16 boundaries (4h) and could mass-suspend if backfilled EV/PSI data is missing. Preflight (D1.3 step 4) is the gate.
- `V3_ADMIN_SECRET` rotation is not in scope for cutover; schedule for 90-day cadence after stable.
