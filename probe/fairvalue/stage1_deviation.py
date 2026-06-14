#!/usr/bin/env python3
"""Fair-Value Deviation Probe — Stage 1 (offline, Polymarket prints vs Bybit Φ).

BOUNDED-MEMORY + VECTORIZED. Prior versions OOM'd a 15GB box twice:
  v1 held ~50M prints x12 fields in Python lists (~18GB);
  v2 cached all 4 symbols' 1s arrays (~6GB) AND fetchall'd 12M-row cells.
This version:
  - processes ONE symbol at a time, frees its spot arrays before the next;
  - reads trades via a chunked cursor (fetchmany), converts each chunk to
    numpy, vectorizes, accumulates into fixed-size counters, frees the chunk;
  - Φ via scipy.special.ndtr (C), spot lookup via np.searchsorted (C).
Peak RAM ~ one symbol's spot (~1.4GB) + one 2M-row chunk (~0.1GB).

P_market: Polymarket print -> P(up)=price if token=='Up' else 1-price (indep of spot).
P_fair  : Φ( dist_return / (sigma_1s * sqrt(remaining_sec)) ), Bybit spot.

Controls: 1 sigma robustness (EWMA/1d/oracle) 2 empirical fair value
3 OOS 70/30 concentration 4 capturable proxy 5 dual fee (PM 0.07 / Kalshi 0.0175).
Two streamed passes per symbol: pass A -> empirical P(up) grid; pass B -> deviations.
"""
from __future__ import annotations

import argparse
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl
import sqlite3
from scipy.signal import lfilter
from scipy.special import ndtr

SYM = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}
HALF_SPREAD = 0.005
DIST_LO, DIST_HI = -10, 10
CHUNK = 2_000_000
DURS = [5, 15]


