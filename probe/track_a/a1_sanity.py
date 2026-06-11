#!/usr/bin/env python3
"""A1 sanity gates — run BEFORE any wallet ranking (pre-registered).

Gates (probe/PREREGISTRATION.md §A1):
  1. Daily volume order-of-magnitude report (eyeball vs public figures).
  2. >= 95% of trades joinable to a resolved outcome.
  3. Bybit-window-direction vs market outcomePrices agreement >= 98% on
     non-flat windows. BELOW 98% = HARD STOP, report to owner.
  4. Clip breakdown by symbol x window (depth-cap bias quantification).

Bybit side of gate 3 comes from /data/v3.db predictions rows: price_at_open/
price_at_close are Bybit L2 mid at the same window boundaries our paper trader
scored (ts_contract_open_ms == market window start, market_window_seconds ==
duration). Direction: close>open=up, close<open=down, equal=flat (excluded).

Usage: python3 a1_sanity.py --probe-db /data/probe_track_a.db --v3-db /data/v3.db
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-db", required=True)
    ap.add_argument("--v3-db", required=True)
    args = ap.parse_args()

    db = sqlite3.connect(args.probe_db)
    db.row_factory = sqlite3.Row

    print("=" * 72)
    print("A1 SANITY GATES — evidence verbatim")
    print("=" * 72)

    # ── completeness ────────────────────────────────────────────────
    n_mk, n_found, n_done, n_clip = db.execute(
        "SELECT COUNT(*), SUM(found), SUM(trades_done), SUM(clipped) FROM markets"
    ).fetchone()
    n_tr = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    print(f"markets: {n_mk} enumerated, {n_found} found, {n_done} harvested, "
          f"{n_clip} clipped | trade rows: {n_tr}")

    # ── gate 1: daily volume ────────────────────────────────────────
    print("\nGATE 1 — daily notional (USDC, size*price summed; last 10 days):")
    for r in db.execute("""
        SELECT date(ts, 'unixepoch') d, COUNT(*) n,
               CAST(SUM(size*price) AS INT) notional
        FROM trades GROUP BY 1 ORDER BY 1 DESC LIMIT 10"""):
        print(f"  {r['d']}  trades={r['n']:>8}  notional=${r['notional']:>12,}")

    # ── gate 2: outcome joinability ─────────────────────────────────
    n_join = db.execute("""
        SELECT COUNT(*) FROM trades t JOIN markets m
        ON t.condition_id = m.condition_id
        WHERE m.outcome_up IS NOT NULL""").fetchone()[0]
    pct = 100 * n_join / n_tr if n_tr else 0
    g2 = "PASS" if pct >= 95 else "FAIL"
    print(f"\nGATE 2 — outcome join: {n_join}/{n_tr} = {pct:.2f}%  -> {g2} (need >=95%)")

    # ── gate 3: Bybit vs market resolution agreement ────────────────
    v3 = sqlite3.connect(f"file:{args.v3_db}?mode=ro", uri=True)
    v3.row_factory = sqlite3.Row
    # one Bybit direction per (symbol, window, boundary) — any resolved row
    sym_map = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}
    agree = disagree = no_bybit = flat_skip = 0
    examples = []
    mkts = db.execute("""
        SELECT symbol, duration_min, boundary_ts, outcome_up FROM markets
        WHERE found=1 AND outcome_up IS NOT NULL""").fetchall()
    for m in mkts:
        bybit_sym = sym_map[m["symbol"]]
        open_ms = m["boundary_ts"] * 1000
        win_s = m["duration_min"] * 60
        row = v3.execute("""
            SELECT price_at_open, price_at_close FROM predictions
            WHERE symbol=? AND market_window_seconds=? AND ts_contract_open_ms=?
              AND resolved=1 AND price_at_open IS NOT NULL
              AND price_at_close IS NOT NULL LIMIT 1""",
            (bybit_sym, win_s, open_ms)).fetchone()
        if row is None:
            no_bybit += 1
            continue
        if row["price_at_close"] == row["price_at_open"]:
            flat_skip += 1
            continue
        bybit_up = 1.0 if row["price_at_close"] > row["price_at_open"] else 0.0
        if bybit_up == m["outcome_up"]:
            agree += 1
        else:
            disagree += 1
            if len(examples) < 5:
                examples.append((m["symbol"], m["duration_min"], m["boundary_ts"],
                                 m["outcome_up"], row["price_at_open"],
                                 row["price_at_close"]))
    total = agree + disagree
    apct = 100 * agree / total if total else 0
    g3 = "PASS" if apct >= 98 else "HARD STOP"
    print(f"\nGATE 3 — Bybit vs market resolution (non-flat):")
    print(f"  matched windows: {total} (no Bybit row: {no_bybit}, flat-skipped: {flat_skip})")
    print(f"  agree={agree} disagree={disagree} -> {apct:.3f}%  -> {g3} (need >=98%)")
    for e in examples:
        ts = datetime.fromtimestamp(e[2], tz=timezone.utc).strftime("%m-%d %H:%M")
        print(f"    disagree: {e[0]}-{e[1]}m {ts} market_up={e[3]} "
              f"bybit {e[4]} -> {e[5]}")

    # ── gate 4: clip breakdown ──────────────────────────────────────
    print("\nGATE 4 — clipped markets by symbol x window:")
    print(f"  {'cell':>10} {'markets':>8} {'clipped':>8} {'clip%':>6} {'vol_share%':>10}")
    for r in db.execute("""
        SELECT m.symbol, m.duration_min, COUNT(*) n, SUM(m.clipped) c,
          100.0*SUM(m.clipped)/COUNT(*) cp,
          100.0 * SUM(CASE WHEN m.clipped=1 THEN m.n_trades ELSE 0 END)
                / NULLIF(SUM(m.n_trades),0) vs
        FROM markets m WHERE m.found=1 AND m.trades_done=1
        GROUP BY 1,2 ORDER BY 1,2"""):
        print(f"  {r['symbol']+'-'+str(r['duration_min'])+'m':>10} {r['n']:>8} "
              f"{r['c']:>8} {r['cp']:>6.2f} {r['vs'] or 0:>10.2f}")

    print("\nVERDICT:", "ALL GATES PASS — proceed to A2"
          if (g2 == "PASS" and g3 == "PASS") else f"gate2={g2} gate3={g3} — see above")


if __name__ == "__main__":
    main()
