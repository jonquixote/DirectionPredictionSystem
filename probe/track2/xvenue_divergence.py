#!/usr/bin/env python3
"""Cross-venue divergence scan (Track 4 hypothesis #1, zero new data).

Same 15m underlying, same poll instant: does PM up-mid vs Kalshi yes-mid diverge
transiently, and does the WIDER venue revert toward the other by window close?
Both are calibrated individually; a persistent tradeable gap would be new.

Uses /data/track2_latency.db ticks (ms-aligned mids on both venues). Reports:
  - distribution of |pm_mid - k_mid| when BOTH present
  - frequency of |gap| >= 2c / 5c, and per coin
  - reversion test: at gap>=2c, sign of (mid_at_close - mid_now) for each venue vs the gap
    (does the venue that's HIGH come down / LOW come up?) — directional, executability-gated
    caveat noted (mids, not executable bid/ask; a real signal must clear the touch).
"""
from __future__ import annotations
import argparse, sqlite3
from collections import defaultdict
import numpy as np

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--gap", type=float, default=0.02)
    a = ap.parse_args()
    c = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    coins = [r[0] for r in c.execute("SELECT DISTINCT coin FROM ticks")]

    allgaps = []
    per = defaultdict(list)
    # reversion: for each (coin, window), find first tick with |gap|>=gap; compare each
    # venue mid then vs at window's last tick.
    rev = {"pm": [0, 0], "k": [0, 0]}   # [came_toward, went_away]
    for coin in coins:
        rows = c.execute(
            "SELECT ts_ms, boundary_ts, k_yes_bid, k_yes_ask, pm_up_bid, pm_up_ask "
            "FROM ticks WHERE coin=? ORDER BY ts_ms", (coin,)).fetchall()
        byw = defaultdict(list)
        for ts, bd, kyb, kya, pmb, pma in rows:
            km = (kyb + kya) / 2 if kyb is not None and kya is not None else None
            pm = (pmb + pma) / 2 if pmb is not None and pma is not None else None
            if km is not None and pm is not None:
                g = pm - km
                allgaps.append(abs(g)); per[coin].append(abs(g))
                byw[bd].append((ts, km, pm))
        for bd, seq in byw.items():
            fired = None
            for ts, km, pm in seq:
                if abs(pm - km) >= a.gap:
                    fired = (km, pm); break
            if not fired:
                continue
            km0, pm0 = fired
            kmL, pmL = seq[-1][1], seq[-1][2]
            # did each venue move toward the other's initial level?
            rev["k"][0 if abs(kmL - pm0) < abs(km0 - pm0) else 1] += 1
            rev["pm"][0 if abs(pmL - km0) < abs(pm0 - km0) else 1] += 1

    g = np.array(allgaps)
    print("=" * 84)
    print(f"Cross-venue divergence: PM up-mid vs Kalshi yes-mid (n={len(g)} paired ticks)")
    print("=" * 84)
    print(f"|gap|: median={np.median(g):.3f} p95={np.percentile(g,95):.3f} max={g.max():.3f}")
    for thr in (0.02, 0.05, 0.10):
        print(f"  |gap|>={100*thr:.0f}c: {100*(g>=thr).mean():.2f}% of paired ticks")
    print("per coin |gap| median / %>=2c:")
    for coin in sorted(per):
        v = np.array(per[coin])
        print(f"   {coin}: median={np.median(v):.3f} p>=2c={100*(v>=0.02).mean():.1f}%")
    print(f"\nreversion after |gap|>={100*a.gap:.0f}c (toward/away by window close):")
    for v in ("pm", "k"):
        t, w = rev[v]; n = t + w
        if n:
            print(f"   {v}: toward={t} away={w} -> {100*t/n:.0f}% revert "
                  f"({'mean-reverting' if t>w else 'no reversion / drift'})")
    print("\nNOTE: mids, not executable bid/ask. Any tradeable signal must clear BOTH touches")
    print("  (cross the spread on each venue) + survive fees — EV-gate before any claim.")
    return 0

if __name__ == "__main__":
    import sys; sys.exit(main())
