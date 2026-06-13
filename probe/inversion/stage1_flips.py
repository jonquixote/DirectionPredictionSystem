#!/usr/bin/env python3
"""Inversion Stage 1 — flip-rate histogram (registered, offline).

Window-open = first 1s mid at/after window start (flat=UP, >= semantics —
not needed for flip counting, recorded for parity). Flip = sign(mid-open)
changes between consecutive 1s samples, sign in {-1,0,+1}, a flip counts
only on +->- or -->+ (zero treated as no-sign, does not start/end a flip).

Buckets flips by window-elapsed decile. Reports mean flips/window in the
first 66% (deciles 1-7 ~ 0-70%, the registered "first 66%" proxy) and the
terminal-dampening ratio = flip-rate(last 2 deciles)/flip-rate(first 6).

STRUCTURE-EXISTS (registered): mean flips/window in first 66% >= 3.0 AND
dampening ratio <= 0.5, pooled AND in >=3/4 symbols.

Uses OFI 1s parquets (have mid_price + cts already): /data/probe_ofi/<sym>.
Windows = 15-min (900s), aligned to unix epoch like the Kalshi markets.

Usage: python3 stage1_flips.py --ofi-dir /data/probe_ofi --win 900
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import polars as pl

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]


def analyze(sym: str, ofi_dir: Path, win_s: int):
    files = sorted((ofi_dir / sym).glob("*_ofi.parquet"))
    if not files:
        return None
    df = pl.concat([pl.read_parquet(f, columns=["cts", "mid_price"]) for f in files]).sort("cts")
    cts = (df["cts"].to_numpy() // 1000).astype(np.int64)
    mid = df["mid_price"].to_numpy()
    win = win_s

    # group rows into windows by floor(sec/win)
    widx = cts // win
    # decile-bucketed flip accumulators
    flips_by_dec = np.zeros(10)
    n_windows = 0
    flips_first66 = []   # per-window flip count in deciles 0-6 (0-70%)
    flips_last20 = []    # per-window flip count in deciles 8-9 (80-100%)
    flips_first6 = []    # deciles 0-5 (0-60%) for dampening denominator

    # iterate contiguous window groups
    order = np.argsort(widx, kind="stable")
    widx_s = widx[order]; cts_s = cts[order]; mid_s = mid[order]
    bounds = np.searchsorted(widx_s, np.unique(widx_s))
    uniq = np.unique(widx_s)
    for gi, w in enumerate(uniq):
        lo = bounds[gi]
        hi = bounds[gi + 1] if gi + 1 < len(bounds) else len(widx_s)
        if hi - lo < 30:  # need enough samples
            continue
        wc = cts_s[lo:hi]; wm = mid_s[lo:hi]
        wstart = w * win
        open_px = wm[0]
        elapsed = (wc - wstart) / win  # 0..1
        sign = np.sign(wm - open_px)
        # flips: consecutive nonzero sign changes
        nz = sign != 0
        # build a per-sample "is this a flip vs last nonzero sign"
        last = 0
        per_dec = np.zeros(10)
        f66 = f20 = f6 = 0
        for k in range(len(sign)):
            s = sign[k]
            if s == 0:
                continue
            if last != 0 and s != last:
                d = min(int(elapsed[k] * 10), 9)
                per_dec[d] += 1
                if d <= 6:
                    f66 += 1
                if d <= 5:
                    f6 += 1
                if d >= 8:
                    f20 += 1
            last = s
        flips_by_dec += per_dec
        flips_first66.append(f66)
        flips_first6.append(f6)
        flips_last20.append(f20)
        n_windows += 1

    if n_windows == 0:
        return None
    mean_by_dec = flips_by_dec / n_windows
    mean_f66 = float(np.mean(flips_first66))
    # dampening: rate per decile in last2 vs first6
    rate_last2 = float(np.mean(flips_last20)) / 2
    rate_first6 = float(np.mean(flips_first6)) / 6
    damp = rate_last2 / rate_first6 if rate_first6 > 0 else float("inf")
    return dict(sym=sym, n=n_windows, mean_by_dec=mean_by_dec,
                mean_f66=mean_f66, damp=damp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ofi-dir", default="/data/probe_ofi")
    ap.add_argument("--win", type=int, default=900)
    args = ap.parse_args()
    od = Path(args.ofi_dir)

    print("=" * 78)
    print(f"INVERSION STAGE 1 — flip-rate histogram (win={args.win}s) — evidence verbatim")
    print("=" * 78)
    print("STRUCTURE threshold: mean flips/window first-66% >= 3.0 AND dampening <= 0.5")
    print()

    results = []
    agg_dec = np.zeros(10); agg_n = 0; agg_f66 = []; agg_damp_num = 0; agg_damp_den = 0
    for sym in SYMBOLS:
        r = analyze(sym, od, args.win)
        if r is None:
            print(f"{sym}: no data")
            continue
        results.append(r)
        hist = "  ".join(f"{v:.2f}" for v in r["mean_by_dec"])
        passed = r["mean_f66"] >= 3.0 and r["damp"] <= 0.5
        print(f"{sym}: n={r['n']} windows")
        print(f"  flips/window by decile [0-10%..90-100%]: {hist}")
        print(f"  mean flips first-66% = {r['mean_f66']:.3f} | dampening = {r['damp']:.3f} "
              f"-> {'STRUCTURE' if passed else 'no structure'}")

    n_pass = sum(1 for r in results if r["mean_f66"] >= 3.0 and r["damp"] <= 0.5)
    # pooled
    if results:
        tot_n = sum(r["n"] for r in results)
        pooled_dec = sum(r["mean_by_dec"] * r["n"] for r in results) / tot_n
        pooled_f66 = sum(r["mean_f66"] * r["n"] for r in results) / tot_n
        # pooled dampening from pooled deciles
        rl2 = (pooled_dec[8] + pooled_dec[9]) / 2
        rf6 = pooled_dec[:6].sum() / 6
        pooled_damp = rl2 / rf6 if rf6 > 0 else float("inf")
        print(f"\nPOOLED: n={tot_n}")
        print(f"  flips/window by decile: {'  '.join(f'{v:.2f}' for v in pooled_dec)}")
        print(f"  mean flips first-66% = {pooled_f66:.3f} | dampening = {pooled_damp:.3f}")
        pooled_pass = pooled_f66 >= 3.0 and pooled_damp <= 0.5
        verdict = pooled_pass and n_pass >= 3
        print(f"\nsymbols passing: {n_pass}/4 | pooled: "
              f"{'pass' if pooled_pass else 'fail'}")
        print("STAGE 1 VERDICT:",
              "STRUCTURE EXISTS — Stage 2 eligible (pending sign-off)" if verdict
              else "NO STRUCTURE — Stage-1 KILL")


if __name__ == "__main__":
    main()
