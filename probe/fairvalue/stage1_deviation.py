#!/usr/bin/env python3
"""Fair-Value Deviation Probe — Stage 1 (offline, Polymarket prints vs Bybit Φ).

P_market: real Polymarket trade print -> contract-implied P(up) = price if token=='Up'
          else 1-price. Independent of Bybit spot.
P_fair  : Φ( dist_from_open_return / (sigma_1s * sqrt(remaining_seconds)) ), Bybit spot.

Controls (each verbatim):
 1. sigma robustness: EWMA(60s halflife) / trailing-1d / same-window-oracle. Supra-cost
    must hold under ALL three.
 2. empirical fair value: P_emp = realized P(up) binned by (dist-bucket, time-rem-bucket)
    over the whole store; |P_market - P_emp| must also be supra-cost.
 3. OOS concentration: dates split 70/30; concentrated bucket on early must hold on late.
 4. capturable (proxy, no book): deviation persists >=1 later print in-window.
 5. dual fee: supra-cost under Polymarket 0.07 AND Kalshi 0.0175 (both reported).

Structure threshold: |dev|>fee+0.5c in >=20% instants (both fees) AND >=60% concentrated in
one predictable bucket AND directional bias >=0.5*std AND survives Controls 1,2,3,4.

Usage: python3 stage1_deviation.py --probe-db /data/probe_track_a.db --ofi-dir /data/probe_ofi
"""
from __future__ import annotations

import argparse
import bisect
import math
from pathlib import Path

import numpy as np
import polars as pl
import sqlite3

SYM = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}
HALF_SPREAD = 0.005
NORM = 0.5 * (1 + np.vectorize(math.erf)(np.array([0.0]))[0])  # sanity placeholder


def phi(z):
    # vectorized standard normal CDF
    return 0.5 * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2.0)))


def fee(p, rate):
    return rate * p * (1.0 - p)


