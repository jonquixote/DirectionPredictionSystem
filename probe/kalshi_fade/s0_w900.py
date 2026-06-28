#!/usr/bin/env python3
"""Kalshi-fade §0 — does the fade-rich edge survive on w900 (15m) ONLY?

⚠ SUPERSEDED (2026-06-27): reads v3.db p_market = NON-executable stale-cache Gamma artifact
(see PMARKET_ARTIFACT_FINDING.md). The §0 PASS is the artifact; executable book-mid has no
edge (probe/synthesis.py). Kept for the record.


Kalshi has only 15-min crypto up/down. The offline edge was pooled 5m+15m.
Gate (frozen): w900-only fade-rich liquid-band per-share EV bootstrap 95% LB > 0
on n >= 300 (up>=0.55 & up<=0.70 & Bybit spot dev<5bps, buy DOWN @real price, OOS).
Contrast w300. Report fire-rate (windows/day satisfying rule) for capacity (§0.4).
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
    move=(mid[min(fi,len(mid)-1)]-mid[oi])/mid[oi]
    uppx=pmk if pdir=="up" else 1-pmk
    recs.append((uppx,1 if res=="up" else 0,move,ts,win,sym,bts))

def boot(pnl,nb=20000,seed=7):
    a=np.asarray(pnl)
    if len(a)<2: return (float('nan'),float('nan'))
    rng=np.random.default_rng(seed)
    m=np.array([a[rng.integers(0,len(a),len(a))].mean() for _ in range(nb)])
    return float(np.percentile(m,2.5)),float(np.percentile(m,97.5))

def slice_ev(win, X=0.55):
    pnl=[]; wins=0
    for uppx,out,move,ts,w,sym,bts in recs:
        if w!=win or ts<=SPLIT: continue
        if not(X<=uppx<=0.70 and abs(move)<0.0005): continue
        entry=1-uppx; won=(out==0)
        pnl.append(((1-entry) if won else -entry)-tfee(entry)); wins+=won
    if len(pnl)<2: return None
    lo,hi=boot(pnl)
    return len(pnl),100*wins/len(pnl),float(np.mean(pnl)),lo,hi

print("="*84)
print("§0 GATE — fade-rich liquid-band (up 0.55-0.70, spot<5bps, buy DOWN), OOS, bootstrap CI")
print("="*84)
print(f"  {'window':8s} {'n':>6} {'downwin%':>9} {'EV/share':>9}  boot95")
for win in (900,300):
    r=slice_ev(win)
    if r:
        n,acc,ev,lo,hi=r
        gate = "PASS(LB>0,n>=300)" if (lo>0 and n>=300) else ("LB>0 but n<300" if lo>0 else "FAIL")
        tag = " <- KALSHI WINDOW" if win==900 else ""
        print(f"  w{win:<6} {n:>6} {acc:>8.1f}% {ev:>+8.4f}  [{lo:+.4f},{hi:+.4f}] {gate}{tag}")

# threshold sweep on w900 only
print("\n  w900 X-threshold sweep (up>=X & up<=0.70):")
for X in (0.55,0.58,0.60,0.62,0.65):
    r=slice_ev(900,X)
    if r: n,acc,ev,lo,hi=r; print(f"    up>={X:.2f}: n={n:>5} downwin%={acc:5.1f} EV={ev:+.4f} [{lo:+.4f},{hi:+.4f}] {'+' if lo>0 else ''}")

# §0.4 fire-rate / capacity (w900, full span for stable daily est)
print("\n"+"="*84)
print("§0.4 FIRE-RATE / CAPACITY (w900, rule up>=0.55 & spot<5bps)")
print("="*84)
days=set(); fires_by_day=defaultdict(int); total_w900=defaultdict(int)
for uppx,out,move,ts,w,sym,bts in recs:
    if w!=900: continue
    d=datetime.datetime.fromtimestamp(bts/1000,datetime.timezone.utc).strftime("%Y-%m-%d")
    days.add(d); total_w900[d]+=1
    if 0.55<=uppx<=0.70 and abs(move)<0.0005: fires_by_day[d]+=1
nd=len(days); tot_fires=sum(fires_by_day.values()); tot_w=sum(total_w900.values())
print(f"  days={nd}  total w900 windows seen={tot_w} ({tot_w/nd:.0f}/day)  rule-fires={tot_fires} ({tot_fires/nd:.1f}/day)")
print(f"  fire rate: {100*tot_fires/tot_w:.1f}% of w900 windows")
# CORRECTED economics (see CAPACITY_AND_KELLY.md; the old $25/3c/$28 framing had a
# units bug). EV is ~+16.3% per $ STAKED (stake ~40c entry -> +6.5c/contract).
fpd=tot_fires/nd
for stake in (100,300):
    print(f"  @ ${stake} stake/fill: ~${stake*0.163*fpd:.0f}/day net (EV +16.3%/stake x {fpd:.1f} fires/day)")
print(f"  NB: w900 only; 5m (w300) adds ~2.7x the fires (see xdur). Liquidity caps the per-fill size.")
