#!/usr/bin/env python3
"""Verify the underdog/consensus signal — stress tests, not new fishing.

The find: buying the underdog side (entry<0.50) is +EV OOS fleet-wide; strengthens
with fleet consensus. Edge = market underprices the consensus side (acc~50.5% at
entry~43c), NOT prediction (acc~coin-flip). Now stress it:

 1. SLIPPAGE SENSITIVITY: consensus-underdog EV with extra slippage {0,1,2,3}c.
    Where does it die? (executable price != logged p_market is the #1 risk)
 2. CONSENSUS OOS-ONLY: Part-3 redone on test half only (ts>split).
 3. WEEKLY WALK-FORWARD: equity curve of consensus-underdog (T>=0.8) by week.
    Rising line = real; random walk = artifact.
 4. LOCALIZATION: consensus-underdog EV per symbol x window.

Consensus = among fleet at (sym,win,boundary): fraction agreeing on direction.
Trade agreed side iff it's the underdog (market price<0.50). Taker fee shown
(honest; maker is upper-bound). slip = extra cents subtracted from payout.
"""
from __future__ import annotations
import sqlite3, math, datetime
from collections import defaultdict

db=sqlite3.connect("file:/data/v3.db?mode=ro",uri=True)
SPLIT=datetime.datetime(2026,5,27,tzinfo=datetime.timezone.utc).timestamp()*1000
def isoweek(ts): return datetime.datetime.fromtimestamp(ts/1000,datetime.timezone.utc).strftime("%G-W%V")

# boundary store: (sym,win,bts)-> votes_up, total, outcome_up, sum_pmkt_up, npm, ts
bnd={}
q=("SELECT market_window_seconds,ts_model_ran_ms,ts_contract_open_ms,p_market,pred_direction,warmup,symbol,contract_result "
   "FROM predictions WHERE resolved=1 AND contract_result IN('up','down') AND market_window_seconds IN(300,900) "
   "AND above_threshold=1 AND p_market IS NOT NULL")
for win,ts,bts,pmk,pdir,warm,sym,res in db.execute(q):
    if warm or not(0<pmk<1): continue
    k=(sym,win,bts)
    b=bnd.get(k)
    if b is None: b=[0,0,1 if res=="up" else 0,0.0,0,ts]; bnd[k]=b
    if pdir=="up": b[0]+=1
    b[1]+=1
    b[3]+= (pmk if pdir=="up" else 1-pmk); b[4]+=1

def taker_fee(e): return 0.07*e*(1-e)

def consensus_trades(T, oos=None, slip=0.0):
    """yield (won, entry, ts) for consensus-underdog trades at threshold T."""
    for (sym,win,bts),(up,tot,out,spm,npm,ts) in bnd.items():
        if tot<5 or npm==0: continue
        if oos is True and ts<=SPLIT: continue
        if oos is False and ts>SPLIT: continue
        upf=up/tot; agree=max(upf,1-upf)
        if agree<T: continue
        maj_up=upf>=0.5
        pmkt_up=spm/npm
        entry=pmkt_up if maj_up else 1-pmkt_up
        if entry>=0.50: continue
        won=(maj_up==(out==1))
        yield won, entry, ts

def stats(trades, slip=0.0):
    n=0; sp=0.0; sp2=0.0; cw=0; se_=0.0
    for won,entry,ts in trades:
        pnl=((1-entry) if won else -entry)-taker_fee(entry)-slip
        n+=1; sp+=pnl; sp2+=pnl*pnl; cw+=won; se_+=entry
    if n<2: return None
    m=sp/n; var=max(sp2/n-m*m,0); se=math.sqrt(var/n)
    return n,100*cw/n,100*se_/n,m,m-1.96*se,m+1.96*se

print("="*90)
print("1. SLIPPAGE SENSITIVITY — consensus-underdog T>=0.8, TAKER fee, extra slippage (OOS)")
print("="*90)
print("  slip   n     acc%   entry   EV        CI")
for slip in (0.0,0.01,0.02,0.03):
    r=stats(consensus_trades(0.8,oos=True),slip=slip)
    if r: n,acc,en,ev,lo,hi=r; print(f"  {slip*100:.0f}c  {n:>6} {acc:5.2f} {en:5.2f}c {ev:+.4f}  [{lo:+.4f},{hi:+.4f}] {'+' if lo>0 else '0/-'}")

print("\n"+"="*90)
print("2. CONSENSUS OOS-ONLY — sliding T, taker fee, no extra slippage")
print("="*90)
print("  T     n      acc%   entry   EV        CI")
for T in (0.6,0.7,0.8,0.9,1.0):
    r=stats(consensus_trades(T,oos=True))
    if r: n,acc,en,ev,lo,hi=r; print(f"  {T:.1f} {n:>6} {acc:5.2f} {en:5.2f}c {ev:+.4f}  [{lo:+.4f},{hi:+.4f}] {'SIGNIF+' if lo>0 else '~0/-'}")

print("\n"+"="*90)
print("3. WEEKLY WALK-FORWARD — consensus-underdog T>=0.8, taker, cumulative EV/$ (equity curve)")
print("="*90)
wk=defaultdict(lambda:[0,0.0,0])
for won,entry,ts in consensus_trades(0.8):
    pnl=((1-entry) if won else -entry)-taker_fee(entry)
    w=isoweek(ts); wk[w][0]+=1; wk[w][1]+=pnl; wk[w][2]+=won
cum=0.0
print("  week      n    acc%   weekEV    cumEV/$")
for w in sorted(wk):
    n,sp,cwn=wk[w]; ev=sp/n; cum+=sp
    print(f"  {w}  {n:>5} {100*cwn/n:5.2f}  {ev:+.4f}   {cum:+.2f}")

print("\n"+"="*90)
print("4. LOCALIZATION — consensus-underdog T>=0.8 EV per symbol x window (taker, OOS)")
print("="*90)
loc=defaultdict(lambda:[0,0.0,0.0,0])
for (sym,win,bts),(up,tot,out,spm,npm,ts) in bnd.items():
    if tot<5 or npm==0 or ts<=SPLIT: continue
    upf=up/tot; agree=max(upf,1-upf)
    if agree<0.8: continue
    maj_up=upf>=0.5; pmkt_up=spm/npm; entry=pmkt_up if maj_up else 1-pmkt_up
    if entry>=0.50: continue
    won=(maj_up==(out==1)); pnl=((1-entry) if won else -entry)-taker_fee(entry)
    g=loc[(sym,win)]; g[0]+=1; g[1]+=pnl; g[2]+=pnl*pnl; g[3]+=won
print("  sym/win        n     acc%    EV        CI")
for (sym,win),(n,sp,sp2,cw) in sorted(loc.items()):
    if n<2: continue
    m=sp/n; se=math.sqrt(max(sp2/n-m*m,0)/n)
    print(f"  {sym:4s} w{win:<4} {n:>6} {100*cw/n:5.2f}  {m:+.4f}  [{m-1.96*se:+.4f},{m+1.96*se:+.4f}] {'+' if m-1.96*se>0 else ''}")
