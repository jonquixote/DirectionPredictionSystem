#!/usr/bin/env python3
"""Model-independent proof: does the MARKET overprice UP?

The edge mechanism (deep3): market long-bias -> UP overpriced -> cheap DOWN
underpriced -> betting DOWN is +EV in all regimes. If true, it must show in the
MARKET's own calibration, with NO reference to our models: across all boundaries,
realized up-rate vs p_market(up), bucketed by p_market(up).
  - if realized < p_market for high up-prices -> UP overpriced (down underpriced)
  - the tradeable rule needs no model: fade extreme up-prices (buy the cheap side)

Dedup to one row per (sym,win,boundary). p_market(up) = market's up price at fire.
Report full + OOS + by regime. Then the model-free rule EV: buy DOWN when
p_market(up) >= X (down priced <= 1-X), by threshold.
"""
from __future__ import annotations
import sqlite3, math, datetime
from collections import defaultdict
db=sqlite3.connect("file:/data/v3.db?mode=ro",uri=True)
SPLIT=datetime.datetime(2026,5,27,tzinfo=datetime.timezone.utc).timestamp()*1000
def tfee(e): return 0.07*e*(1-e)
def day(ts): return datetime.datetime.fromtimestamp(ts/1000,datetime.timezone.utc).strftime("%Y-%m-%d")

# dedup boundary -> market up-price + outcome. p_market is logged per-row; take first.
# need p_market as UP price: row's p_market with pred_direction tells side. reconstruct up price.
bnd={}
for win,ts,bts,pmk,pdir,sym,res,warm in db.execute(
    "SELECT market_window_seconds,ts_model_ran_ms,ts_contract_open_ms,p_market,pred_direction,symbol,contract_result,warmup "
    "FROM predictions WHERE resolved=1 AND contract_result IN('up','down') AND market_window_seconds IN(300,900) "
    "AND p_market IS NOT NULL"):
    if warm or not(0<pmk<1): continue
    k=(sym,win,bts)
    if k in bnd: continue
    up_price = pmk if pdir=="up" else 1-pmk
    bnd[k]=(up_price, 1 if res=="up" else 0, ts, sym)

# drift per (sym,date)
drift=defaultdict(lambda:[0,0])
for (sym,win,bts),(uppx,out,ts,_) in bnd.items():
    d=drift[(sym,day(bts))]; d[1]+=1; d[0]+=out
def regime(sym,bts):
    d=drift.get((sym,day(bts)))
    if not d or d[1]<5: return "?"
    f=d[0]/d[1]; return "up" if f>0.52 else ("down" if f<0.48 else "flat")

print(f"boundaries: {len(bnd)}")
print("="*78)
print("MARKET CALIBRATION — realized up-rate vs p_market(up), by up-price bucket (ALL)")
print("="*78)
cal=defaultdict(lambda:[0,0])
for (sym,win,bts),(uppx,out,ts,_) in bnd.items():
    bk=min(int(uppx/0.1),9); g=cal[bk]; g[1]+=1; g[0]+=out
print("  up_price    n      realized_up%   gap(real-price)")
for bk in range(10):
    n=cal[bk][1]
    if n<20: continue
    realized=cal[bk][0]/n; mid=bk*0.1+0.05
    print(f"  {bk*10}-{bk*10+10}c  {n:>7}   {100*realized:6.2f}%      {100*(realized-mid):+6.2f}pp")

print("\n"+"="*78)
print("MODEL-FREE RULE — buy DOWN when up_price>=X (down cheap), EV net taker fee, OOS")
print("="*78)
print("  X(up>=)  n      down_win%  entry(down)  EV        CI         | up-day EV (alpha chk)")
for X in (0.50,0.55,0.60,0.65,0.70):
    tot=[0,0.0,0.0,0]; ud=[0,0.0,0,0.0]
    for (sym,win,bts),(uppx,out,ts,_) in bnd.items():
        if ts<=SPLIT or uppx<X: continue
        entry=1-uppx  # buy down at its price
        won=(out==0)
        pnl=((1-entry) if won else -entry)-tfee(entry)
        tot[0]+=1; tot[1]+=pnl; tot[2]+=pnl*pnl; tot[3]+=won
        if regime(sym,bts)=="up":
            ud[0]+=1; ud[1]+=pnl; ud[3]+=pnl*pnl
    if tot[0]>=2:
        n=tot[0]; m=tot[1]/n; se=math.sqrt(max(tot[2]/n-m*m,0)/n)
        udev = ud[1]/ud[0] if ud[0]>=2 else float('nan')
        udse = math.sqrt(max(ud[3]/ud[0]-udev*udev,0)/ud[0]) if ud[0]>=2 else float('nan')
        wr=100*tot[3]/n; en=1-X
        udflag = f"{udev:+.4f} (n={ud[0]}, lo={udev-1.96*udse:+.4f})" if ud[0]>=2 else "n<2"
        print(f"  {X:.2f}   {n:>7}  {wr:6.2f}    ~{100*(1-X):.0f}c       {m:+.4f}  [{m-1.96*se:+.4f},{m+1.96*se:+.4f}] | {udflag}")
