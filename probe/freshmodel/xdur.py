#!/usr/bin/env python3
"""CROSS-DURATION characterization of the long-bias fade edge.

Polymarket up/down (vs window-open) markets exist at 5m/15m/30m in the v3.db
history (300/900/1800s; gamma serves only 5m/15m live now — 30m discontinued).
Question the owner posed: "knowing one duration helps the other" — does the
fade edge appear at ALL horizons, and how does its magnitude scale with horizon?

Methodology = the VALIDATED w24_regime rule, split by duration instead of week:
  per window, dedupe to one fire by (sym,window,boundary); spot dev-from-open from
  the OFI feature store; rule up>=0.55 & |spot dev|<5bps & entry 30-49c -> buy DOWN.
Report per duration: window count, fade fires, down-win%, mean entry, structural
gap (down-rate - implied down-price), EV/share, and the GROSS calibration gap
(realized up-rate vs mean up-price among rich-up contracts) to see scaling.
"""
from __future__ import annotations
import bisect, datetime, sqlite3, math
from pathlib import Path
from collections import defaultdict
import numpy as np
import polars as pl

def tfee(e): return 0.07*e*(1-e)
db=sqlite3.connect("file:/data/v3.db?mode=ro",uri=True)

SYMS=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]
spot={}
for sym in SYMS:
    fs=sorted(Path(f"/data/probe_ofi/{sym}").glob("*_ofi.parquet"))
    if not fs: continue
    d=pl.concat([pl.read_parquet(f,columns=["cts","mid_price"]) for f in fs]).sort("cts")
    cts=(d["cts"].to_numpy()//1000).astype(np.int64); mid=d["mid_price"].to_numpy()
    spot[sym]=(cts,mid)

# one fire per window: (sym,win,bts). collect uppx, outcome, spot move at fire.
seen=set()
# per duration: list of (uppx, down_outcome, move_bps)
byd=defaultdict(list)
for win,ts,bts,pmk,pdir,sym,res in db.execute(
    "SELECT market_window_seconds,ts_model_ran_ms,ts_contract_open_ms,p_market,pred_direction,symbol,contract_result "
    "FROM predictions WHERE resolved=1 AND contract_result IN('up','down') "
    "AND market_window_seconds IN(300,900,1800) AND p_market IS NOT NULL AND warmup=0 "
    "ORDER BY ts_model_ran_ms"):
    if not(0<pmk<1) or sym not in spot: continue
    k=(sym,win,bts)
    if k in seen: continue
    seen.add(k)
    cts,mid=spot[sym]
    oi=bisect.bisect_left(cts,bts//1000); fi=bisect.bisect_left(cts,ts//1000)
    if oi>=len(cts) or abs(cts[oi]-bts//1000)>3 or fi>=len(cts): continue
    move_bps=abs(mid[min(fi,len(mid)-1)]-mid[oi])/mid[oi]*1e4
    uppx=pmk if pdir=="up" else 1-pmk
    byd[win].append((uppx,1 if res=="down" else 0,move_bps))

DUR={300:"5m",900:"15m",1800:"30m"}
print("="*104)
print("CROSS-DURATION fade characterization (rule: up>=0.55, |spotDev|<5bps, entry 30-49c -> buy DOWN)")
print("="*104)
print(f"  {'dur':4s} {'windows':>8} {'fires':>6} {'down_win%':>9} {'meanEntry':>9} {'gap(pp)':>8} {'EV/share':>9} {'boot95_EV':>22}")
rng=np.random.default_rng(7)
for win in (300,900,1800):
    rows=byd[win]
    nwin=len(rows)
    pnls=[]; ents=[]; dwin=0
    for uppx,dout,mb in rows:
        if uppx<0.55 or mb>=5: continue
        entry=1-uppx
        if not(0.30<=entry<=0.49): continue
        won=(dout==1); pnl=((1-entry) if won else -entry)-tfee(entry)
        pnls.append(pnl); ents.append(entry); dwin+=won
    if not pnls:
        print(f"  {DUR[win]:4s} {nwin:>8} {0:>6}  (no fires)"); continue
    a=np.array(pnls); me=float(np.mean(ents)); acc=dwin/len(pnls)
    gap=100*(acc-me)
    # bootstrap EV CI
    bs=[a[rng.integers(0,len(a),len(a))].mean() for _ in range(2000)]
    lo,hi=np.percentile(bs,[2.5,97.5])
    print(f"  {DUR[win]:4s} {nwin:>8} {len(pnls):>6} {100*acc:>8.1f}% {100*me:>8.2f}c {gap:>+7.2f} {a.mean():>+9.4f} "
          f"[{lo:+.4f},{hi:+.4f}]")

print()
print("="*104)
print("GROSS calibration by duration (no spot filter) — realized up-rate vs mean up-PRICE, by richness bucket")
print("="*104)
for win in (300,900,1800):
    rows=byd[win]
    print(f"\n  {DUR[win]} (n_windows={len(rows)}):")
    print(f"    {'up-px bucket':14s} {'n':>6} {'mean_upPx':>9} {'realized_up%':>12} {'gap(real-px)pp':>15}")
    for lo,hi in [(0.50,0.55),(0.55,0.60),(0.60,0.70),(0.70,1.01)]:
        sel=[(u,d) for u,d,mb in rows if lo<=u<hi]
        if len(sel)<20:
            print(f"    [{lo:.2f},{hi:.2f})     {len(sel):>6}  (thin)"); continue
        mu=np.mean([u for u,d in sel]); upr=np.mean([1-d for u,d in sel])  # d=down outcome -> up=1-d
        print(f"    [{lo:.2f},{hi:.2f})     {len(sel):>6} {mu:>9.3f} {100*upr:>11.1f}% {100*(upr-mu):>+14.2f}")
print()
print("read: negative gap in GROSS table = market over-prices UP (realized up-rate below up-price)")
print("  = the long-bias substrate. If it strengthens/holds across 5m->30m, the edge is horizon-robust.")
