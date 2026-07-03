#!/usr/bin/env python3
"""Populate windows.kalshi_result from Kalshi settlement (Track 1).

Paginates GET /markets?series_ticker=KX{COIN}15M&status=settled per series,
maps ticker -> result ('yes'|'no'), updates windows rows by ticker.
Semantics verified 2026-07-03: title "BTC price up in next 15 mins?", result=yes => UP won.
Idempotent; run daily (cron) or on demand. Stdlib only.

Usage: python3 backfill_settlements.py --db /data/kalshi_fade.db
"""
from __future__ import annotations
import argparse, json, sqlite3, time, urllib.request, urllib.parse, sys

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ["KXBTC15M","KXETH15M","KXSOL15M","KXXRP15M","KXDOGE15M","KXBNB15M","KXHYPE15M"]

def get(url, timeout=15, retries=4):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "track1-backfill/1"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(2 ** i)
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    a = ap.parse_args()
    db = sqlite3.connect(a.db, timeout=60)

    results = {}   # ticker -> 'yes'|'no'
    for series in SERIES:
        cursor = ""
        pages = 0
        while True:
            q = f"{KALSHI}/markets?series_ticker={series}&status=settled&limit=1000"
            if cursor:
                q += f"&cursor={urllib.parse.quote(cursor)}"
            d = get(q)
            if not d or not d.get("markets"):
                break
            for m in d["markets"]:
                r = m.get("result")
                if r in ("yes", "no"):
                    results[m["ticker"]] = r
            cursor = d.get("cursor") or ""
            pages += 1
            if not cursor or pages > 40:
                break
            time.sleep(0.25)
        print(f"{series}: settled markets fetched so far total={len(results)}", flush=True)

    todo = db.execute(
        "SELECT ticker FROM windows WHERE kalshi_result IS NULL AND ticker IS NOT NULL "
        "AND close_ts < strftime('%s','now')").fetchall()
    matched = 0
    for (t,) in todo:
        r = results.get(t)
        if r:
            db.execute("UPDATE windows SET kalshi_result=?, settled=1 WHERE ticker=?", (r, t))
            matched += 1
    db.commit()
    still = db.execute("SELECT COUNT(*) FROM windows WHERE kalshi_result IS NULL "
                       "AND close_ts < strftime('%s','now')").fetchone()[0]
    tot = db.execute("SELECT COUNT(*) FROM windows").fetchone()[0]
    print(f"backfill: updated={matched} of {len(todo)} pending; still_missing={still} / total_windows={tot}")
    # diagnostics: spot-proxy vs settlement agreement (reported only; test uses settlement)
    agree, dis, n = 0, 0, 0
    for so, sc, res in db.execute(
        "SELECT spot_open, spot_close, kalshi_result FROM windows "
        "WHERE kalshi_result IS NOT NULL AND spot_open IS NOT NULL AND spot_close IS NOT NULL"):
        proxy_up = sc >= so   # contract convention: flat resolves UP
        n += 1
        if proxy_up == (res == "yes"):
            agree += 1
        else:
            dis += 1
    if n:
        print(f"spot-proxy vs settlement: n={n} agree={100*agree/n:.2f}% disagree={dis}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
