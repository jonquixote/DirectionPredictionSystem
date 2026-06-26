#!/usr/bin/env python3
"""FIRST-LOOK re-derivation of the fade edge on KALSHI prices (logger data).

The whole build candidate hinges on: does up>=0.55 & spot-flat -> buy DOWN win
above breakeven, on KALSHI prices (it was measured on Polymarket). This reconstructs
it from /data/kalshi_fade.db. <24h of overnight data -> DIRECTIONAL ONLY, not
conclusive; report n and CI honestly.

Per CLOSED window: scan obs chronologically, fire at the FIRST obs where
  up_mid >= X  AND  |dev_bps| < T (spot flat).
up_mid from orderbook: yes_bid=max(yes_dollars price), no_bid=max(no_dollars),
yes_ask=1-no_bid, up_mid=(yes_bid+yes_ask)/2. Buy DOWN at down_ask = 1 - yes_bid.
Outcome: down wins if spot_close < spot_open (flat=up per contract). Taker fee.
"""
from __future__ import annotations
import sqlite3, json, math, argparse
from collections import defaultdict
db=sqlite3.connect("file:/data/kalshi_fade.db?mode=ro",uri=True)

def tfee(e): return 0.07*e*(1-e)
def book_prices(ob_json):
    o=json.loads(ob_json); y=o.get("yes_dollars") or []; n=o.get("no_dollars") or []
    if not y: return None
    yb=max(float(p) for p,s in y)
    nb=max((float(p) for p,s in n), default=None)
    ya=(1-nb) if nb is not None else None
    up_mid=(yb+ya)/2 if ya is not None else yb
    return yb,nb,up_mid

def run(X,T):
    # closed windows with valid open/close
    wins=db.execute("SELECT symbol,boundary_ts,close_ts,spot_open,spot_close FROM windows "
                    "WHERE spot_open IS NOT NULL AND spot_close IS NOT NULL "
                    "AND close_ts < strftime('%s','now')").fetchall()
    pnls=[]; entries=[]; wins_n=0; fires=0; checked=0
    for sym,bts,cts,sopen,sclose in wins:
        if not sopen or not sclose: continue
        checked+=1
        rows=db.execute("SELECT ts,orderbook_json,dev_bps,phase FROM obs WHERE symbol=? AND boundary_ts=? ORDER BY ts",
                        (sym,bts)).fetchall()
        fired=False
        for ts,ob,dev,ph in rows:
            if ph is None or ph>0.8: continue
            bp=book_prices(ob)
            if not bp: continue
            yb,nb,up_mid=bp
            if up_mid>=X and abs(dev)<T:
                entry=1-yb            # buy DOWN (lift implied down ask)
                if not(0.02<entry<0.98): break
                down_win = sclose < sopen
                pnl=((1-entry) if down_win else -entry)-tfee(entry)
                pnls.append(pnl); entries.append(entry); wins_n+=down_win; fires+=1
                fired=True; break
    return checked,fires,wins_n,pnls,entries

print("="*78)
print("FIRST-LOOK Kalshi-price fade re-derivation (logger data, <24h — DIRECTIONAL)")
print("="*78)
for X in (0.55,0.58,0.60):
    checked,fires,wins_n,pnls,entries=run(X,5.0)
    if fires<1:
        print(f"  up>={X:.2f}: 0 fires (of {checked} closed windows)"); continue
    import numpy as np
    a=np.array(pnls); m=a.mean(); se=a.std()/math.sqrt(len(a)) if len(a)>1 else float('nan')
    acc=100*wins_n/fires; me=100*np.mean(entries)
    print(f"  up>={X:.2f}: closed_windows={checked} fires={fires} down_win%={acc:.1f} "
          f"entry~{me:.1f}c EV/share={m:+.4f} "+(f"95%[{m-1.96*se:+.4f},{m+1.96*se:+.4f}]" if len(a)>1 else "(n=1)"))
# per-symbol at X=0.55
print("\n  per-symbol (up>=0.55):")
wins=db.execute("SELECT symbol,boundary_ts,spot_open,spot_close FROM windows WHERE spot_open IS NOT NULL AND spot_close IS NOT NULL AND close_ts<strftime('%s','now')").fetchall()
bysym=defaultdict(lambda:[0,0,0.0])
for sym,bts,so,sc in wins:
    rows=db.execute("SELECT orderbook_json,dev_bps,phase FROM obs WHERE symbol=? AND boundary_ts=? ORDER BY ts",(sym,bts)).fetchall()
    for ob,dev,ph in rows:
        if ph is None or ph>0.8: continue
        bp=book_prices(ob)
        if not bp: continue
        yb,nb,um=bp
        if um>=0.55 and abs(dev)<5:
            entry=1-yb
            if not(0.02<entry<0.98): break
            dw=sc<so; bysym[sym][0]+=1; bysym[sym][1]+=dw; bysym[sym][2]+=((1-entry) if dw else -entry)-tfee(entry); break
for sym in sorted(bysym):
    n,w,p=bysym[sym]
    if n: print(f"    {sym}: fires={n} down_win%={100*w/n:.0f} EV/share={p/n:+.4f}")
print("\n  NOTE: <24h overnight data. n is small; CIs wide. Directional read only —")
print("  whether the SIGN/magnitude is consistent with the +5-8c Polymarket edge.")
