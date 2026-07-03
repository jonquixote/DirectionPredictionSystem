#!/usr/bin/env python3
"""Track 1 registered decision — runs the FROZEN test in prereg_track1.json end-to-end.

Fires: first obs per window with up_mid >= X, |dev_bps| < T, phase <= PH.
Entry: executable down ask (1 - yes_bid), taker fee. Outcome: KALSHI SETTLEMENT ONLY
(windows.kalshi_result; unsettled windows excluded — run backfill_settlements.py first).
Inference: cluster bootstrap by boundary_ts. Verdict rendered ONLY at >= min_clusters;
below that it prints diagnostics and exits 2 (peek, no verdict — no early stopping).

Books are read from BOTH the hot db (obs.orderbook_json, last ~7d) AND the gzip
archives written by archive_prune_books.py (full history) — the daily prune NULLs
old blobs, so without the archives this test could never reach 2,500 clusters.
obs summary columns (dev_bps, phase) are never pruned and are joined by (symbol, ts).

Usage: python3 decide_track1.py --db /data/kalshi_fade.db --prereg prereg_track1.json \
       [--archive-dir /data/archives]
"""
from __future__ import annotations
import argparse, glob, gzip, json, os, sqlite3, sys
from collections import defaultdict
import numpy as np

def tfee(e): return 0.07 * e * (1 - e)

def book(ob_json):
    o = json.loads(ob_json)
    y = o.get("yes_dollars") or []; n = o.get("no_dollars") or []
    if not y: return None
    yb = max(float(p) for p, s in y)
    nb = max((float(p) for p, s in n), default=None)
    ya = (1 - nb) if nb is not None else None
    return yb, (yb + ya) / 2 if ya is not None else yb

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--prereg", required=True)
    ap.add_argument("--archive-dir", default="/data/archives")
    a = ap.parse_args()
    P = json.load(open(a.prereg))
    X = P["rule"]["X_up_mid_min"]; T = P["rule"]["T_dev_bps_max"]; PH = P["rule"]["PH_phase_max"]
    lo_e, hi_e = P["rule"]["entry_band"]
    min_clusters = P["sample"]["min_clusters"]

    db = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    wins = {(sym, bts): res for sym, bts, res in db.execute(
        "SELECT symbol, boundary_ts, kalshi_result FROM windows "
        "WHERE kalshi_result IN ('yes','no') AND close_ts < strftime('%s','now')")}
    n_unsettled = db.execute(
        "SELECT COUNT(*) FROM windows WHERE kalshi_result IS NULL "
        "AND close_ts < strftime('%s','now')").fetchone()[0]

    # obs summary (never pruned): (symbol, ts) -> (boundary_ts, dev_bps, phase)
    meta = {}
    for sym, ts, bts, dev, ph in db.execute(
            "SELECT symbol, ts, boundary_ts, dev_bps, phase FROM obs"):
        if (sym, bts) in wins:
            meta[(sym, ts)] = (bts, dev, ph)

    clusters = defaultdict(list)   # boundary_ts -> [(entry, pnl)]
    fired = set()                  # (symbol, boundary_ts)
    entries = []

    def consider(sym, ts, ob_json):
        """Evaluate one (chronologically fed) book row against the frozen rule."""
        m = meta.get((sym, ts))
        if not m:
            return
        bts, dev, ph = m
        key = (sym, bts)
        if key in fired or key not in wins:
            return
        if ph is None or ph > PH or dev is None or abs(dev) >= T:
            return
        bp = book(ob_json)
        if not bp:
            return
        yb, um = bp
        if um < X:
            return
        e = 1 - yb
        fired.add(key)             # first qualifying rich obs decides (fire or band-reject)
        if not (lo_e < e < hi_e):
            return
        down_win = (wins[key] == "no")   # settlement: yes => UP won
        clusters[bts].append(((1 - e) if down_win else -e) - tfee(e))
        entries.append(e)

    # 1) archives (oldest first; ts-ordered within each file; full history <= watermark)
    wm_path = os.path.join(a.archive_dir, "kalshi_books.watermark")
    watermark = int(open(wm_path).read().strip() or 0) if os.path.exists(wm_path) else 0
    n_arch = 0
    for f in sorted(glob.glob(os.path.join(a.archive_dir, "kalshi_books_*.jsonl.gz"))):
        with gzip.open(f, "rt", encoding="utf-8") as gz:
            for line in gz:
                r = json.loads(line)
                consider(r["symbol"], r["ts"], r["book"])
                n_arch += 1
    # 2) hot db rows newer than the watermark (not yet archived)
    for sym, ts, ob in db.execute(
            "SELECT symbol, ts, orderbook_json FROM obs "
            "WHERE ts > ? AND orderbook_json IS NOT NULL ORDER BY ts", (watermark,)):
        consider(sym, ts, ob)

    allp = [x for v in clusters.values() for x in v]
    fires = len(allp)
    nc = len(clusters)
    print(f"prereg={P['name']} frozen={P['frozen_utc']}")
    print(f"settled_windows={len(wins)} unsettled_excluded={n_unsettled} "
          f"archived_rows_scanned={n_arch} fires={fires} clusters={nc} "
          f"(min for verdict: {min_clusters})")
    if fires < 2:
        print("insufficient fires"); return 2
    a_ = np.array(allp)
    keys = list(clusters.keys()); rng = np.random.default_rng(7)
    means = []
    for _ in range(5000):
        pick = rng.integers(0, len(keys), len(keys))
        s = [x for i in pick for x in clusters[keys[i]]]
        means.append(np.mean(s))
    lo, hi = np.percentile(means, [2.5, 97.5])
    print(f"EV/share={a_.mean():+.4f}  clustered-95=[{lo:+.4f},{hi:+.4f}]  "
          f"mean_entry={np.mean(entries):.3f}")
    if nc < min_clusters:
        print(f"PEEK ONLY — {nc} < {min_clusters} clusters. NO VERDICT (registered rule).")
        return 2
    if lo > 0:
        print(f"VERDICT: PASS — {P['decision']['pass']}")
        return 0
    print(f"VERDICT: FAIL — {P['decision']['fail']}")
    return 1

if __name__ == "__main__":
    sys.exit(main())
