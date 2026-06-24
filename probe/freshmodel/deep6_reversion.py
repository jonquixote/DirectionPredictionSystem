#!/usr/bin/env python3
"""Is the reversion FAST-FADE (latency game, undeployable) or SLOW (enter-and-hold)?

deep5: p_market aligned & real; early spot moves overshoot and revert by window
close (up priced 75c after early up-move resolves 36%). Two questions:

 A. MODEL-FREE SPOT REVERSION (Bybit only, no contract): for each 5/15min window,
    measure spot move at 20% elapsed (early), then sign of close-vs-open. If an
    early UP move predicts a DOWN close (and vice versa), reversion is real in spot.
    Report P(close up | early move bucket).

 B. DEPLOYABLE 'enter-at-20%-and-hold' EV: at 20% elapsed, observe spot dev-from-open;
    if |dev| large (overshoot), bet the REVERSION side, entry = model-free fair price
    from dev (Phi), hold to close, taker fee. This enters at the OBSERVED post-move
    price (no latency race) and holds. If +EV, the reversion is slow/capturable.
    Sweep the early-move threshold.

Spot from OFI parquets. BTC+ETH+SOL+XRP. Window 300 & 900.
"""
from __future__ import annotations
import math, bisect
from pathlib import Path
from collections import defaultdict
import numpy as np
import polars as pl

SYMS=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]
def tfee(e): return 0.07*e*(1-e)

# global sigma per symbol (1s log-ret std) for a crude fair-price; we mainly use sign
revA=defaultdict(lambda:[0,0])     # (movebucket)->[n, close_up]   model-free spot
evB=defaultdict(lambda:[0,0.0,0.0,0])  # (win, thr)->[n,sumpnl,sumpnl2,wins]

for sym in SYMS:
    files=sorted(Path(f"/data/probe_ofi/{sym}").glob("*_ofi.parquet"))
    if not files: continue
    df=pl.concat([pl.read_parquet(f,columns=["cts","mid_price"]) for f in files]).sort("cts")
    cts=(df["cts"].to_numpy()//1000).astype(np.int64); mid=df["mid_price"].to_numpy()
    # sigma_1s
    ret=np.diff(np.log(mid)); sig1=float(np.std(ret[np.isfinite(ret)]))
    for win in (300,900):
        b0=(int(cts[0])//win+1)*win; b1=(int(cts[-1])//win)*win
        for bts in range(b0,b1,win):
            oi=bisect.bisect_left(cts,bts)
            if oi>=len(cts) or cts[oi]-bts>3: continue
            open_px=mid[oi]
            t20=bts+int(win*0.2)
            i20=bisect.bisect_left(cts,t20)
            if i20>=len(cts) or cts[i20]-t20>3: continue
            ci_=bisect.bisect_left(cts,bts+win)
            if ci_>=len(cts) or cts[ci_]-(bts+win)>3: continue
            dev20=(mid[i20]-open_px)/open_px           # early move
            close_up = 1 if mid[ci_]>=open_px else 0
            mb=int(np.clip(dev20*1e4//5,-6,6))         # 5bps buckets
            g=revA[mb]; g[0]+=1; g[1]+=close_up
            # B: deployable enter-at-20% reversion bet
            rem=win*0.8
            z=dev20/(sig1*math.sqrt(rem)) if sig1>0 else 0
            p_up_fair=0.5*(1+math.erf(z/math.sqrt(2)))   # model-free fair P(up) at 20%
            for thr in (0.0005,0.0010,0.0015,0.0020):    # early-move thresholds in return
                if abs(dev20)<thr: continue
                # bet AGAINST the move: if moved up (dev>0), buy DOWN at price (1-p_up_fair)
                if dev20>0:
                    entry=1-p_up_fair; won=(close_up==0)
                else:
                    entry=p_up_fair; won=(close_up==1)
                if not(0.02<entry<0.98): continue
                pnl=((1-entry) if won else -entry)-tfee(entry)
                e=evB[(win,thr)]; e[0]+=1; e[1]+=pnl; e[2]+=pnl*pnl; e[3]+=won

print("="*78)
print("A. MODEL-FREE SPOT REVERSION — P(close UP | early move at 20% elapsed)")
print("   reversion real iff early-UP -> low close-up%, early-DOWN -> high close-up%")
print("="*78)
print("  early move(20%)   n       close_up%")
for mb in range(-6,7):
    g=revA[mb]
    if g[0]<50: continue
    lab=f"{mb*5}..{mb*5+5}bps"
    print(f"  {lab:>14} {g[0]:>7}   {100*g[1]/g[0]:6.2f}%")

print("\n"+"="*78)
print("B. DEPLOYABLE enter-at-20%-and-hold reversion bet — EV net taker, by move thr")
print("   (+EV => reversion is SLOW/capturable, no latency race; entry=model-free fair px)")
print("="*78)
print("  win   thr(ret)   n       acc%    EV        CI")
for win in (300,900):
    for thr in (0.0005,0.0010,0.0015,0.0020):
        e=evB[(win,thr)]; n=e[0]
        if n<2: continue
        m=e[1]/n; se=math.sqrt(max(e[2]/n-m*m,0)/n)
        print(f"  {win}  {thr:.4f}   {n:>7} {100*e[3]/n:5.2f}   {m:+.4f}  [{m-1.96*se:+.4f},{m+1.96*se:+.4f}] {'+' if m-1.96*se>0 else ''}")
