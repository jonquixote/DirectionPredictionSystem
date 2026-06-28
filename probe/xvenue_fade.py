#!/usr/bin/env python3
"""UNIFIED cross-venue fade analyzer — the rigorous transfer test.

⚠ SUPERSEDED (2026-06-27): the PM side reads v3.db p_market, which is the NON-executable
stale-cache Gamma artifact (see PMARKET_ARTIFACT_FINDING.md / probe/synthesis.py). Its
"+12pp PM gap" is the artifact; on executable book-mid there is no edge. Kept for the record.


The transfer verdict (does the Polymarket-measured long-bias fade survive on Kalshi)
was previously a method mismatch: PM scripts dedup to ONE early-window snapshot per
window, while the Kalshi rederive does an intra-window first-fire SCAN. This applies
the IDENTICAL rule, the IDENTICAL way, to BOTH venues:

  intra-window FIRST-FIRE scan: per window, walk obs in time order; fire at the first
  obs with up>=X AND |spot dev-from-open| < T(bps) AND phase<=0.8; buy DOWN at the
  executable down price; hold to resolution. Outcome = SPOT-DERIVED for both venues
  (down wins if spot_close < spot_open) so the comparison shares one truth basis.
  Taker fee 0.07*e*(1-e). Bootstrap-95 on EV/share.

Polymarket prices  <- v3.db (p_market), intra-window over ALL prediction rows (no dedup),
                      OFI spot for dev + spot-derived outcome (cross-checked vs contract_result).
Kalshi prices      <- kalshi_fade.db (orderbook_fp), Coinbase spot.
Reports PM-all, PM-recent (date-matched to Kalshi), and Kalshi side by side -> apples to apples.
Kalshi is 15m-only, so PM is sliced to w900 for the head-to-head.
"""
from __future__ import annotations
import bisect, datetime, sqlite3, math, json
from pathlib import Path
from collections import defaultdict
import numpy as np
import polars as pl

def tfee(e): return 0.07*e*(1-e)
def boot(a, nb=5000, seed=7):
    a=np.asarray(a)
    if len(a)<2: return (float('nan'),float('nan'))
    rng=np.random.default_rng(seed)
    m=np.array([a[rng.integers(0,len(a),len(a))].mean() for _ in range(nb)])
    return float(np.percentile(m,2.5)),float(np.percentile(m,97.5))

T_BPS=5.0
SYMS=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]

