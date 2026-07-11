#!/usr/bin/env python3
"""Track 3 — Kalshi maker/spread-capture simulation (offline replay).

Replays logged Kalshi 15m up/down books (archives + hot db) and simulates a
symmetric maker: quote BOTH sides of YES at mid +/- offset (or join-the-touch),
size = 1 contract per side, requote every snapshot, inventory-capped.

REGISTERED FILL MODEL (conservative, per docs/plans/4track/track3-maker-sim.md):
  Our resting quote fills ONLY when the opposing touch CROSSES it between
  consecutive snapshots (trade-through) — not when it merely touches:
    bid at b fills iff next yes_ask <  b   (ask traded strictly through us)
    ask at a fills iff next yes_bid >  a   (bid traded strictly through us)
  10s snapshots can't see queue position; strict crossing is the defensible
  lower bound on fills. Fill price = our quote. Maker fee 0.0175*e*(1-e).
  Terminal inventory settles at the venue result (windows.kalshi_result,
  Track 1 settlement backfill): yes -> $1/contract, no -> $0.

Gaps: if the next snapshot is >60s away, quotes are treated as cancelled
(no fill check) — models a live system pulling quotes on feed loss.

Adverse-selection flag: mid moves >=2c against the fill side within 60s.

Sweep: offset in {join, 1c, 1.5c} x inventory cap in {5, 10, 20} x 7 coins.
Stress cut: top-decile |spot_close-spot_open| (bps) windows reported separately.

Usage:
  maker_sim.py --db /data/kalshi_fade.db --archive-dir /data/archives [--out DIR]
  maker_sim.py --selftest
"""
from __future__ import annotations
import argparse, glob, gzip, json, math, os, sqlite3, sys
from collections import defaultdict
from datetime import datetime, timezone

WINDOW_SECS = 900
GAP_CANCEL_SECS = 60
ADVERSE_SECS = 60
ADVERSE_TICKS = 0.02
MAKER_FEE_RATE = 0.0175
OFFSETS = [("join", None), ("1c", 0.01), ("1.5c", 0.015)]
CAPS = [5, 10, 20]


def fee(e: float) -> float:
    return MAKER_FEE_RATE * e * (1.0 - e)


def parse_book(raw: str):
    """-> (yes_bid, yes_ask) or None if not two-sided."""
    try:
        b = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    ob = b.get("orderbook_fp", b)
    yd, nd = ob.get("yes_dollars"), ob.get("no_dollars")
    if not yd or not nd:
        return None
    yes_bid = max(float(p) for p, _ in yd)
    yes_ask = 1.0 - max(float(p) for p, _ in nd)
    if not (0.0 < yes_bid < 1.0 and 0.0 < yes_ask < 1.0) or yes_ask <= yes_bid - 1e-9:
        return None
    return yes_bid, yes_ask


