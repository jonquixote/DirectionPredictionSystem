#!/usr/bin/env python3
"""deep7 showed fade-rich-contract +20% AND momentum-with-spot +12%, both +EV. They
bet opposite sides of overlapping windows — both can't be right at +20% unless the
per-share EV is inflated by the convexity tail (cheap-priced sides with big payouts).

DECISIVE: re-run 2a (fade rich contract) restricted to LIQUID BAND (down entry >=30c,
i.e., contract up-price <=70c). If +EV survives there, real and deployable; if it
collapses, the headline was the longshot tail.

Also: weight by approx capturable size — proxy = trade volume in the price band
within +/-30s of the fire (probe_track_a.db), per-window. Reports dollar-weighted
EV where size is realistic, OOS.
"""
from __future__ import annotations
import math, bisect, datetime, sqlite3, statistics
from pathlib import Path
from collections import defaultdict
import numpy as np
import polars as pl

SPLIT=datetime.datetime(2026,5,27,tzinfo=datetime.timezone.utc).timestamp()*1000
def tfee(e): return 0.07*e*(1-e)
db=sqlite3.connect("file:/data/v3.db?mode=ro",uri=True)
pdb=sqlite3.connect("file:/data/probe_track_a.db?mode=ro",uri=True)

# spot
spot={}
for sym in ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]:
    fs=sorted(Path(f"/data/probe_ofi/{sym}").glob("*_ofi.parquet"))
    if not fs: continue
    d=pl.concat([pl.read_parquet(f,columns=["cts","mid_price"]) for f in fs]).sort("cts")
    spot[sym]=((d["cts"].to_numpy()//1000).astype(np.int64), d["mid_price"].to_numpy())

# dedup boundary earliest fire
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
print(f"joined boundaries: {len(recs)}")

def ev(trades):
    n=0;sp=0.0;sp2=0.0;cw=0
    for won,entry in trades:
        p=((1-entry) if won else -entry)-tfee(entry); n+=1;sp+=p;sp2+=p*p;cw+=won
    if n<2: return None
    m=sp/n; se=math.sqrt(max(sp2/n-m*m,0)/n); return n,100*cw/n,m,m-1.96*se,m+1.96*se

print("="*84)
print("1. FADE-RICH-CONTRACT restricted to LIQUID BAND (down entry >=30c i.e. up_price <=70c), OOS")
print("="*84)
for X in (0.51,0.53,0.55,0.60,0.65,0.70):
    # liquid: 0.51 <= uppx <= 0.70
    tr=[((out==0),1-uppx) for uppx,out,move,ts,win,sym,bts in recs
        if ts>SPLIT and X<=uppx<=0.70 and abs(move)<0.0005]
    r=ev(tr)
    if r: n,acc,m,lo,hi=r; print(f"  up>={X:.2f} & up<=0.70 & spot_flat: n={n:>6} downwin%={acc:5.1f} EV={m:+.4f} [{lo:+.4f},{hi:+.4f}] {'+' if lo>0 else ''}")

print("\n"+"="*84)
print("2. MOMENTUM restricted to LIQUID entries (entry >=30c), real prices, OOS")
print("="*84)
for M in (0.0005,0.0010,0.0020):
    tr=[]
    for uppx,out,move,ts,win,sym,bts in recs:
        if ts<=SPLIT or abs(move)<M: continue
        if move>0: entry=uppx; won=(out==1)
        else: entry=1-uppx; won=(out==0)
        if 0.30<=entry<=0.70: tr.append((won,entry))
    r=ev(tr)
    if r: n,acc,m,lo,hi=r; print(f"  M>={M:.4f} & entry in [0.30,0.70]: n={n:>6} win%={acc:5.1f} EV={m:+.4f} [{lo:+.4f},{hi:+.4f}] {'+' if lo>0 else ''}")

print("\n"+"="*84)
print("3. DOLLAR-WEIGHTED — fade-rich-contract weighted by Polymarket fill capacity at the price")
print("   (capacity proxy: total $ traded within window's life at price <=down_entry+1c)")
print("="*84)
# pull market lookups from probe DB (sym maps)
sym2pfx={"BTCUSDT":"btc","ETHUSDT":"eth","SOLUSDT":"sol","XRPUSDT":"xrp"}
# for each candidate trade, look up the market and integrate liquidity at the side price
# heavy: do only on the X>=0.55 OOS flat slice
cands=[(uppx,out,move,ts,win,sym,bts) for uppx,out,move,ts,win,sym,bts in recs
       if ts>SPLIT and 0.55<=uppx<=0.70 and abs(move)<0.0005]
print(f"  candidate trades (X>=0.55, liquid, flat, OOS): {len(cands)}")
tot=0; sized_pnl=0.0; sized_cnt=0; vols=[]
for uppx,out,move,ts,win,sym,bts in cands[:2000]:  # cap for runtime
    pfx=sym2pfx[sym]; dur=win//60
    m=pdb.execute("SELECT condition_id FROM markets WHERE symbol=? AND duration_min=? AND boundary_ts=? LIMIT 1",
                  (pfx,dur,bts//1000)).fetchone()
    if not m: continue
    cid=m[0]
    entry=1-uppx
    band_lo, band_hi = max(0.01, entry-0.01), entry+0.01
    # NO is the "down" token in polymarket up/down; trades stored with outcome 'Up'/'Down' and price
    v=pdb.execute("SELECT SUM(size*price) FROM trades WHERE condition_id=? AND outcome='Down' AND price BETWEEN ? AND ?",
                  (cid,band_lo,band_hi)).fetchone()[0] or 0.0
    vols.append(v)
    pnl=((1-entry) if (out==0) else -entry)-tfee(entry)
    # cap our size at min($25, v/4) — quarter of band liquidity to be realistic
    size=min(25.0, v/4) if v>0 else 0
    sized_pnl += pnl*size; sized_cnt += size; tot+=1
if tot>0:
    print(f"  scanned {tot} trades")
    print(f"  median band liquidity ($Down vol at entry+/-1c): ${statistics.median(vols):.2f}")
    print(f"  p25/p75: ${np.percentile(vols,25):.2f} / ${np.percentile(vols,75):.2f}")
    print(f"  trades with band liquidity >=$10: {sum(1 for v in vols if v>=10)}/{tot}")
    if sized_cnt>0:
        print(f"  $-weighted EV (cap $25 or band/4): EV/$={sized_pnl/sized_cnt:+.4f} on ${sized_cnt:.0f} simulated size")
