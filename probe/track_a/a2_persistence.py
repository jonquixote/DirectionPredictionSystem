#!/usr/bin/env python3
"""A2 persistence test — pre-registered core experiment.

Per-trade realized PnL (taker perspective, valued to resolution):
  BUY  outcome-token at p, size s: pnl = s*(won - p) - fee
  SELL outcome-token at p, size s: pnl = s*(p - won) - fee
  won = 1 if the traded outcome token resolved to 1, else 0.
  (A BUY later closed by a SELL telescopes to the roundtrip p2 - p1.)
  fee = s * 0.07 * p * (1-p), taker-only, current schedule across the whole
  window (registered); zero-fee sensitivity reported alongside.

Split: W2 = most recent ~12 days, adjusted so W2 holds >= 25% of volume.
Eligibility: >= 200 resolved W1 trades AND >= 50 W2 trades.
Cohorts: top decile / top quartile by W1 net PnL; random control of equal
size drawn from eligible wallets EXCLUDING the cohort.
Metrics (all on W2, untouched by selection): cohort net PnL + win rate with
bootstrap 95% CI (wallet-level resample, 10k draws) vs control; Spearman
rank corr of W1 vs W2 wallet PnL.

Usage: python3 a2_persistence.py --probe-db /data/probe_track_a.db \
    [--w2-days 12] [--seed 42]
"""
from __future__ import annotations

import argparse
import sqlite3

import numpy as np


