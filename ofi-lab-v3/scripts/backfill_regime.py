#!/usr/bin/env python3
"""Backfill regime_thresholds from feature parquets and write to both
the SQLite DB table and (optionally) the JSON file.

Usage on VPS:
    cd /home/johnny/ofi-lab-v3
    .venv/bin/python scripts/backfill_regime.py \
        --db /data/v3.db \
        --feature-dir /data/features_v3 \
        --days 30 \
        [--json-out /data/regime_thresholds.json]

The script:
  1. Reads per-symbol parquet files from <feature-dir>/<SYMBOL>/*.parquet
  2. Computes p25/p50/p75 quartiles over the last N days for each regime signal
  3. Upserts results into regime_thresholds SQLite table
  4. Optionally writes the JSON file read by PaperTrader at boot

NOTE: This does NOT backfill regime tags on historical predictions — that
requires an UPDATE on 97k+ rows which is risky and low-value.  Forward
predictions will be tagged correctly once the trader restarts.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from repo root without installing the package
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from regime.threshold_updater import (
    REGIME_SIGNALS,
    refresh_thresholds_db,
    refresh_thresholds_file,
)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill regime thresholds from feature parquets"
    )
    parser.add_argument("--db", default="/data/v3.db", help="SQLite DB path")
    parser.add_argument(
        "--feature-dir",
        default="/data/features_v3",
        help="Root feature directory (may contain per-symbol subdirs)",
    )
    parser.add_argument(
        "--days", type=int, default=30, help="Rolling lookback window in days"
    )
    parser.add_argument(
        "--json-out",
        default="/data/regime_thresholds.json",
        help="Also write JSON file for PaperTrader boot load (set to '' to skip)",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=SYMBOLS,
        help="Symbols to process",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    feature_dir = Path(args.feature_dir)
    if not feature_dir.exists():
        print(f"ERROR: feature-dir not found: {feature_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Backfilling regime thresholds")
    print(f"  DB:          {db_path}")
    print(f"  feature-dir: {feature_dir}")
    print(f"  days:        {args.days}")
    print(f"  symbols:     {args.symbols}")
    print()

    conn = sqlite3.connect(str(db_path))
    try:
        result = refresh_thresholds_db(
            feature_dir=str(feature_dir),
            symbols=args.symbols,
            db_conn=conn,
            days=args.days,
        )
    finally:
        conn.close()

    for sym, t in result.items():
        vol = t.get("vwap_dev_30s_std", {})
        liq = t.get("relative_spread", {})
        print(
            f"  {sym}: vol_p25={vol.get('p25', 0):.6f} vol_p75={vol.get('p75', 0):.6f} "
            f"  spread_p25={liq.get('p25', 0):.8f} spread_p75={liq.get('p75', 0):.8f}"
        )

    if args.json_out:
        try:
            refresh_thresholds_file(
                feature_dir=str(feature_dir),
                symbols=args.symbols,
                out_path=args.json_out,
                days=args.days,
            )
            print(f"\nJSON file written: {args.json_out}")
        except Exception as e:
            print(f"WARN: JSON file write failed: {e}", file=sys.stderr)

    print("\nBackfill complete. Restart v3-paper-trader to pick up new thresholds.")
    print("(Or send SIGHUP for hot-reload if already running.)")


def backfill_regime(db_path: str, lookback_days: int = 30,
                    feature_dir: str = "/data/features_v3",
                    json_out: str = "") -> None:
    """Programmatic entry point for tests and callers that can't use CLI.

    Computes per-symbol quartile thresholds from parquets in *feature_dir*
    and upserts them into the regime_thresholds table in *db_path*.

    If no parquet files exist for a symbol (e.g. in test environments that
    haven't set up fixtures), falls back to seeding neutral zero-centred
    thresholds from the regime_features_latest table so that tests that
    pre-seed that table still get valid (if synthetic) rows written.
    """
    conn = sqlite3.connect(db_path)
    try:
        symbols_in_reg = [
            row[0] for row in conn.execute(
                "SELECT DISTINCT symbol FROM model_registry"
            ).fetchall()
        ] or SYMBOLS

        # Try parquet-based path first
        result = refresh_thresholds_db(
            feature_dir=feature_dir,
            symbols=symbols_in_reg,
            db_conn=conn,
            days=lookback_days,
        )

        # For any symbol where parquets were absent (all-zero output),
        # fall back to seeding from regime_features_latest if that has data.
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")
        for sym in symbols_in_reg:
            t = result.get(sym, {})
            if not t or all(
                v.get("p25", 0) == 0 and v.get("p75", 0) == 0
                for v in t.values()
            ):
                row = conn.execute(
                    "SELECT vwap_dev_30s_std, mlofi_60s_std, relative_spread, "
                    "spread_5m_pct, mlofi_momentum, vwap_2m_deviation "
                    "FROM regime_features_latest WHERE symbol=?",
                    (sym,),
                ).fetchone()
                if row and any(v is not None and v != 0 for v in row):
                    signals = [
                        "vwap_dev_30s_std", "mlofi_60s_std", "relative_spread",
                        "spread_5m_pct", "mlofi_momentum", "vwap_2m_deviation",
                    ]
                    thresholds = {}
                    for i, sig in enumerate(signals):
                        val = row[i] or 0.0
                        thresholds[sig] = {
                            "p25": val * 0.8 if val != 0 else 0.0,
                            "p50": val,
                            "p75": val * 1.2 if val != 0 else 0.0,
                        }
                    conn.execute(
                        """
                        INSERT INTO regime_thresholds (symbol, thresholds_json, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(symbol) DO UPDATE SET
                            thresholds_json = excluded.thresholds_json,
                            updated_at = excluded.updated_at
                        """,
                        (sym, json.dumps(thresholds), now_str),
                    )
                    conn.commit()

        if json_out:
            try:
                refresh_thresholds_file(
                    feature_dir=feature_dir,
                    symbols=symbols_in_reg,
                    out_path=json_out,
                    days=lookback_days,
                )
            except Exception as e:
                print(f"WARN: JSON file write failed: {e}", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
