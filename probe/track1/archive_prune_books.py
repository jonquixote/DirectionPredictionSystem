#!/usr/bin/env python3
"""Archive-then-prune Kalshi orderbook blobs (Track 1 disk guard; Track 3 input preserved).

CRITICAL ORDERING: Track 3 (maker/spread-capture sim) replays obs.orderbook_json.
Pruning without archiving destroys that input. This script therefore:
  1. APPENDS all not-yet-archived blobs (watermark file) to gzip JSONL in --archive-dir
     — the archive holds the FULL history; Track 3 reads archives + hot db.
  2. NULLs orderbook_json on rows older than --keep-days (default 7).
     Freed pages are reused by subsequent inserts, so the db file plateaus even
     without VACUUM (VACUUM needs the logger paused; use --vacuum only then).

Idempotent; run daily via cron. Stdlib only.

Usage: python3 archive_prune_books.py --db /data/kalshi_fade.db --archive-dir /data/archives [--keep-days 7] [--vacuum]
"""
from __future__ import annotations
import argparse, gzip, json, os, sqlite3, sys, time
from datetime import datetime, timezone

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--archive-dir", required=True)
    ap.add_argument("--keep-days", type=int, default=7)
    ap.add_argument("--vacuum", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.archive_dir, exist_ok=True)
    wm_path = os.path.join(a.archive_dir, "kalshi_books.watermark")
    watermark = 0
    if os.path.exists(wm_path):
        watermark = int(open(wm_path).read().strip() or 0)

    db = sqlite3.connect(a.db, timeout=120)
    db.execute("PRAGMA journal_mode=WAL")

    # 1) archive everything new since watermark
    out = os.path.join(a.archive_dir,
                       f"kalshi_books_{datetime.now(timezone.utc):%Y%m%d}.jsonl.gz")
    n, bytes_in, max_ts = 0, 0, watermark
    cur = db.execute(
        "SELECT ts, symbol, ticker, boundary_ts, orderbook_json FROM obs "
        "WHERE ts > ? AND orderbook_json IS NOT NULL ORDER BY ts", (watermark,))
    with gzip.open(out, "at", encoding="utf-8") as gz:
        while True:
            rows = cur.fetchmany(5000)
            if not rows:
                break
            for ts, sym, tick, bts, ob in rows:
                gz.write(json.dumps({"ts": ts, "symbol": sym, "ticker": tick,
                                     "boundary_ts": bts, "book": ob},
                                    separators=(",", ":")) + "\n")
                n += 1; bytes_in += len(ob); max_ts = max(max_ts, ts)
    if n:
        with open(wm_path, "w") as f:
            f.write(str(max_ts))
    print(f"archived: rows={n} raw={bytes_in/1e6:.0f}MB -> {out} "
          f"({os.path.getsize(out)/1e6:.0f}MB on disk) watermark={max_ts}", flush=True)

    # 2) prune blobs older than keep-days (must be <= watermark, i.e. archived)
    cutoff = int(time.time()) - a.keep_days * 86400
    safe_cutoff = min(cutoff, max_ts if max_ts else cutoff)
    r = db.execute("UPDATE obs SET orderbook_json=NULL "
                   "WHERE ts < ? AND orderbook_json IS NOT NULL", (safe_cutoff,))
    db.commit()
    print(f"pruned: {r.rowcount} blobs older than {a.keep_days}d "
          f"(cutoff={datetime.fromtimestamp(safe_cutoff, timezone.utc):%Y-%m-%d %H:%M}Z)")

    if a.vacuum:
        try:
            db.execute("VACUUM")
            print("vacuum: done")
        except sqlite3.OperationalError as e:
            print(f"vacuum: skipped ({e}) — pause the logger and rerun with --vacuum")
    sz = os.path.getsize(a.db)
    print(f"db size now: {sz/1e6:.0f}MB")
    return 0

if __name__ == "__main__":
    sys.exit(main())
