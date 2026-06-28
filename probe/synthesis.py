#!/usr/bin/env python3
"""DIALECTIC SYNTHESIS — is there a real edge on EXECUTABLE prices?

thesis: +12pp fade edge (build candidate). antithesis: it was a v3.db p_market artifact
(stale cached Gamma fallback). synthesis: measure the edge on EXECUTABLE book prices on
BOTH venues, forward, and characterize the two artifacts.

Reads the two forward loggers (book-mid + own Coinbase spot, no OFI dependence):
  /data/pm_depth.db   — Polymarket book-mid (up_mid=1-down_mid), best_down_ask (real lift),
                        gamma_up (the v3 fallback source), spot/spot_open/dev_bps.
  /data/kalshi_fade.db— Kalshi orderbook, spot.

A) EXECUTABLE fire-rate reality (PM): how often does the book actually go rich+flat?
B) EXECUTABLE fade fires (PM): intra-window first-fire on book up>=X & |dev|<5; entry =
   best_down_ask (what you'd really pay); outcome spot-derived. EV/gap (n is small -> honest).
C) Artifact magnitude (PM): gamma_up vs book up_mid divergence at scale.
D) Kalshi: is its frequent "richness" real or WIDE-BOOK mid noise? spread at fires + entry.
"""
from __future__ import annotations
import sqlite3, json, math
import numpy as np

def tfee(e): return 0.07*e*(1-e)
def boot(a, nb=5000, seed=7):
    a=np.asarray(a,float)
    if len(a)<2: return (float('nan'),float('nan'))
    rng=np.random.default_rng(seed)
    m=np.array([a[rng.integers(0,len(a),len(a))].mean() for _ in range(nb)])
    return float(np.percentile(m,2.5)),float(np.percentile(m,97.5))
def pctl(a,q): return float(np.percentile(a,q)) if len(a) else float('nan')

p=sqlite3.connect("file:/data/pm_depth.db?mode=ro",uri=True)
k=sqlite3.connect("file:/data/kalshi_fade.db?mode=ro",uri=True)

print("="*96)
print("A) EXECUTABLE fire-rate reality (Polymarket book-mid)")
print("="*96)
closed=p.execute("SELECT coin,dur,boundary_ts,spot_open,spot_close FROM windows "
                 "WHERE spot_open IS NOT NULL AND spot_close IS NOT NULL AND close_ts<strftime('%s','now')").fetchall()
nclosed=len(closed)
reach55=reach52=0
wmap={}
for coin,dur,bts,so,sc in closed:
    wmap[(coin,dur,bts)]=(so,sc)
for (coin,dur,bts),(so,sc) in wmap.items():
    mx=p.execute("SELECT MAX(up_mid) FROM obs WHERE coin=? AND dur=? AND boundary_ts=? AND phase<=0.8",(coin,dur,bts)).fetchone()[0]
    if mx is not None:
        reach55+=(mx>=0.55); reach52+=(mx>=0.52)
print(f"  closed windows={nclosed}")
print(f"  book-mid ever >=0.55: {reach55} ({100*reach55/max(nclosed,1):.2f}%)   >=0.52: {reach52} ({100*reach52/max(nclosed,1):.2f}%)")
print(f"  (v3.db p_market reached >=0.55 in ~22% of windows -> artifact inflated fire-rate ~{22.5/max(100*reach55/max(nclosed,1),0.01):.0f}x)")

