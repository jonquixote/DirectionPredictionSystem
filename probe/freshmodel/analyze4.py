#!/usr/bin/env python3
"""Fresh-model edge — angle 4: DEPLOYMENT SIMULATION (temporal-split selection).

The owner's "run the models that (or whose siblings) prove successful" idea,
tested honestly: select cells profitable in the FIRST half of history, measure
their realized EV in the HELD-OUT second half. Selection on past EV is exactly
what a track-record-based deployment does. If past-profitable cells don't stay
profitable OOS, the strategy is undeployable.

Tradeable windows only (300,900). Fired (above_threshold). EV net of Kalshi fee.
Controls: all-cells OOS EV (the baseline a random deploy would get), and a
n-weighted aggregate (what capital would actually earn).
"""
from __future__ import annotations
import sqlite3, re, statistics
from collections import defaultdict

NAME=re.compile(r"^h(\d+)_([a-z]+)_v3_(\d+)d_(\d{8})$")
FEE=0.0175; HS=0.005
db=sqlite3.connect("file:/data/v3.db?mode=ro",uri=True)

# split date = median ts of fired tradeable rows
mn_ts,mx_ts=db.execute("SELECT MIN(ts_model_ran_ms),MAX(ts_model_ran_ms) FROM predictions "
  "WHERE resolved=1 AND market_window_seconds IN(300,900) AND above_threshold=1").fetchone()
split=(mn_ts+mx_ts)//2
import datetime
f=lambda m: datetime.datetime.fromtimestamp(m/1000,datetime.timezone.utc).strftime("%Y-%m-%d")
print(f"split: train<= {f(split)} < test  (full {f(mn_ts)}..{f(mx_ts)})")

h1=defaultdict(lambda:[0,0.0]); h2=defaultdict(lambda:[0,0.0])  # (model,win)->[n,pnl]
q=("SELECT model_name,market_window_seconds,ts_model_ran_ms,prediction_correct,p_market,pred_direction,warmup "
   "FROM predictions WHERE resolved=1 AND contract_result IN('up','down') AND market_window_seconds IN(300,900) "
   "AND above_threshold=1 AND p_market IS NOT NULL")
for mn,win,ts,correct,pmk,pdir,warm in db.execute(q):
    if warm or not(0<pmk<1): continue
    if not NAME.match(mn or ""): continue
    entry=pmk if pdir=="up" else 1-pmk
    pnl=((1-entry) if correct==1 else -entry)-(FEE*entry*(1-entry)+HS)
    (h1 if ts<=split else h2)[(mn,win)]
    d=(h1 if ts<=split else h2)[(mn,win)]; d[0]+=1; d[1]+=pnl

# select cells profitable in first half (n>=100), measure their second-half EV
selected=[k for k,(n,pnl) in h1.items() if n>=100 and pnl/n>0]
print(f"\ncells with first-half n>=100: {sum(1 for n,_ in (v for v in h1.values()) if n>=100)}")
print(f"selected (first-half EV>0, n>=100): {len(selected)}")

def agg(keys, half):
    tn=tp=0; evs=[]
    for k in keys:
        if k in half and half[k][0]>=50:
            n,pnl=half[k]; tn+=n; tp+=pnl; evs.append(pnl/n)
    if tn==0: return None
    return tn, tp/tn, (statistics.mean(evs) if evs else 0), len(evs)

sel2=agg(selected,h2)
all2=agg([k for k,(n,_) in h2.items() if n>=50], h2)
print("\n"+"="*72)
print("OUT-OF-SAMPLE (second half) EV of cells SELECTED on first-half profit")
print("="*72)
if sel2:
    tn,wev,mev,nc=sel2
    print(f"  selected cells OOS: {nc} cells, {tn:,} fired trades")
    print(f"    n-weighted EV/$: {wev:+.4f}   mean-across-cells EV/$: {mev:+.4f}")
if all2:
    tn,wev,mev,nc=all2
    print(f"  ALL cells OOS (baseline): {nc} cells, {tn:,} trades, n-weighted EV/$: {wev:+.4f}")
print("\n  -> if selected-OOS <= all-OOS or <0: track-record selection does NOT carry forward")

# also: first-half EV of the selected (what the track record promised)
s1=agg(selected,h1)
if s1: print(f"\n  (selected cells' FIRST-half EV/$ — the 'track record' that sold them: {s1[1]:+.4f})")
