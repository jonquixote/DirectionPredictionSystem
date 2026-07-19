#!/usr/bin/env python3.12
"""Track 4 week-1 EV checkpoint (prereg: track4-vol-magnitude-into-kalshi-strikes).

Forward-only replay of strike_logger data from collector start to the
preregistered checkpoint instant 2026-07-18T08:00:00Z.

Frozen rules applied here:
- executable: buy-yes at yes_ask, buy-no at (1 - yes_bid); book must be two-sided
  (0 < bid < ask < 1) at the fire row; no fallbacks.
- fee = 0.07 * e * (1 - e), taker, e = executable entry.
- fire: EV = p_side - e - fee >= 0.02; first crossing per ticker only (one
  position per market, held to close).
- label: Coinbase spot at close vs strike (candle at/just before close_ts);
  venue settlement is verification only.
- stats: realized P&L per contract, clustered by close_ts; cluster bootstrap
  5000 resamples, seed 7; pass = mean > 0 AND CI-low > 0 at >= 200 effective
  clusters. < 50 fires => fire-rate reality report, one-time <=7d extension.
Kill checks: median fired spread > 3c; collector uptime < 80%.
"""
import argparse, csv, json, sqlite3, time
from datetime import datetime, timezone

import joblib  # loads only the bundle train_track4_model.py wrote on this host (trusted, 0644 root path)
import numpy as np

from track4_features import bar_features, market_features

CHECKPOINT_TS = int(datetime(2026, 7, 18, 8, 0, 0, tzinfo=timezone.utc).timestamp())
EV_FIRE = 0.02
FEE = lambda e: 0.07 * e * (1.0 - e)  # noqa: E731
SEED = 7
N_BOOT = 5000


