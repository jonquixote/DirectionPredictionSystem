#!/usr/bin/env python3
"""Polymarket up/down PRICE + DOWN-BOOK DEPTH logger — forward collection.

Two jobs the offline data can't answer:
  1) DEPTH AT RICH MOMENTS (job C): the fade fires when up>=0.55 (down<=0.45). At-the-
     money snapshots show ~$0 resting in the 30-49c band, but that's because nothing is
     rich at that instant. Polling continuously catches windows AS they go rich and records
     the down-side ask depth THERE -> settles whether Polymarket-direct execution is viable.
  2) NEW-COIN PRICE PATHS (job B): doge/bnb/hype have ~59d Polymarket history but their
     intra-window prices are only in the (disk-blocked, 33GB) trade harvest. Going forward,
     one snapshot/poll captures their price path cheaply -> does the long-bias hold on
     retail/degen coins.

Per poll (~12s), for each coin x {5m,15m} live window:
  - gamma: current open market (clobTokenIds, conditionId, slug, boundary)
  - CLOB /book on the DOWN token: best_down_ask (cost to BUY down now), depth in 30-49c
    fade band ($ notional), full raw book JSON (derive anything offline, no parse lock-in)
  - Coinbase spot + window-OPEN spot (first obs) -> dev_bps (the spot-flat filter input)
Down-mid -> up_mid = 1 - down_mid. Store raw to SQLite. Resolution derived later from the
logged spot path (down wins if spot_close < spot_open) and/or Polymarket settlement.
Stdlib only (urllib). Runs forever; resumable.

Usage (VPS): python3 logger.py --db /data/pm_depth.db --interval 12
"""
from __future__ import annotations
import argparse, json, sqlite3, time, urllib.request, sys
from datetime import datetime, timezone

GAMMA="https://gamma-api.polymarket.com"
CLOB="https://clob.polymarket.com"
COINBASE="https://api.exchange.coinbase.com/products/{}/ticker"
COINS=["btc","eth","sol","xrp","doge","bnb","hype"]
DURS={"5m":300,"15m":900}
CBPROD={"btc":"BTC-USD","eth":"ETH-USD","sol":"SOL-USD","xrp":"XRP-USD",
        "doge":"DOGE-USD","bnb":"BNB-USD","hype":"HYPE-USD"}

