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

---

## 7. Transfer Progress (NEW = Akamai/Linode 172.233.148.62) — updated 2026-07-15

NEW is **Akamai/Linode** (not GCP); OLD IP 34.67.75.48 is **ephemeral**.
Cutover = Hostinger DNS A-record flip of `bet.octavo.press` (TTL already 60s).

### Status checklist
- [x] **P0 provisioning** — johnny user, nginx/certbot/venv/rsync/ufw, Etc/UTC, /data dirs (incl. /data/observe), ufw 22/80/443.
- [x] **P1 bulk copy** — parquet 76G, probe_exports 14G, features*/models 1.6G, logs, archives+watermarks, code, /data operational files; v3 systemd units + nginx site + letsencrypt + frontend; venv built from OLD freeze (versions match, libgomp1 added).
- [x] **P2.1 collectors started on NEW** — T₀=1784077797 (01:09:57Z); verified writing.
- [x] **P2.2 OLD snapshots** — kalshi_fade.snap.db (1.8G), kalshi_strikes.snap.db (489M) @ ~01:40Z pulled to NEW; watermarks present (books=1783999790, strikes=1784000981).
- [x] **P2.3 merge OLD→NEW (DONE + VERIFIED)**
  - Script: `/data/logs/merge_final.py` (indexed/idempotent `INSERT OR IGNORE`, settlement backfill for windows + strike_markets).
  - Final counts (NEW live / OLD snap): obs 1,162,775 / 1,162,355 (+420 live overlap, **0 missing**); windows 12,955 / 12,941 (+14, **0 missing**); strikes 355,001 / 354,380 (+621, **0 missing**); strike_markets 3,035 / 3,033 (+2, **0 missing**).
  - EXCEPT completeness: **all 4 tables 0 missing rows** → no loss.
  - Settlement backfill: windows with `kalshi_result` NOT NULL = 12,318 (== OLD) ✓; strike_markets settled 0 in both (consistent).
  - **decide_track1 acceptance: clusters=1498 (≥1246 ✓), fires=6446 (≥2 ✓), settled_windows=12318 ✓.**

### Remaining
- [x] **P3a (staged 2026-07-15)** — NEW archive/prune/backfill crons ENABLED:
  `archive_prune_books` 03:30, `backfill_settlements` 03:40, `archive_prune_strikes` 03:50
  (all → /data/logs/*.log). v3-daily-features, snapshot_observe, weekly-retrain, and the
  07-28 04:30Z one-shot `decide_track1` remain COMMENTED on NEW. OLD left fully live.
- [ ] **P3b** — disable the matching crons on OLD (coordinate at P4 cutover; keep OLD live until DNS flip + verify).
- [ ] **P4** — v3 cutover + Hostinger DNS flip (do AFTER P3b; pick a 15-min boundary away from 02:00/03:30–03:50/04:30/08:00Z cron windows):
  1. Pre-verify DNS state: `hostinger dns records list octavo.press` — confirm `bet` A → `34.67.75.48` and TTL already 60s (lower it first if not).
  2. Stop OLD v3 (`systemctl stop v3-ws-feed v3-paper-trader v3-dashboard`); snapshot+rsync OLD `v3.db` → NEW `/data/v3.db`; start NEW v3.
  3. **One-shot move (CRITICAL, C3):** comment OLD `30 4 28 7 * decide_track1` AND uncomment NEW's — whichever box is DNS-live at 07-28 04:30Z owns the decision; never both.
  4. Flip DNS: `hostinger dns records update octavo.press --zone '[{"name":"bet","type":"A","ttl":60,"records":[{"content":"172.233.148.62"}]}]' --overwrite=true` (note: `--overwrite=true` replaces only RRs matching name+type, so www/MX/TXT are safe).
  5. Reload nginx on NEW; confirm `https://bet.octavo.press` + `/api/` auth. Uncomment NEW v3 crons (daily-features, snapshot_observe, weekly-retrain); comment those on OLD.
  6. Keep rollback note: flip `bet` A back to `34.67.75.48` if NEW verification fails.
  - Pre-cutover hardening already DONE (2026-07-15): NEW `v3-*.service` **disabled** (C1, no auto-start double-trader on reboot); `api_server.py:1351` bound to `127.0.0.1` + `ufw deny 8080` (C2, 8080 loopback-only).
- [ ] **P5** — retire OLD (after DNS propagated + verified); confirm 07-28 04:30Z Track-1 one-shot fires once on NEW.

### Open cutover risks to confirm before P3/P4
- OLD is still the live producer (collectors + crons + v3). Do NOT disable OLD
  crons or flip DNS until NEW verified end-to-end post-merge.
- 07-18 08:00Z Track-4 checkpoint must not be straddled by the cutover.
- 8080 on NEW must remain loopback-only (ufw NOT opened) per §1 note.
