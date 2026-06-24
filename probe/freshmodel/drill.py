#!/usr/bin/env python3
"""Per-cell deep dissection — specific models, not pooled.

For each watchlist (cell_prefix, window): fired (above_threshold), tradeable,
EV vs p_market net Kalshi fee. Break down:
  1. per-sibling (retrain date): n, acc, EV, bootstrap 95% CI on EV
  2. week-by-week: n, acc, EV  (is it stable or one spike?)
  3. EV by confidence band (max(p,1-p) raw)
  4. EV by entry-price bucket
Bootstrap CI answers: is this cell's EV>0 real or lucky-tail?
"""
from __future__ import annotations
import sqlite3, re, statistics, datetime
import numpy as np
from collections import defaultdict

FEE=0.0175; HS=0.005
db=sqlite3.connect("file:/data/v3.db?mode=ro",uri=True)
NAME=re.compile(r"^(h\d+_[a-z]+_v3_\d+d)_(\d{8})$")

WATCH=[("h60_btc_v3_329d",900),("h60_btc_v3_329d",300),
       ("h300_sol_v3_329d",300),("h180_eth_v3_89d",900),
       ("h900_btc_v3_179d",900),("h300_eth_v3_329d",900)]

def boot(pnl, nb=20000, seed=1):
    a=np.asarray(pnl)
    if len(a)<2: return (float("nan"),float("nan"))
    rng=np.random.default_rng(seed)
    m=np.array([a[rng.integers(0,len(a),len(a))].mean() for _ in range(nb)])
    return float(np.percentile(m,2.5)),float(np.percentile(m,97.5))

def isoweek(ts):
    d=datetime.datetime.fromtimestamp(ts/1000,datetime.timezone.utc)
    return d.strftime("%G-W%V")

for cell,win in WATCH:
    rows=db.execute("SELECT model_name,ts_model_ran_ms,prediction_correct,p_market,pred_direction,pred_proba_raw,warmup "
        "FROM predictions WHERE resolved=1 AND contract_result IN('up','down') AND market_window_seconds=? "
        "AND above_threshold=1 AND p_market IS NOT NULL AND model_name LIKE ?",(win,cell+"\\_%",)).fetchall() \
        if False else db.execute(
        "SELECT model_name,ts_model_ran_ms,prediction_correct,p_market,pred_direction,pred_proba_raw,warmup "
        "FROM predictions WHERE resolved=1 AND contract_result IN('up','down') AND market_window_seconds=? "
        "AND above_threshold=1 AND p_market IS NOT NULL AND model_name LIKE ?",(win,cell+"_%")).fetchall()
    # build per-trade records
    recs=[]
    for mn,ts,correct,pmk,pdir,praw,warm in rows:
        if warm or not(0<pmk<1): continue
        m=NAME.match(mn)
        if not m or m.group(1)!=cell: continue
        entry=pmk if pdir=="up" else 1-pmk
        pnl=((1-entry) if correct==1 else -entry)-(FEE*entry*(1-entry)+HS)
        recs.append((m.group(2),ts,correct,entry,praw,pnl))
    print("="*82)
    print(f"CELL {cell}  w{win}   fired trades={len(recs)}")
    print("="*82)
    if len(recs)<50:
        print("  too few"); continue
    allpnl=[r[5] for r in recs]; allc=sum(r[2] for r in recs)
    lo,hi=boot(allpnl)
    print(f"  OVERALL: n={len(recs)} acc={100*allc/len(recs):.2f}% EV/$={statistics.mean(allpnl):+.4f} "
          f"boot95=[{lo:+.4f},{hi:+.4f}]  {'EV>0 SIGNIF' if lo>0 else 'CI spans 0'}")
    # per sibling
    print("  per-sibling (retrain):")
    sib=defaultdict(list)
    for d,ts,c,e,pr,p in recs: sib[d].append((c,p))
    for d in sorted(sib):
        arr=sib[d]; n=len(arr)
        if n<30: continue
        pnl=[p for _,p in arr]; lo,hi=boot(pnl)
        print(f"    {d}: n={n:>5} acc={100*sum(c for c,_ in arr)/n:.1f}% EV={statistics.mean(pnl):+.4f} boot95=[{lo:+.4f},{hi:+.4f}]")
    # weekly
    print("  weekly:")
    wk=defaultdict(list)
    for d,ts,c,e,pr,p in recs: wk[isoweek(ts)].append((c,p))
    for w in sorted(wk):
        arr=wk[w]; n=len(arr)
        if n<30: continue
        pnl=[p for _,p in arr]
        print(f"    {w}: n={n:>5} acc={100*sum(c for c,_ in arr)/n:.1f}% EV={statistics.mean(pnl):+.4f}")
    # confidence band
    print("  by confidence (raw max(p,1-p)):")
    cb=defaultdict(list)
    for d,ts,c,e,pr,p in recs:
        side=max(pr,1-pr); bk=min(int((side-0.5)/0.05),5)
        cb[bk].append((c,p))
    for bk in sorted(cb):
        arr=cb[bk]; n=len(arr)
        if n<30: continue
        pnl=[p for _,p in arr]
        print(f"    conf {50+bk*5}-{55+bk*5}%: n={n:>5} acc={100*sum(c for c,_ in arr)/n:.1f}% EV={statistics.mean(pnl):+.4f}")
    # entry price bucket
    print("  by entry price (p_market on traded side):")
    eb=defaultdict(list)
    for d,ts,c,e,pr,p in recs:
        bk=min(int(e/0.1),9); eb[bk].append((c,p))
    for bk in sorted(eb):
        arr=eb[bk]; n=len(arr)
        if n<30: continue
        pnl=[p for _,p in arr]
        print(f"    entry {bk*10}-{bk*10+10}c: n={n:>5} acc={100*sum(c for c,_ in arr)/n:.1f}% EV={statistics.mean(pnl):+.4f}")
