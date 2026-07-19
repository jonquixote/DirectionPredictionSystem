#!/usr/bin/env python3.12
"""Adversarial validation + extension of the Track 4 week-1 DEAD verdict.

Sections:
 1. Fire diagnostics — realized P&L by claimed-EV bucket, entry, side, underlying,
    tte, day. A real signal shows monotonicity in the EV buckets.
 2. Calibration — model p vs venue outcome on fires; market mid vs venue outcome
    (is the market simply better calibrated than the model?).
 3. Latency robustness — re-price every fire at the NEXT poll's book (~30s later).
    If EV collapses further, fires were stale-quote illusions.
 4. Verdict robustness — iid bootstrap, fire-weighted cluster bootstrap, and
    trimmed variants. Does ANY reasonable estimator flip CI-low > 0?
 5. Range (B-type) markets evaluated CORRECTLY as an unregistered extension:
    p_range = p(spot>=floor) - p(spot>=cap) from the same frozen model,
    same EV>=2c gate, venue-settled. Doubles the evidence base for any
    successor prereg. (Cannot resurrect the registered verdict.)
"""
import json, os, sqlite3, time, urllib.parse, urllib.request
from collections import defaultdict

import joblib  # trusted: bundle written by train_track4_model.py on this host
import numpy as np

from track4_features import bar_features, market_features
from week1_checkpoint import CHECKPOINT_TS, EV_FIRE, FEE, SETTLE_CACHE

DB = "/data/kalshi_strikes.db"
MODEL = "/data/models/track4_week1.joblib"
FIRES = "/data/logs/track4_week1_fires.json"
B_STRIKES_CACHE = "/data/logs/track4_b_strikes.json"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SEED = 7


def sec(title):
    print(f"\n===== {title} =====", flush=True)


def bucket_table(fires, key, label):
    groups = defaultdict(list)
    for f in fires:
        groups[key(f)].append(f["pnl"])
    print(f"-- by {label}:")
    for k in sorted(groups):
        v = groups[k]
        print(f"   {k}: n={len(v)} mean_pnl={np.mean(v):+.4f}")


def cluster_ci(pnl, clusters, weighted=False, n_boot=5000):
    uniq = np.unique(clusters)
    rng = np.random.default_rng(SEED)
    sums = {c: (pnl[clusters == c].sum(), (clusters == c).sum()) for c in uniq}
    stats = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        s = np.array([sums[uniq[i]] for i in pick])
        stats.append(s[:, 0].sum() / s[:, 1].sum() if weighted
                     else np.mean(s[:, 0] / s[:, 1]))
    return np.percentile(stats, [2.5, 97.5])


