#!/usr/bin/env python3
"""B4 single-touch evaluation — design FROZEN before any test-span read.

Registered design (committed before execution; see PREREGISTRATION.md §B):

  Decision points: dataset rows in the test span (window_start >= 2026-05-24),
  i.e. every 10s sample. Market price at the same instant from probe-DB trade
  prints: last print at/before t in that window's market, p_up = price if the
  print's outcome token is 'Up' else 1 - price; require a print within 120s,
  else no decision.

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
PRINT_TOL_MS = 120_000
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

            fired_windows = set()
            for i in np.argsort(cts):  # chronological -> first fire per window
                ws = int(wstart[i])
                if ws in fired_windows or ws not in mk or ws not in prints:
                    continue
                ts_arr, pu_arr = prints[ws]
                j = bisect.bisect_right(ts_arr, int(cts[i])) - 1
                if j < 0 or cts[i] - ts_arr[j] > PRINT_TOL_MS:
                    continue
                p_up = min(max(pu_arr[j], 0.01), 0.99)
                P = proba[i]
                if abs(P - p_up) <= theta(p_up):
                    continue
                fired_windows.add(ws)
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
                                       pnl=float(pnl), t_rem=float(t_rem[i])))

    print("=" * 72)
    print("B4 SINGLE-TOUCH EVALUATION — evidence verbatim")
    print("=" * 72)

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

    for dur in (5, 15):
        report(f"  {dur}m pooled", [t for t in all_trades if t["dur"] == dur])
        for sym in SYMBOLS:
            report(f"    {dur}m {sym}", [t for t in all_trades
                                         if t["dur"] == dur and t["sym"] == sym])
    report("  ALL pooled", all_trades)
    report("  window-open slice (t_rem>=0.97)",
           [t for t in all_trades if t["t_rem"] >= 0.97])

    n = len(all_trades)
    if n:
        pnl = np.array([t["pnl"] for t in all_trades])
        wins = sum(t["win"] for t in all_trades)
        boots = np.array([pnl[rng.integers(0, n, n)].mean() for _ in range(10_000)])
        ev_lb = float(np.percentile(boots, 2.5))
        acc_lb = wilson_lb(wins, n)
        g = (pnl.mean() > 0 and ev_lb > 0 and acc_lb >= 0.52 and n >= 2000)
        print(f"\nGATE B (offline component): EV>0 & EV_LB>0: "
              f"{pnl.mean() > 0 and ev_lb > 0} | accLB>=52%: {acc_lb >= 0.52} "
              f"({100*acc_lb:.2f}%) | n>=2000: {n >= 2000} ({n})")
        print("OFFLINE VERDICT:", "PASS — proceed to 48h shadow" if g else "FAIL")
    out = Path(args.ofi_dir) / "b4_trades.json"
    out.write_text(json.dumps(all_trades))
    print(f"trades dumped: {out}")


if __name__ == "__main__":
    main()
