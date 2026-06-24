#!/usr/bin/env python3
"""Deep characterization of the consensus-underdog edge — the WHY.

Edge: when >=80% of fleet agrees on a direction priced <50c, betting it is +EV
(acc~49% at entry~45c). NOT prediction (acc~coinflip) — market underprices the
consensus-cheap side. Characterize so we know if it persists:
 1. WINDOW PHASE: EV by elapsed fraction (early vs late in the 5/15min window)
 2. ENTRY DEPTH: EV by how cheap the underdog is (40-50 / 30-40 / <30c)
 3. SYMMETRY: up-underdog vs down-underdog (directional bias? or symmetric mispricing)
 4. PER-SYMBOL mispricing magnitude (acc - entry gap) — why SOL
 5. CONSENSUS STRENGTH x ENTRY: where is the gap biggest
All OOS (ts>split), T>=0.8 unless noted, taker fee.
"""
from __future__ import annotations
import sqlite3, math, datetime
from collections import defaultdict
db=sqlite3.connect("file:/data/v3.db?mode=ro",uri=True)
SPLIT=datetime.datetime(2026,5,27,tzinfo=datetime.timezone.utc).timestamp()*1000
def tfee(e): return 0.07*e*(1-e)

bnd={}
q=("SELECT market_window_seconds,ts_model_ran_ms,ts_contract_open_ms,p_market,pred_direction,warmup,symbol,contract_result "
   "FROM predictions WHERE resolved=1 AND contract_result IN('up','down') AND market_window_seconds IN(300,900) "
   "AND above_threshold=1 AND p_market IS NOT NULL")
for win,ts,bts,pmk,pdir,warm,sym,res in db.execute(q):
    if warm or not(0<pmk<1): continue
    k=(sym,win,bts)
    b=bnd.get(k)
    if b is None: b=[0,0,1 if res=="up" else 0,0.0,0,ts,bts]; bnd[k]=b
    if pdir=="up": b[0]+=1
    b[1]+=1; b[3]+=(pmk if pdir=="up" else 1-pmk); b[4]+=1

def acc_ci(g):
    n,sp,sp2,cw,sen=g
    if n<2: return None
    m=sp/n; se=math.sqrt(max(sp2/n-m*m,0)/n)
    return n,100*cw/n,100*sen/n,m,m-1.96*se,m+1.96*se
def addg(g,won,entry):
    pnl=((1-entry) if won else -entry)-tfee(entry)
    g[0]+=1;g[1]+=pnl;g[2]+=pnl*pnl;g[3]+=won;g[4]+=entry

def trades(T=0.8,oos=True):
    for (sym,win,bts),(up,tot,out,spm,npm,ts,b) in bnd.items():
        if tot<5 or npm==0: continue
        if oos and ts<=SPLIT: continue
        upf=up/tot; agree=max(upf,1-upf)
        if agree<T: continue
        maj_up=upf>=0.5; pmkt_up=spm/npm; entry=pmkt_up if maj_up else 1-pmkt_up
        if entry>=0.50: continue
        won=(maj_up==(out==1))
        phase=(ts-b)/(win*1000.0)
        yield sym,win,maj_up,entry,won,phase,agree

print("="*78); print("1. WINDOW PHASE — EV by elapsed fraction at fire (T>=0.8, OOS)"); print("="*78)
g=defaultdict(lambda:[0,0.0,0.0,0,0.0])
for sym,win,mu,e,won,ph,ag in trades():
    bk=min(int(ph*5),4); addg(g[bk],won,e)
print("  phase       n     acc%  entry   EV       CI")
for bk in range(5):
    r=acc_ci(g[bk])
    if r: n,acc,en,ev,lo,hi=r; print(f"  {bk*20}-{bk*20+20}%  {n:>6} {acc:5.2f} {en:5.2f}c {ev:+.4f} [{lo:+.4f},{hi:+.4f}]")

print("\n"+"="*78); print("2. ENTRY DEPTH — EV by how cheap the underdog (T>=0.8, OOS)"); print("="*78)
g=defaultdict(lambda:[0,0.0,0.0,0,0.0])
for sym,win,mu,e,won,ph,ag in trades():
    bk = 0 if e<0.30 else (1 if e<0.40 else 2)  # <30 / 30-40 / 40-50
    addg(g[bk],won,e)
labs={0:"<30c",1:"30-40c",2:"40-50c"}
print("  depth     n     acc%  entry   EV       CI")
for bk in (0,1,2):
    r=acc_ci(g[bk])
    if r: n,acc,en,ev,lo,hi=r; print(f"  {labs[bk]:7s} {n:>6} {acc:5.2f} {en:5.2f}c {ev:+.4f} [{lo:+.4f},{hi:+.4f}]")

print("\n"+"="*78); print("3. SYMMETRY — up-underdog vs down-underdog (T>=0.8, OOS)"); print("="*78)
g=defaultdict(lambda:[0,0.0,0.0,0,0.0])
for sym,win,mu,e,won,ph,ag in trades():
    addg(g["up" if mu else "down"],won,e)
for k in ("up","down"):
    r=acc_ci(g[k])
    if r: n,acc,en,ev,lo,hi=r; print(f"  {k:4s}-underdog n={n:>6} acc={acc:5.2f}% entry={en:5.2f}c EV={ev:+.4f} [{lo:+.4f},{hi:+.4f}]")

print("\n"+"="*78); print("4. PER-SYMBOL mispricing (acc - entry gap), T>=0.8 OOS"); print("="*78)
g=defaultdict(lambda:[0,0.0,0.0,0,0.0])
for sym,win,mu,e,won,ph,ag in trades():
    addg(g[sym],won,e)
print("  sym    n     acc%   entry   gap(acc-entry)  EV       CI")
for sym in sorted(g):
    r=acc_ci(g[sym])
    if r: n,acc,en,ev,lo,hi=r; print(f"  {sym:4s} {n:>6} {acc:5.2f} {en:5.2f}c  {acc-en:+5.2f}pp        {ev:+.4f} [{lo:+.4f},{hi:+.4f}]")

print("\n"+"="*78); print("5. CONSENSUS x ENTRY-DEPTH grid — EV (T sweep x depth), OOS"); print("="*78)
grid=defaultdict(lambda:[0,0.0,0.0,0,0.0])
for T in (0.6,0.8,1.0):
    for sym,win,mu,e,won,ph,ag in trades(T=T):
        d = 0 if e<0.40 else 1
        addg(grid[(T,d)],won,e)
print("  T    depth     n     acc%   EV")
for T in (0.6,0.8,1.0):
    for d in (0,1):
        r=acc_ci(grid[(T,d)])
        if r: n,acc,en,ev,lo,hi=r; print(f"  {T:.1f}  {'<40c' if d==0 else '40-50c'}  {n:>6} {acc:5.2f}  {ev:+.4f} [{lo:+.4f},{hi:+.4f}]")
