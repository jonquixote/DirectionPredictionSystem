#!/usr/bin/env python3
"""Track 2 — spot->contract repricing-latency logger.

Every ~1.25s, snapshot in PARALLEL (ms timestamps):
  - Coinbase best bid/ask (ticker) x 7 coins
  - Kalshi 15m up/down best yes bid/ask x 7 coins (open ticker cached, refreshed
    every 60s or past close)
  - Polymarket 15m up-token best bid/ask via ONE batch POST /books (token ids
    cached per 15m boundary via one gamma request)
Per-source request latency recorded; snapshot ts = poll start. Rows are summary-only
(no ladders) -> ~60MB/day. Exits at --deadline (unix ts); daemon restarts until then.

Rate budget @1.25s: Coinbase ~5.6 req/s, Kalshi ~5.6 req/s, PM CLOB ~0.8 req/s. All fine.

Usage: latency_logger.py --db /data/track2_latency.db --deadline 1751700000 [--interval 1.25]
"""
from __future__ import annotations
import argparse, json, sqlite3, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

COINS = ["btc", "eth", "sol", "xrp", "doge", "bnb", "hype"]
CB = {c: f"{c.upper()}-USD" for c in COINS}
KSERIES = {c: f"KX{c.upper()}15M" for c in COINS}
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

def get(url, timeout=4):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "track2/1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()), int(1000 * (time.time() - t0))
    except Exception:
        return None, int(1000 * (time.time() - t0))

def post(url, body, timeout=4):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "track2/1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()), int(1000 * (time.time() - t0))
    except Exception:
        return None, int(1000 * (time.time() - t0))