def bootstrap_ci(vals: np.ndarray, n_boot=10_000, seed=0, agg=np.sum):
    rng = np.random.default_rng(seed)
    n = len(vals)
    stats = np.empty(n_boot)
    for i in range(n_boot):
        stats[i] = agg(vals[rng.integers(0, n, n)])
    return float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-db", required=True)
    ap.add_argument("--w2-days", type=int, default=12)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    db = sqlite3.connect(args.probe_db)
    db.row_factory = sqlite3.Row

    # split point: w2_days back from max ts; verify >= 25% volume in W2
    tmin, tmax = db.execute("SELECT MIN(ts), MAX(ts) FROM trades").fetchone()
    split = tmax - args.w2_days * 86400
    vol_w2, vol_all = db.execute(
        "SELECT SUM(CASE WHEN ts >= ? THEN size*price ELSE 0 END), SUM(size*price)"
        " FROM trades", (split,)).fetchone()
    while vol_w2 / vol_all < 0.25:
        split -= 86400  # widen W2 by a day until >= 25% of volume
        vol_w2 = db.execute(
            "SELECT SUM(size*price) FROM trades WHERE ts >= ?", (split,)).fetchone()[0]
    print(f"W1: {tmin}..{split} | W2: {split}..{tmax} "
          f"(W2 volume share {100*vol_w2/vol_all:.1f}%)")

    # per-wallet, per-period aggregates (SQL does the heavy lifting)
    q = """
    SELECT t.wallet,
      SUM(CASE WHEN t.ts <  :split THEN 1 ELSE 0 END) n1,
      SUM(CASE WHEN t.ts >= :split THEN 1 ELSE 0 END) n2,
      SUM(CASE WHEN t.ts <  :split THEN
        (CASE WHEN t.side='BUY'
              THEN t.size*((CASE WHEN (t.outcome='Up')=(m.outcome_up=1.0) THEN 1.0 ELSE 0.0 END) - t.price)
              ELSE t.size*(t.price - (CASE WHEN (t.outcome='Up')=(m.outcome_up=1.0) THEN 1.0 ELSE 0.0 END)) END)
        - t.size*0.07*t.price*(1.0-t.price) ELSE 0 END) pnl1,
      SUM(CASE WHEN t.ts >= :split THEN
        (CASE WHEN t.side='BUY'
              THEN t.size*((CASE WHEN (t.outcome='Up')=(m.outcome_up=1.0) THEN 1.0 ELSE 0.0 END) - t.price)
              ELSE t.size*(t.price - (CASE WHEN (t.outcome='Up')=(m.outcome_up=1.0) THEN 1.0 ELSE 0.0 END)) END)
        - t.size*0.07*t.price*(1.0-t.price) ELSE 0 END) pnl2,
      SUM(CASE WHEN t.ts >= :split THEN
        (CASE WHEN t.side='BUY'
              THEN t.size*((CASE WHEN (t.outcome='Up')=(m.outcome_up=1.0) THEN 1.0 ELSE 0.0 END) - t.price)
              ELSE t.size*(t.price - (CASE WHEN (t.outcome='Up')=(m.outcome_up=1.0) THEN 1.0 ELSE 0.0 END)) END)
        ELSE 0 END) pnl2_nofee,
      SUM(CASE WHEN t.ts >= :split AND
        ((t.side='BUY' AND (t.outcome='Up')=(m.outcome_up=1.0)) OR
         (t.side='SELL' AND (t.outcome='Up')!=(m.outcome_up=1.0)))
        THEN 1 ELSE 0 END) wins2
    FROM trades t JOIN markets m ON t.condition_id = m.condition_id
    WHERE m.outcome_up IS NOT NULL AND m.outcome_up IN (0.0, 1.0)
    GROUP BY t.wallet
    HAVING n1 >= 200 AND n2 >= 50
    """
    rows = db.execute(q, {"split": split}).fetchall()
    print(f"eligible wallets (>=200 W1, >=50 W2 resolved): {len(rows)}")
    if len(rows) < 40:
        print("TOO FEW ELIGIBLE WALLETS — report and stop")
        return

    w = np.array([r["wallet"] for r in rows])
    pnl1 = np.array([r["pnl1"] for r in rows])
    pnl2 = np.array([r["pnl2"] for r in rows])
    pnl2_nf = np.array([r["pnl2_nofee"] for r in rows])
    n2 = np.array([r["n2"] for r in rows])
    wins2 = np.array([r["wins2"] for r in rows])

    # Spearman W1 vs W2
    from scipy.stats import spearmanr
    rho, pval = spearmanr(pnl1, pnl2)
    print(f"\nSpearman(W1 pnl, W2 pnl) over {len(rows)} wallets: "
          f"rho={rho:.4f} p={pval:.2e}")

    rng = np.random.default_rng(args.seed)
    order = np.argsort(-pnl1)
    for label, frac in [("top decile", 0.10), ("top quartile", 0.25)]:
        k = max(1, int(len(rows) * frac))
        cohort = order[:k]
        pool = np.setdiff1d(np.arange(len(rows)), cohort)
        control = rng.choice(pool, size=k, replace=False)

        for name, idx in [(label, cohort), (f"control({label})", control)]:
            tot = pnl2[idx].sum()
            lo, hi = bootstrap_ci(pnl2[idx], seed=args.seed)
            wr = 100 * wins2[idx].sum() / n2[idx].sum()
            nf = pnl2_nf[idx].sum()
            print(f"  {name:22s} k={k:>4} W2pnl=${tot:>12,.0f} "
                  f"CI95=[{lo:>12,.0f},{hi:>12,.0f}] wr={wr:5.2f}% "
                  f"(no-fee ${nf:,.0f})")
        c_lo, _ = bootstrap_ci(pnl2[cohort], seed=args.seed)
        _, x_hi = bootstrap_ci(pnl2[control], seed=args.seed + 1)
        sep = "NON-OVERLAPPING (cohort > control)" if c_lo > x_hi else "overlapping"
        print(f"  -> CI separation: {sep}")

    print("\ntop 20 W1 wallets (descriptive, for A3 fingerprinting):")
    for i in order[:20]:
        print(f"  {w[i]}  W1=${pnl1[i]:>10,.0f}  W2=${pnl2[i]:>10,.0f}  n2={n2[i]}")


if __name__ == "__main__":
    main()
