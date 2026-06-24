#!/usr/bin/env python3
"""Make-or-break: is p_market ALIGNED with the outcome window, or a stale-price artifact?

deep4 showed the market price is ~uncorrelated with outcome and inverted at extremes
(90c-up resolves 44%). Either (A) p_market is misaligned/stale (price from the wrong
window) -> the whole 'edge' is an artifact, OR (B) real intra-window mean-reversion
(price overshoots mid-window, reverts).

DECISIVE: a correctly-aligned up/down market opens AT-THE-MONEY (~50c). So:
 1. p_market(up) distribution by window-phase. Fresh (<5% elapsed, ~first 15-45s)
    MUST be tightly ~50c if aligned. Wide spread at open = stale/misaligned.
 2. Calibration (realized vs price) at phase<10% vs phase>50%, separately.
    - artifact: miscalibrated even when fresh
    - mean-reversion: well-calibrated fresh, gap only late
 3. p_market vs |spot move from window open| at fire (does price track spot?).
    Needs OFI spot. If p_market deviates from 50c WITHOUT a spot move -> stale.
"""
from __future__ import annotations
import sqlite3, math, datetime, statistics, bisect, re
from pathlib import Path
from collections import defaultdict
import numpy as np
import polars as pl
db=sqlite3.connect("file:/data/v3.db?mode=ro",uri=True)

# phase-bucketed p_market(up) — dedup boundary, take EARLIEST fire (closest to open)
rows=db.execute("SELECT market_window_seconds,ts_model_ran_ms,ts_contract_open_ms,p_market,pred_direction,symbol,contract_result,warmup "
   "FROM predictions WHERE resolved=1 AND contract_result IN('up','down') AND market_window_seconds IN(300,900) AND p_market IS NOT NULL "
   "ORDER BY ts_model_ran_ms")
seen={}
recs=[]  # (phase, up_price, out, sym, win, dist_ms, bts)
for win,ts,bts,pmk,pdir,sym,res,warm in rows:
    if warm or not(0<pmk<1): continue
    k=(sym,win,bts)
    if k in seen: continue
    seen[k]=1
    uppx=pmk if pdir=="up" else 1-pmk
    phase=(ts-bts)/(win*1000.0)
    recs.append((phase,uppx,1 if res=="up" else 0,sym,win,ts-bts,bts))
print(f"boundaries: {len(recs)}")

print("="*78)
print("1. p_market(up) distribution by window-phase  (aligned => fresh ~50c tight)")
print("="*78)
ph=defaultdict(list)
for phase,uppx,out,sym,win,dms,bts in recs:
    bk=min(int(phase*10),9) if phase>=0 else -1
    if bk>=0: ph[bk].append(uppx)
print("  phase     n      mean   p10    p50    p90    %in[.45,.55]")
for bk in range(10):
    v=ph[bk]
    if len(v)<20: continue
    a=np.array(v)
    inb=100*np.mean((a>=0.45)&(a<=0.55))
    print(f"  {bk*10}-{bk*10+10}% {len(v):>6}  {a.mean():.3f}  {np.percentile(a,10):.3f}  {np.percentile(a,50):.3f}  {np.percentile(a,90):.3f}   {inb:5.1f}%")

print("\n  VERY-FRESH slice (elapsed < 30s): p_market spread")
vf=[uppx for phase,uppx,out,sym,win,dms,bts in recs if dms<30000]
if vf:
    a=np.array(vf); print(f"    n={len(a)} mean={a.mean():.3f} std={a.std():.3f} p10={np.percentile(a,10):.3f} p90={np.percentile(a,90):.3f} %in[.45,.55]={100*np.mean((a>=.45)&(a<=.55)):.1f}%")
    print("    -> if std large / p90 near .9 at <30s, price is NOT the fresh at-the-money window = MISALIGNED")

def calib(lo,hi,label):
    c=defaultdict(lambda:[0,0])
    for phase,uppx,out,sym,win,dms,bts in recs:
        if not(lo<=phase<hi): continue
        bk=min(int(uppx/0.1),9); c[bk][1]+=1; c[bk][0]+=out
    print(f"\n  CALIBRATION {label}:  up_price -> realized_up% (gap)")
    for bk in range(10):
        n=c[bk][1]
        if n<20: continue
        r=c[bk][0]/n; mid=bk*0.1+0.05
        print(f"    {bk*10}-{bk*10+10}c n={n:>6} real={100*r:5.1f}% gap={100*(r-mid):+5.1f}pp")

print("\n"+"="*78); print("2. CALIBRATION by phase — fresh vs late"); print("="*78)
calib(0.0,0.10,"phase 0-10% (FRESH — should be ~50c & calibrated if aligned)")
calib(0.50,1.01,"phase 50-100% (LATE)")

# 3. spot cross-check (BTC only, fast): does p_market deviate only when spot moved?
print("\n"+"="*78); print("3. SPOT CROSS-CHECK (BTC): p_market vs |spot move from open| at fire"); print("="*78)
try:
    files=sorted(Path("/data/probe_ofi/BTCUSDT").glob("*_ofi.parquet"))
    sdf=pl.concat([pl.read_parquet(f,columns=["cts","mid_price"]) for f in files]).sort("cts")
    scts=(sdf["cts"].to_numpy()//1000).astype(np.int64); smid=sdf["mid_price"].to_numpy()
    buck=defaultdict(lambda:[0,0.0,0.0])  # |move|bps bucket -> [n, sum|uppx-.5|, sum]
    for phase,uppx,out,sym,win,dms,bts in recs:
        if sym!="BTCUSDT": continue
        fire_sec=bts//1000 + int(dms/1000)
        oi=bisect.bisect_left(scts,bts//1000); fi=bisect.bisect_left(scts,fire_sec)
        if oi>=len(scts) or fi>=len(scts): continue
        if abs(scts[oi]-bts//1000)>5: continue
        move_bps=abs(smid[min(fi,len(smid)-1)]-smid[oi])/smid[oi]*1e4
        mb=min(int(move_bps/5),6)
        g=buck[mb]; g[0]+=1; g[1]+=abs(uppx-0.5); g[2]+=move_bps
    print("  |spot move|   n     mean|up_price-50c|   (aligned: price deviates from 50c only WITH spot move)")
    for mb in range(7):
        g=buck[mb]
        if g[0]<20: continue
        lab=f"{mb*5}-{mb*5+5}bps" if mb<6 else ">=30bps"
        print(f"  {lab:>10} {g[0]:>6}   {100*g[1]/g[0]:5.2f}c")
except Exception as e:
    print("  spot check skipped:",e)
