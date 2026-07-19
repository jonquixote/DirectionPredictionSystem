"""Track 4 frozen feature construction — shared by training and evaluation.

Prereg (track4-vol-magnitude-into-kalshi-strikes, frozen 2026-07-11T08:10Z) freezes:
    log_rv_1m, log_rv_5m, log_rv_15m, log_range_15m,
    signed_distance_bps = ln(strike/spot)*1e4,
    tte_minutes,
    z = signed_distance_bps / (rv_15m * sqrt(tte_minutes))

Estimator convention (fixed here, identical for training candles and live replay):
- Bars: 1-minute close series (candle close, or last 30s spot sample in the minute).
- r_t = ln(close_t / close_{t-1});  rv_Nm = population std of the trailing N returns
  (rv_1m = |r_t|), expressed in bps (x1e4).
- range_15m = ln(max/min of the trailing 15 closes) in bps.
- log_* = ln(max(value_bps, 1e-6)).
- z uses rv_15m in bps per sqrt-minute against distance in bps.
"""
import numpy as np

FEATURES = ["log_rv_1m", "log_rv_5m", "log_rv_15m", "log_range_15m",
            "signed_distance_bps", "tte_minutes", "z"]
EPS = 1e-6


def bar_features(close: np.ndarray) -> dict[str, np.ndarray]:
    """Per-bar vol features from a 1m close series. Index i uses bars <= i (no lookahead).
    First 15 bars are warmup (NaN)."""
    n = len(close)
    r = np.full(n, np.nan)
    r[1:] = np.log(close[1:] / close[:-1]) * 1e4          # 1m log returns, bps
    rv1 = np.abs(r)
    rv5 = np.full(n, np.nan)
    rv15 = np.full(n, np.nan)
    rng15 = np.full(n, np.nan)
    for w, out in ((5, rv5), (15, rv15)):
        for i in range(w, n):
            out[i] = np.std(r[i - w + 1:i + 1])
    for i in range(15, n):
        win = close[i - 14:i + 1]
        rng15[i] = np.log(win.max() / win.min()) * 1e4
    return {
        "log_rv_1m": np.log(np.maximum(rv1, EPS)),
        "log_rv_5m": np.log(np.maximum(rv5, EPS)),
        "log_rv_15m": np.log(np.maximum(rv15, EPS)),
        "log_range_15m": np.log(np.maximum(rng15, EPS)),
        "rv_15m_bps": rv15,
    }


def market_features(bf: dict, i: int, spot: float, strike: float, tte_min: float):
    """Feature vector for one (bar index, strike, tte) — order matches FEATURES."""
    d_bps = np.log(strike / spot) * 1e4
    rv15 = bf["rv_15m_bps"][i]
    if not np.isfinite(rv15) or rv15 <= 0 or tte_min <= 0:
        return None
    z = d_bps / (max(rv15, EPS) * np.sqrt(tte_min))
    row = [bf["log_rv_1m"][i], bf["log_rv_5m"][i], bf["log_rv_15m"][i],
           bf["log_range_15m"][i], d_bps, tte_min, z]
    if not all(np.isfinite(row)):
        return None
    return row
