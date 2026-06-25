#!/usr/bin/env python3
"""Kalshi price + Bybit spot logger — accumulates the dataset to validate the
fade-rich edge on KALSHI prices (it was measured on Polymarket; that transfer
is the one offline-unverifiable gate).

Per poll (~10s), for each of BTC/ETH/SOL/XRP 15-min up/down markets:
  - Kalshi: current open market ticker, yes_bid/yes_ask/last, full orderbook_fp
    (raw JSON — derive executable down-ask + depth offline, no parse assumption)
  - Bybit spot last price (REST), and the window-OPEN spot (first obs of each window)
Store raw to SQLite. Resolution derived later from the logged spot path + Kalshi
settlement. Stdlib only (urllib) — no deps. Runs forever; resumable.

Usage (VPS):  python3 logger.py --db /data/kalshi_fade.db --interval 10
"""
from __future__ import annotations
import argparse, json, sqlite3, time, urllib.request, urllib.error, sys
from datetime import datetime, timezone

KALSHI="https://api.elections.kalshi.com/trade-api/v2"
# Bybit REST is geo-blocked (403) from the GCP datacenter. Coinbase works and the
# edge filter only needs dev-from-WINDOW-OPEN (intra-15min), where cross-venue
# basis is ~constant -> Coinbase spot is a sound proxy for the spot-flat filter.
COINBASE="https://api.exchange.coinbase.com/products/{}/ticker"
SERIES={"BTCUSDT":"KXBTC15M","ETHUSDT":"KXETH15M","SOLUSDT":"KXSOL15M","XRPUSDT":"KXXRP15M"}
CBPROD={"BTCUSDT":"BTC-USD","ETHUSDT":"ETH-USD","SOLUSDT":"SOL-USD","XRPUSDT":"XRP-USD"}

def get(url, timeout=8):
    try:
        req=urllib.request.Request(url, headers={"User-Agent":"fade-logger/1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"_err": str(e)}

def opendb(path):
    c=sqlite3.connect(path, timeout=30); c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS obs(
      ts INTEGER, symbol TEXT, ticker TEXT, boundary_ts INTEGER, close_ts INTEGER,
      yes_bid REAL, yes_ask REAL, last_price REAL, volume REAL,
      spot REAL, spot_open REAL, dev_bps REAL, phase REAL, orderbook_json TEXT);
    CREATE INDEX IF NOT EXISTS idx_obs ON obs(symbol, boundary_ts, ts);
    CREATE TABLE IF NOT EXISTS windows(
      symbol TEXT, boundary_ts INTEGER, close_ts INTEGER, ticker TEXT,
      spot_open REAL, spot_close REAL, kalshi_result TEXT, settled INTEGER DEFAULT 0,
      PRIMARY KEY(symbol, boundary_ts));
    """)
    return c

def parse_boundary(m):
    # close_time ISO -> close_ts; boundary = close - 900
    ct=m.get("close_time")
    if not ct: return None,None
    try:
        cts=int(datetime.fromisoformat(ct.replace("Z","+00:00")).timestamp())
        return cts-900, cts
    except Exception:
        return None,None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--interval", type=int, default=10)
    a=ap.parse_args()
    db=opendb(a.db)
    open_spot={}   # (symbol,boundary)-> first spot
    print(f"logger start {datetime.now(timezone.utc).isoformat()} db={a.db}", flush=True)
    polls=0
    while True:
        t0=time.time(); now=int(t0)
        for sym,series in SERIES.items():
            mk=get(f"{KALSHI}/markets?series_ticker={series}&status=open&limit=1")
            ms=mk.get("markets") if isinstance(mk,dict) else None
            if not ms: continue
            m=ms[0]; ticker=m.get("ticker")
            bts,cts=parse_boundary(m)
            ob=get(f"{KALSHI}/markets/{ticker}/orderbook?depth=30")
            bb=get(COINBASE.format(CBPROD[sym]))
            spot=None
            try: spot=float(bb["price"])
            except Exception: pass
            if spot is None or bts is None: continue
            k=(sym,bts)
            if k not in open_spot:
                open_spot[k]=spot
                db.execute("INSERT OR IGNORE INTO windows(symbol,boundary_ts,close_ts,ticker,spot_open) VALUES(?,?,?,?,?)",
                           (sym,bts,cts,ticker,spot))
            sopen=open_spot[k]
            dev_bps=(spot-sopen)/sopen*1e4 if sopen else 0.0
            phase=(now-bts)/900.0
            db.execute("INSERT INTO obs(ts,symbol,ticker,boundary_ts,close_ts,yes_bid,yes_ask,last_price,volume,spot,spot_open,dev_bps,phase,orderbook_json) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (now,sym,ticker,bts,cts,m.get("yes_bid"),m.get("yes_ask"),m.get("last_price"),m.get("volume"),
                        spot,sopen,dev_bps,phase,json.dumps(ob.get("orderbook_fp",ob))))
            # update spot_close (latest) on the window row
            db.execute("UPDATE windows SET spot_close=? WHERE symbol=? AND boundary_ts=?",(spot,sym,bts))
        db.commit(); polls+=1
        if polls%30==0:
            n=db.execute("SELECT COUNT(*) FROM obs").fetchone()[0]
            w=db.execute("SELECT COUNT(*) FROM windows").fetchone()[0]
            print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} polls={polls} obs={n} windows={w}", flush=True)
        dt=a.interval-(time.time()-t0)
        if dt>0: time.sleep(dt)

if __name__=="__main__":
    sys.exit(main())
