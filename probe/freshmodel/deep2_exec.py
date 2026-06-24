#!/usr/bin/env python3
"""Executability gate — real Polymarket spread at the underdog price region.

The edge needs execution within ~1.5c of logged price. Measure the ACTUAL
bid/ask spread from trade prints (probe_track_a.db): in a CLOB, BUY market
orders lift the ask, SELL hit the bid, so over a short window
  ask ~ median(BUY prices), bid ~ median(SELL prices), spread = ask - bid.
Restrict to the underdog region (price 0.35-0.50) where we'd transact.
Report per symbol/duration: median spread, %% of markets with spread<2c/<3c.
If typical spread <~2c, the +EV survives execution; if wider, only maker can.

Proxy caveat: trade-derived spread (no live book). Focus SOL/BTC, OOS overlap.
"""
from __future__ import annotations
import sqlite3, statistics
from collections import defaultdict
db=sqlite3.connect("file:/data/probe_track_a.db?mode=ro",uri=True)

# markets in OOS overlap (boundary 2026-05-27..06-10) for sol/btc/eth, 5m/15m
import datetime
lo=int(datetime.datetime(2026,5,27,tzinfo=datetime.timezone.utc).timestamp())
hi=int(datetime.datetime(2026,6,11,tzinfo=datetime.timezone.utc).timestamp())
mkts=db.execute("SELECT condition_id,symbol,duration_min FROM markets WHERE found=1 AND duration_min IN(5,15) "
                "AND boundary_ts BETWEEN ? AND ? AND symbol IN('sol','btc','eth')",(lo,hi)).fetchall()
print(f"markets in window: {len(mkts)}")

# per (symbol,dur) collect per-market spread in underdog region
spreads=defaultdict(list)       # (sym,dur) -> [spread per market with data]
depth_at_underdog=defaultdict(list)  # (sym,dur)-> total size traded in 0.40-0.50 per market
nproc=0
for cid,sym,dur in mkts:
    rows=db.execute("SELECT ts,price,size,side FROM trades WHERE condition_id=? ORDER BY ts",(cid,)).fetchall()
    if len(rows)<20: continue
    # bucket by 5s; in each bucket estimate ask=median buy, bid=median sell in underdog region
    buck=defaultdict(lambda:([],[]))   # tsec5 -> (buys, sells)
    sz_und=0.0
    for ts,price,size,side in rows:
        if 0.35<=price<=0.50:
            sz_und+=size
        b=buck[ts//5]
        if side=="BUY": b[0].append(price)
        else: b[1].append(price)
    msp=[]
    for tsec,(bs,ss) in buck.items():
        if bs and ss:
            ask=statistics.median(bs); bid=statistics.median(ss)
            if 0.30<=((ask+bid)/2)<=0.55 and ask>=bid:   # near underdog region
                msp.append(ask-bid)
    if msp:
        spreads[(sym,dur)].append(statistics.median(msp))
    depth_at_underdog[(sym,dur)].append(sz_und)
    nproc+=1

print(f"markets processed: {nproc}\n")
print("="*72)
print("REAL SPREAD at underdog region (trade-derived bid/ask), per symbol x duration")
print("="*72)
print("  sym/dur     mkts   median_spread  %<2c   %<3c   med_underdog_depth($)")
for (sym,dur) in sorted(spreads):
    sp=spreads[(sym,dur)]
    if not sp: continue
    med=statistics.median(sp)
    p2=100*sum(1 for s in sp if s<0.02)/len(sp)
    p3=100*sum(1 for s in sp if s<0.03)/len(sp)
    dep=depth_at_underdog[(sym,dur)]
    medd=statistics.median(dep) if dep else 0
    print(f"  {sym:3s} {dur:>2}m   {len(sp):>5}   {100*med:5.2f}c       {p2:4.0f}%  {p3:4.0f}%   ${medd:,.0f}")

print("\nread: half-spread = spread/2 is the taker execution cost vs mid.")
print("edge dies at ~1.5c extra slip beyond the 0.5c already modeled -> need spread <~4c (half<2c).")
print("if median spread < ~3c and depth supports size, taker-executable; else maker-only.")
