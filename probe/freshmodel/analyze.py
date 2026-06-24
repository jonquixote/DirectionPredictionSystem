#!/usr/bin/env python3
"""Fresh-model edge analysis — the axis the probe never tested.

Three questions, one run:
 A. DECAY CURVE: is per-model accuracy higher in the first days after a model
    starts predicting, decaying with age? (the reason retrains are weekly)
 B. SIBLING CONSISTENCY: for each cell (symbol,horizon,train_days), do the
    independent weekly retrains ALL show fresh-window edge? (multiple-comparisons-
    proof — chance does not repeat across independent retrains of the same cell)
 C. TRADEABILITY: at fire (above_threshold=1), EV vs logged p_market net of
    Kalshi/Polymarket fee, per model, fresh window. Raw accuracy != money.

model_name = h{H}_{sym}_v3_{days}d_{YYYYMMDD}. cell=(H,sym,days). sibling=cell w/ diff date.
Fresh window = first FRESH_DAYS of a model's own predictions. warmup excluded.
"""
from __future__ import annotations
import sqlite3, re, math, datetime
from collections import defaultdict

FRESH_DAYS = 7
FEE_KALSHI = 0.0175
HALF_SPREAD = 0.005
NAME = re.compile(r"^h(\d+)_([a-z]+)_v3_(\d+)d_(\d{8})$")

def wilson(w, n, z=1.96):
    if n == 0: return (0,0)
    p=w/n; d=1+z*z/n; c=p+z*z/(2*n); s=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))
    return ((c-s)/d,(c+s)/d)

db = sqlite3.connect("file:/data/v3.db?mode=ro", uri=True)

# first-prediction ts per model (model age origin)
first_ts = {}
for mn, t in db.execute("SELECT model_name, MIN(ts_model_ran_ms) FROM predictions "
                        "WHERE resolved=1 GROUP BY model_name"):
    first_ts[mn] = t

# pull resolved directional rows, stream-accumulate
# accumulators
age_bucket = defaultdict(lambda: [0,0])          # global decay: age_dec -> [n, correct]
fresh_model = defaultdict(lambda: [0,0])         # (model,win) fresh-window -> [n,correct]
full_model  = defaultdict(lambda: [0,0])         # (model,win) all-age -> [n,correct]
fresh_ev    = defaultdict(lambda: [0,0.0,0])     # (model,win) fired fresh -> [n, pnl_sum, wins]

q = ("SELECT model_name, market_window_seconds, ts_model_ran_ms, prediction_correct, "
     "p_market, pred_direction, above_threshold, warmup, contract_result "
     "FROM predictions WHERE resolved=1 AND contract_result IN ('up','down')")
for mn, win, ts, correct, pmk, pdir, above, warm, res in db.execute(q):
    if warm: continue
    m = NAME.match(mn or "")
    if not m: continue
    H, sym, days, date = m.group(1), m.group(2), m.group(3), m.group(4)
    f0 = first_ts.get(mn)
    if f0 is None: continue
    age_d = (ts - f0) / 86400000.0
    full_model[(mn,win)][0]+=1; full_model[(mn,win)][1]+=correct
    ad = min(int(age_d), 27)
    age_bucket[ad][0]+=1; age_bucket[ad][1]+=correct
    if age_d < FRESH_DAYS:
        fresh_model[(mn,win)][0]+=1; fresh_model[(mn,win)][1]+=correct
        if above and pmk is not None and 0<pmk<1:
            entry = pmk if pdir=="up" else 1-pmk
            won = (correct==1)
            pnl = ((1-entry) if won else -entry) - (FEE_KALSHI*entry*(1-entry)+HALF_SPREAD)
            e=fresh_ev[(mn,win)]; e[0]+=1; e[1]+=pnl; e[2]+=won

print("="*86)
print("A. GLOBAL DECAY CURVE — accuracy by model-age-day (all models pooled, warmup excl)")
print("="*86)
print(f"{'age_day':>7} {'n':>9} {'acc%':>7}")
for d in range(28):
    n,c = age_bucket[d]
    if n: print(f"{d:>7} {n:>9,} {100*c/n:>6.2f}")

# helper for cell grouping
def cell_of(mn):
    m=NAME.match(mn); return (m.group(1),m.group(2),m.group(3)) if m else None
def date_of(mn):
    m=NAME.match(mn); return m.group(4) if m else None

print("\n"+"="*86)
print(f"B. SIBLING CONSISTENCY — fresh-window (<{FRESH_DAYS}d) acc per retrain, per cell+window")
print("   (only cells with >=2 sibling retrains each n>=100; sorted by mean fresh acc)")
print("="*86)
# group fresh_model by (cell,win) -> list of (date, n, acc)
cellwin = defaultdict(list)
for (mn,win),(n,c) in fresh_model.items():
    if n>=100:
        cl=cell_of(mn)
        if cl: cellwin[(cl,win)].append((date_of(mn), n, c/n))
rows=[]
for (cl,win),sibs in cellwin.items():
    if len(sibs)>=2:
        accs=[a for _,_,a in sibs]
        rows.append((sum(accs)/len(accs), cl, win, sorted(sibs)))
rows.sort(reverse=True)
for mean_a, cl, win, sibs in rows[:25]:
    sibstr = " ".join(f"{d}:{100*a:.1f}%(n{n})" for d,n,a in sibs)
    allpos = all(a>0.52 for _,_,a in sibs)
    print(f"  h{cl[0]}_{cl[1]}_{cl[2]}d w{win}: mean {100*mean_a:.1f}% {'CONSISTENT>52' if allpos else ''}")
    print(f"      {sibstr}")

print("\n"+"="*86)
print(f"C. TRADEABILITY — fired (above_threshold), fresh window, EV vs p_market net fee")
print("   (BTC/ETH/SOL only — XRP has no p_market; n>=100 fired; sorted by EV)")
print("="*86)
evrows=[]
for (mn,win),(n,pnl,wins) in fresh_ev.items():
    if n>=100:
        evrows.append((pnl/n, mn, win, n, wins/n))
evrows.sort(reverse=True)
print(f"  {'model':40s} {'win':>5} {'n':>5} {'acc%':>6} {'EV/$':>8}")
for ev,mn,win,n,acc in evrows[:30]:
    print(f"  {mn:40s} {win:>5} {n:>5} {100*acc:>6.2f} {ev:>+8.4f}")
pos = sum(1 for ev,_,_,_,_ in evrows if ev>0)
print(f"\n  fired-fresh cells n>=100: {len(evrows)}  EV>0: {pos} ({100*pos/len(evrows) if evrows else 0:.0f}%)")
if evrows:
    import statistics
    allpnl=[ev for ev,_,_,_,_ in evrows]
    print(f"  mean EV/$ across cells: {statistics.mean(allpnl):+.4f}  median: {statistics.median(allpnl):+.4f}")
