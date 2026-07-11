# Server Transfer Plan — zero recorded gap in live collection

Companion to `SERVER_STATE.md` (replication target-state). OLD = 34.67.75.48,
NEW = the clone. Names below assume NEW is reachable as `vps-new`.

## Design: why there is no gap

The live collectors (kalshi_fade @10s, kalshi_strikes @30s) poll **public**
APIs and write append-only SQLite with natural primary keys (`obs(ts,symbol)`,
`strikes(ts,ticker)`). So:

1. **Run both servers' collectors in parallel** (separate IPs — no shared rate
   limit; Kalshi budget ~2.2 req/s per IP vs 10/s cap).
2. During the overlap, snapshot OLD's DBs consistently (SQLite backup API, not
   `cp` — WAL) and merge into NEW with `INSERT OR IGNORE` — overlap rows dedup
   on the PK, pre-overlap history lands intact.
3. Stop OLD's collectors only **after** continuity is verified on NEW.

Result: NEW's DBs contain one row per (ts, key) from the first row ever logged
through the present, with no timestamp hole at the switch. The v3 paper trader
is a *generator* (its own predictions), not a collector — it gets a
minutes-long boundary-aligned cutover instead (§P4); running two paper traders
would double-write predictions, so never dual-run it.

## Timing rule

Do the whole transfer **either before 2026-07-16 or after 2026-07-19**, and in
all cases **finish before 2026-07-25**:
- Track 4 week-1 checkpoint is 07-18 08:00Z — don't straddle it mid-merge
  (prereg counts collector uptime; a verified zero-gap merge preserves it).
- Track 1 one-shot decision cron fires 07-28 04:30Z — it must run exactly once,
  on NEW, against merged data. Buffer required.
Recommended: execute this week (P0 today, P2–P5 over 24–48 h).

---

## P0 — Provision + base replication (OLD stays fully live)

1. GCE: create `n2-standard-4`, Ubuntu 24.04 LTS, ≥250 GB disk (193 GB is 76%
   full; buy headroom), same region as OLD **if** you plan to move the static
   IP (IP moves are region-scoped). Replicate GCP VPC firewall rules (esp.
   whatever exposes 8080, if anything — or bind 8080 to loopback on NEW).
2. Check in GCP console whether 34.67.75.48 is a **reserved static IP**.
   - Reserved → plan is "move IP" at cutover (§P4), zero DNS change.
   - Ephemeral → reserve a new static IP for NEW now, and drop the
     `bet.octavo.press` DNS TTL to 60 s today.
3. Base setup on NEW:
   ```bash
   adduser johnny && usermod -aG sudo johnny
   # install Mac key ~/.ssh/id_vps_n2.pub into /home/johnny/.ssh/authorized_keys
   ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable
   apt update && apt install -y nginx python3.12-venv rsync
   mkdir -p /data/logs /data/archives /data/observe && chown -R johnny /data
   ```