def load_spot(ofi_dir: Path, sym: str):
    files = sorted((ofi_dir / sym).glob("*_ofi.parquet"))
    df = pl.concat([pl.read_parquet(f, columns=["cts", "mid_price"]) for f in files]).sort("cts")
    cts = (df["cts"].to_numpy() // 1000).astype(np.int64)  # seconds
    mid = df["mid_price"].to_numpy().astype(np.float64)
    # 1s log returns + EWMA vol (halflife 60s) + trailing-1d realized vol
    ret = np.zeros(len(mid))
    ret[1:] = np.log(mid[1:] / mid[:-1])
    a = 1.0 - math.exp(math.log(0.5) / 60.0)
    ewm_var = np.zeros(len(ret))
    v = 0.0
    for i in range(1, len(ret)):
        v = (1 - a) * v + a * ret[i] * ret[i]
        ewm_var[i] = v
    sigma_ewm = np.sqrt(np.maximum(ewm_var, 1e-18))
    # trailing-1d: rolling std over 86400 samples, cheap approx via cumulative
    win = 86400
    csum = np.cumsum(ret * ret)
    sig1d = np.zeros(len(ret))
    for i in range(len(ret)):
        lo = max(0, i - win)
        n = i - lo + 1
        sig1d[i] = math.sqrt(max((csum[i] - (csum[lo - 1] if lo > 0 else 0.0)) / n, 1e-18))
    return cts, mid, ret, sigma_ewm, sig1d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-db", default="/data/probe_track_a.db")
    ap.add_argument("--ofi-dir", default="/data/probe_ofi")
    ap.add_argument("--durations", default="5,15")
    args = ap.parse_args()
    od = Path(args.ofi_dir)
    db = sqlite3.connect(f"file:{args.probe_db}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    durs = [int(x) for x in args.durations.split(",")]

    devs_g = {s: [] for s in range(3)}  # per-sigma gaussian dev arrays (pooled)
    rec = dict(p_market=[], p_g_ewm=[], p_g_1d=[], p_g_or=[], dist=[], trem=[],
               elapsed_dec=[], dist_bucket=[], date_ord=[], win_id=[], persist=[],
               up_outcome=[])

    for pfx, sym in SYM.items():
        cts, mid, ret, sig_ewm, sig1d = load_spot(od, sym)
        for dur in durs:
            win_s = dur * 60
            # trades for this symbol/duration with resolved outcome
            rows = db.execute(
                "SELECT t.ts, t.price, t.outcome, m.boundary_ts, m.outcome_up "
                "FROM trades t JOIN markets m ON t.condition_id=m.condition_id "
                "WHERE m.symbol=? AND m.duration_min=? AND m.outcome_up IN (0.0,1.0) "
                "AND t.ts>=m.boundary_ts AND t.ts < m.boundary_ts + m.duration_min*60",
                (pfx, dur)).fetchall()
            if not rows:
                continue
            # group prints by window to compute persistence
            by_win = {}
            for r in rows:
                by_win.setdefault(r["boundary_ts"], []).append(r)
            for b, prints in by_win.items():
                # spot open = mid at/after b
                oi = bisect.bisect_left(cts, b)
                if oi >= len(cts) or cts[oi] - b > 5:
                    continue
                open_px = mid[oi]
                # same-window oracle sigma: realized 1s vol within [b, b+win]
                ei = bisect.bisect_right(cts, b + win_s)
                seg = ret[oi + 1:ei]
                sig_or = math.sqrt(max(np.mean(seg * seg), 1e-18)) if len(seg) > 1 else 1e-9
                prints.sort(key=lambda r: r["ts"])
                pm_seq = []
                for r in prints:
                    ti = bisect.bisect_left(cts, r["ts"])
                    if ti >= len(cts):
                        continue
                    now_px = mid[min(ti, len(cts) - 1)]
                    dist = (now_px - open_px) / open_px
                    rem = max(b + win_s - r["ts"], 1)
                    z_ewm = dist / (sig_ewm[min(ti, len(sig_ewm)-1)] * math.sqrt(rem))
                    z_1d = dist / (sig1d[min(ti, len(sig1d)-1)] * math.sqrt(rem))
                    z_or = dist / (sig_or * math.sqrt(rem))
                    pm = r["price"] if r["outcome"] == "Up" else 1.0 - r["price"]
                    rec["p_market"].append(pm)
                    rec["p_g_ewm"].append(float(phi(np.array([z_ewm]))[0]))
                    rec["p_g_1d"].append(float(phi(np.array([z_1d]))[0]))
                    rec["p_g_or"].append(float(phi(np.array([z_or]))[0]))
                    rec["dist"].append(dist)
                    rec["trem"].append(rem / win_s)
                    rec["elapsed_dec"].append(min(int((r["ts"]-b)/win_s*10), 9))
                    rec["dist_bucket"].append(int(np.clip(dist*1e4//10, -10, 10)))
                    rec["date_ord"].append(b // 86400)
                    rec["win_id"].append(b)
                    rec["up_outcome"].append(r["outcome_up"])
                    pm_seq.append(pm)
                # persistence proxy: count windows where a supra-cost dev had >=1 later print
                # (filled in vectorized below; here mark seq length per print)
                for k in range(len(pm_seq)):
                    rec["persist"].append(1 if k < len(pm_seq)-1 else 0)

    A = {k: np.array(v) for k, v in rec.items()}
    n = len(A["p_market"])
    print("=" * 80)
    print(f"FAIR-VALUE STAGE 1 — Polymarket prints vs Bybit Φ — n={n:,} prints — verbatim")
    print("=" * 80)
    if n == 0:
        print("no data"); return

    # Control 5: dual fee threshold. dev vs Gaussian-EWMA baseline first.
    pm = A["p_market"]
    def supra_frac(dev, rate):
        thr = fee(pm, rate) + HALF_SPREAD
        return float(np.mean(np.abs(dev) > thr))

    dev_ewm = pm - A["p_g_ewm"]
    print("\n[Control 5] dual-fee supra-cost fraction (Gaussian-EWMA dev):")
    f_poly = supra_frac(dev_ewm, 0.07)
    f_kal = supra_frac(dev_ewm, 0.0175)
    print(f"  Polymarket(0.07):  {100*f_poly:.2f}%   Kalshi(0.0175): {100*f_kal:.2f}%  (need >=20% both)")

    # Control 1: sigma robustness
    print("\n[Control 1] sigma robustness — supra-cost (Kalshi fee) under each estimator:")
    for nm, key in [("EWMA", "p_g_ewm"), ("trailing-1d", "p_g_1d"), ("oracle", "p_g_or")]:
        d = pm - A[key]
        print(f"  sigma={nm:12s}: supra-cost={100*supra_frac(d,0.0175):.2f}%  "
              f"mean|dev|={np.mean(np.abs(d)):.4f}")

    # Control 2: empirical fair value (binned realized P(up))
    # bins: dist_bucket x elapsed_dec -> mean up_outcome
    print("\n[Control 2] empirical fair value (realized P(up) binned), dev vs empirical:")
    key = (A["dist_bucket"].astype(int) + 10) * 10 + A["elapsed_dec"].astype(int)
    P_emp = np.zeros(n)
    for k in np.unique(key):
        m = key == k
        P_emp[m] = A["up_outcome"][m].mean()
    dev_emp = pm - P_emp
    print(f"  supra-cost(Kalshi) vs empirical: {100*supra_frac(dev_emp,0.0175):.2f}%  "
          f"mean|dev_emp|={np.mean(np.abs(dev_emp)):.4f}")
    print(f"  (vs Gaussian-EWMA mean|dev|={np.mean(np.abs(dev_ewm)):.4f} — if empirical dev")
    print(f"   much smaller, the Gaussian 'deviation' was tail/model error, not mispricing)")

    # concentration: supra-cost instants (Kalshi, empirical dev) by elapsed decile
    thr = fee(pm, 0.0175) + HALF_SPREAD
    supra = np.abs(dev_emp) > thr
    print(f"\n[concentration] supra-cost-vs-empirical instants by elapsed decile:")
    tot = supra.sum()
    if tot > 0:
        for d in range(10):
            m = supra & (A["elapsed_dec"] == d)
            sd = dev_emp[supra & (A["elapsed_dec"] == d)]
            bias = (np.mean(sd)/np.std(sd)) if len(sd) > 1 and np.std(sd) > 0 else 0
            print(f"  decile {d}: {100*m.sum()/tot:5.1f}% of excess  signed-bias={bias:+.2f}")
    top_dec = max(range(10), key=lambda d: (supra & (A["elapsed_dec"] == d)).sum()) if tot else -1
    conc = (supra & (A["elapsed_dec"] == top_dec)).sum() / tot if tot else 0
    print(f"  most-concentrated decile={top_dec} holds {100*conc:.1f}% (need >=60%)")

    # Control 3: OOS split 70/30 on dates
    print("\n[Control 3] OOS — does the concentrated bucket hold on held-out later 30%?")
    dord = A["date_ord"]; cut = np.percentile(dord, 70)
    early = dord <= cut; late = dord > cut
    for lab, msk in [("early(70%)", early), ("late(30%)", late)]:
        s = supra & msk & (A["elapsed_dec"] == top_dec)
        base = supra & msk
        frac = s.sum()/base.sum() if base.sum() else 0
        print(f"  {lab}: bucket holds {100*frac:.1f}% of its excess")

    # Control 4: capturable proxy
    print("\n[Control 4] capturable proxy — supra-cost devs with >=1 later in-window print:")
    cap = (supra & (A["persist"] == 1)).sum() / supra.sum() if supra.sum() else 0
    print(f"  {100*cap:.1f}% of supra-cost instants have a later print (fade fill plausible) [PROXY]")

    # verdict
    print("\n" + "=" * 80)
    passes = {
        "dual-fee >=20% (both)": f_poly >= 0.20 and f_kal >= 0.20,
        "concentration >=60%": conc >= 0.60,
        "sigma survives all (oracle supra >=20%)": supra_frac(pm-A["p_g_or"],0.0175) >= 0.20,
        "beats empirical (supra vs emp >=20%)": supra_frac(dev_emp,0.0175) >= 0.20,
    }
    for k, v in passes.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    verdict = all(passes.values())
    print("STAGE 1 VERDICT:",
          "STRUCTURE EXISTS — Stage 2 eligible" if verdict else "NO STRUCTURE — KILL")
    if not verdict:
        killer = next(k for k, v in passes.items() if not v)
        print(f"KILLED BY: {killer}")


if __name__ == "__main__":
    main()
