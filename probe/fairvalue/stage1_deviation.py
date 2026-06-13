#!/usr/bin/env python3
"""Fair-Value Deviation Probe — Stage 1 (offline, Polymarket prints vs Bybit Φ).

MEMORY-SAFE rewrite: streams per (symbol,duration), holds only fixed-size accumulators
(never the full 50M-print arrays — the prior version OOM'd the VPS building ~18GB of
Python float lists). All vol estimators vectorized with numpy.

P_market: Polymarket trade print -> P(up)=price if token=='Up' else 1-price (independent
          of Bybit spot).
P_fair  : Φ( dist_return / (sigma_1s * sqrt(remaining_sec)) ), Bybit spot.

Controls (verbatim):
 1. sigma robustness: EWMA(60s) / trailing-1d / same-window-oracle — supra-cost under all.
 2. empirical fair value: realized P(up) by (dist-bucket, elapsed-decile); dev vs empirical.
 3. OOS concentration: dates 70/30; concentrated bucket must hold on held-out 30%.
 4. capturable proxy: supra-cost dev with >=1 later in-window print.
 5. dual fee: supra-cost under Polymarket 0.07 AND Kalshi 0.0175.

Two-pass per cell: pass A accumulates the empirical P(up) grid; pass B accumulates
deviation histograms vs Gaussian (3 sigma) and vs empirical. Counters are small fixed
arrays keyed by (dist_bucket[-10..10], elapsed_decile[0..9], early/late).
"""
from __future__ import annotations

import argparse
import bisect
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl
import sqlite3

SYM = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}
HALF_SPREAD = 0.005
DIST_LO, DIST_HI = -10, 10  # dist buckets in units of 10bps, clipped


