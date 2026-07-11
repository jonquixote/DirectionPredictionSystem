#!/usr/bin/env python3
"""Track 4 Step 1 — MI/correlation screen: trailing features vs future |move|.

Hypothesis (track4-signal-discovery.md, first choice): vol/magnitude is
forecastable while sign is not. Target here is the PROXY label (registered):
  |log-return| of Coinbase spot over the next 15m and 60m, in bps
  (continuous + above-per-coin-median indicator).

Features: trailing realized vol / |return| / range at 1/5/15m, hour-of-day,
plus book spread, |mid-0.5|, volume-delta where the summary columns have coverage.

Controls & inference (program lessons):
  - date-block CV: per-UTC-day Spearman; report mean, sd, and sign-consistency.
  - permutation baseline: target shuffled WITHIN day -> excess MI = MI - MI_perm
    (quantile-bin MI estimators are biased up; the baseline nets that out).
  - sampling stride 60s (10s data is too autocorrelated for a screen).

Registered kill (from the spec, frozen before this run): a feature "passes" only if
  excess MI >= 0.005 bits AND mean per-day |spearman| >= 0.03 with the same sign
  on >= 80% of days. If NO feature passes for any target: hypothesis dead at Step 1.

Usage: mi_screen.py --db /data/kalshi_fade.db [--stride 60]
"""
from __future__ import annotations
import argparse, math, sqlite3, sys
from collections import defaultdict
from datetime import datetime, timezone
import numpy as np

HORIZONS = {"15m": 900, "60m": 3600}
TRAILS = {"1m": 60, "5m": 300, "15m": 900}
BINS = 8
SEED = 7


def spearman(x, y):
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    rx -= rx.mean(); ry -= ry.mean()
    d = math.sqrt((rx * rx).sum() * (ry * ry).sum())
    return float((rx * ry).sum() / d) if d > 0 else 0.0