def load_windows(db_path: str):
    """settled windows: (symbol, boundary_ts) -> (result, dev_bps_of_window)."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out = {}
    for sym, bts, res, so, sc in db.execute(
            "SELECT symbol, boundary_ts, kalshi_result, spot_open, spot_close "
            "FROM windows WHERE settled=1 AND kalshi_result IN ('yes','no')"):
        move_bps = abs(sc - so) / so * 1e4 if (so and sc) else 0.0
        out[(sym, bts)] = (res, move_bps)
    db.close()
    return out


def iter_books(db_path: str, archive_dir: str):
    """Yield (symbol, boundary_ts, ts, raw_book) from archives then hot db.

    Archives are watermark-incremental and chronological; the hot db holds rows
    past the archive watermark. Dedup on (symbol, ts).
    """
    seen_max_ts = 0
    for path in sorted(glob.glob(os.path.join(archive_dir, "kalshi_books_*.jsonl.gz"))):
        with gzip.open(path, "rt", encoding="utf-8") as gz:
            for line in gz:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                seen_max_ts = max(seen_max_ts, r["ts"])
                yield r["symbol"], r["boundary_ts"], r["ts"], r["book"]
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = db.execute(
        "SELECT symbol, boundary_ts, ts, orderbook_json FROM obs "
        "WHERE ts > ? AND orderbook_json IS NOT NULL ORDER BY ts", (seen_max_ts,))
    while True:
        rows = cur.fetchmany(5000)
        if not rows:
            break
        for sym, bts, ts, ob in rows:
            yield sym, bts, ts, ob
    db.close()


def build_window_series(db_path: str, archive_dir: str, windows: dict):
    """(symbol, boundary_ts) -> sorted [(ts, yes_bid, yes_ask)], settled only."""
    series = defaultdict(dict)  # dedup by ts within window
    n_raw = n_bad = 0
    for sym, bts, ts, raw in iter_books(db_path, archive_dir):
        if (sym, bts) not in windows:
            continue
        if not (bts <= ts <= bts + WINDOW_SECS):
            continue
        n_raw += 1
        pb = parse_book(raw)
        if pb is None:
            n_bad += 1
            continue
        series[(sym, bts)][ts] = pb
    out = {k: sorted((t, b, a) for t, (b, a) in v.items()) for k, v in series.items()}
    print(f"loaded books: usable={n_raw - n_bad} one-sided/bad={n_bad} "
          f"windows_with_books={len(out)}", flush=True)
    return out


def replay_window(snaps, result: str, offset, cap: int, max_spread=None):
    """Simulate one window. -> dict(pnl, n_fills, n_adverse, max_abs_inv).

    max_spread: if set, only quote when the observed book spread <= this
    (diagnostic — a real maker would not price off the mid of a 50c-wide book).
    """
    inv, cash = 0, 0.0
    fills = []  # (idx, side, mid_at_fill_snapshot)
    payout = 1.0 if result == "yes" else 0.0
    n = len(snaps)
    for k in range(n - 1):
        ts0, bid0, ask0 = snaps[k]
        ts1, bid1, ask1 = snaps[k + 1]
        if ts1 - ts0 > GAP_CANCEL_SECS:
            continue
        if max_spread is not None and ask0 - bid0 > max_spread + 1e-9:
            continue
        mid0 = (bid0 + ask0) / 2.0
        if offset is None:  # join-the-touch
            qb, qa = round(bid0, 2), round(ask0, 2)
        else:
            qb = math.floor((mid0 - offset) * 100 + 1e-9) / 100.0
            qa = math.ceil((mid0 + offset) * 100 - 1e-9) / 100.0
        if not (0.01 <= qb <= 0.99 and 0.01 <= qa <= 0.99 and qb < qa):
            continue
        quote_bid = inv + 1 <= cap
        quote_ask = inv - 1 >= -cap
        # registered fill rule: strict trade-through at the NEXT snapshot.
        # adverse reference = mid at the FILL snapshot (post-fill drift);
        # measuring from mid0 is tautological (the fill condition already
        # implies mid moved >= offset+1c against by the fill snapshot).
        mid1 = (bid1 + ask1) / 2.0
        if quote_bid and ask1 < qb - 1e-9:
            cash -= qb + fee(qb)
            inv += 1
            fills.append((k + 1, +1, mid1))
        if quote_ask and bid1 > qa + 1e-9:
            cash += qa - fee(qa)
            inv -= 1
            fills.append((k + 1, -1, mid1))
    cash += inv * payout
    # adverse selection: mid >=2c against the fill side within 60s of fill
    n_adverse = 0
    for idx, side, mid_f in fills:
        t_f = snaps[idx][0]
        for j in range(idx, n):
            ts_j, b_j, a_j = snaps[j]
            if ts_j - t_f > ADVERSE_SECS:
                break
            mid_j = (b_j + a_j) / 2.0
            if (mid_f - mid_j if side > 0 else mid_j - mid_f) >= ADVERSE_TICKS - 1e-9:
                n_adverse += 1
                break
    max_inv = 0
    inv_run = 0
    for _, side, _ in fills:
        inv_run += side
        max_inv = max(max_inv, abs(inv_run))
    return {"pnl": cash, "n_fills": len(fills), "n_adverse": n_adverse,
            "max_abs_inv": max_inv}


def run_sweep(series, windows, out_dir, max_spread=None):
    # stress decile threshold on |spot move| bps across replayed windows
    moves = sorted(windows[k][1] for k in series)
    p90 = moves[int(0.9 * len(moves))] if moves else float("inf")
    day_of = lambda bts: datetime.fromtimestamp(bts, timezone.utc).strftime("%Y-%m-%d")
    n_days = len({day_of(k[1]) for k in series})
    n_coins = len({k[0] for k in series})
    lines = []
    P = lines.append
    P(f"Track 3 maker sim — {len(series)} windows, {n_coins} coins, {n_days} days "
      f"(UTC), stress decile: |spot move| >= {p90:.1f}bps")
    P(f"fill model: strict trade-through (registered); fee={MAKER_FEE_RATE}*e*(1-e); "
      f"gap-cancel>{GAP_CANCEL_SECS}s; settle at kalshi_result"
      + (f"; DIAGNOSTIC quote-filter: book spread <= {max_spread * 100:.0f}c"
         if max_spread is not None else ""))
    P("")
    P(f"{'offset':>7} {'cap':>4} | {'PnL/day':>9} {'Sharpe_d':>8} {'maxDD':>8} "
      f"{'fills/day':>9} {'adv%':>6} | {'stress PnL/day':>14} {'stress adv%':>11}")
    best = None
    for oname, off in OFFSETS:
        for cap in CAPS:
            daily = defaultdict(float)
            fills = adverse = 0
            s_daily = defaultdict(float)
            s_fills = s_adverse = 0
            for key, snaps in series.items():
                if len(snaps) < 2:
                    continue
                result, mv = windows[key]
                r = replay_window(snaps, result, off, cap, max_spread)
                d = day_of(key[1])
                daily[d] += r["pnl"]
                fills += r["n_fills"]
                adverse += r["n_adverse"]
                if mv >= p90:
                    s_daily[d] += r["pnl"]
                    s_fills += r["n_fills"]
                    s_adverse += r["n_adverse"]
            ds = sorted(daily)
            vals = [daily[d] for d in ds]
            mean = sum(vals) / len(vals) if vals else 0.0
            sd = (sum((v - mean) ** 2 for v in vals) / max(len(vals) - 1, 1)) ** 0.5
            sharpe = mean / sd if sd > 1e-12 else 0.0
            cum = peak = dd = 0.0
            for v in vals:
                cum += v
                peak = max(peak, cum)
                dd = max(dd, peak - cum)
            adv_pct = 100.0 * adverse / fills if fills else 0.0
            s_vals = list(s_daily.values())
            s_mean = sum(s_vals) / len(s_vals) if s_vals else 0.0
            s_adv = 100.0 * s_adverse / s_fills if s_fills else 0.0
            P(f"{oname:>7} {cap:>4} | {mean:>9.2f} {sharpe:>8.2f} {dd:>8.2f} "
              f"{fills / max(len(ds), 1):>9.1f} {adv_pct:>6.1f} | "
              f"{s_mean:>14.2f} {s_adv:>11.1f}")
            cell = {"offset": oname, "cap": cap, "pnl_day": mean, "sharpe_d": sharpe,
                    "max_dd": dd, "fills_day": fills / max(len(ds), 1),
                    "adverse_pct": adv_pct, "stress_pnl_day": s_mean,
                    "stress_adverse_pct": s_adv, "n_days": len(ds)}
            if best is None or cell["pnl_day"] > best["pnl_day"]:
                best = cell
    P("")
    P("=== KILL CRITERIA (pre-registered) ===")
    b = best or {}
    P(f"best cell: offset={b.get('offset')} cap={b.get('cap')} "
      f"PnL/day=${b.get('pnl_day', 0):.2f} (at size=1/side/coin)")
    k1 = b.get("pnl_day", 0) < 30
    k2 = b.get("stress_adverse_pct", 100) > 40
    k3 = b.get("fills_day", 0) / max(n_coins, 1) < 5
    P(f"[{'FAIL' if k1 else 'pass'}] PnL/day >= $30 at realistic capacity "
      f"(size-1 baseline: ${b.get('pnl_day', 0):.2f})")
    P(f"[{'FAIL' if k2 else 'pass'}] stress-decile adverse <= 40% "
      f"({b.get('stress_adverse_pct', 0):.1f}%)")
    P(f"[{'FAIL' if k3 else 'pass'}] fills/day/coin >= 5 at best cell "
      f"({b.get('fills_day', 0) / max(n_coins, 1):.1f})")
    txt = "\n".join(lines)
    print(txt)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        with open(os.path.join(out_dir, f"track3_sweep_{stamp}.txt"), "w") as f:
            f.write(txt + "\n")
    return 0


# ---------------------------------------------------------------- selftest
def selftest():
    ok = True

    def check(name, cond):
        nonlocal ok
        print(f"  [{'ok' if cond else 'FAIL'}] {name}")
        ok = ok and cond

    # 1) ask trades strictly through our bid -> one buy fill at our price
    snaps = [(0, 0.48, 0.52), (10, 0.40, 0.44), (20, 0.40, 0.44)]
    r = replay_window(snaps, "yes", 0.01, 5)
    # mid=0.50, bid=0.49; next ask 0.44 < 0.49 -> fill; then quotes from mid .42
    check("trade-through bid fills", r["n_fills"] >= 1)
    # 2) touch-not-cross does NOT fill
    snaps = [(0, 0.48, 0.52), (10, 0.48, 0.49), (20, 0.48, 0.49)]
    r = replay_window(snaps, "no", 0.01, 5)
    check("touch (ask==bid quote) no fill", r["n_fills"] == 0)
    # 3) inventory cap respected
    snaps = [(i * 10, 0.80 - i * 0.05, 0.84 - i * 0.05) for i in range(12)]
    r = replay_window(snaps, "no", 0.01, 3)
    check("cap=3 respected", r["max_abs_inv"] <= 3)
    # 4) settlement math: forced long into 'yes' profits, into 'no' loses
    snaps = [(0, 0.48, 0.52), (10, 0.30, 0.34), (20, 0.30, 0.34)]
    ry = replay_window(snaps, "yes", 0.01, 5)
    rn = replay_window(snaps, "no", 0.01, 5)
    check("long settles yes>no", ry["pnl"] > rn["pnl"])
    check("yes-settle pnl sign", ry["pnl"] > 0 and rn["pnl"] < 0)
    # 5) gap cancels quotes: huge move across >60s gap must NOT fill
    snaps = [(0, 0.48, 0.52), (120, 0.20, 0.24), (130, 0.20, 0.24)]
    r = replay_window(snaps, "no", 0.01, 5)
    check("gap>60s no fill across gap", r["n_fills"] == 0)
    # 6) fee formula
    check("fee(0.5)=0.004375", abs(fee(0.5) - 0.004375) < 1e-12)
    # 7) parse_book: yes_ask from no_dollars, one-sided rejected
    b = json.dumps({"yes_dollars": [["0.40", "10"], ["0.42", "5"]],
                    "no_dollars": [["0.55", "10"]]})
    pb = parse_book(b)
    check("parse_book bid/ask",
          pb is not None and abs(pb[0] - 0.42) < 1e-9 and abs(pb[1] - 0.45) < 1e-9)
    check("one-sided rejected",
          parse_book(json.dumps({"yes_dollars": [], "no_dollars": [["0.5", "1"]]})) is None)
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--archive-dir")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-spread", type=float, default=None,
                    help="diagnostic: only quote when book spread <= this (e.g. 0.05)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.db or not a.archive_dir:
        ap.error("--db and --archive-dir required (or --selftest)")
    windows = load_windows(a.db)
    print(f"settled windows: {len(windows)}", flush=True)
    series = build_window_series(a.db, a.archive_dir, windows)
    return run_sweep(series, windows, a.out, a.max_spread)


if __name__ == "__main__":
    sys.exit(main())
