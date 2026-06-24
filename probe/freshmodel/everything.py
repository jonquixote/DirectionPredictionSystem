#!/usr/bin/env python3
"""Test everything — underdog-side, maker fees, fresh, consensus. With discipline.

KEY IDENTITY: EV/$1 = accuracy - entry_price - fee.  (entry = p_market on traded side)
So the whole question is: does model accuracy beat the market-implied prob (entry),
net of fee, on a given subset, OUT OF SAMPLE.

Fees: taker 0.07*p(1-p); maker 0.0175*p(1-p).
MAKER CAVEAT (printed): a limit order only fills when price moves TO it = adverse
selection + non-fills. Swapping fee on the SAME (taker) fills = UPPER BOUND on maker EV.
If the upper bound fails, maker is dead for sure.

Discipline: lead with FLEET-WIDE (no cell cherry-pick) + temporal OOS holdout
(train<=split used only to define 'fresh'/consensus members; the OOS column is the
test). CIs analytic (mean +/- 1.96*SE) — bounded memory, one pass.
Parts: 1 fleet matrix (side x fee x [full/OOS/fresh]) ; 2 per-cell underdog+maker ;
3 consensus sliding scale (trade when >=T of fleet agree, underdog+maker).
"""
from __future__ import annotations
import sqlite3, re, math, datetime
from collections import defaultdict

db=sqlite3.connect("file:/data/v3.db?mode=ro",uri=True)
NAME=re.compile(r"^(h\d+_[a-z]+_v3_\d+d)_(\d{8})$")
def fee(p,maker): return (0.0175 if maker else 0.07)*p*(1-p)
SPLIT=datetime.datetime(2026,5,27,tzinfo=datetime.timezone.utc).timestamp()*1000
WATCH={"h60_btc_v3_329d","h300_sol_v3_329d","h900_btc_v3_179d","h60_eth_v3_179d","h180_eth_v3_89d"}

# accumulators: key -> [n, sum_pnl, sum_pnl2, sum_correct, sum_entry]
G=defaultdict(lambda:[0,0.0,0.0,0,0.0])
def add(key,won,entry,maker):
    pnl=((1-entry) if won else -entry)-fee(entry,maker)
    g=G[key]; g[0]+=1; g[1]+=pnl; g[2]+=pnl*pnl; g[3]+=won; g[4]+=entry
def ci(key):
    g=G[key]; n=g[0]
    if n<2: return None
    m=g[1]/n; var=max(g[2]/n-m*m,0); se=math.sqrt(var/n)
    return n,100*g[3]/n,g[4]/n,m,m-1.96*se,m+1.96*se

# first-pred ts (fresh) + per-boundary consensus store
first_ts={mn:t for mn,t in db.execute("SELECT model_name,MIN(ts_model_ran_ms) FROM predictions WHERE resolved=1 GROUP BY model_name")}
bnd=defaultdict(lambda:[0,0,-1,0.0,0])  # (sym,win,bts)->[up,total,outcome,sum_pmkt,npm]

q=("SELECT model_name,market_window_seconds,ts_model_ran_ms,ts_contract_open_ms,prediction_correct,"
   "p_market,pred_direction,warmup,symbol,contract_result FROM predictions "
   "WHERE resolved=1 AND contract_result IN('up','down') AND market_window_seconds IN(300,900) "
   "AND above_threshold=1 AND p_market IS NOT NULL")
for mn,win,ts,bts,correct,pmk,pdir,warm,sym,res in db.execute(q):
    if warm or not(0<pmk<1): continue
    m=NAME.match(mn or "")
    if not m: continue
    entry=pmk if pdir=="up" else 1-pmk
    won=(correct==1)
    side="under" if entry<0.50 else "fav"
    oos = ts>SPLIT
    fresh = (ts-first_ts[mn])/86400000.0 < 7
    for maker in (False,True):
        fk="mk" if maker else "tk"
        add(("FLEET",fk,"all","full"),won,entry,maker)
        add(("FLEET",fk,side,"full"),won,entry,maker)
        if oos:
            add(("FLEET",fk,"all","oos"),won,entry,maker)
            add(("FLEET",fk,side,"oos"),won,entry,maker)
        if fresh:
            add(("FLEET",fk,side,"fresh"),won,entry,maker)
        cell=m.group(1)
        if cell in WATCH:
            add(("CELL",cell,fk,side),won,entry,maker)
    # consensus boundary store (direction votes + market up-price)
    b=bnd[(sym,win,bts)]
    if pdir=="up": b[0]+=1
    b[1]+=1; b[2]=1 if res=="up" else 0
    pmkt_up = pmk if pdir=="up" else 1-pmk   # market's UP price
    b[3]+=pmkt_up; b[4]+=1

def show(label,key):
    r=ci(key)
    if not r: print(f"  {label:46s} n<2"); return
    n,acc,entry,ev,lo,hi=r
    sig = "SIGNIF+" if lo>0 else ("SIGNIF-" if hi<0 else "~0")
    print(f"  {label:46s} n={n:>7} acc={acc:5.2f}% entry={100*entry:5.2f}c EV={ev:+.4f} CI[{lo:+.4f},{hi:+.4f}] {sig}")

print("="*100)
print("MAKER NOTE: maker EV here = same taker fills at 0.0175 fee = UPPER BOUND (real fills are adverse). ")
print("EV/$ = acc - entry - fee. Question: does acc beat entry net fee, OOS?")
print("="*100)
print("\nPART 1 — FLEET-WIDE (all tradeable fired, w300+w900)")
for fk,fl in (("tk","TAKER"),("mk","MAKER-ub")):
    print(f" [{fl}]")
    for scope in ("full","oos","fresh"):
        for side in ("all","under","fav"):
            if side=="all" and scope=="fresh": continue
            show(f"{scope:5s} {side}", ("FLEET",fk,side,scope))

print("\nPART 2 — PER-CELL (watchlist), underdog vs fav, taker vs maker-ub (full history)")
for cell in sorted(WATCH):
    print(f" {cell}")
    for fk,fl in (("tk","TAKER"),("mk","MAKER-ub")):
        for side in ("under","fav"):
            show(f"  {fl} {side}", ("CELL",cell,fk,side))

print("\nPART 3 — CONSENSUS sliding scale (trade agreed side when >=T of fleet agree; underdog-only; maker-ub)")
print("  T      n_trades  acc%   meanEntry  EV(maker-ub)   CI")
for T in (0.6,0.7,0.8,0.9,1.0):
    n=0; sp=0.0; sp2=0.0; cw=0; se_=0.0
    for (sym,win,bts),(up,tot,out,spm,npm) in bnd.items():
        if tot<5 or out<0 or npm==0: continue
        upf=up/tot; agree=max(upf,1-upf)
        if agree<T: continue
        maj_up = upf>=0.5
        pmkt_up=spm/npm
        entry = pmkt_up if maj_up else 1-pmkt_up
        if entry>=0.50: continue            # underdog-only
        won = (maj_up == (out==1))
        pnl=((1-entry) if won else -entry)-fee(entry,True)
        n+=1; sp+=pnl; sp2+=pnl*pnl; cw+=won; se_+=entry
    if n>=2:
        m=sp/n; var=max(sp2/n-m*m,0); se=math.sqrt(var/n)
        print(f"  {T:.1f}  {n:>8} {100*cw/n:5.2f}  {100*se_/n:6.2f}c   {m:+.4f}   [{m-1.96*se:+.4f},{m+1.96*se:+.4f}]")
    else:
        print(f"  {T:.1f}  n<2")
