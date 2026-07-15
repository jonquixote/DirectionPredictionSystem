# Server Transfer Plan — UPDATED 2026-07-14 (audit-corrected)

> ## ✅ EXECUTED 2026-07-15 (03:48–04:45Z) — see completion log at bottom.
> ## ✅ FINALIZED 2026-07-15 04:55Z — GCP deletion green-lit.
> Linode firewall opened (public 200 @137ms, API 401-auth, 301 redirect, certbot
> dry-run renewal SUCCESS). Deletion-safety sweep synced the **1,866 /data files
> the P0 glob-copy missed** (incl. all calibration maps — trader had booted in
> identity mode; restarted 04:53, "loaded 7 bins + 1,147 outcomes", 8080 now
> loopback). Final diffs: /home = 0; /data = 0 except live-append
> calibration_*_outcomes.jsonl where NEW is ahead (correct). 6/6 services active
> AND enabled. Only things not replicated from OLD: system journals + the stale
> pre-merge copies of the hot DBs (both worthless).

Replaces the prior `TRANSFER_PLAN.md`. OLD = `34.67.75.48` (GCP `us-central1-a`, ephemeral IP, `Etc/UTC`). NEW = `172.233.148.62` (Akamai/Linode, **not GCP**, Ubuntu 24.04, 6 vCPU / 15 GB / 319 GB). Cutover = **Hostinger DNS A-record flip** (IP move is impossible across providers, and OLD's IP is ephemeral).

## 0. Critical corrections from the 2026-07-14 live audit

1. **NEW is Akamai, not GCP.** The plan's "move reserved static IP" branch is dead. Cutover is a DNS flip of `bet.octavo.press` via the **Hostinger CLI** (logged in). Lower TTL to 60s *now*, flip at §P4.
2. **`obs` has NO primary key** (only a non-unique index). The plan's `INSERT OR IGNORE INTO obs …` will **duplicate** every pre-overlap row and fail its own `HAVING COUNT(*)>1` gate. Fix: merge `obs` with `NOT EXISTS` on `(ts, symbol)`.
3. **`windows` merge loses settlements.** `INSERT OR IGNORE` keeps NEW's `NULL` `kalshi_result` over OLD's settled truth → `decide_track1` cluster count drops below 1246. Fix: add a backfill `UPDATE … SET kalshi_result/settled FROM old.windows WHERE windows.kalshi_result IS NULL`.
4. **`strike_markets` merge also loses `result`/`settled`.** Add the same backfill.
5. **Secrets manifest is incomplete.** Plan copied only `/etc/v3/env`. The paper trader also needs **`/data/kalshi.env`** and **`/data/kalshi_private_key.pem`** — and the live PEM (`md5 831f45115dde2ee77d5b869f8c333035`) **differs from the repo `secrets/` copy** (which is stale/rotated). Copy the live `/data` files, not the repo copies. Keep them at the same `/data` paths (`0600 johnny:johnny`).
6. **`/data/observe` is missing on OLD** → the `08:00` `snapshot_observe` cron has been silently failing. Create `/data/observe` on NEW.
7. **Archives + watermarks are a hard prerequisite** for `decide_track1`. Copy `/data/archives/*.jsonl.gz` **and** the `.watermark` files, or the cluster count collapses.
8. **NEW must be set to `Etc/UTC`** so `30 4 28 7 *` still fires at 04:30Z.
9. **8080 is bound by `v3-paper-trader` on `0.0.0.0`** and is **not** opened in ufw (GCP firewall was the only shield). NEW has no GCP firewall → **bind 8080 to loopback** and rely on ufw (80/443/SSH only).
10. **Large cross-provider rsyncs** (parquet 76G, probe_exports 14G, v3.db 14G, features*/models/logs). Do them **OLD→NEW directly** (set up a key from NEW→OLD), not via the Mac as a middle-hop.
11. Doc size figures (07-11) are stale; current HOT sizes: `kalshi_fade.db` 1.7G, `kalshi_strikes.db` 449M, `v3.db` 13.85G.

## 1. Timing (unchanged, still feasible)
Execute **now (07-14)**; finish **before 07-16**; absolutely do not straddle the **07-18 08:00Z** Track-4 checkpoint mid-merge; complete **before 07-25**; the **07-28 04:30Z** Track-1 decision must fire exactly once, on NEW.

## P0 — Provision + base replication (OLD stays fully live)
NEW already exists, so skip GCE creation. On NEW (`root`):
```bash
adduser johnny && usermod -aG sudo johnny
apt update && apt install -y nginx certbot python3-certbot-nginx python3.12-venv rsync ufw
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable
mkdir -p /data/{logs,archives,observe,parquet,probe_exports,features,features_v2,features_v3,models,observe} && chown -R johnny /data
timedatectl set-timezone Etc/UTC      # so 30 4 28 7 * == 04:30Z
```
- **DNS (do today):** via Hostinger CLI, set `bet.octavo.press` A-record TTL to 60s. Confirm exact subcommand with `hostinger --help` / `hcli --help` on the Mac.
- **Direct OLD→NEW rsync key:** generate a keypair on NEW, add NEW's pubkey to `johnny@34.67.75.48:~/.ssh/authorized_keys`, then run bulk rsyncs **from NEW**:
  ```bash
  # from NEW, as root (or johnny):  biggest first, incremental, rerun for deltas
  rsync -avH --info=progress2 johnny@34.67.75.48:/data/parquet/        /data/parquet/
  rsync -avH johnny@34.67.75.48:/data/probe_exports/                   /data/probe_exports/
  rsync -avH johnny@34.67.75.48:/data/features/ /data/features_v2/ /data/features_v3/ /data/models/ /data/logs/ /data/observe/ /data/archives/ /data/
  rsync -avH johnny@34.67.75.48:/data/pm_depth.db /data/track2_latency.db /data/
  # operational /data files the dashboard + paper trader expect:
  rsync -avH johnny@34.67.75.48:/data/kalshi_orders.jsonl /data/calibration_*.json /data/calibration_*_outcomes.jsonl /data/regime_thresholds.json /data/training_ready.json /data/dashboard_baseline.json /data/v3_kill_switch.resumed.*.json /data/
  rsync -avH --exclude ofi-lab-v3/.venv johnny@34.67.75.48:/home/johnny/ /home/johnny/
  ```
- **venv:** rebuild from `/home/johnny/ofi-lab-v3/requirements-freeze-20260711.txt` (do not copy `.venv`).
- **Secrets (secure channel, never git):**
  ```bash
  ssh johnny@34.67.75.48 'sudo cat /etc/v3/env'        | sudo install -m 0640 -o root -g johnny /dev/stdin /etc/v3/env
  ssh johnny@34.67.75.48 'sudo cat /data/kalshi.env'   | install -m 0600 -o johnny -g johnny /dev/stdin /data/kalshi.env
  ssh johnny@34.67.75.48 'sudo cat /data/kalshi_private_key.pem' | install -m 0600 -o johnny -g johnny /dev/stdin /data/kalshi_private_key.pem
  ```
  Verify the copied PEM's md5 == `831f45115dde2ee77d5b869f8c333035`.

## P1 — Replicate services COLD (installed + enabled, NOT started)
1. Copy the three `v3-*.service` units (verify `User=johnny`, `EnvironmentFile=/etc/v3/env`); `daemon-reload && enable` (don't start).
2. **Collectors as systemd units** (fixes the reboot gap) — `kalshi-fade-logger.service` and `track4-strike-logger.service` per the prior template, `ExecStart` pointing at `/home/johnny/kalshi_fade_logger.py --db /data/kalshi_fade.db --interval 10` and `/home/johnny/track4/strike_logger.py --db /data/kalshi_strikes.db --interval 30`. Enable; start at §P2.
3. **Nginx + TLS:** `rsync -a johnny@34.67.75.48:/etc/letsencrypt/ /etc/letsencrypt/`; ensure `certbot.timer` present (install `certbot python3-certbot-nginx`). Copy `sites-enabled/dps-dash`. **Do not reload nginx** until DNS points at NEW.
4. Frontend: `rsync -a johnny@34.67.75.48:/var/www/dps-dash/ /var/www/dps-dash/`.
5. Install the full crontab **commented out**; activate lines in §P3/§P4/§P5. Bind `8080` to loopback (edit the `v3-paper-trader`/`ws_feed` bind or ufw-deny 8080).

## P2 — Collector overlap + merge (the zero-gap core — CORRECTED)
1. **T₀:** `sudo systemctl start kalshi-fade-logger track4-strike-logger`; `date -u +%s | tee /data/logs/overlap_start_ts`. Verify rows + clean logs.
2. **T₀+30m:** snapshot OLD's hot DBs with the **backup API** (never `cp` on a live WAL db), rsync `.snap.db` + `/data/archives/` to NEW.
3. **Merge (run on NEW, loggers keep running). Corrected, idempotent SQL:**
   ```python
   import sqlite3
   def merge(new_db, old_db):
       con = sqlite3.connect(new_db, timeout=120)
       con.execute("PRAGMA journal_mode=WAL")
       con.execute("ATTACH ? AS old", (old_db,))
       # obs: NO PK -> dedup on natural key (ts,symbol)
       con.execute("""INSERT INTO obs(ts,symbol,ticker,boundary_ts,close_ts,yes_bid,yes_ask,
                       last_price,volume,spot,spot_open,dev_bps,phase,orderbook_json)
                      SELECT o.ts,o.symbol,o.ticker,o.boundary_ts,o.close_ts,o.yes_bid,o.yes_ask,
                             o.last_price,o.volume,o.spot,o.spot_open,o.dev_bps,o.phase,o.orderbook_json
                      FROM old.obs o
                      WHERE NOT EXISTS(SELECT 1 FROM obs n WHERE n.ts=o.ts AND n.symbol=o.symbol)""")
       # strikes: PK(ts,ticker) -> INSERT OR IGNORE correct
       con.execute("""INSERT OR IGNORE INTO strikes(ts,underlying,series,ticker,strike,close_ts,
                       yes_bid,yes_ask,spot,orderbook_json)
                      SELECT ts,underlying,series,ticker,strike,close_ts,yes_bid,yes_ask,spot,orderbook_json
                      FROM old.strikes""")
       # windows: PK + must preserve OLD settlements
       con.execute("""INSERT OR IGNORE INTO windows(symbol,boundary_ts,close_ts,ticker,spot_open,
                       spot_close,kalshi_result,settled)
                      SELECT symbol,boundary_ts,close_ts,ticker,spot_open,spot_close,kalshi_result,settled
                      FROM old.windows""")
       con.execute("""UPDATE windows SET kalshi_result=o.kalshi_result, settled=o.settled
                      FROM old.windows o
                      WHERE windows.kalshi_result IS NULL AND o.kalshi_result IS NOT NULL
                        AND windows.symbol=o.symbol AND windows.boundary_ts=o.boundary_ts""")
       # strike_markets: PK + backfill first_seen AND result/settled
       con.execute("""INSERT OR IGNORE INTO strike_markets(ticker,underlying,series,strike,close_ts,
                       first_seen,result,settled)
                      SELECT ticker,underlying,series,strike,close_ts,first_seen,result,settled
                      FROM old.strike_markets""")
       con.execute("""UPDATE strike_markets SET first_seen=MIN(first_seen,o.first_seen)
                      FROM old.strike_markets o
                      WHERE strike_markets.ticker=o.ticker AND o.first_seen<strike_markets.first_seen""")
       con.execute("""UPDATE strike_markets SET result=o.result, settled=o.settled
                      FROM old.strike_markets o
                      WHERE strike_markets.result IS NULL AND o.result IS NOT NULL
                        AND strike_markets.ticker=o.ticker""")
       con.commit(); con.execute("DETACH old"); con.close()
   merge("/data/kalshi_fade.db",     "/data/kalshi_fade.snap.db")
   merge("/data/kalshi_strikes.db",  "/data/kalshi_strikes.snap.db")
   ```
   Then run `backfill_settlements.py` on NEW (idempotent) to refresh any still-NULL results.
4. **Verify continuity (all must pass):**
   ```sql
   -- no dups AND no loss, per table (run with old attached):
   SELECT COUNT(*) FROM (SELECT ts,symbol FROM obs GROUP BY 1,2 HAVING COUNT(*)>1);          -- 0
   SELECT COUNT(*) FROM (SELECT ts,symbol FROM old.obs EXCEPT SELECT ts,symbol FROM obs);     -- 0
   SELECT COUNT(*) FROM (SELECT symbol,boundary_ts FROM old.windows
                         WHERE kalshi_result IN('yes','no')
                         EXCEPT SELECT symbol,boundary_ts FROM windows WHERE kalshi_result IN('yes','no')); -- 0
   -- strikes / strike_markets: same EXCEPT pattern -> 0
   ```
   Then the end-to-end proof:
   ```bash
   .venv/bin/python3.12 /home/johnny/track1/decide_track1.py \
     --db /data/kalshi_fade.db --prereg /home/johnny/track1/prereg_track1.json --archive-dir /data/archives
   # expect: clusters >= 1246 (peek 2026-07-11) AND fires >= 2
   ```
5. Leave BOTH collectors running until §P5.

## P3 — Archives / cron activation on NEW
1. Archives + watermarks copied in P2. NEW's DBs are supersets → watermarks valid (≤ max archived ts ≤ db max ts).
2. Activate on NEW (uncomment): archive+prune (03:30 books, 03:50 strikes) + settlements backfill (03:40).
3. **Disable the same lines on OLD** that day. Backfill is idempotent; decision cron stays on OLD until §P5.
4. Next morning verify `/data/logs/track1_archive.log` + `track4_archive.log` on NEW.

## P4 — v3 production cutover (minutes; DNS flip)
1. Pre-stage 13.85G `v3.db` via backup API (as P2.2), `rsync --inplace` to `/data/v3.db.staged`.
2. Final deltas: `features_v3/`, `models/`, `observe/`, `logs/`, `/home/johnny/ofi-lab-v3/`, `/var/www/dps-dash/`, and refresh `/data/kalshi.env` + PEM if changed.
3. **Cutover at a 15-min boundary:**
   ```bash
   ssh johnny@34.67.75.48 'sudo systemctl stop v3-paper-trader v3-dashboard v3-ws-feed'
   # final v3.db backup-API snapshot on OLD; rsync --inplace to /data/v3.db.staged; mv to /data/v3.db
   sudo systemctl start v3-ws-feed v3-paper-trader v3-dashboard
   ```
 4. **Point the world at NEW via Hostinger CLI:** pre-verify `hostinger dns records list octavo.press` (bet A→34.67.75.48, TTL 60s), then flip `bet.octavo.press` A → `172.233.148.62` (TTL 60). Reload nginx on NEW; confirm `https://bet.octavo.press` + `/api/` auth. Keep rollback: flip back to 34.67.75.48 on failure.
 5. Verify: `journalctl -u v3-paper-trader -f` shows predictions resuming; dashboard freshness green; new `v3.db` rows.
 6. **One-shot move (CRITICAL):** comment OLD `30 4 28 7 * decide_track1` AND uncomment NEW's, so only the DNS-live box fires the 07-28 04:30Z decision exactly once.
 7. Activate remaining v3 crons on NEW (daily features, snapshot_observe, weekly retrain); disable on OLD.

## P5 — Retire OLD (only after every P2.4 test passed)
1. Stop OLD collectors (`pkill -f` by distinct script name).
2. Final incremental merge (repeat P2.2–P2.4). NEW is a strict superset; rerun continuity queries.
3. `ssh johnny@34.67.75.48 'crontab -r'` — guarantees the 07-28 decision fires only on NEW. Verify NEW's `30 4 28 7 *` is active.
4. GCE disk snapshot of OLD, then **stop (don't delete)**; keep 14+ days. Delete only after 07-28 decision logged.
5. Update Mac `~/.ssh/config` / notes with new IP; update any repo docs embedding `34.67.75.48`.

## Risk table (updated)
| risk | guard |
|---|---|
| `cp` of live WAL db → corrupt | always backup API (P2.2, P4.1) |
| **`obs` no PK → merge duplicates** | `NOT EXISTS` merge on `(ts,symbol)` (P2.3) |
| **`windows`/`strike_markets` settlement loss** | backfill `kalshi_result`/`result` from OLD (P2.3) |
| **Missed Kalshi creds → trader can't auth** | copy live `/data/kalshi.env` + `/data/kalshi_private_key.pem` (md5 `831f…`), not repo copy (P0) |
| **Wrong PEM deployed** | verify md5 `831f45115dde2ee77d5b869f8c333035` after copy |
| Watermark newer than db → prune destroys blobs | copy db+archives+watermark as unit; merge makes NEW superset first (P3.1) |
| **`/data/observe` missing → 08:00 cron fails** | `mkdir -p /data/observe` on NEW (P0) |
| **DNS flip not possible** | Hostinger CLI (logged in); lower TTL to 60s in P0, flip in P4.4 |
| Track-1 decision fires twice/never | cron commented on NEW until P3; `crontab -r` on OLD at P5.3; verify |
| Double paper trader | v3 units cold; started only after OLD stopped (P4.3) |
| **NEW 8080 publicly exposed** | bind loopback; ufw allows only 80/443/SSH (P1.5) |
| **NEW not UTC → decision at wrong time** | `timedatectl set-timezone Etc/UTC` (P0) |
| **Slow cross-provider rsync** | OLD→NEW direct via NEW-authed key, not via Mac (P0) |
| 07-18 checkpoint invalidated | schedule transfer off the date; verified merge preserves uptime |

---

# APPENDIX — SERVER_STATE.md (replication target-state, as of 2026-07-11 08:30Z)

> Audited live (not from memory). This is the replication target-state for the new
> server. Companion doc: `TRANSFER_PLAN.md` (zero-gap migration procedure).
>
> **2026-07-14 audit diff:** see §0 of the plan above. Key deltas — NEW is Akamai
> (not GCP), OLD IP is ephemeral, `obs` has no PK, secrets need `/data/kalshi.env`
> + PEM, `/data/observe` missing on OLD, 8080 is a systemd service not nginx,
> doc sizes stale. Use the items in §0 as the post-transfer checklist.

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

# COMPLETION LOG — executed 2026-07-15 03:48–04:45Z

| step | time (Z) | result |
|---|---|---|
| Pulse OLD (reference) | 03:48 | clusters=1584 fires=6834 settled=12990 EV=−0.0421 CI[−0.0604,−0.0226] |
| P3b: OLD archive/backfill crons disabled | 03:52 | backup at ~/crontab.backup.20260715; 3 lines commented |
| DNS pre-verify + payload validate | 03:53 | bet A→34.67.75.48 TTL 60; hostinger validate accepted |
| v3.db warm pre-stage (13.92 GB) | 03:50–03:56 | 39 MB/s, rc=0 |
| OLD v3 stop + WAL checkpoint | 04:05–04:08 | checkpoint (0,0,0); final db 13,920,399,360 B |
| Final delta + NEW v3 start | 04:08–04:10 | ws-feed/paper/dashboard/nginx active; enabled for boot |
| Cold-start wrinkles | 04:10–04:20 | paper trader ×2 lock-contention exits then clean (Restart=on-failure); nginx needed reload (master predated config copy); dashboard precompute ~100% CPU ×2 workers before binding 8081 |
| 8080 loopback re-patch | 04:16 | trading/api_server.py:1351 → 127.0.0.1 (code rsync from OLD had reverted it; applies next restart; ufw DENY 8080 active) |
| **DNS FLIP** | 04:22 | bet → 172.233.148.62, live at 1.1.1.1 + 8.8.8.8 |
| Cron swap | 04:23 | OLD: 0 active lines. NEW: all 7 active incl. `30 4 28 7 *` one-shot |
| OLD collectors stopped | ~04:24 | verified NO_COLLECTORS_RUNNING; NEW collectors continuous throughout |
| Final merge (P5) | 04:33 | dups=0, missing_obs=0, missing_settlements=0, strikes clean → PASS |
| Archive completion | 04:40 | OLD's 07-15 archive files copied as *-oldsrv.jsonl.gz (decide dedups by fired-set) |
| **Acceptance peek on NEW** | 04:41 | **clusters=1584 fires=6834 EV=−0.0421 CI[−0.0604,−0.0226] — exact match to OLD** |
| Continuity | 04:33 | seam gaps >60s across cutover window: 0; collector lag 9–10 s |
| Housekeeping | 04:44 | *.final.db/*.snap.db removed; NEW disk 42% |

Bonus finding: Bybit L2 websocket connects from Linode (was geoblocked on GCP).

## Outstanding
1. **Linode Cloud Firewall: allow inbound TCP 80+443** (user, Cloud Manager). Site
   unreachable externally until done; everything else operational.
2. OLD (GCP): quiesced (no crons, no collectors, v3 stopped, nginx still up).
   Snapshot + stop via GCP console; keep ≥14 days; delete only after the 07-28
   decision is logged on NEW.
3. Paper trader restart (any time): picks up the 127.0.0.1:8080 bind.
4. Mac ~/.ssh/config + repo memory updated to 172.233.148.62.
