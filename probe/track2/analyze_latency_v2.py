#!/usr/bin/env python3
"""Track 2 lag analysis WITH CONTROLS — is post-event repricing causal or just noise?

v1 measured only time-to-first-1c-move IN the spot direction and called >2.5s "viable".
But a contract mid wanders >=1c on its own volatility, so that number is confounded.
This adds the controls that isolate a real, exploitable latency lag:

  SAME  : time to first >=tick move in the spot direction  (<=horizon, else censored)
  OPP   : time to first >=tick move AGAINST the spot dir    (within-event control)
  PLACEBO: same measurement anchored at a random NON-event tick (baseline hazard)

Reprice is causal/exploitable only if SAME fires materially MORE OFTEN and FASTER than
OPP and PLACEBO. If SAME ~= OPP ~= PLACEBO, the "lag" is just mid volatility (no edge).
Also reports directional hit-rate: of events that moved >=tick within horizon, what
fraction moved WITH spot (50% => coin flip => nothing to take).

Usage: analyze_latency_v2.py --db /data/track2_latency.db [--event-bps 5] [--tick 0.01]
"""
from __future__ import annotations
import argparse, sqlite3
import numpy as np

def first_move(series_ts, series_mid, i, sign, tick, horizon_ms):
    base = series_mid[i]
    if base is None:
        return None
    for j in range(i + 1, len(series_mid)):
        if series_ts[j] - series_ts[i] > horizon_ms:
            return None
        m = series_mid[j]
        if m is None:
            continue
        if (m - base) * sign >= tick:
            return (series_ts[j] - series_ts[i]) / 1000.0
    return None

def summ(x):
    x = [v for v in x if v is not None]
    if not x:
        return "n=0"
    a = np.array(x)
    return f"n={len(a)} med={np.median(a):.2f}s p75={np.percentile(a,75):.2f}s"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--event-bps", type=float, default=5.0)
    ap.add_argument("--tick", type=float, default=0.01)
    ap.add_argument("--horizon", type=float, default=60.0)
    a = ap.parse_args()
    H = a.horizon * 1000
    db = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    coins = [r[0] for r in db.execute("SELECT DISTINCT coin FROM ticks")]
    rng = np.random.default_rng(7)

    res = {v: {"same": [], "opp": [], "placebo": [], "with": 0, "against": 0}
           for v in ("kalshi", "pm")}
    n_events = 0
    for coin in coins:
        rows = db.execute(
            "SELECT ts_ms, boundary_ts, cb_bid, cb_ask, k_yes_bid, k_yes_ask, pm_up_bid, pm_up_ask "
            "FROM ticks WHERE coin=? ORDER BY ts_ms", (coin,)).fetchall()
        ts = [r[0] for r in rows]; bd = [r[1] for r in rows]
        cb = [( (r[2]+r[3])/2 if r[2] and r[3] else None) for r in rows]
        km = [((r[4]+r[5])/2 if r[4] is not None and r[5] is not None else None) for r in rows]
        pm = [((r[6]+r[7])/2 if r[6] is not None and r[7] is not None else None) for r in rows]
        n = len(ts)
        def censor(i):
            return min(H, (bd[i] + 900) * 1000 - ts[i])
        event_idx = []
        for i in range(1, n):
            if cb[i] is None or cb[i-1] is None or cb[i-1] == 0 or bd[i] is None:
                continue
            if bd[i] != bd[i-1]:
                continue
            mv = (cb[i] - cb[i-1]) / cb[i-1] * 1e4
            if abs(mv) >= a.event_bps:
                event_idx.append((i, 1 if mv > 0 else -1))
        n_events += len(event_idx)
        for i, sgn in event_idx:
            for venue, S in (("kalshi", km), ("pm", pm)):
                res[venue]["same"].append(first_move(ts, S, i, sgn, a.tick, censor(i)))
                res[venue]["opp"].append(first_move(ts, S, i, -sgn, a.tick, censor(i)))
                s = res[venue]["same"][-1]; o = res[venue]["opp"][-1]
                if s is not None and (o is None or s < o):
                    res[venue]["with"] += 1
                elif o is not None and (s is None or o < s):
                    res[venue]["against"] += 1
        # placebo: random in-window anchors, random sign, same measurement
        if event_idx:
            picks = rng.integers(1, n, size=len(event_idx))
            for i in picks:
                i = int(i)
                if bd[i] is None:
                    continue
                sgn = 1 if rng.random() < 0.5 else -1
                for venue, S in (("kalshi", km), ("pm", pm)):
                    res[venue]["placebo"].append(first_move(ts, S, i, sgn, a.tick, censor(i)))

    print("=" * 90)
    print(f"Track 2 lag WITH CONTROLS (event>={a.event_bps}bps, reprice>={100*a.tick:.0f}c, horizon {a.horizon:.0f}s)")
    print(f"events={n_events}")
    print("=" * 90)
    for venue in ("kalshi", "pm"):
        R = res[venue]
        ns = len([x for x in R["same"] if x is not None])
        no = len([x for x in R["opp"] if x is not None])
        npl = len([x for x in R["placebo"] if x is not None])
        tot = len(R["same"]); totpl = len(R["placebo"])
        print(f"\n{venue.upper()}:")
        print(f"  SAME dir : {summ(R['same'])}  fire%={100*ns/max(tot,1):.0f}")
        print(f"  OPP  dir : {summ(R['opp'])}  fire%={100*no/max(tot,1):.0f}")
        print(f"  PLACEBO  : {summ(R['placebo'])}  fire%={100*npl/max(totpl,1):.0f}")
        w, ag = R["with"], R["against"]
        if w + ag:
            print(f"  directional hit-rate (first mover WITH spot): {100*w/(w+ag):.0f}% "
                  f"(with={w} against={ag}) [50% = coin flip = no edge]")
        # verdict
        sfire = ns/max(tot,1); ofire = no/max(tot,1); pfire = npl/max(totpl,1)
        smed = np.median([x for x in R['same'] if x is not None]) if ns else 99
        omed = np.median([x for x in R['opp'] if x is not None]) if no else 99
        if sfire > 1.3*max(ofire, pfire) and smed < 0.8*omed:
            print(f"  VERDICT[{venue}]: SAME fires more & faster than controls -> REAL directional lag; taker worth a sim")
        else:
            print(f"  VERDICT[{venue}]: SAME ~= controls -> lag is mid VOLATILITY, not exploitable repricing")
    return 0

if __name__ == "__main__":
    import sys; sys.exit(main())
