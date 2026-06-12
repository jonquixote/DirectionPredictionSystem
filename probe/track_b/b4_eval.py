#!/usr/bin/env python3
"""B4 single-touch evaluation — design FROZEN before any test-span read.

Registered design (committed before execution; see PREREGISTRATION.md §B):

  Decision points: dataset rows in the test span (window_start >= 2026-05-24),
  i.e. every 10s sample. Market price at the same instant from probe-DB trade
  prints: last print at/before t in that window's market, p_up = price if the
  print's outcome token is 'Up' else 1 - price.

  STALENESS RULE (owner-binding, registered before the run): the PRIMARY gate
  population uses only fires whose reference print is <= 15s old at the
  decision instant — older prints are prices nobody offers anymore; scoring
  against them manufactures backtest-only latency edge. Diagnostics report
  the print-age distribution of ALL candidate fires and EV sliced by age
  bucket (<=15s / 15-60s / 60-120s); edge living only in stale buckets is
  the latency mirage, made explicit. If the <=15s slice cannot reach
  n >= 2000, that is a legitimate insufficient-n result — the staleness
  window is NOT widened after the fact.

  Trade rule (theta registered): fire when |P_model - p_up| > theta(p_up),
    theta(p) = 0.07 * p * (1-p) + 0.01      (fee curve + half-spread proxy)
  Direction: UP if P_model > p_up else DOWN.
  Entry price: p_up + 0.01 adverse (buy UP at p_up+0.01; buy DOWN at
  (1-p_up)+0.01). Fee charged on entry price per the live formula.

  Clustering: at most ONE trade per market window per duration — the first
  fire in the window. Fires within one window share an outcome and are not
  independent observations; n in the gate means clustered trades.

  Outcome: the market's own outcomePrices (Gate-3 amendment) — the thing
  that pays. Windows without resolved outcomePrices are skipped.

  Reported: EV per $1 share net of fees (bootstrap 95% CI, 10k draws),
  directional accuracy on fired trades (Wilson 95% LB), n, per-duration and
  pooled; secondary slice: window-open-only fires (time_remaining >= 0.97).

GATE B (unchanged): EV net > 0 with bootstrap LB > 0 AND accuracy Wilson
LB >= 52% AND n >= 2000, then 48h shadow consistency.

Usage: python3 b4_eval.py --ofi-dir /data/probe_ofi --probe-db /data/probe_track_a.db
"""
from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl
import lightgbm as lgb

TEST_START_MS = int(datetime(2026, 5, 24, tzinfo=timezone.utc).timestamp() * 1000)
SYMBOLS = {"BTCUSDT": "btc", "ETHUSDT": "eth", "SOLUSDT": "sol", "XRPUSDT": "xrp"}
PRINT_TOL_MS = 120_000      # diagnostic ceiling
FRESH_MS = 15_000           # PRIMARY gate slice: print age <= 15s (binding)
HALF_SPREAD = 0.01


def theta(p: float) -> float:
    return 0.07 * p * (1 - p) + HALF_SPREAD