4. Pre-copy the **static** bulk while OLD runs (order: biggest first; rerun
   rsync later for deltas — it's incremental):
   ```bash
   # from the Mac, or OLD→NEW directly with an agent-forwarded key
   rsync -avH --info=progress2 johnny@34.67.75.48:/data/parquet/        /data/parquet/
   rsync -avH johnny@34.67.75.48:/data/probe_exports/                   /data/probe_exports/
   rsync -avH johnny@34.67.75.48:/data/features/ /data/features_v2/ ... # features, features_v2, features_v3, models, logs, observe
   rsync -avH johnny@34.67.75.48:/data/pm_depth.db /data/track2_latency.db /data/
   rsync -avH johnny@34.67.75.48:/home/johnny/ /home/johnny/ \
     --exclude ofi-lab-v3/.venv   # venv is rebuilt, not copied
   ```
5. Rebuild the venv from the pinned freeze (do not copy .venv binaries):
   ```bash
   cd /home/johnny/ofi-lab-v3 && python3.12 -m venv .venv
   .venv/bin/pip install -r requirements-freeze-20260711.txt
   ```
6. Secrets — over ssh only, never git:
   ```bash
   ssh OLD 'sudo cat /etc/v3/env' | ssh NEW 'sudo install -m 0640 -o root -g johnny /dev/stdin /etc/v3/env'
   ```

## P1 — Replicate services COLD (installed + enabled, NOT started)

1. Copy the three `v3-*.service` unit files (contents in SERVER_STATE.md §2A);
   `systemctl daemon-reload && systemctl enable v3-ws-feed v3-paper-trader v3-dashboard`
   — **do not start** (started at §P4 cutover).
2. **Improvement (fixes the reboot gap): install the collectors as systemd
   units on NEW instead of nohup daemons.** Same restart semantics as the
   wrappers, plus boot persistence:
   ```ini
   # /etc/systemd/system/kalshi-fade-logger.service
   [Unit]
   Description=Track1 kalshi fade logger (10s books+spot)
   After=network-online.target
   [Service]
   User=johnny
   ExecStart=/home/johnny/ofi-lab-v3/.venv/bin/python3.12 -u /home/johnny/kalshi_fade_logger.py --db /data/kalshi_fade.db --interval 10
   Restart=always
   RestartSec=15
   StandardOutput=append:/data/logs/kalshi_fade.log
   StandardError=inherit
   [Install]
   WantedBy=multi-user.target

   # /etc/systemd/system/track4-strike-logger.service — same shell with
   #   ExecStart=… /home/johnny/track4/strike_logger.py --db /data/kalshi_strikes.db --interval 30
   #   StandardOutput=append:/data/logs/track4_strike_logger.log
   ```
   Enable both; started in §P2. (Do NOT also run the old nohup wrappers on NEW.)
3. Nginx + TLS: copy `sites-enabled/dps-dash`. Cert: simplest is
   `rsync /etc/letsencrypt/` OLD→NEW (cert+renewal config move cleanly);
   certbot.timer is stock Ubuntu. Don't reload nginx with the cert until DNS/IP
   points at NEW (renewal challenges would fail harmlessly until then).
4. Frontend: `rsync /var/www/dps-dash/` OLD→NEW.
5. Crontab on NEW: install the full crontab from SERVER_STATE.md §2C but
   **commented out entirely**. Individual lines are activated in §P3/§P4/§P5.

## P2 — Collector overlap + merge (the zero-gap core)

1. **T₀ — start NEW collectors** (fresh, empty DBs; schema auto-creates):
   ```bash
   sudo systemctl start kalshi-fade-logger track4-strike-logger
   # record T0:
   date -u +%s | tee /data/logs/overlap_start_ts
   ```
   Verify rows appear in both DBs (`SELECT COUNT(*), MAX(ts) …`) and logs are
   clean (no 429s; NEW has its own IP so budgets don't stack).
2. **T₀+30 min — snapshot OLD's hot collector DBs** (backup API = consistent
   despite WAL; `cp` is NOT safe on a live WAL db):
   ```bash
   ssh OLD '/home/johnny/ofi-lab-v3/.venv/bin/python3.12 - <<EOF
   import sqlite3
   for db in ("kalshi_fade", "kalshi_strikes"):
       src = sqlite3.connect(f"/data/{db}.db")
       dst = sqlite3.connect(f"/data/{db}.snap.db")
       src.backup(dst); dst.close(); src.close()
       print(db, "snapshotted")
   EOF'
   rsync -avH johnny@34.67.75.48:/data/*.snap.db /data/
   rsync -avH johnny@34.67.75.48:/data/archives/ /data/archives/   # incl. *.watermark
   ```
3. **Merge OLD history into NEW live DBs** (run on NEW; loggers keep running —
   WAL tolerates the writer):
   ```bash
   /home/johnny/ofi-lab-v3/.venv/bin/python3.12 - <<'EOF'
   import sqlite3
   # kalshi_fade: obs PK(ts,symbol)-equivalent unique rows + windows
   db = sqlite3.connect("/data/kalshi_fade.db", timeout=120)
   db.execute("ATTACH '/data/kalshi_fade.snap.db' AS old")
   db.execute("INSERT OR IGNORE INTO obs SELECT * FROM old.obs")
   db.execute("INSERT OR IGNORE INTO windows SELECT * FROM old.windows")
   db.commit(); db.execute("DETACH old"); db.close()
   # kalshi_strikes: strikes PK(ts,ticker) + strike_markets (keep earliest first_seen)
   db = sqlite3.connect("/data/kalshi_strikes.db", timeout=120)
   db.execute("ATTACH '/data/kalshi_strikes.snap.db' AS old")
   db.execute("INSERT OR IGNORE INTO strikes SELECT * FROM old.strikes")
   db.execute("INSERT OR IGNORE INTO strike_markets SELECT * FROM old.strike_markets")
   db.execute("""UPDATE strike_markets SET first_seen = o.first_seen
                 FROM old.strike_markets o
                 WHERE strike_markets.ticker = o.ticker
                   AND o.first_seen < strike_markets.first_seen""")
   db.commit(); db.execute("DETACH old"); db.close()
   print("merged")
   EOF
   ```
4. **Verify continuity** (acceptance tests — all must pass before touching OLD):
   ```sql
   -- (a) no gap at the seam: max inter-row gap per symbol in [T0-2h, now]
   --     kalshi_fade: must be <= ~60s; strikes: <= ~120s
   SELECT symbol, MAX(gap) FROM (
     SELECT symbol, ts - LAG(ts) OVER (PARTITION BY symbol ORDER BY ts) AS gap
     FROM obs WHERE ts > :t0 - 7200) GROUP BY symbol;
   -- (b) counts: merged obs rows >= OLD snapshot rows (nothing lost)
   -- (c) dedup: SELECT ts, symbol, COUNT(*) FROM obs GROUP BY 1,2 HAVING COUNT(*)>1;  -- must be empty
   ```
   Then the Track 1 end-to-end proof on NEW (reads archives + merged db):
   ```bash
   .venv/bin/python3.12 /home/johnny/track1/decide_track1.py \
     --db /data/kalshi_fade.db --prereg /home/johnny/track1/prereg_track1.json
   # expect: PEEK ONLY, clusters >= 1246 (the 2026-07-11 peek on OLD)
   ```
   Same-shape check for strikes: row count + distinct tickers ≥ OLD snapshot.
5. Overlap costs nothing — leave BOTH collectors running until §P5. The overlap
   window is the safety net; don't rush to close it.

## P3 — Archives / cron activation on NEW

1. Archives + watermarks were copied in P2.2. Rule from SERVER_STATE.md §5:
   **never let a watermark be newer than the db it guards.** After the P2 merge
   NEW's DBs are supersets of OLD's snapshots, so the copied watermarks are
   valid (≤ max archived ts ≤ db max ts). Safe.
2. Activate on NEW (uncomment): the two archive+prune crons (03:30, 03:50) and
   settlements backfill (03:40).
3. **Disable the same three lines on OLD** the same day — two archivers against
   two diverging DBs is harmless, but single-writer is cleaner. Backfill is
   idempotent; decision cron stays put until §P5.
4. Next morning, check `/data/logs/track1_archive.log` + `track4_archive.log`
   on NEW: archived rows > 0, watermark advanced, prune ran, db size plateau.

## P4 — v3 production cutover (the only downtime, minutes)

1. Pre-stage the 13 GB `v3.db` while OLD runs (cuts final downtime to the delta):
   ```bash
   ssh OLD '… src.backup(dst) … /data/v3.snap.db'      # backup API, as in P2.2
   rsync -avH --inplace johnny@34.67.75.48:/data/v3.snap.db /data/v3.db.staged
   ```
2. Final rsync deltas for `features_v3/`, `models/`, `observe/`, `logs/`,
   `/home/johnny/ofi-lab-v3/` (code), `/var/www/dps-dash/`.
3. **Cutover at a 15-minute boundary** (minimizes a torn prediction window):
   ```bash
   ssh OLD 'sudo systemctl stop v3-paper-trader v3-dashboard v3-ws-feed'
   ssh OLD '… final v3.db backup-API snapshot …'
   rsync -avH --inplace johnny@34.67.75.48:/data/v3.snap.db /data/v3.db.staged
   mv /data/v3.db.staged /data/v3.db
   sudo systemctl start v3-ws-feed v3-paper-trader v3-dashboard
   ```
4. Point the world at NEW: move the reserved static IP (GCP: detach from OLD,
   attach to NEW — same region) **or** flip the `bet.octavo.press` A record
   (TTL was lowered in P0). Reload nginx; confirm
   `https://bet.octavo.press` loads and `/api/…` auths (creds from /etc/v3/env).
5. Verify: `journalctl -u v3-paper-trader -f` shows predictions resuming;
   dashboard freshness widgets green; new rows in v3.db.
6. Activate on NEW the remaining v3 crons (daily features, snapshot_observe,
   weekly retrain); disable them on OLD.
7. This is a paper trader — the gap is a few minutes of *generated* predictions
   at a window boundary, recorded as a service restart, not a data-history hole.

## P5 — Retire OLD (only after every P2.4 test passed)

1. Stop OLD collectors:
   `ssh OLD 'pkill -f "kalshi_[f]ade_daemon"; pkill -f "kalshi_[f]ade_logger"; pkill -f "track4_[s]trike_daemon"; pkill -f "strike_[l]ogger"'`
2. Final incremental merge (repeat P2.2–P2.4 once): snapshot OLD's DBs again,
   `INSERT OR IGNORE` into NEW — catches rows OLD logged after the first merge.
   NEW is now a strict superset for all history. Rerun the continuity queries.
3. `ssh OLD 'crontab -r'` — **guarantees the 07-28 Track 1 decision fires only
   on NEW.** Verify NEW's crontab has the `30 4 28 7 *` line active.
4. Take a final GCE disk snapshot of OLD, then **stop (don't delete)** the
   instance. Keep it 14+ days as rollback. Delete only after the 07-28 decision
   has run and its log is committed.
5. Update Mac `~/.ssh/config` / notes: new IP. Update the repo memory/docs that
   embed `34.67.75.48` if the IP changed.

## Risk table

| risk | guard |
|---|---|
| `cp` of a live WAL db → corrupt snapshot | always the SQLite **backup API** (P2.2, P4.1) |
| Watermark newer than db → prune destroys unarchived blobs | move db+archives+watermark as a unit; merge makes NEW a superset before any NEW-side prune runs (P3.1) |
| Track 1 decision fires twice / never | cron commented on NEW until P3; `crontab -r` on OLD at P5.3; explicit verify step |
| Double paper trader | v3 units installed cold (P1.1), started only at P4.3 after OLD's are stopped |
| Overlap rows differ slightly (two pollers, same second) | PK dedup keeps one row — invariant is "one row per (ts,key)", both candidates are valid book reads |
| Reboot kills collectors (old failure mode) | NEW runs them as systemd units (P1.2) |
| Kalshi 429s from doubled polling | per-IP limits; each server ~2.2 req/s vs 10/s — no interaction |
| Cert renewal breaks after move | rsync /etc/letsencrypt preserves account+renewal conf; first renewal happens post-DNS-flip |
| 07-18 Track 4 checkpoint invalidated | uptime is preserved by the verified merge; schedule transfer off the checkpoint date anyway |