def load_spot_bars(con, underlying, t_end):
    """1m bars from the logger's own 30s Coinbase spot samples (last sample per minute)."""
    rows = con.execute(
        "SELECT ts, spot FROM strikes WHERE underlying=? AND spot IS NOT NULL AND ts<? "
        "GROUP BY ts ORDER BY ts", (underlying, t_end)).fetchall()
    bars = {}
    for ts, spot in rows:
        bars[ts // 60] = float(spot)   # last sample in minute wins (ordered scan)
    mins = np.array(sorted(bars))
    close = np.array([bars[m] for m in mins])
    return mins, close


def candle_close_lookup(paths):
    lut = {}
    for p in paths:
        with open(p) as f:
            for row in csv.DictReader(f):
                lut[int(row["ts"]) // 60] = float(row["close"])
    return lut


def spot_at(lut, ts):
    """Candle close at or just before ts (<=5 min back)."""
    m = ts // 60
    for k in range(6):
        if m - k in lut:
            return lut[m - k]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--btc-candles", required=True)
    ap.add_argument("--eth-candles", required=True)
    a = ap.parse_args()

    bundle = joblib.load(a.model)
    scaler, logit, iso = bundle["scaler"], bundle["logit"], bundle["iso"]
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)

    label_lut = {"BTC": candle_close_lookup([a.btc_candles]),
                 "ETH": candle_close_lookup([a.eth_candles])}

    # --- collector uptime over week 1 ---
    t0 = con.execute("SELECT MIN(ts) FROM strikes").fetchone()[0]
    n_poll_min = con.execute(
        "SELECT COUNT(DISTINCT ts/60) FROM strikes WHERE ts<?", (CHECKPOINT_TS,)).fetchone()[0]
    expected_min = (CHECKPOINT_TS - t0) / 60.0
    uptime = n_poll_min / expected_min

    fires = []
    no_fire_reasons = {"one_sided": 0, "warmup": 0, "no_ev": 0}
    for und in ("BTC", "ETH"):
        mins, close = load_spot_bars(con, und, CHECKPOINT_TS)
        bf = bar_features(close)
        min_idx = {int(m): i for i, m in enumerate(mins)}
        fired_tickers = set()
        rows = con.execute(
            "SELECT ts, ticker, strike, close_ts, yes_bid, yes_ask, spot FROM strikes "
            "WHERE underlying=? AND ts<? AND strike IS NOT NULL ORDER BY ts",
            (und, CHECKPOINT_TS)).fetchall()
        for ts, ticker, strike, close_ts, bid, ask, spot in rows:
            if ticker in fired_tickers or close_ts is None or spot is None:
                continue
            if bid is None or ask is None or not (0.0 < bid < ask < 1.0):
                no_fire_reasons["one_sided"] += 1
                continue
            i = min_idx.get(ts // 60)
            if i is None or i < 15:
                no_fire_reasons["warmup"] += 1
                continue
            tte_min = (close_ts - ts) / 60.0
            row = market_features(bf, i, float(spot), float(strike), tte_min)
            if row is None:
                no_fire_reasons["warmup"] += 1
                continue
            p = float(iso.predict(logit.predict_proba(scaler.transform([row]))[:, 1])[0])
            e_yes, e_no = float(ask), 1.0 - float(bid)
            ev_yes = p - e_yes - FEE(e_yes)
            ev_no = (1.0 - p) - e_no - FEE(e_no)
            side, ev, entry = ("yes", ev_yes, e_yes) if ev_yes >= ev_no else ("no", ev_no, e_no)
            if ev < EV_FIRE:
                no_fire_reasons["no_ev"] += 1
                continue
            spot_close = spot_at(label_lut[und], close_ts)
            if spot_close is None:
                continue  # label unavailable — excluded, reported
            settle_yes = spot_close >= strike
            win = (side == "yes") == settle_yes
            pnl = (1.0 if win else 0.0) - entry - FEE(entry)
            fired_tickers.add(ticker)
            fires.append({"ticker": ticker, "und": und, "ts": ts, "close_ts": close_ts,
                          "side": side, "entry": entry, "ev_model": ev, "p": p,
                          "spread_c": round((ask - bid) * 100, 1),
                          "settle_yes": bool(settle_yes), "win": bool(win),
                          "pnl": round(pnl, 4)})

    n = len(fires)
    print(f"collector_start={time.strftime('%FT%TZ', time.gmtime(t0))} "
          f"checkpoint={time.strftime('%FT%TZ', time.gmtime(CHECKPOINT_TS))}")
    print(f"uptime={uptime:.1%} (kill if <80%)  poll_minutes={n_poll_min}")
    print(f"fires={n}  no_fire={no_fire_reasons}")
    if n == 0:
        print("VERDICT: zero fires — fire-rate reality: EV>=2c never reached. "
              "Prereg allows ONE <=7d extension; else DEAD.")
        return

    pnl = np.array([f["pnl"] for f in fires])
    clusters = np.array([f["close_ts"] for f in fires])
    uniq = np.unique(clusters)
    cl_means = np.array([pnl[clusters == c].mean() for c in uniq])
    rng = np.random.default_rng(SEED)
    boot = np.array([cl_means[rng.integers(0, len(cl_means), len(cl_means))].mean()
                     for _ in range(N_BOOT)])
    lo, hi = np.percentile(boot, [2.5, 97.5])
    med_spread = float(np.median([f["spread_c"] for f in fires]))

    print(f"mean_pnl_per_contract={pnl.mean():+.4f}  cluster-95CI=[{lo:+.4f},{hi:+.4f}]")
    print(f"effective_clusters={len(uniq)} (need >=200)  win_rate={np.mean([f['win'] for f in fires]):.1%}")
    print(f"median_fired_spread={med_spread:.1f}c (kill if >3c)")
    print(f"mean_model_ev_at_fire={np.mean([f['ev_model'] for f in fires]):+.4f}")

    if uptime < 0.80:
        verdict = "DEAD — collector uptime <80% (data invalid; clock restart allowed once)"
    elif med_spread > 3.0:
        verdict = "DEAD — liquidity regression (median fired spread >3c)"
    elif n < 50:
        verdict = (f"LOW FIRE RATE ({n} < 50) — prereg permits one <=7d extension; "
                   "CI below is informational")
    elif pnl.mean() > 0 and lo > 0 and len(uniq) >= 200:
        verdict = "PASS — mean>0, CI-low>0, >=200 clusters"
    else:
        verdict = "DEAD — week-1 CI-low <= 0 (or <200 clusters); no 'one more week'"
    print(f"VERDICT: {verdict}")

    with open("/data/logs/track4_week1_fires.json", "w") as f:
        json.dump(fires, f, indent=1)
    print("fires detail -> /data/logs/track4_week1_fires.json")


if __name__ == "__main__":
    main()