def wilson_lb(w, n, z=1.96):
    if n == 0:
        return 0.0
    ph = w / n
    d = 1 + z * z / n
    c = ph + z * z / (2 * n)
    s = z * ((ph * (1 - ph) / n + z * z / (4 * n * n)) ** 0.5)
    return (c - s) / d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ofi-dir", default="/data/probe_ofi")
    ap.add_argument("--probe-db", default="/data/probe_track_a.db")
    args = ap.parse_args()

    pdb = sqlite3.connect(f"file:{args.probe_db}?mode=ro", uri=True)
    pdb.row_factory = sqlite3.Row

    all_trades = []
    rng = np.random.default_rng(42)

    for dur in (5, 15):
        booster = lgb.Booster(model_file=str(Path(args.ofi_dir) / f"model_{dur}m/model.lgb"))
        winner = json.loads((Path(args.ofi_dir) / f"model_{dur}m/winner.json").read_text())
        feat_cols = winner["feature_names"]

        for sym, pfx in SYMBOLS.items():
            ds = pl.read_parquet(Path(args.ofi_dir) / f"dataset_{sym}_{dur}m.parquet")
            ds = ds.filter(pl.col("window_start_ms") >= TEST_START_MS)
            if len(ds) == 0:
                continue
            X = ds.select(feat_cols).to_numpy().astype(np.float64)
            proba = booster.predict(X)
            cts = ds["cts"].to_numpy()
            wstart = ds["window_start_ms"].to_numpy()
            t_rem = ds["time_remaining"].to_numpy()

            # market outcomes + trade prints for this symbol/duration
            mk = {r["boundary_ts"] * 1000: r["outcome_up"] for r in pdb.execute(
                "SELECT boundary_ts, outcome_up FROM markets"
                " WHERE symbol=? AND duration_min=? AND outcome_up IN (0.0,1.0)"
                " AND boundary_ts*1000 >= ?", (pfx, dur, TEST_START_MS))}
            cid_by_ws = {r["boundary_ts"] * 1000: r["condition_id"] for r in pdb.execute(
                "SELECT boundary_ts, condition_id FROM markets"
                " WHERE symbol=? AND duration_min=? AND boundary_ts*1000 >= ?",
                (pfx, dur, TEST_START_MS))}
            prints: dict = {}
            for ws, cid in cid_by_ws.items():
                rows = pdb.execute(
                    "SELECT ts, price, outcome FROM trades WHERE condition_id=?"
                    " ORDER BY ts", (cid,)).fetchall()
                if rows:
                    ts_arr = [r["ts"] * 1000 for r in rows]
                    pu_arr = [r["price"] if r["outcome"] == "Up" else 1 - r["price"]
                              for r in rows]
                    prints[ws] = (ts_arr, pu_arr)

            # collect ALL candidate fires (no clustering yet) with print age
            for i in np.argsort(cts):
                ws = int(wstart[i])
                if ws not in mk or ws not in prints:
                    continue
                ts_arr, pu_arr = prints[ws]
                j = bisect.bisect_right(ts_arr, int(cts[i])) - 1
                if j < 0:
                    continue
                age_ms = int(cts[i]) - ts_arr[j]
                if age_ms > PRINT_TOL_MS:
                    continue
                p_up = min(max(pu_arr[j], 0.01), 0.99)
                P = proba[i]
                if abs(P - p_up) <= theta(p_up):
                    continue
                outcome_up = mk[ws]
                if P > p_up:  # buy UP
                    entry = min(p_up + HALF_SPREAD, 0.99)
                    win = outcome_up == 1.0
                else:         # buy DOWN
                    entry = min((1 - p_up) + HALF_SPREAD, 0.99)
                    win = outcome_up == 0.0
                fee = 0.07 * entry * (1 - entry)
                pnl = (1 - entry if win else -entry) - fee
                all_trades.append(dict(dur=dur, sym=sym, ws=ws, t=int(cts[i]),
                                       P=float(P), p_up=float(p_up), win=bool(win),
                                       pnl=float(pnl), t_rem=float(t_rem[i]),
                                       age_ms=age_ms))

    print("=" * 72)
    print("B4 SINGLE-TOUCH EVALUATION — evidence verbatim")
    print("=" * 72)

    def cluster(trades):
        """One trade per (dur, sym, window): first fire chronologically."""
        seen = set()
        out = []
        for t in sorted(trades, key=lambda t: t["t"]):
            k = (t["dur"], t["sym"], t["ws"])
            if k in seen:
                continue
            seen.add(k)
            out.append(t)
        return out

    def report(label, trades):
        n = len(trades)
        if n == 0:
            print(f"{label}: n=0")
            return
        pnl = np.array([t["pnl"] for t in trades])
        wins = sum(t["win"] for t in trades)
        boots = np.array([pnl[rng.integers(0, n, n)].mean() for _ in range(10_000)])
        lo, hi = np.percentile(boots, [2.5, 97.5])
        acc = wins / n
        wl = wilson_lb(wins, n)
        print(f"{label}: n={n}  EV/share=${pnl.mean():+.4f} "
              f"CI95=[{lo:+.4f},{hi:+.4f}]  acc={100*acc:.2f}% "
              f"wilsonLB={100*wl:.2f}%  total=${pnl.sum():+,.0f}/share-unit")

    # print-age distribution of ALL candidate fires (pre-clustering)
    ages = np.array([t["age_ms"] for t in all_trades])
    print(f"\ncandidate fires (pre-clustering): {len(all_trades)}")
    if len(ages):
        print(f"print-age distribution: p50={np.percentile(ages,50)/1000:.1f}s "
              f"p90={np.percentile(ages,90)/1000:.1f}s "
              f"<=15s: {100*(ages<=15000).mean():.1f}% "
              f"15-60s: {100*((ages>15000)&(ages<=60000)).mean():.1f}% "
              f"60-120s: {100*(ages>60000).mean():.1f}%")

    # EV by age bucket (clustered within bucket) — latency-mirage diagnostic
    print("\nEV by print-age bucket (clustered per bucket):")
    for lab, lo, hi in [("<=15s", 0, 15_000), ("15-60s", 15_001, 60_000),
                        ("60-120s", 60_001, 120_000)]:
        report(f"  {lab}", cluster([t for t in all_trades
                                    if lo <= t["age_ms"] <= hi]))

    # PRIMARY GATE POPULATION: fresh fires only, clustered
    fresh = cluster([t for t in all_trades if t["age_ms"] <= FRESH_MS])
    print("\nPRIMARY (fresh <=15s, clustered):")
    for dur in (5, 15):
        report(f"  {dur}m pooled", [t for t in fresh if t["dur"] == dur])
        for sym in SYMBOLS:
            report(f"    {dur}m {sym}", [t for t in fresh
                                         if t["dur"] == dur and t["sym"] == sym])
    report("  ALL fresh pooled", fresh)
    report("  window-open slice (t_rem>=0.97)",
           [t for t in fresh if t["t_rem"] >= 0.97])

    n = len(fresh)
    if n:
        pnl = np.array([t["pnl"] for t in fresh])
        wins = sum(t["win"] for t in fresh)
        boots = np.array([pnl[rng.integers(0, n, n)].mean() for _ in range(10_000)])
        ev_lb = float(np.percentile(boots, 2.5))
        acc_lb = wilson_lb(wins, n)
        g = (pnl.mean() > 0 and ev_lb > 0 and acc_lb >= 0.52 and n >= 2000)
        print(f"\nGATE B (offline, PRIMARY fresh slice): EV>0 & EV_LB>0: "
              f"{pnl.mean() > 0 and ev_lb > 0} | accLB>=52%: {acc_lb >= 0.52} "
              f"({100*acc_lb:.2f}%) | n>=2000: {n >= 2000} ({n})")
        print("OFFLINE VERDICT:", "PASS — proceed to 48h shadow" if g else "FAIL")
    else:
        print("\nGATE B: zero fresh fires — FAIL (insufficient n)")
    out = Path(args.ofi_dir) / "b4_trades.json"
    out.write_text(json.dumps(all_trades))
    print(f"trades dumped: {out}")


if __name__ == "__main__":
    main()