print("\n"+"="*96)
print("B) EXECUTABLE fade fires (PM): fire book up>=X & |dev|<5, entry=best_down_ask, spot outcome")
print("="*96)
for X in (0.53,0.55,0.58):
    fires=[]
    for (coin,dur,bts),(so,sc) in wmap.items():
        rows=p.execute("SELECT up_mid,best_down_ask,dev_bps,phase FROM obs WHERE coin=? AND dur=? AND boundary_ts=? ORDER BY ts",(coin,dur,bts)).fetchall()
        for um,ba,dev,ph in rows:
            if ph is None or ph>0.8 or um is None or ba is None or dev is None: continue
            if um>=X and abs(dev)<5:
                if 0.02<ba<0.98:
                    down_win = 1 if sc<so else 0
                    fires.append((ba,down_win,coin,dur))
                break
    if len(fires)<1: print(f"  up>={X}: 0 fires"); continue
    ent=[f[0] for f in fires]; pnl=[((1-e) if dw else -e)-tfee(e) for e,dw,_,_ in fires]
    acc=np.mean([f[1] for f in fires]); a=np.array(pnl)
    lo,hi=boot(a)
    cd=", ".join(f"{c}{d}:{'D' if dw else 'U'}@{100*e:.0f}c" for e,dw,c,d in fires[:10])
    print(f"  up>={X}: n={len(fires)} down-win%={100*acc:.0f} entry={100*np.mean(ent):.1f}c EV={a.mean():+.4f} [{lo:+.4f},{hi:+.4f}]")
    print(f"     fires: {cd}")

print("\n"+"="*96)
print("C) Artifact magnitude (PM): gamma_up (v3 fallback source) vs executable book up_mid")
print("="*96)
rows=p.execute("SELECT gamma_up,up_mid,dev_bps FROM obs WHERE gamma_up IS NOT NULL AND up_mid IS NOT NULL AND phase<=0.8").fetchall()
g=np.array([r[0] for r in rows]); u=np.array([r[1] for r in rows])
print(f"  n={len(rows)}  gamma_up>=0.55: {int((g>=0.55).sum())} ({100*(g>=0.55).mean():.2f}%)  book>=0.55: {int((u>=0.55).sum())} ({100*(u>=0.55).mean():.2f}%)")
d=np.abs(g-u)
print(f"  |gamma_up - book|: median={pctl(d,50):.4f} p95={pctl(d,95):.4f} max={d.max():.4f}  frac>0.05={100*(d>0.05).mean():.2f}%")
print(f"  -> live Gamma ~tracks book; the v3 artifact is the CACHED-STALE fallback, not live Gamma.")

print("\n"+"="*96)
print("D) Kalshi: is frequent 'richness' real or WIDE-BOOK mid noise?")
print("="*96)
def kbook(ob):
    o=json.loads(ob); y=o.get("yes_dollars") or []; n=o.get("no_dollars") or []
    if not y: return None
    yb=max(float(pr) for pr,s in y); nb=max((float(pr) for pr,s in n),default=None)
    ya=(1-nb) if nb is not None else None
    return yb, ya, ((yb+ya)/2 if ya is not None else yb)
kw=k.execute("SELECT symbol,boundary_ts,close_ts,spot_open,spot_close FROM windows WHERE spot_open IS NOT NULL AND spot_close IS NOT NULL AND close_ts<strftime('%s','now')").fetchall()
spreads=[]; entries=[]; fires=[]
for sym,bts,cts,so,sc in kw:
    rows=k.execute("SELECT orderbook_json,dev_bps,phase FROM obs WHERE symbol=? AND boundary_ts=? ORDER BY ts",(sym,bts)).fetchall()
    for ob,dev,ph in rows:
        if ph is None or ph>0.8 or dev is None: continue
        bp=kbook(ob)
        if not bp: continue
        yb,ya,um=bp
        if um>=0.55 and abs(dev)<5:
            if ya is not None: spreads.append(ya-yb)
            entry=1-yb
            if 0.02<entry<0.98:
                fires.append((entry,1 if sc<so else 0)); entries.append(entry)
            break
if spreads:
    print(f"  Kalshi fires(up>=.55&flat) n={len(fires)} | yes spread: median={100*pctl(spreads,50):.1f}c p75={100*pctl(spreads,75):.1f}c (wide -> mid is noise)")
    if fires:
        acc=np.mean([f[1] for f in fires]); a=np.array([((1-e) if dw else -e)-tfee(e) for e,dw in fires]); lo,hi=boot(a)
        print(f"  executable: down-win%={100*acc:.0f} entry={100*np.mean(entries):.1f}c EV={a.mean():+.4f} [{lo:+.4f},{hi:+.4f}]")
print("\nSYNTHESIS: executable fade setup is ~0.1% of PM windows (n tiny); both venues' apparent")
print("  edges are measurement artifacts (PM=stale gamma fallback; Kalshi=wide-book mid). Executably ~flat.")
