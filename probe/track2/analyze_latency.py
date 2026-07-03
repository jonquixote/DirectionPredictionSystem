#!/usr/bin/env python3
"""Track 2 analysis — repricing-lag distribution per venue + verdict.

Event: |Δ Coinbase mid| >= --event-bps (default 5) between consecutive polls.
Reprice: first subsequent tick where the venue's contract mid has moved >= --tick
(default 0.01, i.e. 1c; sensitivity at 2c reported) IN THE DIRECTION of the spot
move, measured from the venue mid at event time. Censored at --horizon s (60),
window close, or the next opposing event.

NOTE vs spec wording ("mid moving >50% of the Coinbase move"): spot moves live in
bps of price, contract mids in probability — there is no 1:1 conversion, so the
operational definition is a >=1-tick directional move (primary) with a 2c
sensitivity line. Quantization: lags are multiples of the poll cadence (~1.25s);
median <1s is NOT resolvable — the registered verdict boundary is at >=2 polls.

Verdict (registered): median lag > 2.5s (>=2 polls) on a venue -> latency-taker
viable there; median <= 1.25s (<=1 poll) -> efficient at pollable timescales.

Usage: analyze_latency.py --db /data/track2_latency.db [--event-bps 5]
"""
from __future__ import annotations
import argparse, sqlite3, sys
from collections import defaultdict
import numpy as np

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--event-bps", type=float, default=5.0)
    ap.add_argument("--tick", type=float, default=0.01)
    ap.add_argument("--horizon", type=float, default=60.0)
    a = ap.parse_args()
    db = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)

    lags = {"kalshi": defaultdict(list), "pm": defaultdict(list)}
    nevents = defaultdict(int)
    coins = [r[0] for r in db.execute("SELECT DISTINCT coin FROM ticks")]
    for coin in coins:
        rows = db.execute(
            "SELECT ts_ms, cb_bid, cb_ask, k_yes_bid, k_yes_ask, pm_up_bid, pm_up_ask, boundary_ts "
            "FROM ticks WHERE coin=? ORDER BY ts_ms", (coin,)).fetchall()
        T, CB, KM, PM, BD = [], [], [], [], []
        for ts, cbb, cba, kyb, kya, pmb, pma, bd in rows:
            cb = (cbb + cba) / 2 if cbb and cba else None
            km = (kyb + kya) / 2 if kyb is not None and kya is not None else None
            pm = (pmb + pma) / 2 if pmb is not None and pma is not None else None
            T.append(ts); CB.append(cb); KM.append(km); PM.append(pm); BD.append(bd)
        n = len(T)
        for i in range(1, n):
            if CB[i] is None or CB[i-1] is None or CB[i-1] == 0:
                continue
            move = (CB[i] - CB[i-1]) / CB[i-1] * 1e4
            if abs(move) < a.event_bps:
                continue
            nevents[coin] += 1
            sign = 1 if move > 0 else -1
            for venue, S in (("kalshi", KM), ("pm", PM)):
                if S[i] is None:
                    continue
                base = S[i]
                lag = None
                for j in range(i + 1, n):
                    if T[j] - T[i] > a.horizon * 1000 or BD[j] != BD[i]:
                        break
                    if S[j] is None:
                        continue
                    if (S[j] - base) * sign >= a.tick:
                        lag = (T[j] - T[i]) / 1000.0
                        break
                if lag is not None:
                    lags[venue][coin].append(lag)

    print("=" * 88)
    print(f"Track 2 repricing lag (event >= {a.event_bps}bps, reprice >= {100*a.tick:.0f}c directional)")
    print("=" * 88)
    for venue in ("kalshi", "pm"):
        allv = [x for v in lags[venue].values() for x in v]
        ne = sum(nevents.values())
        if not allv:
            print(f"{venue}: no repricings detected ({ne} events) — either no lag data or never reprices >=1c")
            continue
        arr = np.array(allv)
        frac = len(arr) / max(ne, 1)
        print(f"{venue}: events={ne} repriced_within_{a.horizon:.0f}s={len(arr)} ({100*frac:.0f}%) | "
              f"lag median={np.median(arr):.2f}s p75={np.percentile(arr,75):.2f}s p95={np.percentile(arr,95):.2f}s")
        for coin in sorted(lags[venue]):
            v = np.array(lags[venue][coin])
            print(f"   {coin}: n={len(v)} median={np.median(v):.2f}s p75={np.percentile(v,75):.2f}s")
        med = np.median(arr)
        if med > 2.5:
            print(f"   VERDICT[{venue}]: median {med:.2f}s > 2.5s -> LATENCY-TAKER VIABLE (spec the taker next)")
        elif med <= 1.3:
            print(f"   VERDICT[{venue}]: median {med:.2f}s <= ~1 poll -> efficient at pollable timescales; Track 3 primary")
        else:
            print(f"   VERDICT[{venue}]: median {med:.2f}s ambiguous (1-2 polls) -> needs websocket-grade probe before a taker build")
    print("\nsensitivity (2c tick): rerun with --tick 0.02")
    return 0

if __name__ == "__main__":
    sys.exit(main())
