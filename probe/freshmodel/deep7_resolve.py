#!/usr/bin/env python3
"""Resolve deep4(contract overprices up, 75c->36%) vs deep6A(spot momentum, +move->continues).
REAL PRICES ONLY. No Gaussian.

For each fired prediction (dedup boundary, earliest fire): join spot dev-from-open
at the fire instant. Then:

 1. Calibration split: bucket by contract up-price AND by whether spot actually
    moved in that direction. Does 'contract rich vs spot' (priced up but spot
    didn't move up) resolve low (structural overpricing), while 'priced up AND
    spot moved up' resolves high (momentum)?

 2. DEPLOYABLE clean bet (no Gaussian): the 'fade the rich contract' rule —
    when contract up-price is high (>=X) but spot move is small (<5bps), buy DOWN
    at the REAL price (1-uppx), hold to close, taker fee. And the momentum rule —
    when spot moved >=Mbps, bet WITH it at real price. EV + CI, OOS.
"""
from __future__ import annotations
import math, bisect, datetime, sqlite3
from pathlib import Path
from collections import defaultdict
import numpy as np
import polars as pl

SPLIT=datetime.datetime(2026,5,27,tzinfo=datetime.timezone.utc).timestamp()*1000
def tfee(e): return 0.07*e*(1-e)
db=sqlite3.connect("file:/data/v3.db?mode=ro",uri=True)

# load spot per symbol
spot={}
for sym in ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]:
    fs=sorted(Path(f"/data/probe_ofi/{sym}").glob("*_ofi.parquet"))
    if not fs: continue
    d=pl.concat([pl.read_parquet(f,columns=["cts","mid_price"]) for f in fs]).sort("cts")
    spot[sym]=((d["cts"].to_numpy()//1000).astype(np.int64), d["mid_price"].to_numpy())

# dedup boundary earliest fire; gather (uppx, out, spot_move_at_fire, ts)
seen=set(); recs=[]
for win,ts,bts,pmk,pdir,sym,res,warm in db.execute(
    "SELECT market_window_seconds,ts_model_ran_ms,ts_contract_open_ms,p_market,pred_direction,symbol,contract_result,warmup "
    "FROM predictions WHERE resolved=1 AND contract_result IN('up','down') AND market_window_seconds IN(300,900) "
    "AND p_market IS NOT NULL ORDER BY ts_model_ran_ms"):
    if warm or not(0<pmk<1) or sym not in spot: continue
    k=(sym,win,bts)
    if k in seen: continue
    seen.add(k)
    cts,mid=spot[sym]
    oi=bisect.bisect_left(cts,bts//1000); fi=bisect.bisect_left(cts,ts//1000)
    if oi>=len(cts) or abs(cts[oi]-bts//1000)>3 or fi>=len(cts): continue
    move=(mid[min(fi,len(mid)-1)]-mid[oi])/mid[oi]   # spot move open->fire (signed return)
    uppx=pmk if pdir=="up" else 1-pmk
    recs.append((uppx,1 if res=="up" else 0,move,ts,win))
print(f"joined boundaries: {len(recs)}")

print("="*84)
print("1. CALIBRATION split by contract up-price x whether spot moved up (>=5bps)  [real]")
print("="*84)
g=defaultdict(lambda:[0,0])
for uppx,out,move,ts,win in recs:
    pbk=min(int(uppx/0.1),9)
    smoved = "spot_up>=5bps" if move>=0.0005 else ("spot_dn>=5bps" if move<=-0.0005 else "spot_flat<5bps")
    k=(pbk,smoved); g[k][1]+=1; g[k][0]+=out
print(f"  {'up_price':9s} {'spot':14s} {'n':>6} {'realized_up%':>12}")
for pbk in range(10):
    for sm in ("spot_up>=5bps","spot_flat<5bps","spot_dn>=5bps"):
        n=g[(pbk,sm)][1]
        if n<30: continue
        print(f"  {pbk*10}-{pbk*10+10}c  {sm:14s} {n:>6} {100*g[(pbk,sm)][0]/n:>11.1f}%")

def ev(trades):
    n=0;sp=0.0;sp2=0.0;cw=0
    for won,entry in trades:
        p=((1-entry) if won else -entry)-tfee(entry); n+=1;sp+=p;sp2+=p*p;cw+=won
    if n<2: return None
    m=sp/n; se=math.sqrt(max(sp2/n-m*m,0)/n); return n,100*cw/n,m,m-1.96*se,m+1.96*se

print("\n"+"="*84)
print("2a. FADE-RICH-CONTRACT (no Gaussian): up_price>=X AND spot_flat(<5bps) -> buy DOWN @real, OOS")
print("="*84)
for X in (0.55,0.60,0.65):
    tr=[((out==0),1-uppx) for uppx,out,move,ts,win in recs if ts>SPLIT and uppx>=X and abs(move)<0.0005]
    r=ev(tr)
    if r: n,acc,m,lo,hi=r; print(f"  X={X:.2f} flat: n={n:>5} downwin%={acc:5.1f} EV={m:+.4f} [{lo:+.4f},{hi:+.4f}] {'+' if lo>0 else ''}")
    # contrast: same up_price but spot DID move up (momentum regime)
    tr2=[((out==0),1-uppx) for uppx,out,move,ts,win in recs if ts>SPLIT and uppx>=X and move>=0.0005]
    r2=ev(tr2)
    if r2: n,acc,m,lo,hi=r2; print(f"        spot-up: n={n:>5} downwin%={acc:5.1f} EV={m:+.4f} [{lo:+.4f},{hi:+.4f}] (momentum regime; expect down loses)")

print("\n2b. MOMENTUM (no Gaussian): spot moved >=Mbps at fire -> bet WITH move @real price, OOS")
for M in (0.0005,0.0010,0.0020):
    tr=[]
    for uppx,out,move,ts,win in recs:
        if ts<=SPLIT or abs(move)<M: continue
        if move>0: entry=uppx; won=(out==1)     # buy up at real up price
        else: entry=1-uppx; won=(out==0)         # buy down at real down price
        if 0.02<entry<0.98: tr.append((won,entry))
    r=ev(tr)
    if r: n,acc,m,lo,hi=r; print(f"  M={M:.4f}: n={n:>6} win%={acc:5.1f} EV={m:+.4f} [{lo:+.4f},{hi:+.4f}] {'+' if lo>0 else ''}")