# ---------- spot (OFI) for Polymarket ----------
spot={}
for sym in SYMS:
    fs=sorted(Path(f"/data/probe_ofi/{sym}").glob("*_ofi.parquet"))
    if not fs: continue
    d=pl.concat([pl.read_parquet(f,columns=["cts","mid_price"]) for f in fs]).sort("cts")
    spot[sym]=((d["cts"].to_numpy()//1000).astype(np.int64), d["mid_price"].to_numpy())

def spot_at(sym, t_s):
    cts,mid=spot[sym]; i=bisect.bisect_left(cts,t_s)
    if i>=len(cts): i=len(cts)-1
    if i>0 and (i>=len(cts) or abs(cts[i-1]-t_s)<abs(cts[i]-t_s)): i-=1
    return mid[i], cts[i]

# ---------- Polymarket: intra-window first-fire scan ----------
def pm_fires(X, win, since_ms=0):
    """Return list of (entry, down_win_spot, contract_down) for PM w/ intra-window scan."""
    pmdb=sqlite3.connect("file:/data/v3.db?mode=ro",uri=True)
    wins=defaultdict(list)  # (sym,bts) -> [(ts,pmk,pdir,contract_res)]
    for ts,bts,pmk,pdir,sym,res in pmdb.execute(
        "SELECT ts_model_ran_ms,ts_contract_open_ms,p_market,pred_direction,symbol,contract_result "
        "FROM predictions WHERE resolved=1 AND contract_result IN('up','down') "
        "AND market_window_seconds=? AND p_market IS NOT NULL AND warmup=0 AND ts_model_ran_ms>? "
        "ORDER BY ts_model_ran_ms",(win,since_ms)):
        if not(0<pmk<1) or sym not in spot: continue
        wins[(sym,bts)].append((ts,pmk,pdir,res))
    out=[]
    for (sym,bts),rows in wins.items():
        open_s=bts//1000
        so,co=spot_at(sym,open_s)
        if abs(co-open_s)>3 or not so: continue
        sc,_=spot_at(sym,open_s+win)            # spot at window close
        down_win_spot = 1 if sc<so else 0
        contract_down = 1 if rows[0][3]=="down" else 0
        for ts,pmk,pdir,res in rows:
            cur,_=spot_at(sym,ts//1000)
            move_bps=abs(cur-so)/so*1e4
            uppx=pmk if pdir=="up" else 1-pmk
            phase=(ts//1000-open_s)/win
            if phase>0.8: break
            if uppx>=X and move_bps<T_BPS:
                entry=1-uppx
                if 0.02<entry<0.98:
                    out.append((entry,down_win_spot,contract_down))
                break
    return out

# ---------- Kalshi: intra-window first-fire scan ----------
def book_up_mid(ob_json):
    o=json.loads(ob_json); y=o.get("yes_dollars") or []; n=o.get("no_dollars") or []
    if not y: return None
    yb=max(float(p) for p,s in y)
    nb=max((float(p) for p,s in n), default=None)
    ya=(1-nb) if nb is not None else None
    return yb, ((yb+ya)/2 if ya is not None else yb)

def kalshi_fires(X, since_ts=0):
    kdb=sqlite3.connect("file:/data/kalshi_fade.db?mode=ro",uri=True)
    wins=kdb.execute("SELECT symbol,boundary_ts,close_ts,spot_open FROM windows "
                     "WHERE spot_open IS NOT NULL AND close_ts<strftime('%s','now') AND boundary_ts>?",
                     (since_ts,)).fetchall()
    out=[]
    for sym,bts,cts,sopen in wins:
        rows=kdb.execute("SELECT ts,orderbook_json,dev_bps,phase,spot FROM obs WHERE symbol=? AND boundary_ts=? ORDER BY ts",
                         (sym,bts)).fetchall()
        if not rows: continue
        # spot-derived outcome: last obs spot at/<=close vs open
        sclose=None
        for ts,ob,dev,ph,sp in rows:
            if ts<=cts and sp is not None: sclose=sp
        if sclose is None: continue
        down_win = 1 if sclose<sopen else 0
        for ts,ob,dev,ph,sp in rows:
            if ph is None or ph>0.8: continue
            bp=book_up_mid(ob)
            if not bp: continue
            yb,um=bp
            if um>=X and dev is not None and abs(dev)<T_BPS:
                entry=1-yb
                if 0.02<entry<0.98:
                    out.append((entry,down_win,down_win))
                break
    return out

def summarize(fires, label, use_contract=False):
    if len(fires)<2:
        print(f"  {label:26s} n={len(fires)} (insufficient)"); return
    pnl=[]; ents=[]; wn=0
    for entry,dw_spot,dw_contract in fires:
        won = (dw_contract if use_contract else dw_spot)==1
        pnl.append(((1-entry) if won else -entry)-tfee(entry)); ents.append(entry); wn+=won
    a=np.array(pnl); me=float(np.mean(ents)); acc=wn/len(pnl); gap=100*(acc-me)
    lo,hi=boot(a)
    print(f"  {label:26s} n={len(pnl):>5} down-win%={100*acc:5.1f} entry={100*me:5.1f}c "
          f"gap={gap:+6.2f}pp EV={a.mean():+.4f} [{lo:+.4f},{hi:+.4f}]")

# date-match window: earliest Kalshi window -> use as PM since_ms
kdb=sqlite3.connect("file:/data/kalshi_fade.db?mode=ro",uri=True)
kmin=kdb.execute("SELECT MIN(boundary_ts) FROM windows WHERE spot_open IS NOT NULL").fetchone()[0] or 0
kmin_ms=kmin*1000
print("="*94)
print(f"UNIFIED cross-venue fade (intra-window first-fire scan, |dev|<{T_BPS}bps, spot-outcome, taker)")
print(f"Kalshi data starts {datetime.datetime.fromtimestamp(kmin,datetime.timezone.utc):%Y-%m-%d %H:%M}Z; PM-recent matched to it")
print("="*94)
for X in (0.55,0.58,0.60):
    print(f"\n up>={X:.2f}  (PM=w900 for Kalshi head-to-head)")
    pm_all=pm_fires(X,900,0)
    pm_rec=pm_fires(X,900,kmin_ms)
    kal   =kalshi_fires(X,0)
    summarize(pm_all,"Polymarket w900 (all)")
    summarize(pm_all,"  PM (all, CONTRACT res)",use_contract=True)
    summarize(pm_rec,"Polymarket w900 (recent)")
    summarize(kal,   "Kalshi 15m (spot res)")
print("\nread: same rule, same intra-window scan, same spot-outcome basis on both venues.")
print("  PM(all) spot vs CONTRACT line = sanity that spot-derived ~ real settlement.")
print("  If PM gap >> Kalshi gap on the DATE-MATCHED slice -> edge is venue-specific (no transfer).")