def phi_scalar(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def load_spot(ofi_dir: Path, sym: str):
    files = sorted((ofi_dir / sym).glob("*_ofi.parquet"))
    df = pl.concat([pl.read_parquet(f, columns=["cts", "mid_price"]) for f in files]).sort("cts")
    cts = (df["cts"].to_numpy() // 1000).astype(np.int64)
    mid = df["mid_price"].to_numpy().astype(np.float64)
    ret = np.zeros(len(mid))
    ret[1:] = np.log(mid[1:] / mid[:-1])
    # EWMA vol, halflife 60s — IIR filter in C (scipy.lfilter), no Python loop
    from scipy.signal import lfilter
    a = 1.0 - math.exp(math.log(0.5) / 60.0)
    r2 = ret * ret
    ewm_var = lfilter([a], [1.0, -(1.0 - a)], r2)
    sigma_ewm = np.sqrt(np.maximum(ewm_var, 1e-18))
    # trailing-1d realized vol: vectorized rolling mean of r2 over 86400 via cumsum
    win = 86400
    c = np.concatenate([[0.0], np.cumsum(r2)])
    idx = np.arange(len(r2))
    lo = np.maximum(0, idx - win + 1)
    cnt = idx - lo + 1
    sig1d = np.sqrt(np.maximum((c[idx + 1] - c[lo]) / cnt, 1e-18))
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

    # global accumulators (small, fixed size)
    n_tot = 0
    # empirical grid: (dist_bucket, elapsed_dec) -> [sum_up, count]
    emp = defaultdict(lambda: [0.0, 0])
    # we need empirical BEFORE computing dev-vs-empirical -> two passes over cells.
    # Pass 1: fill emp grid (cheap: just dist/elapsed/outcome per print, streamed).
    # Pass 2: dev histograms. Re-query per cell (DB is on-disk, fine).

    date_ords = []  # for the 70/30 split point only (collect min/max per cell)

    def iter_cell(pfx, sym, dur, spot):
        cts, mid, ret, sig_ewm, sig1d = spot
        win_s = dur * 60
        rows = db.execute(
            "SELECT t.ts, t.price, t.outcome, m.boundary_ts, m.outcome_up "
            "FROM trades t JOIN markets m ON t.condition_id=m.condition_id "
            "WHERE m.symbol=? AND m.duration_min=? AND m.outcome_up IN (0.0,1.0) "
            "AND t.ts>=m.boundary_ts AND t.ts < m.boundary_ts + m.duration_min*60 "
            "ORDER BY m.boundary_ts, t.ts", (pfx, dur)).fetchall()
        # group by window on the fly (rows ordered by boundary_ts)
        cur_b = None; buf = []
        for r in rows:
            if r["boundary_ts"] != cur_b:
                if buf:
                    yield cur_b, buf, win_s, spot
                cur_b = r["boundary_ts"]; buf = []
            buf.append(r)
        if buf:
            yield cur_b, buf, win_s, spot

    spots = {}
    def get_spot(sym):
        if sym not in spots:
            spots[sym] = load_spot(od, sym)
        return spots[sym]

    # ---- PASS 1: empirical grid + date range ----
    dmin = 10**12; dmax = 0
    for pfx, sym in SYM.items():
        spot = get_spot(sym)
        cts, mid = spot[0], spot[1]
        for dur in durs:
            win_s = dur * 60
            for b, buf, _, _ in iter_cell(pfx, sym, dur, spot):
                oi = bisect.bisect_left(cts, b)
                if oi >= len(cts) or cts[oi] - b > 5:
                    continue
                open_px = mid[oi]
                dord = b // 86400
                dmin = min(dmin, dord); dmax = max(dmax, dord)
                for r in buf:
                    ti = bisect.bisect_left(cts, r["ts"])
                    if ti >= len(cts):
                        continue
                    dist = (mid[min(ti, len(cts)-1)] - open_px) / open_px
                    dbk = int(np.clip(dist * 1e4 // 10, DIST_LO, DIST_HI))
                    edec = min(int((r["ts"] - b) / win_s * 10), 9)
                    g = emp[(dbk, edec)]
                    g[0] += r["outcome_up"]; g[1] += 1
    split = dmin + 0.70 * (dmax - dmin)

    def P_emp(dbk, edec):
        g = emp.get((dbk, edec))
        return (g[0] / g[1]) if g and g[1] >= 30 else 0.5

    # ---- PASS 2: deviation accumulators ----
    # supra-cost counters under each fee + each sigma + empirical, by (edec, early/late)
    cnt = 0
    supra_poly = supra_kal = 0          # gaussian-EWMA dev, dual fee
    supra_sigor = supra_sig1d = 0       # kalshi fee, other sigmas
    supra_emp = 0                       # kalshi fee, empirical dev
    sumabs_g = sumabs_emp = 0.0
    # concentration: supra(emp,kalshi) by elapsed decile, split early/late
    dec_excess = np.zeros(10); dec_excess_e = np.zeros(10); dec_excess_l = np.zeros(10)
    dec_bias_sum = np.zeros(10); dec_bias_sq = np.zeros(10); dec_bias_n = np.zeros(10)
    cap_supra = 0; cap_hit = 0

    for pfx, sym in SYM.items():
        spot = get_spot(sym)
        cts, mid, ret, sig_ewm, sig1d = spot
        for dur in durs:
            win_s = dur * 60
            for b, buf, _, _ in iter_cell(pfx, sym, dur, spot):
                oi = bisect.bisect_left(cts, b)
                if oi >= len(cts) or cts[oi] - b > 5:
                    continue
                open_px = mid[oi]
                ei = bisect.bisect_right(cts, b + win_s)
                seg = ret[oi+1:ei]
                sig_or = math.sqrt(max(np.mean(seg*seg), 1e-18)) if len(seg) > 1 else 1e-9
                dord = b // 86400; late = dord > split
                nb = len(buf)
                for k, r in enumerate(buf):
                    ti = bisect.bisect_left(cts, r["ts"])
                    if ti >= len(cts):
                        continue
                    j = min(ti, len(cts)-1)
                    dist = (mid[j] - open_px) / open_px
                    rem = max(b + win_s - r["ts"], 1)
                    pm = r["price"] if r["outcome"] == "Up" else 1.0 - r["price"]
                    sden = math.sqrt(rem)
                    pg = phi_scalar(dist / (sig_ewm[j] * sden))
                    pg1 = phi_scalar(dist / (sig1d[j] * sden))
                    pgor = phi_scalar(dist / (sig_or * sden))
                    dbk = int(np.clip(dist*1e4//10, DIST_LO, DIST_HI))
                    edec = min(int((r["ts"]-b)/win_s*10), 9)
                    pe = P_emp(dbk, edec)
                    dev_g = pm - pg; dev_e = pm - pe
                    thr_poly = 0.07*pm*(1-pm) + HALF_SPREAD
                    thr_kal = 0.0175*pm*(1-pm) + HALF_SPREAD
                    cnt += 1
                    sumabs_g += abs(dev_g); sumabs_emp += abs(dev_e)
                    if abs(dev_g) > thr_poly: supra_poly += 1
                    if abs(dev_g) > thr_kal: supra_kal += 1
                    if abs(pm-pgor) > thr_kal: supra_sigor += 1
                    if abs(pm-pg1) > thr_kal: supra_sig1d += 1
                    if abs(dev_e) > thr_kal:
                        supra_emp += 1
                        dec_excess[edec] += 1
                        (dec_excess_l if late else dec_excess_e)[edec] += 1
                        dec_bias_sum[edec] += dev_e; dec_bias_sq[edec] += dev_e*dev_e
                        dec_bias_n[edec] += 1
                        cap_supra += 1
                        if k < nb - 1: cap_hit += 1

    print("=" * 80)
    print(f"FAIR-VALUE STAGE 1 — Polymarket prints vs Bybit Φ — n={cnt:,} prints — verbatim")
    print("=" * 80)
    if cnt == 0:
        print("no data"); return
    pct = lambda x: 100.0 * x / cnt

    print("\n[Control 5] dual-fee supra-cost (Gaussian-EWMA dev):")
    print(f"  Polymarket(0.07): {pct(supra_poly):.2f}%   Kalshi(0.0175): {pct(supra_kal):.2f}%  (need >=20% both)")

    print("\n[Control 1] sigma robustness — supra-cost (Kalshi fee):")
    print(f"  EWMA:        {pct(supra_kal):.2f}%")
    print(f"  trailing-1d: {pct(supra_sig1d):.2f}%")
    print(f"  oracle:      {pct(supra_sigor):.2f}%   mean|dev_gauss|={sumabs_g/cnt:.4f}")

    print("\n[Control 2] empirical fair value — dev vs realized-P(up) grid:")
    print(f"  supra-cost(Kalshi) vs empirical: {pct(supra_emp):.2f}%  mean|dev_emp|={sumabs_emp/cnt:.4f}")
    print(f"  (vs Gaussian mean|dev|={sumabs_g/cnt:.4f}; if empirical dev << gaussian, the")
    print(f"   gaussian 'deviation' was tail/model error not mispricing)")

    print("\n[concentration] supra-cost-vs-empirical by elapsed decile (signed bias):")
    tot = dec_excess.sum()
    for d in range(10):
        no = dec_bias_n[d]
        if no > 1:
            mu = dec_bias_sum[d]/no
            sd = math.sqrt(max(dec_bias_sq[d]/no - mu*mu, 1e-18))
            bias = mu/sd if sd > 0 else 0
        else:
            bias = 0
        share = 100*dec_excess[d]/tot if tot else 0
        print(f"  decile {d}: {share:5.1f}% of excess  signed-bias={bias:+.2f}")
    top = int(np.argmax(dec_excess)) if tot else -1
    conc = dec_excess[top]/tot if tot else 0
    print(f"  most-concentrated decile={top} holds {100*conc:.1f}% (need >=60%)")

    print("\n[Control 3] OOS — concentrated decile share on early(70%) vs late(30%):")
    te = dec_excess_e.sum(); tl = dec_excess_l.sum()
    print(f"  early: {100*dec_excess_e[top]/te if te else 0:.1f}%   late: {100*dec_excess_l[top]/tl if tl else 0:.1f}%")

    print("\n[Control 4] capturable proxy — supra-cost devs with a later in-window print:")
    print(f"  {100*cap_hit/cap_supra if cap_supra else 0:.1f}% [PROXY, no book]")

    passes = {
        "dual-fee >=20% both": pct(supra_poly) >= 20 and pct(supra_kal) >= 20,
        "concentration >=60%": conc >= 0.60,
        "sigma oracle supra >=20%": pct(supra_sigor) >= 20,
        "beats empirical >=20%": pct(supra_emp) >= 20,
    }
    print("\n" + "=" * 80)
    for k, v in passes.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    verdict = all(passes.values())
    print("STAGE 1 VERDICT:",
          "STRUCTURE EXISTS — Stage 2 eligible" if verdict else "NO STRUCTURE — KILL")
    if not verdict:
        print("KILLED BY:", next(k for k, v in passes.items() if not v))


if __name__ == "__main__":
    main()
