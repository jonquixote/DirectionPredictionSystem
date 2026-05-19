#!/usr/bin/env python3
"""Backfill model_overlap from existing predictions rows.

For each (ts_contract_open_ms, symbol, market_window_seconds) group in
predictions, aggregates all model scores and writes one model_overlap row.
Idempotent — uses INSERT OR IGNORE so re-running is safe.

Usage:
    python scripts/backfill_model_overlap.py --db /data/v3.db
    python scripts/backfill_model_overlap.py --db /data/v3.db --dry-run
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill model_overlap from predictions")
    p.add_argument("--db", default="/data/v3.db", help="Path to v3.db")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be inserted without writing")
    return p.parse_args()


def backfill(db_path: str, dry_run: bool = False) -> int:
    """Return number of rows inserted."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")

    # Pull all predictions that have a calibrated proba (resolved or not —
    # overlap is about scoring agreement, not resolution).
    rows = conn.execute(
        "SELECT ts_contract_open_ms, symbol, market_window_seconds,"
        "       model_name, pred_proba_calibrated, pred_direction,"
        "       registry_load_generation"
        " FROM predictions"
        " WHERE pred_proba_calibrated IS NOT NULL"
        " ORDER BY ts_contract_open_ms, symbol, market_window_seconds, model_name"
    ).fetchall()

    if not rows:
        print("No predictions found — nothing to backfill.", file=sys.stderr)
        return 0

    # Group by (ts_contract_open_ms, symbol, market_window_seconds)
    groups: dict = defaultdict(list)
    gen_for_group: dict = {}
    for r in rows:
        key = (r["ts_contract_open_ms"], r["symbol"], r["market_window_seconds"])
        groups[key].append(r)
        # Use the max generation seen for a group (most recent calibration)
        gen_for_group[key] = max(
            gen_for_group.get(key, 0),
            r["registry_load_generation"] or 0,
        )

    inserted = 0
    for key, group_rows in groups.items():
        ts, symbol, window = key
        models = [r["model_name"] for r in group_rows]
        directions = {r["model_name"]: r["pred_direction"] for r in group_rows}
        confidences = {
            r["model_name"]: float(r["pred_proba_calibrated"])
            for r in group_rows
        }

        dir_set = set(directions.values())
        consensus = 1 if len(dir_set) == 1 else 0
        consensus_direction = next(iter(dir_set)) if consensus else None

        total_w = len(group_rows) or 1
        weighted = sum(confidences.values()) / total_w

        gen = gen_for_group[key]

        if dry_run:
            print(
                f"WOULD INSERT: ts={ts} sym={symbol} win={window} "
                f"models={len(models)} consensus={consensus} wc={weighted:.4f}"
            )
            inserted += 1
            continue

        conn.execute(
            "INSERT OR IGNORE INTO model_overlap ("
            " ts_contract_open_ms, symbol, market_window_seconds,"
            " models_scored_json, directions_json, confidences_json,"
            " consensus, consensus_direction, weighted_confidence,"
            " registry_load_generation"
            ") VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                ts, symbol, window,
                json.dumps(models),
                json.dumps(directions),
                json.dumps(confidences),
                consensus, consensus_direction, weighted,
                gen,
            ),
        )
        inserted += 1

    if not dry_run:
        conn.commit()
    conn.close()
    return inserted


def main() -> None:
    args = _parse_args()
    db_path = args.db
    if not Path(db_path).exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    n = backfill(db_path, dry_run=args.dry_run)
    label = "WOULD INSERT" if args.dry_run else "Inserted"
    print(f"{label} {n} model_overlap rows.")


if __name__ == "__main__":
    main()