def get(url, timeout=10):
    try:
        req=urllib.request.Request(url, headers={"User-Agent":"pm-depth/1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"_err": str(e)}

def opendb(path):
    c=sqlite3.connect(path, timeout=30); c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS obs(
      ts INTEGER, coin TEXT, dur TEXT, slug TEXT, boundary_ts INTEGER, close_ts INTEGER,
      best_down_ask REAL, down_mid REAL, up_mid REAL,
      down_band_depth REAL, down_book_total REAL,
      gamma_last REAL, gamma_up REAL,
      spot REAL, spot_open REAL, dev_bps REAL, phase REAL, book_json TEXT);
    CREATE INDEX IF NOT EXISTS idx_obs ON obs(coin, dur, boundary_ts, ts);
    CREATE TABLE IF NOT EXISTS windows(
      coin TEXT, dur TEXT, boundary_ts INTEGER, close_ts INTEGER, slug TEXT,
      condition_id TEXT, down_token TEXT,
      spot_open REAL, spot_close REAL,
      PRIMARY KEY(coin, dur, boundary_ts));
    """)
    # idempotent migration: add gamma columns to a pre-existing obs table
    for col in ("gamma_last","gamma_up"):
        try: c.execute(f"ALTER TABLE obs ADD COLUMN {col} REAL")
        except sqlite3.OperationalError: pass
    return c

def parse_tokens(m):
    """Return (down_token_id, outcomes) — Polymarket outcomes/clobTokenIds are
    parallel arrays; pick the index whose outcome string is 'Down'."""
    tid=m.get("clobTokenIds"); outc=m.get("outcomes")
    if isinstance(tid,str): tid=json.loads(tid)
    if isinstance(outc,str): outc=json.loads(outc)
    if not tid or not outc or len(tid)!=len(outc): return None
    di=None
    for i,o in enumerate(outc):
        if str(o).strip().lower()=="down": di=i
    if di is None: di=1 if len(tid)>1 else 0   # convention fallback: [Up,Down]
    up_idx=(1-di) if len(tid)==2 else None     # for Gamma outcomePrices[up]
    return tid[di], up_idx

def band_depth(levels, lo, hi):
    return sum(float(L["price"])*float(L["size"]) for L in levels
              if lo<=float(L["price"])<=hi)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--interval", type=int, default=12)
    a=ap.parse_args()
    db=opendb(a.db)
    open_spot={}
    print(f"pm-depth logger start {datetime.now(timezone.utc).isoformat()} db={a.db}", flush=True)
    polls=0
    while True:
        t0=time.time(); now=int(t0)
        # The active window's slug = close time of the window currently in progress.
        # (gamma's startDate-ordered event list returns NEXT-DAY pre-listed markets, NOT
        # the live one — so construct the active slug directly: close = next dur-boundary.)
        slugmap={}
        for coin in COINS:
            for dur,secs in DURS.items():
                close=(now//secs+1)*secs
                slugmap[f"{coin}-updown-{dur}-{close}"]=(coin,dur,close,secs)
        q="&".join(f"slug={s}" for s in slugmap)
        data=get(f"{GAMMA}/markets?closed=false&{q}")
        live={}
        if isinstance(data,list):
            for m in data:
                s=m.get("slug")
                if s in slugmap:
                    coin,dur,close,secs=slugmap[s]
                    live[(coin,dur)]=(s, close-secs, close, m)
        spot_cache={}
        for (coin,dur),(slug,bts,cts,m) in live.items():
            pt=parse_tokens(m)
            if not pt: continue
            dtok,up_idx=pt
            # Gamma prices — the NON-executable side. v3's get_p_market falls back to
            # outcomePrices[up] (cached, stale, can spike to ~0.99 while book sits at .50)
            # whenever CLOB /midpoint fails. Logging both lets us quantify, forward, how
            # far p_market diverges from the executable book-mid (the artifact magnitude).
            glast=m.get("lastTradePrice")
            try: glast=float(glast)
            except Exception: glast=None
            gup=None; op=m.get("outcomePrices")
            if isinstance(op,str):
                try: op=json.loads(op)
                except Exception: op=None
            if op and up_idx is not None and up_idx<len(op):
                try: gup=float(op[up_idx])
                except Exception: gup=None
            bk=get(f"{CLOB}/book?token_id={dtok}")
            asks=bk.get("asks") if isinstance(bk,dict) else None
            bids=bk.get("bids") if isinstance(bk,dict) else None
            if asks is None: continue
            best_ask=min((float(x["price"]) for x in asks), default=None)
            best_bid=max((float(x["price"]) for x in bids), default=None) if bids else None
            down_mid=((best_ask+best_bid)/2) if (best_ask is not None and best_bid is not None) else best_ask
            up_mid=(1-down_mid) if down_mid is not None else None
            band=band_depth(asks,0.30,0.49)
            tot=band_depth(asks,0.01,0.99)
            if coin not in spot_cache:
                bb=get(COINBASE.format(CBPROD[coin]))
                try: spot_cache[coin]=float(bb["price"])
                except Exception: spot_cache[coin]=None
            spot=spot_cache[coin]
            if spot is None: continue
            k=(coin,dur,bts)
            if k not in open_spot:
                open_spot[k]=spot
                db.execute("INSERT OR IGNORE INTO windows(coin,dur,boundary_ts,close_ts,slug,condition_id,down_token,spot_open) VALUES(?,?,?,?,?,?,?,?)",
                           (coin,dur,bts,cts,slug,m.get("conditionId"),dtok,spot))
            sopen=open_spot[k]
            dev_bps=(spot-sopen)/sopen*1e4 if sopen else 0.0
            phase=(now-bts)/DURS[dur]
            # DECLARED truncation (disk guard, VPS /data ~11G free): summary cols
            # (best_ask/mids/band/total) ALWAYS stored — nothing fade-relevant lost.
            # Full raw ladder kept ONLY when down is rich-ish (mid<=0.49), i.e. exactly
            # the windows going into the fade zone. At-the-money books -> book_json NULL.
            bj=json.dumps({"asks":asks,"bids":bids}) if (down_mid is not None and down_mid<=0.49) else None
            db.execute("INSERT INTO obs(ts,coin,dur,slug,boundary_ts,close_ts,best_down_ask,down_mid,up_mid,down_band_depth,down_book_total,gamma_last,gamma_up,spot,spot_open,dev_bps,phase,book_json) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (now,coin,dur,slug,bts,cts,best_ask,down_mid,up_mid,band,tot,glast,gup,spot,sopen,dev_bps,phase,bj))
            db.execute("UPDATE windows SET spot_close=? WHERE coin=? AND dur=? AND boundary_ts=?",(spot,coin,dur,bts))
        db.commit(); polls+=1
        if polls%20==0:
            n=db.execute("SELECT COUNT(*) FROM obs").fetchone()[0]
            w=db.execute("SELECT COUNT(*) FROM windows").fetchone()[0]
            rich=db.execute("SELECT COUNT(*) FROM obs WHERE up_mid>=0.55").fetchone()[0]
            print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} polls={polls} obs={n} windows={w} rich(up>=.55)={rich}", flush=True)
        dt=a.interval-(time.time()-t0)
        if dt>0: time.sleep(dt)

if __name__=="__main__":
    sys.exit(main())
