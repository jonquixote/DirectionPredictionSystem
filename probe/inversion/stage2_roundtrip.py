#!/usr/bin/env python3
"""Inversion Stage 2 — round-trip backtest on real Polymarket prints.

Registered rule: anchor = window open (binary fair ~ 0.5 at open). Rest a BUY at
(0.5 - delta) and a SELL at (0.5 + delta) in contract-price terms. A leg fills only
if a real print trades THROUGH the level (<= for buy, >= for sell) — conservative
queue-priority proxy (no book). Round-trip completes when BOTH legs fill within the
window (either order — market-making is side-agnostic).

Pointed at the early-window slice (Stage-1 fair-value handed us the only lead: a faint
positive bias in elapsed deciles 0-1). PRIMARY gate population = windows whose FIRST
leg fills in deciles 0-1. Pooled-window reported alongside as context.

Cost stack per outcome:
  completed round-trip (1 contract/leg): gross = 2*delta ; fees = maker(0.5-d)+maker(0.5+d),
    maker = 0.0175*p*(1-p) ; adverse-selection haircut H (0.003 / 0.0069, both reported),
    applied once per round-trip on $1 notional. net = 2d - fees - H.
  incomplete (one leg filled, carried to resolution): hold the contract to 0/1.
    long  buy@(0.5-d):  pnl = outcome_token - (0.5-d) - maker(0.5-d)
    short sell@(0.5+d): pnl = (0.5+d) - outcome_token - maker(0.5+d)
  per-window-attempted EV denominator = windows where >=1 leg filled in the slice.

Gate (registered): net EV/window bootstrap 95% LB > 0 on n>=2000 at H=0.003 -> survive.

Bounded memory: stream windows, accumulate per-(delta, haircut) PnL arrays only for
the gated slice (n ~ tens of thousands max).
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict

import numpy as np

SYM = ["btc", "eth", "sol", "xrp"]
DURS = [5, 15]
DELTAS = [0.01, 0.02, 0.03]
HAIRCUTS = [0.003, 0.0069]
EARLY_DECILES = {0, 1}


def maker_fee(p):
    return 0.0175 * p * (1.0 - p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-db", default="/data/probe_track_a.db")
    args = ap.parse_args()
    db = sqlite3.connect(f"file:{args.probe_db}?mode=ro", uri=True)

    # per (delta, haircut, slice) -> list of per-window PnL (slice in {early, all})
    pnl = defaultdict(list)
    n_win = defaultdict(int)        # windows with >=1 leg fill, per (delta, slice)
    n_complete = defaultdict(int)   # completed round-trips, per (delta, slice)

    for pfx in SYM:
        for dur in DURS:
            win_s = dur * 60
            cur = db.execute(
                "SELECT t.ts, t.price, t.outcome, m.boundary_ts, m.outcome_up "
                "FROM trades t JOIN markets m ON t.condition_id=m.condition_id "
                "WHERE m.symbol=? AND m.duration_min=? AND m.outcome_up IN (0.0,1.0) "
                "AND t.ts>=m.boundary_ts AND t.ts < m.boundary_ts + m.duration_min*60 "
                "ORDER BY m.boundary_ts, t.ts", (pfx, dur))
            cur_b = None
            seq = []   # (elapsed_decile, p_up) for current window
            outcome = None

            def flush(seq, outcome, win_s):
                if not seq:
                    return
                # p_up sequence + first-touch deciles for each level
                for d in DELTAS:
                    lo = 0.5 - d; hi = 0.5 + d
                    buy_i = sell_i = -1
                    for k, (dec, p) in enumerate(seq):
                        if buy_i < 0 and p <= lo:
                            buy_i = k
                        if sell_i < 0 and p >= hi:
                            sell_i = k
                        if buy_i >= 0 and sell_i >= 0:
                            break
                    if buy_i < 0 and sell_i < 0:
                        continue  # no leg filled, not an attempted window
                    first_k = min([x for x in (buy_i, sell_i) if x >= 0])
                    first_dec = seq[first_k][0]
                    slices = ["all"] + (["early"] if first_dec in EARLY_DECILES else [])
                    for H in HAIRCUTS:
                        if buy_i >= 0 and sell_i >= 0:
                            net = 2 * d - (maker_fee(lo) + maker_fee(hi)) - H
                            for s in slices:
                                pnl[(d, H, s)].append(net)
                                if H == HAIRCUTS[0]:
                                    n_complete[(d, s)] += 1
                        elif buy_i >= 0:
                            net = outcome - lo - maker_fee(lo)  # hold long to resolution
                            for s in slices:
                                pnl[(d, H, s)].append(net)
                        else:
                            net = hi - outcome - maker_fee(hi)  # hold short
                            for s in slices:
                                pnl[(d, H, s)].append(net)

            for r in cur:
                ts, price, otok, bts, oup = r
                if bts != cur_b:
                    flush(seq, outcome, win_s)
                    cur_b = bts; seq = []; outcome = oup
                dec = min(int((ts - bts) / win_s * 10), 9)
                p_up = price if otok == "Up" else 1.0 - price
                seq.append((dec, p_up))
            flush(seq, outcome, win_s)

    def boot_lb(a, n_boot=10000):
        a = np.asarray(a)
        if len(a) < 2:
            return float("nan")
        rng = np.random.default_rng(42)
        m = np.array([a[rng.integers(0, len(a), len(a))].mean() for _ in range(n_boot)])
        return float(np.percentile(m, 2.5))

    print("=" * 84)
    print("INVERSION STAGE 2 — round-trip backtest (real Polymarket prints) — verbatim")
    print("=" * 84)
    print("gate: net EV/window bootstrap 95% LB > 0 on n>=2000 at haircut=0.003 (primary=early slice)")
    for s in ["early", "all"]:
        print(f"\n--- slice: {s} (first leg fills in {'deciles 0-1' if s=='early' else 'any decile'}) ---")
        for d in DELTAS:
            for H in HAIRCUTS:
                a = pnl[(d, H, s)]
                n = len(a)
                if n == 0:
                    print(f"  d={d:.2f} H={H:.4f}: n=0")
                    continue
                ev = float(np.mean(a)); lb = boot_lb(a)
                comp = n_complete[(d, s)]
                print(f"  d={d*100:.0f}c H={H*100:.2f}%: n={n:>7} EV/win=${ev:+.4f} "
                      f"bootLB=${lb:+.4f} complete_rt={comp} ({100*comp/n:.0f}%)")

    # verdict on primary slice, d that maximizes LB at H=0.003
    print("\n" + "=" * 84)
    best = None
    for d in DELTAS:
        a = pnl[(d, 0.003, "early")]
        if len(a) >= 2000:
            lb = boot_lb(a)
            if best is None or lb > best[1]:
                best = (d, lb, float(np.mean(a)), len(a))
    if best is None:
        print("VERDICT: insufficient n (no early-slice delta reached n>=2000) — KILL (insufficient-n)")
    else:
        d, lb, ev, n = best
        survive = lb > 0 and n >= 2000
        print(f"best early-slice delta={d*100:.0f}c: n={n} EV=${ev:+.4f} bootLB=${lb:+.4f}")
        print("VERDICT:", "SURVIVES — crack of light, follow it" if survive
              else "KILL — earned on the most-favorable ground (early slice, best delta)")


if __name__ == "__main__":
    main()
