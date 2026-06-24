#!/usr/bin/env python3
"""Fresh-model edge — angle 3: TRADEABLE WINDOWS ONLY.

C showed the positive-EV cells cluster in w1800 (30-min) — which has NO market
(Kalshi=15m only; Polymarket probe used 5m/15m) and therefore a suspect p_market.
Strip the mirage: re-run EV-at-fire on w300 (5m) and w900 (15m) ONLY — the windows
that actually trade. If even these are net-negative, conclusive.

Also:
 - p_market integrity by window (min/max/mean/nulls) — is w1800's price real?
 - the leaderboard standouts (h60_btc_329d, h300_sol_329d, h180_eth_89d) EV on
   tradeable windows, full history.
"""
from __future__ import annotations
import sqlite3, re, math, statistics
from collections import defaultdict

NAME = re.compile(r"^h(\d+)_([a-z]+)_v3_(\d+)d_(\d{8})$")
FEE=0.0175; HS=0.005
db = sqlite3.connect("file:/data/v3.db?mode=ro", uri=True)

# p_market integrity by window
print("="*80)
print("p_market integrity by window (resolved directional rows)")
print("="*80)
for win, in db.execute("SELECT DISTINCT market_window_seconds FROM predictions ORDER BY 1"):
    r=db.execute("SELECT COUNT(*), SUM(p_market IS NULL), MIN(p_market), MAX(p_market), AVG(p_market) "
                 "FROM predictions WHERE market_window_seconds=? AND resolved=1 AND contract_result IN('up','down')",(win,)).fetchone()
    n,nulls,mn,mx,av=r
    print(f"  w{win}: n={n:,} nulls={nulls:,} min={mn} max={mx} mean={av if av else 0:.4f}")

# EV at fire, tradeable windows only, full history + fresh
first_ts={mn:t for mn,t in db.execute("SELECT model_name,MIN(ts_model_ran_ms) FROM predictions WHERE resolved=1 GROUP BY model_name")}
full_ev=defaultdict(lambda:[0,0.0,0]); fresh_ev=defaultdict(lambda:[0,0.0,0])
STANDOUTS=("h60_btc_v3_329d","h300_sol_v3_329d","h180_eth_v3_89d")
stand=defaultdict(lambda:[0,0.0,0])
q=("SELECT model_name,market_window_seconds,ts_model_ran_ms,prediction_correct,p_market,pred_direction,warmup,above_threshold "
   "FROM predictions WHERE resolved=1 AND contract_result IN('up','down') AND market_window_seconds IN (300,900) "
   "AND above_threshold=1 AND p_market IS NOT NULL")
for mn,win,ts,correct,pmk,pdir,warm,above in db.execute(q):
    if warm or not(0<pmk<1): continue
    m=NAME.match(mn or "");
    if not m: continue
    entry = pmk if pdir=="up" else 1-pmk
    pnl=((1-entry) if correct==1 else -entry)-(FEE*entry*(1-entry)+HS)
    full_ev[(mn,win)][0]+=1; full_ev[(mn,win)][1]+=pnl; full_ev[(mn,win)][2]+=correct
    age=(ts-first_ts[mn])/86400000.0
    if age<7: fresh_ev[(mn,win)][0]+=1; fresh_ev[(mn,win)][1]+=pnl; fresh_ev[(mn,win)][2]+=correct
    cell="_".join(mn.split("_")[:4])
    if cell in STANDOUTS:
        stand[(cell,win)][0]+=1; stand[(cell,win)][1]+=pnl; stand[(cell,win)][2]+=correct

def summ(d,label):
    cells=[(pnl/n,n,c/n,mn,win) for (mn,win),(n,pnl,c) in d.items() if n>=100]
    print("\n"+"="*80); print(label+f"  (cells n>=100 fired)"); print("="*80)
    if not cells: print("  none"); return
    cells.sort(reverse=True)
    pos=sum(1 for ev,*_ in cells if ev>0)
    allpnl=[ev for ev,*_ in cells]
    # n-weighted aggregate EV
    tn=sum(n for _,n,_,_,_ in cells); tpnl=sum(ev*n for ev,n,_,_,_ in cells)
    print(f"  cells: {len(cells)}  EV>0: {pos} ({100*pos/len(cells):.0f}%)  mean EV/$: {statistics.mean(allpnl):+.4f}  median: {statistics.median(allpnl):+.4f}")
    print(f"  n-WEIGHTED aggregate EV/$ (what you'd actually earn): {tpnl/tn:+.4f}  on {tn:,} fired trades")
    print(f"  top 8:")
    for ev,n,acc,mn,win in cells[:8]:
        print(f"    {mn:38s} w{win} n={n:>5} acc={100*acc:.1f}% EV={ev:+.4f}")

summ(full_ev, "TRADEABLE WINDOWS (300,900) — EV at fire, FULL history")
summ(fresh_ev, "TRADEABLE WINDOWS (300,900) — EV at fire, FRESH window (<7d)")

print("\n"+"="*80); print("LEADERBOARD STANDOUTS on tradeable windows (full history)"); print("="*80)
for (cell,win),(n,pnl,c) in sorted(stand.items()):
    if n: print(f"  {cell} w{win}: n={n:,} acc={100*c/n:.2f}% EV/$={pnl/n:+.4f}")