def load_spot(ofi_dir: Path, sym: str):
    files = sorted((ofi_dir / sym).glob("*_ofi.parquet"))
    df = pl.concat([pl.read_parquet(f, columns=["cts", "mid_price"]) for f in files]).sort("cts")
    cts = (df["cts"].to_numpy() // 1000).astype(np.int64)
    mid = df["mid_price"].to_numpy().astype(np.float64)
    ret = np.zeros(len(mid)); ret[1:] = np.log(mid[1:] / mid[:-1])
    a = 1.0 - math.exp(math.log(0.5) / 60.0)
    r2 = ret * ret
    sig_ewm = np.sqrt(np.maximum(lfilter([a], [1.0, -(1.0 - a)], r2), 1e-18))
    win = 86400
    c = np.concatenate([[0.0], np.cumsum(r2)])
    idx = np.arange(len(r2)); lo = np.maximum(0, idx - win + 1); cnt = idx - lo + 1
    sig1d = np.sqrt(np.maximum((c[idx + 1] - c[lo]) / cnt, 1e-18))
    # prefix sums of r2 for fast same-window (oracle) realized vol over [open, end]
    return cts, mid, ret, sig_ewm, sig1d, c


def cell_chunks(db, pfx, dur):
    cur = db.execute(
        "SELECT t.ts, t.price, t.outcome, m.boundary_ts, m.outcome_up "
        "FROM trades t JOIN markets m ON t.condition_id=m.condition_id "
        "WHERE m.symbol=? AND m.duration_min=? AND m.outcome_up IN (0.0,1.0) "
        "AND t.ts>=m.boundary_ts AND t.ts < m.boundary_ts + m.duration_min*60",
        (pfx, dur))
    while True:
        rows = cur.fetchmany(CHUNK)
        if not rows:
            break
        ts = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
        price = np.fromiter((r[1] for r in rows), dtype=np.float64, count=len(rows))
        is_up = np.fromiter((1 if r[2] == "Up" else 0 for r in rows), dtype=np.int8, count=len(rows))
        bts = np.fromiter((r[3] for r in rows), dtype=np.int64, count=len(rows))
        oup = np.fromiter((r[4] for r in rows), dtype=np.float64, count=len(rows))
        yield ts, price, is_up, bts, oup


def vec_fields(ts, price, is_up, bts, oup, dur, spot):
    cts, mid, ret, sig_ewm, sig1d, csum = spot
    win_s = dur * 60
    # open index = first cts >= bts ; require <=5s gap
    oi = np.searchsorted(cts, bts, side="left")
    ok = (oi < len(cts))
    oi_c = np.clip(oi, 0, len(cts) - 1)
    ok &= (cts[oi_c] - bts <= 5)
    open_px = mid[oi_c]
    # now index = first cts >= ts
    ti = np.searchsorted(cts, ts, side="left"); ti_c = np.clip(ti, 0, len(cts) - 1)
    now_px = mid[ti_c]
    dist = np.where(open_px > 0, (now_px - open_px) / open_px, 0.0)
    rem = np.maximum(bts + win_s - ts, 1).astype(np.float64)
    sden = np.sqrt(rem)
    pm = np.where(is_up == 1, price, 1.0 - price)
    # oracle sigma: realized vol over (open, end] via prefix sums of r2
    ei = np.searchsorted(cts, bts + win_s, side="right"); ei_c = np.clip(ei, 0, len(csum) - 1)
    seg_n = np.maximum(ei_c - (oi_c + 1), 1)
    seg_ss = csum[ei_c] - csum[np.minimum(oi_c + 1, len(csum) - 1)]
    sig_or = np.sqrt(np.maximum(seg_ss / seg_n, 1e-18))
    pg = ndtr(dist / (sig_ewm[ti_c] * sden))
    pg1 = ndtr(dist / (sig1d[ti_c] * sden))
    pgor = ndtr(dist / (sig_or * sden))
    edec = np.clip(((ts - bts) / win_s * 10).astype(np.int64), 0, 9)
    dbk = np.clip((dist * 1e4 // 10).astype(np.int64), DIST_LO, DIST_HI)
    dord = bts // 86400
    return ok, pm, pg, pg1, pgor, dist, edec, dbk, dord, oup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-db", default="/data/probe_track_a.db")
    ap.add_argument("--ofi-dir", default="/data/probe_ofi")
    args = ap.parse_args()
    od = Path(args.ofi_dir)
    db = sqlite3.connect(f"file:{args.probe_db}?mode=ro", uri=True)

    # ---- PASS 1: empirical P(up) grid (dist_bucket, elapsed_dec) + date range ----
    emp = defaultdict(lambda: np.zeros(2))  # key -> [sum_up, count]
    dmin = np.int64(10**12); dmax = np.int64(0)
    for pfx, sym in SYM.items():
        spot = load_spot(od, sym)
        for dur in DURS:
            for chunk in cell_chunks(db, pfx, dur):
                ok, pm, pg, pg1, pgor, dist, edec, dbk, dord, oup = vec_fields(*chunk, dur, spot)
                if ok.sum() == 0:
                    continue
                dmin = min(dmin, dord[ok].min()); dmax = max(dmax, dord[ok].max())
                key = (dbk[ok] + 10) * 10 + edec[ok]
                for k in np.unique(key):
                    m = key == k
                    g = emp[int(k)]; g[0] += oup[ok][m].sum(); g[1] += m.sum()
        del spot
    split = dmin + 0.70 * (dmax - dmin)
    # materialize empirical lookup as a dense array [21*10]
    P_emp = np.full(21 * 10, 0.5)
    for k, g in emp.items():
        if g[1] >= 30:
            P_emp[k] = g[0] / g[1]

    # ---- PASS 2: deviation accumulators (fixed size) ----
    cnt = 0
    supra_poly = supra_kal = supra_sig1d = supra_sigor = supra_emp = 0
    sumabs_g = sumabs_emp = 0.0
    dec_excess = np.zeros(10); dec_e = np.zeros(10); dec_l = np.zeros(10)
    bias_sum = np.zeros(10); bias_sq = np.zeros(10); bias_n = np.zeros(10)
    cap_supra = 0; cap_hit = 0

    for pfx, sym in SYM.items():
        spot = load_spot(od, sym)
        for dur in DURS:
            for chunk in cell_chunks(db, pfx, dur):
                ok, pm, pg, pg1, pgor, dist, edec, dbk, dord, oup = vec_fields(*chunk, dur, spot)
                if ok.sum() == 0:
                    continue
                pm = pm[ok]; pg = pg[ok]; pg1 = pg1[ok]; pgor = pgor[ok]
                edec = edec[ok]; dbk = dbk[ok]; dord = dord[ok]
                key = (dbk + 10) * 10 + edec
                pe = P_emp[key]
                dev_g = pm - pg; dev_e = pm - pe
                thr_poly = 0.07 * pm * (1 - pm) + HALF_SPREAD
                thr_kal = 0.0175 * pm * (1 - pm) + HALF_SPREAD
                cnt += len(pm)
                sumabs_g += np.abs(dev_g).sum(); sumabs_emp += np.abs(dev_e).sum()
                supra_poly += int((np.abs(dev_g) > thr_poly).sum())
                supra_kal += int((np.abs(dev_g) > thr_kal).sum())
                supra_sig1d += int((np.abs(pm - pg1) > thr_kal).sum())
                supra_sigor += int((np.abs(pm - pgor) > thr_kal).sum())
                se = np.abs(dev_e) > thr_kal
                supra_emp += int(se.sum())
                late = dord > split
                for d in range(10):
                    md = se & (edec == d)
                    c = int(md.sum())
                    if c:
                        dec_excess[d] += c
                        dec_e[d] += int((md & ~late).sum()); dec_l[d] += int((md & late).sum())
                        sd = dev_e[md]
                        bias_sum[d] += sd.sum(); bias_sq[d] += (sd * sd).sum(); bias_n[d] += c
                cap_supra += int(se.sum())  # capturable proxy approximated below
        del spot

    print("=" * 80)
    print(f"FAIR-VALUE STAGE 1 — Polymarket prints vs Bybit Φ — n={cnt:,} prints — verbatim")
    print("=" * 80)
    if cnt == 0:
        print("no data"); return
    pct = lambda x: 100.0 * x / cnt

    print("\n[Control 5] dual-fee supra-cost (Gaussian-EWMA dev):")
    print(f"  Polymarket(0.07): {pct(supra_poly):.2f}%   Kalshi(0.0175): {pct(supra_kal):.2f}%  (need >=20% both)")
    print("\n[Control 1] sigma robustness — supra-cost (Kalshi fee):")
    print(f"  EWMA: {pct(supra_kal):.2f}%   trailing-1d: {pct(supra_sig1d):.2f}%   oracle: {pct(supra_sigor):.2f}%")
    print(f"  mean|dev_gauss|={sumabs_g/cnt:.4f}")
    print("\n[Control 2] empirical fair value:")
    print(f"  supra-cost(Kalshi) vs empirical: {pct(supra_emp):.2f}%  mean|dev_emp|={sumabs_emp/cnt:.4f}")
    print(f"  (vs gaussian mean|dev|={sumabs_g/cnt:.4f}; empirical<<gaussian => gaussian dev was model error)")
    print("\n[concentration] supra-cost-vs-empirical by elapsed decile (signed bias):")
    tot = dec_excess.sum()
    for d in range(10):
        no = bias_n[d]
        if no > 1:
            mu = bias_sum[d] / no; sd = math.sqrt(max(bias_sq[d] / no - mu * mu, 1e-18))
            bias = mu / sd if sd > 0 else 0
        else:
            bias = 0
        print(f"  decile {d}: {100*dec_excess[d]/tot if tot else 0:5.1f}% of excess  signed-bias={bias:+.2f}")
    top = int(np.argmax(dec_excess)) if tot else -1
    conc = dec_excess[top] / tot if tot else 0
    print(f"  most-concentrated decile={top} holds {100*conc:.1f}% (need >=60%)")
    print("\n[Control 3] OOS — concentrated decile share early(70%) vs late(30%):")
    te = dec_e.sum(); tl = dec_l.sum()
    print(f"  early: {100*dec_e[top]/te if te else 0:.1f}%   late: {100*dec_l[top]/tl if tl else 0:.1f}%")
    print("\n[Control 4] capturable proxy: supra-cost instants are real fills (a trade printed")
    print(f"  at that price) by construction; book-depth fade-side unobservable -> flagged PROXY")

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
