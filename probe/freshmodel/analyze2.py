#!/usr/bin/env python3
"""Fresh-model edge — angles 2: null control, consensus, forward OOS. One pass.

D. MULTIPLE-COMPARISONS NULL CONTROL (the skeptic test):
   Under H0 (every model truly 50%), how many of the N (model,window) cells
   would show acc significantly >50% by chance? Compare observed vs expected.
   The believer case must beat this. Reports observed p<0.01 count vs N*0.01,
   and the signed-z histogram (should be centered 0 under null, shifted right
   if real edge).

E. CONSENSUS: at each (symbol,window,boundary) count models predicting up.
   Bucket by agreement strength; does the majority side win more when more
   models agree? (the owner's consensus question, directly.)

F. FORWARD OOS PERSISTENCE: per model, fresh-window (0-7d) acc vs next-window
   (7-14d) acc. Does early edge persist or regress to 50%? The deployability
   test — only persistent edge is tradeable forward.
"""
from __future__ import annotations
import sqlite3, re, math
from collections import defaultdict

NAME = re.compile(r"^h(\d+)_([a-z]+)_v3_(\d+)d_(\d{8})$")
db = sqlite3.connect("file:/data/v3.db?mode=ro", uri=True)

first_ts = {}
for mn, t in db.execute("SELECT model_name, MIN(ts_model_ran_ms) FROM predictions "
                        "WHERE resolved=1 GROUP BY model_name"):
    first_ts[mn] = t

full = defaultdict(lambda: [0,0])               # (model,win) -> [n,correct] full history
fresh = defaultdict(lambda: [0,0])              # 0-7d
nxt = defaultdict(lambda: [0,0])                # 7-14d
boundary = defaultdict(lambda: [0,0,-1])        # (sym,win,bts) -> [n_up, n_total, outcome_up]

q = ("SELECT model_name, market_window_seconds, ts_model_ran_ms, ts_contract_open_ms, "
     "prediction_correct, pred_direction, warmup, symbol, contract_result "
     "FROM predictions WHERE resolved=1 AND contract_result IN ('up','down')")
for mn,win,ts,bts,correct,pdir,warm,sym,res in db.execute(q):
    if warm: continue
    m = NAME.match(mn or "")
    if not m: continue
    f0 = first_ts.get(mn)
    if f0 is None: continue
    age = (ts-f0)/86400000.0
    full[(mn,win)][0]+=1; full[(mn,win)][1]+=correct
    if age<7: fresh[(mn,win)][0]+=1; fresh[(mn,win)][1]+=correct
    elif age<14: nxt[(mn,win)][0]+=1; nxt[(mn,win)][1]+=correct
    b=boundary[(sym,win,bts)]
    if pdir=="up": b[0]+=1
    b[1]+=1; b[2]=1 if res=="up" else 0

# ---- D. null control ----
print("="*84)
print("D. MULTIPLE-COMPARISONS NULL CONTROL — full-history cells, n>=200")
print("="*84)
zs=[]; cells=0; p01=0; p001=0
for (mn,win),(n,c) in full.items():
    if n<200: continue
    cells+=1
    z=(c/n-0.5)/math.sqrt(0.25/n)
    zs.append(z)
    if z>2.326: p01+=1     # one-sided p<0.01
    if z>3.09: p001+=1     # one-sided p<0.001
import statistics
print(f"  cells (n>=200): {cells}")
print(f"  observed acc>50% at p<0.01: {p01}   expected under null: {cells*0.01:.1f}")
print(f"  observed acc>50% at p<0.001: {p001}  expected under null: {cells*0.001:.2f}")
print(f"  mean signed-z: {statistics.mean(zs):+.3f}  (null=0; >0 = systematic edge)")
print(f"  median signed-z: {statistics.median(zs):+.3f}")
pos=sum(1 for z in zs if z>0)
print(f"  cells with acc>50%: {pos}/{cells} ({100*pos/cells:.0f}%)  (null=50%)")

# ---- E. consensus ----
print("\n"+"="*84)
print("E. CONSENSUS — majority-side accuracy by agreement strength")
print("="*84)
buck = defaultdict(lambda: [0,0])   # agreement-decile -> [n_boundaries, majority_correct]
for (sym,win,bts),(up,tot,out) in boundary.items():
    if tot<5 or out<0: continue     # need >=5 models at the boundary
    frac_up = up/tot
    maj_up = frac_up>=0.5
    agree = max(frac_up, 1-frac_up)   # 0.5..1.0
    maj_correct = 1 if (maj_up == (out==1)) else 0
    bk = min(int((agree-0.5)/0.05), 9)  # 0:50-55% .. 9:95-100%
    buck[bk][0]+=1; buck[bk][1]+=maj_correct
print(f"  {'agreement':>12} {'n_bndry':>8} {'maj_acc%':>9}")
for bk in range(10):
    n,c=buck[bk]
    if n: print(f"  {50+bk*5:>3}-{55+bk*5:>3}%   {n:>8,} {100*c/n:>8.2f}")

# ---- F. forward OOS ----
print("\n"+"="*84)
print("F. FORWARD OOS — fresh-window (0-7d) acc vs next-window (7-14d) acc, per model")
print("="*84)
pairs=[]
for k in fresh:
    fn,fc = fresh[k];
    if k in nxt:
        nn,nc = nxt[k]
        if fn>=100 and nn>=100:
            pairs.append((fc/fn, nc/nn, fn, nn, k))
if pairs:
    fa=[p[0] for p in pairs]; na=[p[1] for p in pairs]
    mf=statistics.mean(fa); mn_=statistics.mean(na)
    # correlation
    cov=sum((a-mf)*(b-mn_) for a,b,_,_,_ in pairs)
    sf=math.sqrt(sum((a-mf)**2 for a in fa)); sn=math.sqrt(sum((b-mn_)**2 for b in na))
    rho=cov/(sf*sn) if sf*sn>0 else 0
    print(f"  pairs (model,win with fresh n>=100 AND next n>=100): {len(pairs)}")
    print(f"  mean fresh acc: {100*mf:.2f}%   mean next acc: {100*mn_:.2f}%")
    print(f"  corr(fresh acc, next acc): {rho:+.3f}  (>0 = early edge persists; ~0 = reverts)")
    # the money cut: models that looked good fresh -> how do they do next?
    good=[(b,nn) for a,b,fn,nn,_ in pairs if a>=0.53]
    if good:
        gn=sum(nn for _,nn in good); gc=sum(b*nn for b,nn in good)
        print(f"  models fresh-acc>=53% (n={len(good)}): their NEXT-window acc = {100*gc/gn:.2f}%")
    bad=[(b,nn) for a,b,fn,nn,_ in pairs if a<0.50]
    if bad:
        bn=sum(nn for _,nn in bad); bc=sum(b*nn for b,nn in bad)
        print(f"  models fresh-acc<50% (n={len(bad)}): their NEXT-window acc = {100*bc/bn:.2f}%")
else:
    print("  no model has both fresh n>=100 and next n>=100")
