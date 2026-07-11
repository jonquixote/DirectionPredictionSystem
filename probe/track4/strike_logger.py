#!/usr/bin/env python3
"""Track 4 collector — Kalshi BTC/ETH strike (above/below) books + Coinbase spot.

Per prereg_track4.json: markets closing within 26h with strike within +/-3% of
spot, series KXBTC/KXBTCD/KXETH/KXETHD, 30s cadence, <=40 books/poll.
Stores the raw book (dollars format) + summary yes_bid/yes_ask + spot per row.
Fail-loud: no cached prices, no fallbacks — unfetchable book => no row for that
ticker this poll (gap is visible); one-sided book => NULL summary, blob kept.

Usage: strike_logger.py --db /data/kalshi_strikes.db [--interval 30]
"""
from __future__ import annotations
import argparse, json, sqlite3, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = {"BTC": ["KXBTC", "KXBTCD"], "ETH": ["KXETH", "KXETHD"]}
PAIR = {"BTC": "BTC-USD", "ETH": "ETH-USD"}
HORIZON_S = 26 * 3600
STRIKE_PCT = 0.03
MAX_BOOKS = 40
DISCOVER_EVERY_S = 600

SCHEMA = """
CREATE TABLE IF NOT EXISTS strikes (
  ts INTEGER NOT NULL,
  underlying TEXT NOT NULL,
  series TEXT NOT NULL,
  ticker TEXT NOT NULL,
  strike REAL,
  close_ts INTEGER,
  yes_bid REAL, yes_ask REAL,
  spot REAL,
  orderbook_json TEXT,
  PRIMARY KEY (ts, ticker)
);
CREATE INDEX IF NOT EXISTS ix_strikes_ticker_ts ON strikes(ticker, ts);
CREATE TABLE IF NOT EXISTS strike_markets (
  ticker TEXT PRIMARY KEY, underlying TEXT, series TEXT, strike REAL,
  close_ts INTEGER, first_seen INTEGER, result TEXT, settled INTEGER DEFAULT 0
);
"""


def get(url: str, timeout=15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "track4-strikes/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def spot(und: str) -> float:
    d = get(f"https://api.coinbase.com/v2/prices/{PAIR[und]}/spot")
    return float(d["data"]["amount"])


def iso_ts(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def discover(px: dict) -> list[dict]:
    """Markets closing within 26h, strike within +/-3% of spot; nearest first."""
    now = time.time()
    out = []
    for und, serlist in SERIES.items():
        cand = []
        for s in serlist:
            try:
                d = get(f"{BASE}/markets?series_ticker={s}&status=open&limit=200")
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                print(f"[discover] {s}: {e}", flush=True)
                continue
            for m in d.get("markets", []):
                strike = m.get("floor_strike") or m.get("cap_strike")
                ct = m.get("close_time")
                if strike is None or not ct:
                    continue
                cts = iso_ts(ct)
                if not (now < cts <= now + HORIZON_S):
                    continue
                if abs(float(strike) - px[und]) / px[und] > STRIKE_PCT:
                    continue
                cand.append({"ticker": m["ticker"], "underlying": und,
                             "series": s, "strike": float(strike), "close_ts": cts})
            time.sleep(0.3)
        cand.sort(key=lambda c: abs(c["strike"] - px[und]))
        out.extend(cand[:MAX_BOOKS // len(SERIES)])
    return out


def fetch_book(ticker: str):
    d = get(f"{BASE}/markets/{ticker}/orderbook")
    ob = d.get("orderbook") or d.get("orderbook_fp") or {}
    yd, nd = ob.get("yes_dollars"), ob.get("no_dollars")
    yb = max((float(p) for p, _ in yd), default=None) if yd else None
    ya = (1.0 - max(float(p) for p, _ in nd)) if nd else None
    return yb, ya, json.dumps(ob, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--interval", type=float, default=30.0)
    a = ap.parse_args()
    db = sqlite3.connect(a.db, timeout=60)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)

    tracked: list[dict] = []
    last_disc = 0.0
    print(f"strike_logger start {datetime.now(timezone.utc):%F %T}Z "
          f"interval={a.interval}s", flush=True)
    while True:
        t0 = time.time()
        try:
            px = {u: spot(u) for u in SERIES}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print(f"[spot] {e} — skipping poll", flush=True)
            time.sleep(a.interval)
            continue
        if t0 - last_disc > DISCOVER_EVERY_S or not tracked:
            tracked = discover(px)
            last_disc = t0
            ts_now = int(t0)
            for c in tracked:
                db.execute(
                    "INSERT OR IGNORE INTO strike_markets "
                    "(ticker, underlying, series, strike, close_ts, first_seen) "
                    "VALUES (?,?,?,?,?,?)",
                    (c["ticker"], c["underlying"], c["series"], c["strike"],
                     c["close_ts"], ts_now))
            db.commit()
            print(f"[discover] tracking {len(tracked)} markets", flush=True)
        ts = int(time.time())
        live = [c for c in tracked if c["close_ts"] > ts]

        def poll(c):
            try:
                yb, ya, blob = fetch_book(c["ticker"])
                return (ts, c["underlying"], c["series"], c["ticker"], c["strike"],
                        c["close_ts"], yb, ya, px[c["underlying"]], blob)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    return "RATE"
                print(f"[book] {c['ticker']}: HTTP {e.code}", flush=True)
                return None
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                print(f"[book] {c['ticker']}: {e}", flush=True)
                return None

        rate_hit = False
        with ThreadPoolExecutor(max_workers=6) as ex:
            for r in ex.map(poll, live):
                if r == "RATE":
                    rate_hit = True
                elif r:
                    db.execute("INSERT OR REPLACE INTO strikes VALUES "
                               "(?,?,?,?,?,?,?,?,?,?)", r)
        db.commit()
        if rate_hit:
            print("[rate] 429 seen — backing off 10s", flush=True)
            time.sleep(10)
        dt = time.time() - t0
        time.sleep(max(0.0, a.interval - dt))


if __name__ == "__main__":
    sys.exit(main())
