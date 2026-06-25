#!/usr/bin/env python3
"""Is the soft W24 regime-driven or decay? Characterize the rule weekly across ALL
available weeks (3 OOS weeks is too few to judge variance) with regime markers.

Per week: n fires, acc, EV, mean up-price (richness of fires), mean entry, the
structural gap (acc - entry), weekly spot volatility (mean |1s ret| x sqrt scale),
weekly drift (up-fraction). If W24's gap collapsed -> decay/efficiency. If the gap
held but realized acc was unlucky / fires were on thinner setups -> variance/regime.
"""
from __future__ import annotations
import math, bisect, datetime, sqlite3
from pathlib import Path
from collections import defaultdict
import numpy as np
import polars as pl

def tfee(e): return 0.07*e*(1-e)
db=sqlite3.connect("file:/data/v3.db?mode=ro",uri=True)

spot={}; wkvol=defaultdict(list); wkdrift=defaultdict(lambda:[0,0])
for sym in ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]:
    fs=sorted(Path(f"/data/probe_ofi/{sym}").glob("*_ofi.parquet"))
    if not fs: continue
    d=pl.concat([pl.read_parquet(f,columns=["cts","mid_price"]) for f in fs]).sort("cts")
    cts=(d["cts"].to_numpy()//1000).astype(np.int64); mid=d["mid_price"].to_numpy()
    spot[sym]=(cts,mid)
    # weekly realized vol: std of 60s returns
    step=60
    for i in range(0,len(cts)-step,step):
        w=datetime.datetime.fromtimestamp(cts[i],datetime.timezone.utc).strftime("%G-W%V")
        if mid[i]>0: wkvol[w].append(abs(mid[i+step]-mid[i])/mid[i])

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
    move_bps=abs(mid[min(fi,len(mid)-1)]-mid[oi])/mid[oi]*1e4
    uppx=pmk if pdir=="up" else 1-pmk
    w=datetime.datetime.fromtimestamp(ts/1000,datetime.timezone.utc).strftime("%G-W%V")
    wkdrift[w][1]+=1; wkdrift[w][0]+=(res=="up")
    recs.append((uppx,1 if res=="up" else 0,move_bps,w,sym))

# apply rule: up>=0.55, spot<5bps, entry 30-49c -> per week
wk=defaultdict(lambda:[0,0,0.0,0.0,0.0])  # week->[n, wins, sum_pnl, sum_entry, sum_uppx]
for uppx,out,mb,w,sym in recs:
    if uppx<0.55 or mb>=5: continue
    entry=1-uppx
    if not(0.30<=entry<=0.49): continue
    won=(out==0); pnl=((1-entry) if won else -entry)-tfee(entry)
    g=wk[w]; g[0]+=1; g[1]+=won; g[2]+=pnl; g[3]+=entry; g[4]+=uppx

print("="*100)
print("WEEKLY rule characterization (up>=0.55, spot<5bps, entry 30-49c) — all weeks")
print("="*100)
print(f"  {'week':9s} {'n':>5} {'acc%':>6} {'EV':>8} {'meanEntry':>9} {'meanUpPx':>8} {'gap(acc-1+entry)':>16} {'spotVol(bps)':>12} {'drift(up%)':>10}")
for w in sorted(wk):
    n,wins,sp,sen,sup=wk[w]
    if n<20: continue
    acc=wins/n; ev=sp/n; me=sen/n; mu=sup/n
    # structural gap: realized down-rate (acc) vs implied down-prob (entry=down price)
    gap=acc-me
    vol=1e4*np.mean(wkvol[w]) if wkvol.get(w) else float('nan')
    dr=wkdrift[w][0]/wkdrift[w][1] if wkdrift[w][1] else float('nan')
    flag=" <-OOS" if w>="2026-W22" else ""
    print(f"  {w:9s} {n:>5} {100*acc:5.1f} {ev:+.4f} {100*me:8.2f}c {100*mu:7.2f}c {100*gap:+14.2f}pp {vol:11.2f} {100*dr:9.1f}%{flag}")

print("\nread: EV = down-rate(acc) - entry - fee. gap = acc - entry (structural mispricing).")
print("  if W24 gap collapsed -> market got efficient (decay). if gap held but acc unlucky")
print("  or fires on thinner setups (lower upPx) -> variance/regime, not decay.")
