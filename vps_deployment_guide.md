# VPS Architecture & Deployment Guide — V3

This is the canonical operational guide for the live trading system on the VPS. It covers how the system works, how to deploy changes, and what lives where.

> **Important context for AI agents:** The active codebase is `/home/johnny/ofi-lab-v3/`. Any directory named `ofi-lab-v2-ARCHIVED`, `polymarket-ofi-clone`, or similar is **historical only** — do not read from, edit, or deploy to those directories.

---

## 1. Server Access

The system runs on a Google Cloud VM (`vps-n2`), accessed via raw SSH.

- **IP Address:** `34.67.75.48`
- **Username:** `johnny`
- **SSH Key:** `~/.ssh/id_vps_n2`

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48
```

---

## 2. Architecture Overview

```
                    ┌─────────────────────────────────────────────┐
                    │              VPS (34.67.75.48)              │
                    │                                             │
  HTTPS :443 ──▶   │   Nginx (SSL termination)                   │
                    │     ├── /             → /var/www/dps-dash/  │
                    │     ├── /api/kalshi/* → :8080 (paper trader)│
                    │     └── /api/*        → :8081 (dashboard)   │
                    │                                             │
                    │   systemd services:                         │
                    │     v3-ws-feed ────▶ Bybit L2 WebSocket     │
                    │     v3-paper-trader ─▶ 84 models, port 8080 │
                    │     v3-dashboard ───▶ FastAPI API, port 8081 │
                    │                                             │
                    │   Data:                                     │
                    │     /data/v3.db ─── SQLite (predictions,    │
                    │                     trades, model registry) │
                    │     /data/models/fleet/ ── 84 LightGBM .lgb │
                    │     /data/features_v3/ ── daily parquet     │
                    │     /data/parquet/ ──── 68GB historical     │
                    └─────────────────────────────────────────────┘
```

All three services run from `/home/johnny/ofi-lab-v3/` using the venv at `.venv/`.

---

## 3. Service Management

### Systemd Services

| Service | Port | What it does |
|---------|------|-------------|
| `v3-ws-feed` | — | Subscribes to Bybit L2 orderbook WebSocket, writes to shared memory |
| `v3-paper-trader` | 8080 | Loads 84 models, generates predictions every 5 min, runs Kalshi live trader |
| `v3-dashboard` | 8081 | FastAPI dashboard API, reads from SQLite, serves all `/api/*` endpoints |

### Common Commands

```bash
# Check status
systemctl status v3-paper-trader v3-dashboard v3-ws-feed

# View live logs
journalctl -u v3-paper-trader -f

# Restart a service (use sudo)
sudo systemctl restart v3-paper-trader

# Restart all V3 services
sudo systemctl restart v3-ws-feed v3-paper-trader v3-dashboard
```

> **The 30-Minute Warmup Rule:** After restarting `v3-paper-trader`, the WebSocket streams reset. The feature pipeline requires 30 minutes of live L2 data to calculate Moving Average Deviations (MAD). **The model will skip all predictions for 30 minutes after a restart.**

### Service Files

Located at `/etc/systemd/system/v3-*.service`. After editing:
```bash
sudo systemctl daemon-reload
sudo systemctl restart <service-name>
```

---

## 4. Deploying Backend Changes

The VPS does **not** use `git pull`. Code is pushed from the local Mac via `rsync`.

### Step 1: Sync code

```bash
rsync -avz --delete \
  --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
  --exclude='data' --exclude='.pytest_cache' --exclude='*.pyc' \
  -e "ssh -o StrictHostKeyChecking=no -i ~/.ssh/id_vps_n2" \
  /Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3/ \
  johnny@34.67.75.48:/home/johnny/ofi-lab-v3/
```

### Step 2: Post-rsync fixes

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 "
  # Ensure cron scripts keep execute permission
  chmod +x /home/johnny/ofi-lab-v3/deploy/cron/*.sh
  # Clear stale bytecode
  find /home/johnny/ofi-lab-v3 -name '__pycache__' -exec rm -rf {} + 2>/dev/null
"
```

### Step 3: Restart affected services

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 "
  sudo systemctl restart v3-paper-trader v3-dashboard
  sleep 3
  systemctl is-active v3-paper-trader v3-dashboard
"
```

> **Direction matters:** Always sync **local → VPS**, never VPS → local. Running rsync in reverse will overwrite un-deployed local changes.

---

## 5. Deploying Frontend Changes

The frontend is built locally from `dashboard/` and deployed as static files.

### Step 1: Build

```bash
cd /Users/johnny/Code/DirectionPredictionSystem/dashboard
npm run build
```

### Step 2: Deploy

```bash
# Rsync to temp directory (johnny can write there)
rsync -avz --delete \
  -e "ssh -o StrictHostKeyChecking=no -i ~/.ssh/id_vps_n2" \
  /Users/johnny/Code/DirectionPredictionSystem/dashboard/dist/ \
  johnny@34.67.75.48:/tmp/dps-dash-dist/

# Copy to nginx root (requires sudo for /var/www ownership)
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 "
  sudo rm -rf /var/www/dps-dash/*
  sudo cp -r /tmp/dps-dash-dist/* /var/www/dps-dash/
  sudo chown -R www-data:www-data /var/www/dps-dash/
"
```

The frontend is served by Nginx from `/var/www/dps-dash/` at `https://bet.octavo.press/`.

---

## 6. Environment & Secrets

The canonical environment file is `/etc/v3/env`. All three systemd services load it via `EnvironmentFile=/etc/v3/env`.

Key variables (redacted):
```
V3_ENV=prod
V3_DB_PATH=/data/v3.db
STORAGE_DB_PATH=/data/v3.db
V3_KILL_SWITCH_PATH=/data/v3_kill_switch.json
V3_ADMIN_SECRET=<redacted>
DASHBOARD_USER=admin
DASHBOARD_PASS=<redacted>
```

---

## 7. Cron Jobs

| Schedule | Script | What it does |
|----------|--------|-------------|
| 00:30 UTC daily | `deploy/cron/v3-daily-features.sh` | Downloads yesterday's orderbook data, builds features through v1→v2→v3 pipeline. Includes smart backfill: fills gaps up to 7 days (or 30 days on cold start). |

```
30 0 * * * /home/johnny/ofi-lab-v3/deploy/cron/v3-daily-features.sh >> /data/logs/daily_features.log 2>&1
```

Log: `/data/logs/daily_features.log`

---

## 8. Data Layout

### Active (do not delete)

| Path | What | Size |
|------|------|------|
| `/data/v3.db` | SQLite — predictions, paper_trades, model_registry, decay_metrics, calibration_bins, model_selection | ~75MB |
| `/data/models/fleet/` | 84 LightGBM model files (`.lgb`) | 206MB |
| `/data/features_v3/` | Daily parquet feature files per symbol | 477MB |
| `/data/features_v2/` | Intermediate rolling features (v3 pipeline depends on these) | 2.1GB |
| `/data/features/` | Base features from L2 orderbook (v2 pipeline depends on these) | 1.2GB |
| `/data/parquet/` | Raw historical orderbook data | 68GB |
| `/data/calibration.json` | Calibration bins for live trading | small |
| `/data/kalshi_private_key.pem` | RSA key for Kalshi API signing | small |

### Deprecated (historical, see DEPRECATED.md in each)

| Path | What |
|------|------|
| `/data/logs/` | V2-era JSONL predictions + paper trades + misc logs |
| `/data/logs_model_a/` | V2 model-a JSONL logs |
| `/data/logs_model_a_clone/` | V2 clone model JSONL logs |
| `/data/logs_model_b/` | V2 model-b JSONL logs |

---

## 9. Directory Layout on VPS

```
/home/johnny/
├── ofi-lab-v3/              ← ACTIVE — all services run from here
├── ofi-lab-v3-staging/      ← staging files for testing
├── ofi-lab-v2-ARCHIVED/     ← V2 Docker codebase (see ARCHIVED.md)
├── DirectionPredictionSystem/ ← stale rsync copy (not used by services)
├── polymarket-ofi-clone/    ← ancient V1 clone (not used)
├── backups/                 ← Kalshi orders backup
├── docs_artifacts/          ← design docs
└── venvs/                   ← old bot venv
```

---

## 10. Nginx Configuration

Location: `/etc/nginx/sites-enabled/`

```
bet.octavo.press (HTTPS :443)
  /                  → /var/www/dps-dash/index.html (SPA)
  /assets/           → /var/www/dps-dash/assets/ (1y cache)
  /api/kalshi/*      → 127.0.0.1:8080 (paper trader runtime API)
  /api/kalshi-orders → 127.0.0.1:8080/kalshi/orders (legacy rewrite)
  /api/*             → 127.0.0.1:8081 (v3-dashboard FastAPI)
```

SSL via Let's Encrypt (`certbot.timer` auto-renews).

---

## 11. Disabled Services (May 2026)

These were stopped and disabled because they had zero active workload:

| Service | Why disabled | RAM freed | Re-enable |
|---------|-------------|-----------|-----------|
| Docker + containerd | Zero containers running; V3 uses systemd | 127MB | `sudo systemctl enable --now docker containerd` |
| PostgreSQL | Zero application databases | 72MB | `sudo systemctl enable --now postgresql` |
| Redis | Zero keys stored | 13MB | `sudo systemctl enable --now redis-server` |

Docker images are preserved on disk (~21GB) as a V2 rollback option.

---

## Appendix: V2 Docker Reference (Historical)

The V2 system ran inside Docker container `h300-retrain-shifted-v2-clone` (image `golden-goose-v2:clone`). It was decommissioned in May 2026. The archived codebase is at `/home/johnny/ofi-lab-v2-ARCHIVED/`.

Key V2 facts (for reference only):
- Container had ~20 env vars injected via `docker run`
- Mounted `/data` → `/data` for persistent state
- Kill switch was memory-only, toggled via `curl` to port 8080
- Required `docker cp` + `docker restart` for hot-patch deploys
- Required full `docker build` + `docker run` for dependency changes
