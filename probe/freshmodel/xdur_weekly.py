#!/usr/bin/env python3
"""⚠ SUPERSEDED (2026-06-27): uses v3.db p_market = NON-executable stale-cache Gamma
artifact (see PMARKET_ARTIFACT_FINDING.md). The weekly gaps reflect the artifact, not a
tradeable edge. Kept for the record.

Temporal x horizon: does the W24 structural-gap collapse (the one YELLOW risk,
seen on 15m) also appear at 5m and 30m? If only 15m collapsed in W24 -> unlucky
partial week (variance). If all three collapse -> genuine decay. Uses the 5m/30m
durations as independent temporal witnesses against the 15m persistence worry.

Per (week,duration): fade fires (up>=0.55, |spotDev|<5bps, entry 30-49c), down-rate,
mean entry, structural gap (down-rate - entry). Same validated rule as w24_regime.
"""
from __future__ import annotations
import bisect, datetime, sqlite3
from pathlib import Path
from collections import defaultdict
import numpy as np
import polars as pl

def tfee(e): return 0.07*e*(1-e)
db=sqlite3.connect("file:/data/v3.db?mode=ro",uri=True)
SYMS=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]
spot={}
for sym in SYMS:
    fs=sorted(Path(f"/data/probe_ofi/{sym}").glob("*_ofi.parquet"))
    if not fs: continue
    d=pl.concat([pl.read_parquet(f,columns=["cts","mid_price"]) for f in fs]).sort("cts")
    spot[sym]=((d["cts"].to_numpy()//1000).astype(np.int64), d["mid_price"].to_numpy())

seen=set()
# (win,week) -> [n, down_wins, sum_entry, sum_pnl]
cell=defaultdict(lambda:[0,0,0.0,0.0])
for win,ts,bts,pmk,pdir,sym,res in db.execute(
    "SELECT market_window_seconds,ts_model_ran_ms,ts_contract_open_ms,p_market,pred_direction,symbol,contract_result "
    "FROM predictions WHERE resolved=1 AND contract_result IN('up','down') "
    "AND market_window_seconds IN(300,900,1800) AND p_market IS NOT NULL AND warmup=0 "
    "ORDER BY ts_model_ran_ms"):
    if not(0<pmk<1) or sym not in spot: continue
    k=(sym,win,bts)
    if k in seen: continue
    seen.add(k)
    cts,mid=spot[sym]
    oi=bisect.bisect_left(cts,bts//1000); fi=bisect.bisect_left(cts,ts//1000)
    if oi>=len(cts) or abs(cts[oi]-bts//1000)>3 or fi>=len(cts): continue
    move_bps=abs(mid[min(fi,len(mid)-1)]-mid[oi])/mid[oi]*1e4
    uppx=pmk if pdir=="up" else 1-pmk
    if uppx<0.55 or move_bps>=5: continue
    entry=1-uppx
    if not(0.30<=entry<=0.49): continue
    wk=datetime.datetime.fromtimestamp(ts/1000,datetime.timezone.utc).strftime("%G-W%V")
    won=(res=="down"); pnl=((1-entry) if won else -entry)-tfee(entry)
    g=cell[(win,wk)]; g[0]+=1; g[1]+=won; g[2]+=entry; g[3]+=pnl

DUR={300:"5m",900:"15m",1800:"30m"}
weeks=sorted({wk for (_,wk) in cell})
print("="*94)
print("WEEKLY structural gap by duration (gap = down-rate - entry, pp). >0 = fade edge live.")
print("="*94)
print(f"  {'week':9s} "+" ".join(f"{DUR[w]+'_n':>7} {DUR[w]+'_gap':>8}" for w in (300,900,1800)))
for wk in weeks:
    parts=[]
    for w in (300,900,1800):
        n,dw,se,sp=cell[(w,wk)]
        if n<15: parts.append(f"{n:>7} {'--':>8}")
        else:
            gap=100*(dw/n - se/n); parts.append(f"{n:>7} {gap:>+7.1f}")
    flag=" <-W24" if wk=="2026-W24" else (" <-OOS" if wk>="2026-W22" else "")
    print(f"  {wk:9s} "+" ".join(parts)+flag)
print()
print("read: if W24 gap stayed +10..+20 at 5m/30m while 15m dipped -> 15m W24 was an unlucky")
print("  partial week, NOT decay. if all three collapsed together -> genuine efficiency onset.")
