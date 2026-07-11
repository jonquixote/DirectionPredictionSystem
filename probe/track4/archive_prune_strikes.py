#!/usr/bin/env python3
"""Archive-then-prune for /data/kalshi_strikes.db (Track 4 disk guard).

Same pattern as track1/archive_prune_books.py, adapted to the strikes table:
1. append not-yet-archived blobs (watermark) to gzip JSONL in --archive-dir;
2. NULL orderbook_json on rows older than --keep-days (summary cols kept forever).

Usage: archive_prune_strikes.py --db /data/kalshi_strikes.db --archive-dir /data/archives [--keep-days 7]
"""
from __future__ import annotations
import argparse, gzip, json, os, sqlite3, sys, time
from datetime import datetime, timezone

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--archive-dir", required=True)
    ap.add_argument("--keep-days", type=int, default=7)
    a = ap.parse_args()
    os.makedirs(a.archive_dir, exist_ok=True)
    wm_path = os.path.join(a.archive_dir, "kalshi_strikes.watermark")
    watermark = int(open(wm_path).read().strip() or 0) if os.path.exists(wm_path) else 0

    db = sqlite3.connect(a.db, timeout=120)
    db.execute("PRAGMA journal_mode=WAL")
    out = os.path.join(a.archive_dir,
                       f"kalshi_strikes_{datetime.now(timezone.utc):%Y%m%d}.jsonl.gz")
    n, bytes_in, max_ts = 0, 0, watermark
    cur = db.execute(
        "SELECT ts, underlying, series, ticker, strike, close_ts, orderbook_json "
        "FROM strikes WHERE ts > ? AND orderbook_json IS NOT NULL ORDER BY ts",
        (watermark,))
    with gzip.open(out, "at", encoding="utf-8") as gz:
        while True:
            rows = cur.fetchmany(5000)
            if not rows:
                break
            for ts, und, ser, tick, strike, cts, ob in rows:
                gz.write(json.dumps(
                    {"ts": ts, "underlying": und, "series": ser, "ticker": tick,
                     "strike": strike, "close_ts": cts, "book": ob},
                    separators=(",", ":")) + "\n")
                n += 1; bytes_in += len(ob); max_ts = max(max_ts, ts)
    if n:
        with open(wm_path, "w") as f:
            f.write(str(max_ts))
    print(f"archived: rows={n} raw={bytes_in/1e6:.0f}MB -> {out} "
          f"({os.path.getsize(out)/1e6:.0f}MB on disk) watermark={max_ts}", flush=True)

    cutoff = int(time.time()) - a.keep_days * 86400
    safe_cutoff = min(cutoff, max_ts if max_ts else cutoff)
    r = db.execute("UPDATE strikes SET orderbook_json=NULL "
                   "WHERE ts < ? AND orderbook_json IS NOT NULL", (safe_cutoff,))
    db.commit()
    print(f"pruned: {r.rowcount} blobs older than {a.keep_days}d "
          f"(cutoff={datetime.fromtimestamp(safe_cutoff, timezone.utc):%F %H:%M}Z)")
    print(f"db size now: {os.path.getsize(a.db)/1e6:.0f}MB")
    return 0

if __name__ == "__main__":
    sys.exit(main())
