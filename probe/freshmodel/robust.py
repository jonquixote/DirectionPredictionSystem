#!/usr/bin/env python3
"""Build-candidate robustness — three decision-relevant checks (not fishing).

Final rule: up_price>=X (Polymarket p_market) AND Bybit spot dev-from-open <T bps
AND down entry in liquid band [0.30,0.49] -> buy DOWN @real, taker, hold to close.

 A. WEEKLY WALK-FORWARD equity curve of the rule (rising vs regime-lumpy?).
 B. THRESHOLD SENSITIVITY: X x T x entry-band (knife-edge vs robust; set params).
 C. CROSS-SYMBOL FIRE CORRELATION: how many symbols fire in the same boundary
    minute (independence assumption behind capacity/Kelly).
All OOS (post 2026-05-27), real prices, taker fee.
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

spot={}
for sym in ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]:
    fs=sorted(Path(f"/data/probe_ofi/{sym}").glob("*_ofi.parquet"))
    if not fs: continue
    d=pl.concat([pl.read_parquet(f,columns=["cts","mid_price"]) for f in fs]).sort("cts")
    spot[sym]=((d["cts"].to_numpy()//1000).astype(np.int64), d["mid_price"].to_numpy())

seen=set(); recs=[]   # (uppx,out,move_bps,ts,win,sym,bts)
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
    move_bps=abs(mid[min(fi,len(mid)-1)]-mid[oi])/mid[oi]*1e4
    uppx=pmk if pdir=="up" else 1-pmk
    recs.append((uppx,1 if res=="up" else 0,move_bps,ts,win,sym,bts))

def fires(X=0.55,T=5.0,elo=0.30,ehi=0.49,oos=True):
    for uppx,out,mb,ts,win,sym,bts in recs:
        if oos and ts<=SPLIT: continue
        if uppx<X or mb>=T: continue
        entry=1-uppx
        if not(elo<=entry<=ehi): continue
        won=(out==0)
        pnl=((1-entry) if won else -entry)-tfee(entry)
        yield pnl,won,ts,sym,bts

def evci(pnls):
    a=np.array(pnls)
    if len(a)<2: return None
    m=a.mean(); se=a.std()/math.sqrt(len(a)); return len(a),m,m-1.96*se,m+1.96*se

# ---- A weekly walk-forward ----
print("="*80); print("A. WEEKLY WALK-FORWARD — final rule (up>=0.55, spot<5bps, entry 30-49c), taker"); print("="*80)
wk=defaultdict(list)
for pnl,won,ts,sym,bts in fires():
    w=datetime.datetime.fromtimestamp(ts/1000,datetime.timezone.utc).strftime("%G-W%V")
    wk[w].append((pnl,won))
cum=0.0
print("  week      n     acc%   weekEV    cumEV/$")
for w in sorted(wk):
    arr=wk[w]; n=len(arr); ev=sum(p for p,_ in arr)/n; cum+=sum(p for p,_ in arr)
    print(f"  {w}  {n:>5} {100*sum(x for _,x in arr)/n:5.1f}  {ev:+.4f}   {cum:+.2f}")

# ---- B threshold sensitivity ----
print("\n"+"="*80); print("B. THRESHOLD SENSITIVITY (X up-price x T spot-flat bps; entry 30-49c), OOS"); print("="*80)
print("  X     T(bps)   n      acc%   EV        CI")
for X in (0.55,0.58,0.60):
    for T in (3.0,5.0,10.0):
        r=evci([p for p,_,_,_,_ in fires(X=X,T=T)])
        if r: n,m,lo,hi=r; print(f"  {X:.2f}  {T:>4.0f}   {n:>6} {100*sum(1 for p,w,_,_,_ in fires(X=X,T=T) if w)/n:5.1f}  {m:+.4f}  [{lo:+.4f},{hi:+.4f}] {'+' if lo>0 else ''}")
print("  entry-band variants at X=0.55,T=5:")
for elo,ehi in ((0.30,0.49),(0.35,0.49),(0.40,0.49),(0.30,0.45)):
    r=evci([p for p,_,_,_,_ in fires(elo=elo,ehi=ehi)])
    if r: n,m,lo,hi=r; print(f"    entry[{elo:.2f},{ehi:.2f}]: n={n:>5} EV={m:+.4f} [{lo:+.4f},{hi:+.4f}]")

# ---- C cross-symbol fire correlation ----
print("\n"+"="*80); print("C. CROSS-SYMBOL FIRE CORRELATION (rule fires per boundary-minute)"); print("="*80)
bymin=defaultdict(set)
for pnl,won,ts,sym,bts in fires():
    bymin[bts//60].add(sym)
dist=defaultdict(int)
for mn,syms in bymin.items(): dist[len(syms)]+=1
tot=sum(dist.values())
print(f"  fire-boundaries: {tot}")
for k in sorted(dist):
    print(f"    {k} symbol(s) firing together: {dist[k]} ({100*dist[k]/tot:.0f}%)")
indep_factor = sum(1 for _ in bymin)/sum(len(s) for s in bymin.values()) if bymin else 0
print(f"  effective-independence: {sum(len(s) for s in bymin.values())} fires across {tot} boundaries")
print(f"  -> if mostly 1-symbol, fires ~independent (capacity/Kelly OK); if 3-4 together, correlated")
