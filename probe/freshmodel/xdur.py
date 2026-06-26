#!/usr/bin/env python3
"""CROSS-DURATION characterization of the long-bias fade edge.

Polymarket up/down (vs window-open) markets exist at 5m/15m/30m in the v3.db
history (300/900/1800s; gamma serves only 5m/15m live now — 30m discontinued).
Question the owner posed: "knowing one duration helps the other" — does the
fade edge appear at ALL horizons, and how does its magnitude scale with horizon?

METHOD (upgraded): intra-window FIRST-FIRE scan (not the old first-row snapshot).
Per window, walk obs in time order; fire at the first row with up>=X AND |spot
dev-from-open|<5bps AND phase<=0.8; buy DOWN at the executable price; hold to
resolution. Outcome = SPOT-DERIVED (down wins if close<open) — same basis as the
cross-venue transfer test (probe/xvenue_fade.py), so durations and venues compare
apples to apples. The GROSS calibration table is a per-window snapshot overview.
"""
from __future__ import annotations
import bisect, sqlite3
from pathlib import Path
from collections import defaultdict
import numpy as np
import polars as pl

def tfee(e): return 0.07*e*(1-e)
T_BPS=5.0
SYMS=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]
db=sqlite3.connect("file:/data/v3.db?mode=ro",uri=True)

spot={}
for sym in SYMS:
    fs=sorted(Path(f"/data/probe_ofi/{sym}").glob("*_ofi.parquet"))
    if not fs: continue
    d=pl.concat([pl.read_parquet(f,columns=["cts","mid_price"]) for f in fs]).sort("cts")
    spot[sym]=((d["cts"].to_numpy()//1000).astype(np.int64), d["mid_price"].to_numpy())

def spot_at(sym, t_s):
    cts,mid=spot[sym]; i=bisect.bisect_left(cts,t_s)
    if i>=len(cts): i=len(cts)-1
    if i>0 and abs(cts[i-1]-t_s)<abs(cts[i]-t_s): i-=1
    return mid[i], cts[i]

# load all rows per window per duration
wins=defaultdict(list)   # (sym,win,bts) -> [(ts,pmk,pdir,res)]
for win,ts,bts,pmk,pdir,sym,res in db.execute(
    "SELECT market_window_seconds,ts_model_ran_ms,ts_contract_open_ms,p_market,pred_direction,symbol,contract_result "
    "FROM predictions WHERE resolved=1 AND contract_result IN('up','down') "
    "AND market_window_seconds IN(300,900,1800) AND p_market IS NOT NULL AND warmup=0 "
    "ORDER BY ts_model_ran_ms"):
    if not(0<pmk<1) or sym not in spot: continue
    wins[(sym,win,bts)].append((ts,pmk,pdir,res))

def scan(X, win):
    """intra-window first-fire -> list of (entry, down_win_spot); plus gross snapshot rows."""
    fires=[]; gross=[]   # gross: (uppx_first, up_outcome_spot)
    for (sym,w,bts),rows in wins.items():
        if w!=win: continue
        open_s=bts//1000
        so,co=spot_at(sym,open_s)
        if abs(co-open_s)>3 or not so: continue
        sc,_=spot_at(sym,open_s+win)
        down_win = 1 if sc<so else 0
        # gross snapshot (first row price)
        ts0,pmk0,pdir0,_=rows[0]
        gross.append((pmk0 if pdir0=="up" else 1-pmk0, 0 if down_win else 1))
        # intra-window scan
        for ts,pmk,pdir,res in rows:
            phase=(ts//1000-open_s)/win
            if phase>0.8: break
            cur,_=spot_at(sym,ts//1000)
            move_bps=abs(cur-so)/so*1e4
            uppx=pmk if pdir=="up" else 1-pmk
            if uppx>=X and move_bps<T_BPS:
                entry=1-uppx
                if 0.02<entry<0.98: fires.append((entry,down_win))
                break
    return fires,gross

DUR={300:"5m",900:"15m",1800:"30m"}
print("="*104)
print("CROSS-DURATION fade (intra-window first-fire scan: up>=0.55, |dev|<5bps, entry 30-49c -> buy DOWN)")
print("="*104)
print(f"  {'dur':4s} {'windows':>8} {'fires':>6} {'down_win%':>9} {'meanEntry':>9} {'gap(pp)':>8} {'EV/share':>9} {'boot95_EV':>22}")
rng=np.random.default_rng(7)
gross_cache={}
for win in (300,900,1800):
    fires,gross=scan(0.55,win); gross_cache[win]=gross
    nwin=sum(1 for k in wins if k[1]==win)
    band=[(e,dw) for e,dw in fires if 0.30<=e<=0.49]
    if not band:
        print(f"  {DUR[win]:4s} {nwin:>8} {0:>6}  (no fires)"); continue
    a=np.array([((1-e) if dw else -e)-tfee(e) for e,dw in band])
    ents=[e for e,dw in band]; acc=np.mean([dw for e,dw in band]); me=float(np.mean(ents))
    bs=[a[rng.integers(0,len(a),len(a))].mean() for _ in range(2000)]
    lo,hi=np.percentile(bs,[2.5,97.5])
    print(f"  {DUR[win]:4s} {nwin:>8} {len(band):>6} {100*acc:>8.1f}% {100*me:>8.2f}c {100*(acc-me):>+7.2f} "
          f"{a.mean():>+9.4f} [{lo:+.4f},{hi:+.4f}]")

print()
print("="*104)
print("GROSS calibration by duration (per-window first-row snapshot) — realized up-rate vs mean up-PRICE")
print("="*104)
for win in (300,900,1800):
    g=gross_cache[win]
    print(f"\n  {DUR[win]} (n_windows={len(g)}):")
    print(f"    {'up-px bucket':14s} {'n':>6} {'mean_upPx':>9} {'realized_up%':>12} {'gap(real-px)pp':>15}")
    for lo,hi in [(0.50,0.55),(0.55,0.60),(0.60,0.70),(0.70,1.01)]:
        sel=[(u,o) for u,o in g if lo<=u<hi]
        if len(sel)<20:
            print(f"    [{lo:.2f},{hi:.2f})     {len(sel):>6}  (thin)"); continue
        mu=np.mean([u for u,o in sel]); upr=np.mean([o for u,o in sel])
        print(f"    [{lo:.2f},{hi:.2f})     {len(sel):>6} {mu:>9.3f} {100*upr:>11.1f}% {100*(upr-mu):>+14.2f}")
print("\nread: fade table now uses the intra-window scan (catches windows that go rich LATER,")
print("  not just open-rich) -> method-consistent with probe/xvenue_fade.py.")