def main():
    bundle = joblib.load(MODEL)
    scaler, logit, iso = bundle["scaler"], bundle["logit"], bundle["iso"]
    predict = lambda rows: iso.predict(  # noqa: E731
        logit.predict_proba(scaler.transform(np.asarray(rows)))[:, 1])
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    fires = json.load(open(FIRES))
    results = json.load(open(SETTLE_CACHE))
    fires = [f for f in fires if "pnl" in f]
    pnl = np.array([f["pnl"] for f in fires])
    clusters = np.array([f["close_ts"] for f in fires])
    print(f"loaded {len(fires)} settled threshold fires, mean {pnl.mean():+.4f}")

    sec("1. FIRE DIAGNOSTICS")
    bucket_table(fires, lambda f: min(int(f["ev_model"] * 100) // 2 * 2, 10), "claimed EV bucket (c, floor/2*2, cap 10)")
    bucket_table(fires, lambda f: round(min(f["entry"], 0.5), 1), "entry price (cap 0.5)")
    bucket_table(fires, lambda f: f["side"], "side")
    bucket_table(fires, lambda f: f["und"], "underlying")
    bucket_table(fires, lambda f: int((f["close_ts"] - f["ts"]) / 3600), "hours to close")
    bucket_table(fires, lambda f: time.strftime("%m-%d", time.gmtime(f["ts"])), "fire day")

    sec("2. CALIBRATION (fires)")
    outc = np.array([1.0 if results[f["ticker"]] == "yes" else 0.0 for f in fires])
    p_model = np.array([f["p"] for f in fires])
    print("-- model p vs venue outcome, deciles of p:")
    for lo in np.arange(0, 1, 0.1):
        m = (p_model >= lo) & (p_model < lo + 0.1)
        if m.sum():
            print(f"   p in [{lo:.1f},{lo + 0.1:.1f}): n={m.sum()} "
                  f"mean_p={p_model[m].mean():.3f} realized={outc[m].mean():.3f}")
    rows = {(f["ticker"], f["ts"]): f for f in fires}
    mids = {}
    for (tk, ts), f in rows.items():
        r = con.execute("SELECT yes_bid, yes_ask FROM strikes WHERE ticker=? AND ts=?",
                        (tk, ts)).fetchone()
        if r and r[0] is not None:
            mids[(tk, ts)] = (r[0] + r[1]) / 2
    mid = np.array([mids.get((f["ticker"], f["ts"]), np.nan) for f in fires])
    ok = np.isfinite(mid)
    print("-- market mid vs venue outcome, deciles of mid:")
    for lo in np.arange(0, 1, 0.1):
        m = ok & (mid >= lo) & (mid < lo + 0.1)
        if m.sum():
            print(f"   mid in [{lo:.1f},{lo + 0.1:.1f}): n={m.sum()} "
                  f"mean_mid={mid[m].mean():.3f} realized={outc[m].mean():.3f}")
    print(f"-- brier: model={np.mean((p_model - outc) ** 2):.4f} "
          f"market_mid={np.mean((mid[ok] - outc[ok]) ** 2):.4f}")

    sec("3. LATENCY ROBUSTNESS (entry at next poll's book)")
    moved = []
    for f in fires:
        r = con.execute(
            "SELECT yes_bid, yes_ask FROM strikes WHERE ticker=? AND ts>? AND ts<=? "
            "AND yes_bid>0 AND yes_ask<1 ORDER BY ts LIMIT 1",
            (f["ticker"], f["ts"], f["ts"] + 120)).fetchone()
        if not r:
            continue
        e2 = float(r[1]) if f["side"] == "yes" else 1.0 - float(r[0])
        win = 1.0 if f["win"] else 0.0
        moved.append(win - e2 - FEE(e2))
    print(f"n={len(moved)} mean_pnl_next_poll={np.mean(moved):+.4f} "
          f"(vs {pnl.mean():+.4f} at fire poll)")

    sec("4. VERDICT ROBUSTNESS")
    rng = np.random.default_rng(SEED)
    iid = np.array([pnl[rng.integers(0, len(pnl), len(pnl))].mean() for _ in range(5000)])
    print(f"iid bootstrap:            [{np.percentile(iid, 2.5):+.4f},{np.percentile(iid, 97.5):+.4f}]")
    lo, hi = cluster_ci(pnl, clusters, weighted=False)
    print(f"cluster (registered):     [{lo:+.4f},{hi:+.4f}]")
    lo, hi = cluster_ci(pnl, clusters, weighted=True)
    print(f"cluster fire-weighted:    [{lo:+.4f},{hi:+.4f}]")
    keep = np.abs(pnl - np.median(pnl)) <= 5 * np.std(pnl)
    lo, hi = cluster_ci(pnl[keep], clusters[keep], weighted=False)
    print(f"cluster 5-sigma-trimmed:  [{lo:+.4f},{hi:+.4f}] (n={keep.sum()})")

    sec("5. RANGE (B-TYPE) MARKETS — proper range labels, unregistered extension")
    b_tickers = [r[0] for r in con.execute(
        "SELECT DISTINCT ticker FROM strikes WHERE ts<?", (CHECKPOINT_TS,))
        if r[0].rsplit("-", 1)[-1].startswith("B")]
    cache = json.load(open(B_STRIKES_CACHE)) if os.path.exists(B_STRIKES_CACHE) else {}
    missing = [t for t in b_tickers if t not in cache]
    print(f"B tickers in window: {len(b_tickers)}, fetching {len(missing)} strike defs")
    for k, t in enumerate(missing):
        url = KALSHI + "/markets/" + urllib.parse.quote(t)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "t4v/1.0"})
            m = json.loads(urllib.request.urlopen(req, timeout=15).read())["market"]
            cache[t] = {"floor": m.get("floor_strike"), "cap": m.get("cap_strike"),
                        "result": m.get("result") or ""}
        except Exception:  # noqa: BLE001
            cache[t] = {"floor": None, "cap": None, "result": ""}
        if (k + 1) % 200 == 0:
            print(f"  {k + 1}/{len(missing)}", flush=True)
            json.dump(cache, open(B_STRIKES_CACHE, "w"))
        time.sleep(0.12)
    json.dump(cache, open(B_STRIKES_CACHE, "w"))

    b_fires = []
    for und in ("BTC", "ETH"):
        rows_und = con.execute(
            "SELECT ts, ticker, close_ts, yes_bid, yes_ask, spot FROM strikes "
            "WHERE underlying=? AND ts<? ORDER BY ts", (und, CHECKPOINT_TS)).fetchall()
        from week1_checkpoint import load_spot_bars
        mins, close = load_spot_bars(con, und, CHECKPOINT_TS)
        bf = bar_features(close)
        min_idx = {int(m): i for i, m in enumerate(mins)}
        fired = set()
        for ts, tk, close_ts, bid, ask, spot in rows_und:
            info = cache.get(tk)
            if (not info or tk in fired or info["floor"] is None
                    or info["cap"] is None or info["result"] not in ("yes", "no")):
                continue
            if bid is None or ask is None or not (0.0 < bid < ask < 1.0):
                continue
            i = min_idx.get(ts // 60)
            if i is None or i < 15 or close_ts is None or spot is None:
                continue
            tte = (close_ts - ts) / 60.0
            rf = market_features(bf, i, float(spot), float(info["floor"]), tte)
            rc = market_features(bf, i, float(spot), float(info["cap"]), tte)
            if rf is None or rc is None:
                continue
            pf, pc = predict([rf, rc])
            p = max(float(pf - pc), 0.0)
            e_yes, e_no = float(ask), 1.0 - float(bid)
            ev_yes = p - e_yes - FEE(e_yes)
            ev_no = (1.0 - p) - e_no - FEE(e_no)
            side, ev, entry = ("yes", ev_yes, e_yes) if ev_yes >= ev_no else ("no", ev_no, e_no)
            if ev < EV_FIRE:
                continue
            fired.add(tk)
            win = (side == "yes") == (info["result"] == "yes")
            b_fires.append({"pnl": (1.0 if win else 0.0) - entry - FEE(entry),
                            "close_ts": close_ts, "ev": ev, "win": win})
    if b_fires:
        bp = np.array([f["pnl"] for f in b_fires])
        bc = np.array([f["close_ts"] for f in b_fires])
        lo, hi = cluster_ci(bp, bc)
        print(f"B-market fires={len(bp)} mean_pnl={bp.mean():+.4f} "
              f"cluster-95CI=[{lo:+.4f},{hi:+.4f}] clusters={len(np.unique(bc))} "
              f"win_rate={np.mean([f['win'] for f in b_fires]):.1%} "
              f"mean_claimed_ev={np.mean([f['ev'] for f in b_fires]):+.4f}")
        both = np.concatenate([pnl, bp])
        bothc = np.concatenate([clusters, bc])
        lo, hi = cluster_ci(both, bothc)
        print(f"POOLED T+B: n={len(both)} mean={both.mean():+.4f} "
              f"CI=[{lo:+.4f},{hi:+.4f}] clusters={len(np.unique(bothc))}")
    else:
        print("no B-market fires")


if __name__ == "__main__":
    main()
