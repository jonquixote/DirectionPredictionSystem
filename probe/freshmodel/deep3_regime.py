#!/usr/bin/env python3
"""Decisive test: is the down-underdog edge ALPHA or BETA?

deep1 found: the edge is entirely in DOWN-underdogs (fleet agrees down, priced
cheap) and in deep entries (<40c). Hypothesis A (alpha): market overprices UP
(long bias) -> cheap down bets underpriced -> +EV in ALL regimes. Hypothesis B
(beta): crypto just fell in the OOS window -> down-bets won mechanically -> the
'edge' is a downtrend artifact that reverses in up markets.

Test: per (symbol,date) compute drift = fraction of that symbol's windows
resolving UP. up-day: drift>0.52 ; down-day: drift<0.48 ; flat: else.
Measure down-underdog and up-underdog EV in each regime. ALPHA iff down-underdog
EV>0 on UP-days. Also report the period's overall drift (was it a down market?).
"""
from __future__ import annotations
import sqlite3, math, datetime
from collections import defaultdict
db=sqlite3.connect("file:/data/v3.db?mode=ro",uri=True)
SPLIT=datetime.datetime(2026,5,27,tzinfo=datetime.timezone.utc).timestamp()*1000
def tfee(e): return 0.07*e*(1-e)
def day(ts): return datetime.datetime.fromtimestamp(ts/1000,datetime.timezone.utc).strftime("%Y-%m-%d")

# per (symbol,date) drift = up-fraction across all resolved windows that day (5m+15m, all preds)
drift=defaultdict(lambda:[0,0])  # (sym,date)->[up,total]  -- use one row per boundary to avoid model double-count
seen=set()
for sym,win,bts,res in db.execute("SELECT symbol,market_window_seconds,ts_contract_open_ms,contract_result "
        "FROM predictions WHERE resolved=1 AND contract_result IN('up','down') AND market_window_seconds IN(300,900)"):
    k=(sym,win,bts)
    if k in seen: continue
    seen.add(k)
    d=drift[(sym,day(bts))]; d[1]+=1; d[0]+= (1 if res=="up" else 0)

# period overall drift
tot_up=sum(u for u,t in drift.values()); tot_n=sum(t for u,t in drift.values())
print(f"OOS+full period overall up-fraction: {100*tot_up/tot_n:.2f}% (50=flat; <50=down market)")

# build boundary consensus store (OOS)
bnd={}
q=("SELECT market_window_seconds,ts_model_ran_ms,ts_contract_open_ms,p_market,pred_direction,warmup,symbol,contract_result "
   "FROM predictions WHERE resolved=1 AND contract_result IN('up','down') AND market_window_seconds IN(300,900) "
   "AND above_threshold=1 AND p_market IS NOT NULL")
for win,ts,bts,pmk,pdir,warm,sym,res in db.execute(q):
    if warm or not(0<pmk<1) or ts<=SPLIT: continue
    k=(sym,win,bts)
    b=bnd.get(k)
    if b is None: b=[0,0,1 if res=="up" else 0,0.0,0,bts]; bnd[k]=b
    if pdir=="up": b[0]+=1
    b[1]+=1; b[3]+=(pmk if pdir=="up" else 1-pmk); b[4]+=1

def regime(sym,bts):
    d=drift.get((sym,day(bts)))
    if not d or d[1]<5: return "?"
    f=d[0]/d[1]
    return "up-day" if f>0.52 else ("down-day" if f<0.48 else "flat-day")

# accumulate EV by (direction, regime) and (direction, regime, deep<40c)
G=defaultdict(lambda:[0,0.0,0.0,0,0.0])
def addg(g,won,e):
    pnl=((1-e) if won else -e)-tfee(e); g[0]+=1;g[1]+=pnl;g[2]+=pnl*pnl;g[3]+=won;g[4]+=e
def ci(g):
    n=g[0]
    if n<2: return None
    m=g[1]/n; se=math.sqrt(max(g[2]/n-m*m,0)/n); return n,100*g[3]/n,100*g[4]/n,m,m-1.96*se,m+1.96*se

for (sym,win,bts),(up,tot,out,spm,npm,b) in bnd.items():
    if tot<5 or npm==0: continue
    upf=up/tot; agree=max(upf,1-upf)
    if agree<0.8: continue
    maj_up=upf>=0.5; pmkt_up=spm/npm; entry=pmkt_up if maj_up else 1-pmkt_up
    if entry>=0.50: continue
    won=(maj_up==(out==1)); reg=regime(sym,bts); dirn="up" if maj_up else "down"
    deep = entry<0.40
    addg(G[(dirn,reg)],won,entry)
    addg(G[(dirn,reg,"deep" if deep else "shal")],won,entry)

print("\n"+"="*80)
print("DOWN-underdog & UP-underdog EV by market regime (T>=0.8, OOS) — ALPHA iff down>0 on up-days")
print("="*80)
print(f"  {'dir':4s} {'regime':9s} {'n':>6} {'acc%':>6} {'entry':>6} {'EV':>9}  CI")
for dirn in ("down","up"):
    for reg in ("up-day","flat-day","down-day"):
        r=ci(G[(dirn,reg)])
        if r: n,acc,en,ev,lo,hi=r; print(f"  {dirn:4s} {reg:9s} {n:>6} {acc:5.1f} {en:5.1f}c {ev:+.4f}  [{lo:+.4f},{hi:+.4f}] {'+' if lo>0 else ''}")

print("\nDEEP (<40c) down-underdog by regime (the fat-EV slice):")
for reg in ("up-day","flat-day","down-day"):
    r=ci(G[("down",reg,"deep")])
    if r: n,acc,en,ev,lo,hi=r; print(f"  down {reg:9s} <40c n={n:>5} acc={acc:5.1f}% entry={en:5.1f}c EV={ev:+.4f} [{lo:+.4f},{hi:+.4f}]")
