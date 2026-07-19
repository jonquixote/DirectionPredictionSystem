#!/usr/bin/env python3.12
"""Track 4 week-1 model: L2 logistic + isotonic on frozen features (prereg-frozen class).

Trains ONLY on Coinbase 1m candles strictly before the collector start timestamp
(forward-only rule). Synthetic threshold markets sampled from the spot path:
label = 1{spot(t + tte) >= strike}, strike = spot(t) * exp(d_bps/1e4).

Sampling scheme (pre-collector data, orthogonal to the frozen model class):
- bar stride 5 min; tte grid within 26h; d ~ U(-300, +300) bps, 6 draws per (t, tte).
- isotonic calibrated on a held-out random 15% of sampled rows. All rows predate the
  collector start, so no forward leakage is possible; overlap between fit and
  calibration windows only risks optimistic calibration, which the forward EV gate
  (not calibration quality) is the arbiter of.

Saves joblib bundle {scaler, logit, iso, meta} to --out.
"""
import argparse, csv, json, time

import joblib  # bundle is written+read only by our own scripts on this host (trusted provenance)
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from track4_features import FEATURES, bar_features, market_features

TTE_GRID = [15, 30, 60, 120, 240, 480, 780, 1080, 1560]  # minutes, <= 26h
N_DIST = 6
DIST_RANGE = 300.0  # bps, matches +/-3% market selection
STRIDE = 5
SEED = 7


def load_candles(path: str, t_max: int):
    ts, close = [], []
    with open(path) as f:
        for row in csv.DictReader(f):
            t = int(row["ts"])
            if t < t_max:
                ts.append(t)
                close.append(float(row["close"]))
    ts = np.array(ts)
    close = np.array(close)
    order = np.argsort(ts)
    return ts[order], close[order]


def build_dataset(ts, close, rng):
    bf = bar_features(close)
    # candle series can have gaps; require contiguous minutes for label lookup
    idx_of_ts = {int(t): i for i, t in enumerate(ts)}
    X, y = [], []
    for i in range(15, len(ts), STRIDE):
        spot = close[i]
        for tte in TTE_GRID:
            j = idx_of_ts.get(int(ts[i]) + tte * 60)
            if j is None:
                continue
            for d in rng.uniform(-DIST_RANGE, DIST_RANGE, N_DIST):
                strike = spot * np.exp(d / 1e4)
                row = market_features(bf, i, spot, strike, float(tte))
                if row is None:
                    continue
                X.append(row)
                y.append(1.0 if close[j] >= strike else 0.0)
    return np.array(X), np.array(y)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candles", nargs="+", required=True, help="csv per product")
    ap.add_argument("--collector-start", type=int, required=True,
                    help="epoch s; training uses candles strictly before this")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    rng = np.random.default_rng(SEED)
    Xs, ys = [], []
    for path in a.candles:
        ts, close = load_candles(path, a.collector_start)
        print(f"{path}: {len(ts)} bars {time.strftime('%F %T', time.gmtime(ts[0]))}"
              f" -> {time.strftime('%F %T', time.gmtime(ts[-1]))}", flush=True)
        X, y = build_dataset(ts, close, rng)
        Xs.append(X)
        ys.append(y)
        print(f"  sampled {len(y)} rows, base rate {y.mean():.3f}", flush=True)

    X = np.vstack(Xs)
    y = np.concatenate(ys)
    n = len(y)
    order = rng.permutation(n)
    X, y = X[order], y[order]
    n_cal = int(n * 0.15)
    X_fit, y_fit = X[:-n_cal], y[:-n_cal]
    X_cal, y_cal = X[-n_cal:], y[-n_cal:]

    scaler = StandardScaler().fit(X_fit)
    logit = LogisticRegression(C=1.0, max_iter=2000)
    logit.fit(scaler.transform(X_fit), y_fit)
    p_cal = logit.predict_proba(scaler.transform(X_cal))[:, 1]
    iso = IsotonicRegression(out_of_bounds="clip").fit(p_cal, y_cal)

    p_fit = iso.predict(logit.predict_proba(scaler.transform(X_fit))[:, 1])
    brier = float(np.mean((p_fit - y_fit) ** 2))
    meta = {
        "prereg": "track4-vol-magnitude-into-kalshi-strikes",
        "trained_utc": time.strftime("%FT%TZ", time.gmtime()),
        "collector_start": a.collector_start,
        "n_train": int(len(y_fit)), "n_cal": int(n_cal),
        "base_rate": float(y.mean()), "brier_in_sample": brier,
        "features": FEATURES, "coef": logit.coef_[0].tolist(),
        "seed": SEED, "tte_grid": TTE_GRID, "stride": STRIDE,
        "n_dist": N_DIST, "dist_range_bps": DIST_RANGE,
    }
    joblib.dump({"scaler": scaler, "logit": logit, "iso": iso, "meta": meta}, a.out)
    print(json.dumps(meta, indent=2), flush=True)
    print(f"saved -> {a.out}", flush=True)


if __name__ == "__main__":
    main()