def opendb(path):
    c = sqlite3.connect(path, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS ticks(
        ts_ms INTEGER, coin TEXT,
        cb_bid REAL, cb_ask REAL,
        k_yes_bid REAL, k_yes_ask REAL,
        pm_up_bid REAL, pm_up_ask REAL,
        lat_cb_ms INTEGER, lat_k_ms INTEGER, lat_pm_ms INTEGER,
        boundary_ts INTEGER, pm_srv_ms INTEGER)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ticks ON ticks(coin, ts_ms)")
    # idempotent migration for a pre-existing ticks table
    try:
        c.execute("ALTER TABLE ticks ADD COLUMN pm_srv_ms INTEGER")
    except sqlite3.OperationalError:
        pass
    return c

class Caches:
    def __init__(self):
        self.k_ticker = {}      # coin -> (ticker, close_ts, fetched)
        self.pm_token = {}      # coin -> (up_token, close_ts)

    def kalshi_ticker(self, coin, now):
        t = self.k_ticker.get(coin)
        if t and now < t[1] and now - t[2] < 60:
            return t[0]
        d, _ = get(f"{KALSHI}/markets?series_ticker={KSERIES[coin]}&status=open&limit=1")
        ms = (d or {}).get("markets") or []
        if not ms:
            return t[0] if t else None
        m = ms[0]
        try:
            cts = int(datetime.fromisoformat(
                m["close_time"].replace("Z", "+00:00")).timestamp())
        except Exception:
            cts = now + 60
        self.k_ticker[coin] = (m["ticker"], cts, now)
        return m["ticker"]

    def pm_tokens(self, now):
        close = (now // 900 + 1) * 900
        stale = [c for c in COINS
                 if c not in self.pm_token or self.pm_token[c][1] != close]
        if stale:
            q = "&".join(f"slug={c}-updown-15m-{close}" for c in stale)
            d, _ = get(f"{GAMMA}/markets?closed=false&{q}", timeout=6)
            for m in d or []:
                slug = m.get("slug", "")
                coin = slug.split("-")[0]
                tid = m.get("clobTokenIds"); outc = m.get("outcomes")
                if isinstance(tid, str): tid = json.loads(tid)
                if isinstance(outc, str): outc = json.loads(outc)
                if not tid or not outc:
                    continue
                ui = 0
                for i, o in enumerate(outc):
                    if str(o).strip().lower() == "up":
                        ui = i
                self.pm_token[coin] = (tid[ui], close)
        return {c: v[0] for c, v in self.pm_token.items() if v[1] == close}

def poll_cb(coin):
    d, lat = get(f"https://api.exchange.coinbase.com/products/{CB[coin]}/ticker")
    if not d:
        return coin, None, None, lat
    try:
        return coin, float(d["bid"]), float(d["ask"]), lat
    except Exception:
        return coin, None, None, lat

def poll_kalshi(coin, ticker):
    # response shape (verified 2026-07-03): {"orderbook_fp": {"yes_dollars": [[price,size]...],
    # "no_dollars": [...]}} — bids per side in dollars; yes_ask = 1 - best_no_bid
    d, lat = get(f"{KALSHI}/markets/{ticker}/orderbook?depth=1")
    ob = (d or {}).get("orderbook_fp") or (d or {}).get("orderbook") or {}
    y = ob.get("yes_dollars") or []
    n = ob.get("no_dollars") or []
    try:
        yb = max(float(p) for p, s in y) if y else None
        nb = max(float(p) for p, s in n) if n else None
        ya = (1 - nb) if nb is not None else None
        return coin, yb, ya, lat
    except Exception:
        return coin, None, None, lat

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--deadline", type=int, required=True)
    ap.add_argument("--interval", type=float, default=1.25)
    a = ap.parse_args()
    db = opendb(a.db)
    caches = Caches()
    print(f"track2 logger start {datetime.now(timezone.utc).isoformat()} "
          f"deadline={datetime.fromtimestamp(a.deadline, timezone.utc).isoformat()}", flush=True)
    polls = 0
    ex = ThreadPoolExecutor(max_workers=16)
    while time.time() < a.deadline:
        t0 = time.time(); now = int(t0); ts_ms = int(t0 * 1000)
        boundary = (now // 900) * 900
        ktickers = {c: caches.kalshi_ticker(c, now) for c in COINS}
        ptokens = caches.pm_tokens(now)

        cb_f = {c: ex.submit(poll_cb, c) for c in COINS}
        k_f = {c: ex.submit(poll_kalshi, c, ktickers[c])
               for c in COINS if ktickers.get(c)}
        pm_body = [{"token_id": ptokens[c]} for c in COINS if c in ptokens]
        pm_map = {}
        lat_pm = None
        if pm_body:
            books, lat_pm = post(f"{CLOB}/books", pm_body)
            tok2coin = {ptokens[c]: c for c in ptokens}
            for b in books or []:
                coin = tok2coin.get(b.get("asset_id"))
                if not coin:
                    continue
                try:
                    bb = max((float(x["price"]) for x in b.get("bids") or []), default=None)
                    ba = min((float(x["price"]) for x in b.get("asks") or []), default=None)
                    # server timestamp per book — freshness proof (POST returns live books,
                    # not a cached/CDN response). Kept as a DIAGNOSTIC; ts_ms (local poll)
                    # stays the common lag clock across venues to avoid per-venue clock skew.
                    srv = b.get("timestamp")
                    srv = int(srv) if srv not in (None, "") else None
                    pm_map[coin] = (bb, ba, srv)
                except Exception:
                    pass

        rows = []
        for c in COINS:
            _, cbb, cba, lcb = cb_f[c].result()
            kyb = kya = lk = None
            if c in k_f:
                _, kyb, kya, lk = k_f[c].result()
            pmb, pma, psrv = pm_map.get(c, (None, None, None))
            rows.append((ts_ms, c, cbb, cba, kyb, kya, pmb, pma, lcb, lk, lat_pm, boundary, psrv))
        db.executemany("INSERT INTO ticks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.commit()
        polls += 1
        if polls % 240 == 0:
            n = db.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
            print(f"{datetime.now(timezone.utc):%H:%M:%S} polls={polls} rows={n}", flush=True)
        dt = a.interval - (time.time() - t0)
        if dt > 0:
            time.sleep(dt)
    print("deadline reached — exiting cleanly", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