def mi_bits(x, y, bins=BINS):
    """MI via per-marginal quantile bins, in bits."""
    qx = np.quantile(x, np.linspace(0, 1, bins + 1)[1:-1])
    qy = np.quantile(y, np.linspace(0, 1, bins + 1)[1:-1])
    ix = np.digitize(x, qx)
    iy = np.digitize(y, qy)
    n = len(x)
    joint = np.zeros((bins, bins))
    np.add.at(joint, (ix, iy), 1.0)
    joint /= n
    px = joint.sum(1, keepdims=True)
    py = joint.sum(0, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = joint * np.log2(joint / (px @ py))
    return float(np.nansum(t))


def load_series(db_path):
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cols = {}
    for sym, in db.execute("SELECT DISTINCT symbol FROM obs"):
        rows = db.execute(
            "SELECT ts, spot, yes_bid, yes_ask, volume FROM obs "
            "WHERE symbol=? AND spot IS NOT NULL ORDER BY ts", (sym,)).fetchall()
        if len(rows) < 5000:
            continue
        a = np.array(rows, dtype=float)  # None -> nan
        cols[sym] = a
    db.close()
    return cols


def build_samples(a, stride):
    """a: [ts, spot, yes_bid, yes_ask, volume] sorted. -> dict of feature arrays."""
    ts = a[:, 0]; sp = a[:, 1]
    logp = np.log(sp)
    idx_of = {int(t): i for i, t in enumerate(ts)}

    def at(t):  # nearest index at-or-before t within 15s
        i = idx_of.get(int(t))
        if i is not None:
            return i
        j = np.searchsorted(ts, t, side="right") - 1
        return j if j >= 0 and t - ts[j] <= 15 else None

    t0, t1 = int(ts[0]) + max(TRAILS.values()), int(ts[-1]) - max(HORIZONS.values())
    F = defaultdict(list)
    for t in range(t0 - t0 % stride, t1, stride):
        i = at(t)
        if i is None:
            continue
        fut = {}
        for hn, hs in HORIZONS.items():
            j = at(t + hs)
            if j is None:
                fut = None
                break
            fut[hn] = abs(logp[j] - logp[i]) * 1e4
        if fut is None:
            continue
        row = {}
        ok = True
        for tn, tsec in TRAILS.items():
            k = at(t - tsec)
            if k is None or i - k < 3:
                ok = False
                break
            r = np.diff(logp[k:i + 1]) * 1e4
            seg = sp[k:i + 1]
            row[f"rv_{tn}"] = float(r.std())
            row[f"absret_{tn}"] = abs(float(logp[i] - logp[k])) * 1e4
            if tn == "15m":
                row["range_15m"] = float((seg.max() - seg.min()) / seg[-1]) * 1e4
        if not ok:
            continue
        row["hour_utc"] = datetime.fromtimestamp(t, timezone.utc).hour
        yb, ya, vol = a[i, 2], a[i, 3], a[i, 4]
        row["spread"] = ya - yb if np.isfinite(yb) and np.isfinite(ya) else np.nan
        row["middist"] = abs((yb + ya) / 2 - 0.5) if np.isfinite(yb) and np.isfinite(ya) else np.nan
        k15 = at(t - 900)
        row["voldelta_15m"] = (a[i, 4] - a[k15, 4]) if (k15 is not None and
            np.isfinite(vol) and np.isfinite(a[k15, 4])) else np.nan
        row["day"] = datetime.fromtimestamp(t, timezone.utc).strftime("%m-%d")
        for hn in HORIZONS:
            row[f"tgt_{hn}"] = fut[hn]
        for k2, v in row.items():
            F[k2].append(v)
    return {k: np.array(v) for k, v in F.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--stride", type=int, default=60)
    a = ap.parse_args()
    rng = np.random.default_rng(SEED)
    series = load_series(a.db)
    print(f"coins: {sorted(series)}")

    # pool all coins
    pooled = defaultdict(list)
    for sym, arr in series.items():
        S = build_samples(arr, a.stride)
        n = len(S.get("tgt_15m", []))
        print(f"  {sym}: samples={n}")
        for k, v in S.items():
            pooled[k].append(v)
    P = {k: np.concatenate(v) for k, v in pooled.items()}
    days = P["day"]
    uniq_days = sorted(set(days.tolist()))
    feats = [k for k in P if not k.startswith("tgt_") and k != "day"]
    print(f"pooled samples={len(days)} days={len(uniq_days)}\n")

    any_pass = False
    for hn in HORIZONS:
        y_all = P[f"tgt_{hn}"]
        print(f"===== target: |move| next {hn} (bps) — pooled median="
              f"{np.median(y_all):.1f} =====")
        print(f"{'feature':>14} {'n':>7} | {'spear_mu':>8} {'spear_sd':>8} "
              f"{'sign%':>5} | {'MI':>6} {'MIperm':>6} {'xsMI':>6} | verdict")
        for f in feats:
            x_all = P[f]
            m = np.isfinite(x_all) & np.isfinite(y_all)
            if m.sum() < 2000:
                print(f"{f:>14} {int(m.sum()):>7} | insufficient coverage")
                continue
            x, y, d = x_all[m], y_all[m], days[m]
            sps = []
            for dd in uniq_days:
                dm = d == dd
                if dm.sum() >= 200:
                    sps.append(spearman(x[dm], y[dm]))
            sps = np.array(sps)
            mu, sd = sps.mean(), sps.std()
            dom_sign = 1.0 if (sps > 0).sum() >= len(sps) / 2 else -1.0
            signpct = 100.0 * (np.sign(sps) == dom_sign).mean()
            mi = mi_bits(x, y)
            yp = y.copy()
            for dd in uniq_days:  # permute within day
                dm = d == dd
                yp[dm] = rng.permutation(yp[dm])
            mip = mi_bits(x, yp)
            xs = mi - mip
            ok = xs >= 0.005 and abs(mu) >= 0.03 and signpct >= 80.0
            any_pass = any_pass or ok
            print(f"{f:>14} {len(x):>7} | {mu:>8.3f} {sd:>8.3f} {signpct:>4.0f}% "
                  f"| {mi:>6.3f} {mip:>6.3f} {xs:>6.3f} | {'PASS' if ok else 'dead'}")
        print()
    print("STEP-1 VERDICT:",
          "PASS — at least one feature clears the registered screen"
          if any_pass else
          "FAIL — no feature clears excess-MI>=0.005 AND |spearman|>=0.03 @80% sign")
    return 0


if __name__ == "__main__":
    sys.exit(main())
