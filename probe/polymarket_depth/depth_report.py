#!/usr/bin/env python3
"""Polymarket fade-entry DEPTH report — answers job C: when the fade fires (window
goes rich + spot flat), is there fillable size at the down entry (~30-49c)?

From /data/pm_depth.db. A fire-moment = obs with up_mid>=0.55 (down cheap) AND
|dev_bps|<5 (spot flat) AND phase<=0.8. At those moments report the $ resting in the
down 30-49c band (down_band_depth) — distribution, by coin/dur. Also: what fraction
of windows EVER reach the fade zone, and the per-window max band depth (the realistic
fillable size if you wait for the best moment in the window).

If median fillable $ at fire is meaningful (>$100) -> Polymarket-direct execution of the
richest 5m slice is viable and SIDESTEPS the Kalshi transfer gate. If ~$0 -> thin, Kalshi
stays the venue.
"""
from __future__ import annotations
import sqlite3
from collections import defaultdict
import numpy as np

db=sqlite3.connect("file:/data/pm_depth.db?mode=ro",uri=True)

def pct(a,p): return float(np.percentile(a,p)) if len(a) else float('nan')

print("="*92)
print("Polymarket fade-entry depth (fire = up_mid>=0.55 & |dev_bps|<5 & phase<=0.8)")
print("="*92)

# overall fire-moment band depth
rows=db.execute("SELECT coin,dur,down_band_depth,best_down_ask,up_mid,dev_bps,phase FROM obs "
                "WHERE up_mid>=0.55 AND abs(dev_bps)<5 AND phase<=0.8").fetchall()
band=[r[2] for r in rows if r[2] is not None]
print(f"\n fire-moments n={len(rows)}")
if band:
    print(f" down 30-49c band $ at fire:  median={pct(band,50):.0f}  p25={pct(band,25):.0f}  "
          f"p75={pct(band,75):.0f}  max={max(band):.0f}  mean={np.mean(band):.0f}")
    nz=[b for b in band if b>0]
    print(f"   fire-moments with depth>0: {len(nz)}/{len(band)} ({100*len(nz)/len(band):.0f}%)")
    asks=[r[3] for r in rows if r[3] is not None]
    print(f" best_down_ask at fire:  median={100*pct(asks,50):.1f}c  min={100*min(asks):.1f}c")

# by coin/dur
print("\n by coin x dur (fire-moments | median band $ | max band $):")
agg=defaultdict(list)
for coin,dur,bd,ba,um,dv,ph in rows:
    if bd is not None: agg[(coin,dur)].append(bd)
for k in sorted(agg):
    v=agg[k]
    print(f"   {k[0]:5s} {k[1]:3s}: n={len(v):>4}  median=${pct(v,50):>6.0f}  max=${max(v):>7.0f}")

# per-window: did it reach the fade zone? best (max) band depth in-window at a fire moment
print("\n per-window fade-zone reach + best fillable $ (max band depth at any fire moment):")
wins=defaultdict(lambda:[0,0.0])  # (coin,dur,boundary)->[reached, max_band]
allwins=set()
for coin,dur,bts in db.execute("SELECT DISTINCT coin,dur,boundary_ts FROM obs"):
    allwins.add((coin,dur,bts))
for coin,dur,bts,bd in db.execute("SELECT coin,dur,boundary_ts,down_band_depth FROM obs "
                                  "WHERE up_mid>=0.55 AND abs(dev_bps)<5 AND phase<=0.8"):
    g=wins[(coin,dur,bts)]; g[0]=1
    if bd is not None and bd>g[1]: g[1]=bd
reached=len(wins); total=len(allwins)
print(f"   windows reaching fade zone: {reached}/{total} ({100*reached/max(total,1):.0f}%)")
if wins:
    best=[g[1] for g in wins.values()]
    print(f"   per-window best fillable $ (max band at a fire moment): median=${pct(best,50):.0f} "
          f"p75=${pct(best,75):.0f} max=${max(best):.0f}")
import time
age=time.time()-(db.execute("SELECT MAX(ts) FROM obs").fetchone()[0] or 0)
n=db.execute("SELECT COUNT(*) FROM obs").fetchone()[0]
print(f"\n (db: {n} obs, freshest {age:.0f}s ago — depth picture sharpens as more windows go rich)")
