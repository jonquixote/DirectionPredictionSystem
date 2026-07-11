# Server State — 34.67.75.48 ("vps-n2") as of 2026-07-11 08:30Z

Audited live (not from memory). This is the replication target-state for the new
server. Companion doc: `TRANSFER_PLAN.md` (zero-gap migration procedure).

## 1. Host

| item | value |
|---|---|
| Provider / type | GCE `n2-standard-4` (4 vCPU, 16 GB RAM) |
| OS | Ubuntu 24.04.4 LTS, kernel 6.17.0-1018-gcp, x86_64 |
| Disk | 193 GB root, 76% used (145 GB) after 2026-07-11 reclaim |
| External IP | 34.67.75.48 — **verify in GCP console whether reserved-static or ephemeral before transfer** |
| DNS | `bet.octavo.press` → this IP (Let's Encrypt cert, certbot.timer auto-renews) |
| User | `johnny` (sudo), SSH key `~/.ssh/id_vps_n2` (from Mac) |
| Firewall | ufw: OpenSSH, 80, 443 (v4+v6). GCP VPC firewall in front (audit rules in console) |
| Listening | 443/80 nginx; 8081 loopback (dashboard API); **8080 on 0.0.0.0** (runtime API — reachable only via GCP firewall rules; replicate rules, or bind loopback on new server) |

## 2. Three workload layers

### Layer A — v3 production (systemd, survives reboot)
Three units in `/etc/systemd/system/` (full contents below), all `enabled`,
`User=johnny`, `WorkingDirectory=/home/johnny/ofi-lab-v3`,
`EnvironmentFile=/etc/v3/env`, journald logging:

| unit | ExecStart | notes |
|---|---|---|
| `v3-ws-feed` | `.venv/bin/python -m api.ws_feed_service` | `Before=v3-paper-trader`; L2 websocket feed |
| `v3-paper-trader` | `.venv/bin/python -m trading.paper_trader` | `After=v3-ws-feed`; writes /data/v3.db continuously |
| `v3-dashboard` | `.venv/bin/uvicorn dashboard_api.main:app --host 127.0.0.1 --port 8081 --workers 2` | behind nginx |

```ini
# /etc/systemd/system/v3-paper-trader.service
[Unit]
Description=ofi-lab-v3 paper trader
After=network.target v3-ws-feed.service
[Service]
Type=simple
User=johnny
WorkingDirectory=/home/johnny/ofi-lab-v3
EnvironmentFile=/etc/v3/env
Environment="PYTHONPATH=/home/johnny/ofi-lab-v3"
ExecStart=/home/johnny/ofi-lab-v3/.venv/bin/python -m trading.paper_trader
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=v3-paper
[Install]
WantedBy=multi-user.target

# /etc/systemd/system/v3-dashboard.service — identical shell except:
#   PYTHONPATH=/home/johnny/ofi-lab-v3:/home/johnny/ofi-lab-v3/dashboard_api
#   ExecStart=.venv/bin/uvicorn dashboard_api.main:app --host 127.0.0.1 --port 8081 --workers 2
#   RestartSec=5, SyslogIdentifier=v3-dashboard, After=network.target only

# /etc/systemd/system/v3-ws-feed.service — identical shell except:
#   ExecStart=.venv/bin/python -m api.ws_feed_service
#   Before=v3-paper-trader.service, RestartSec=5, SyslogIdentifier=v3-ws-feed
```

**Secrets:** `/etc/v3/env` (583 bytes, `root:johnny 0640`). Key names (values NOT
recorded here — copy the file over a secure channel, never via git):
`V3_ENV, V3_ADMIN_SECRET, V3_KILL_SWITCH_PATH, V3_DB_PATH, DASHBOARD_USER,
DASHBOARD_PASS, DASHBOARD_PASSWORD, PYTHONPATH, STORAGE_DB_PATH,
V3_GOVERNANCE_ACTIONS_PAUSED, V3_ANALYSIS_PRECOMPUTE_ENABLED,
V3_ROLLUP_LOOP_ENABLED, V3_ANALYSIS_DEFAULT_HISTORY_DAYS`

**Nginx** (`/etc/nginx/sites-enabled/dps-dash`): serves SPA from
`/var/www/dps-dash` (index.html + assets/, immutable-cached), proxies `/api/*` →
127.0.0.1:8081 (dashboard_api proxies `/kalshi/*` onward to the runtime API on
8080 with auth translation), legacy alias `/api/kalshi-orders` →
`/api/kalshi/orders`. TLS: letsencrypt live cert for `bet.octavo.press`,
`options-ssl-nginx.conf` + `ssl-dhparams.pem`, HTTP→HTTPS 301.

### Layer B — research collectors (**nohup daemons — do NOT survive reboot**)
Known gap on the old server; **fix during transfer: install these as systemd
units on the new server** (templates in TRANSFER_PLAN.md §P1).

| daemon | logger | cadence | db | log |
|---|---|---|---|---|
| `/home/johnny/kalshi_fade_daemon.sh` | `/home/johnny/kalshi_fade_logger.py` | 10 s, 7 coins (KX{COIN}15M books + Coinbase spot) | `/data/kalshi_fade.db` (obs, windows) | `/data/logs/kalshi_fade.log` |
| `/home/johnny/track4/track4_strike_daemon.sh` | `/home/johnny/track4/strike_logger.py` | 30 s, ≤40 BTC/ETH strike books (KXBTC/KXBTCD/KXETH/KXETHD) + spot | `/data/kalshi_strikes.db` (strikes, strike_markets) | `/data/logs/track4_strike_logger.log` |

Wrapper pattern: `while true; do $PY logger.py …; sleep 15; done`, started with
`nohup … & disown`. Restart procedure: kill the *daemon* by its distinct script
name first, then the logger (never `pkill` on a substring that matches your own
shell — recurring incident class).

Stopped/retired (data retained): `pm_depth_daemon.sh`/`pm_depth_logger.py`
(stopped 2026-07-11, PM proven frozen — do not restart), `track2_daemon.sh`
(Track 2 complete 07-05).

### Layer C — cron (user `johnny`)
```cron
30 0 * * *  ofi-lab-v3/deploy/cron/v3-daily-features.sh            >> /data/logs/daily_features.log
0  8 * * *  ofi-lab-v3/scripts/snapshot_observe.sh                 >> /data/observe/cron.log
0  2 * * 4  ofi-lab-v3/deploy/cron/v3-weekly-retrain.sh
30 3 * * *  track1/archive_prune_books.py   --db /data/kalshi_fade.db    --archive-dir /data/archives --keep-days 7   >> /data/logs/track1_archive.log
40 3 * * *  track1/backfill_settlements.py  --db /data/kalshi_fade.db                                                 >> /data/logs/track1_backfill.log
30 4 28 7 * track1/decide_track1.py         --db /data/kalshi_fade.db --prereg track1/prereg_track1.json              >> /data/logs/track1_decision.log
50 3 * * *  track4/archive_prune_strikes.py --db /data/kalshi_strikes.db --archive-dir /data/archives --keep-days 7   >> /data/logs/track4_archive.log
```
(python = `/home/johnny/ofi-lab-v3/.venv/bin/python3.12`; paths under
`/home/johnny/` abbreviated.)

⚠️ **`30 4 28 7 *` is the one-shot Track 1 registered decision (2026-07-28
04:30Z). It must fire exactly once, on whichever server holds the merged
kalshi_fade.db + archives.** See TRANSFER_PLAN.md §P5.

## 3. Code

| path | what | source of truth |
|---|---|---|
| `/home/johnny/ofi-lab-v3/` | v3 production codebase (818 MB incl. .venv) | **rsync from Mac, NOT git** — deployed tree can be ahead of repo HEAD |
| `/home/johnny/track1/`, `track2/`, `track3/`, `track4/` | 4-track program scripts | git repo `DirectionPredictionSystem`, `probe/track{1..4}/` (branch `probe/kalshi-fade`) |
| `/home/johnny/kalshi_fade_logger.py`, `*_daemon.sh` | collector + wrappers at $HOME | repo `probe/` (logger) / this doc (wrappers) |
| `/var/www/dps-dash/` | built SPA (dashboard/ on Mac → `npm run build` → rsync) | repo `dashboard/` |
| `/home/johnny/probe_*.py`, `ofi-lab-v2-ARCHIVED/`, `polymarket-ofi-clone/`, `ofi-lab-v3-staging/` | historical/archived — copy for completeness, nothing runs from them | — |

## 4. Python environment
`/home/johnny/ofi-lab-v3/.venv` — **Python 3.12.3**, 58 packages. Full pin list:
`requirements-freeze-20260711.txt` (beside this doc). Load-bearing pins:
`fastapi==0.136.1, uvicorn==0.46.0, lightgbm==4.6.0, numpy==2.4.4,
pandas==3.0.3, scikit-learn==1.8.0, websockets==16.0`.
Research scripts (track1/2/3/4) are stdlib + numpy only.
Note: `sqlite3` CLI is NOT installed on the box — all DB work goes through venv
python. Bybit is geoblocked from this region — spot comes from Coinbase.

## 5. Data inventory (`/data`, 2026-07-11)

| path | size | state | transfer class |
|---|---|---|---|
| `parquet/` | 74 G | static (no writes since <2026-07-04) | pre-copy (rsync once) |
| `probe_exports/` | 14 G | static (Jun 13 archives of deleted originals) | pre-copy |
| `v3.db` (+ 47 M WAL) | 13 G | **HOT** — paper trader writes continuously | snapshot at cutover (§P4) |
| `features_v2/`, `features/`, `features_v3/` | 4+2.3+0.5 G | features_v3 updated by daily cron; rest static | pre-copy + final delta |
| `models/` | 1.6 G | updated by weekly retrain (Thu 02:00Z) | pre-copy + final delta |
| `kalshi_fade.db` (+ 93 M WAL) | 1.4 G | **HOT** — 10 s appends | overlap + merge (§P2) |
| `pm_depth.db` | 1.1 G | static since 07-11 07:42Z | pre-copy |
| `track2_latency.db` | 108 M | static since 07-05 | pre-copy |
| `logs/` | 153 M | append | pre-copy + final delta |
| `archives/` | 91 M | daily-append gzip JSONL **+ watermark files** | copy with §P3 care |
| `kalshi_strikes.db` | new 07-11 | **HOT** — 30 s appends | overlap + merge (§P2) |
| `observe/` | small | cron snapshots | pre-copy + delta |

**Invariants that must survive transfer:**
1. `obs` PK `(ts, symbol)`... (kalshi_fade), `strikes` PK `(ts, ticker)` — one
   row per tick; merges must be `INSERT OR IGNORE` on these keys.
2. `/data/archives/kalshi_books.watermark` and `kalshi_strikes.watermark` hold
   the max archived `ts`; the archive+prune crons NULL blobs only ≤ watermark.
   **db and archives+watermark move as one unit** — a db newer than its
   watermark is fine (re-archives); a watermark newer than its db would prune
   unarchived blobs. Never regress the db relative to the watermark.
3. Frozen preregs: `track1/prereg_track1.json`, `track4/prereg_track4.json` —
   byte-identical copies; amendments prohibited.
4. `decide_track1.py` reads archives + hot db; its cluster count must be ≥ the
   last peek (1,246 @ 2026-07-11) on the new server — this is the acceptance
   test that history survived.

## 6. Calendar constraints (live at audit time)
- **2026-07-18 08:00Z** — Track 4 week-1 EV checkpoint (needs kalshi_strikes.db
  continuity + collector ≥80% uptime per prereg, else clock restarts).
- **2026-07-28 04:30Z** — Track 1 registered decision cron (one-shot).
- Weekly retrain Thursdays 02:00Z touches `models/` + `v3.db`.
