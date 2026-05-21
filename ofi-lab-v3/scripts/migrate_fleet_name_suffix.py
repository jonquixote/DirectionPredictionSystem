#!/usr/bin/env python3
"""One-shot migration: append `_20260427` suffix to the old (2026-04-27)
fleet's model names across all tables that store model_name as text.

Context: Bug C in scripts/register_model.py omitted train_window_end from the
constructed name, so a NEW fleet's INSERT OR REPLACE would clobber the OLD
fleet's row when they shared (horizon, symbol, train_days). After fixing the
name format to include _YYYYMMDD, the EXISTING old-fleet rows still need the
suffix applied retroactively or the new fleet's training will collide on
re-register.

This script:
  1. Connects to the v3 sqlite DB (default /data/v3.db).
  2. In a single transaction:
     - Builds {old_name: new_name} from model_registry WHERE
       fleet_version='2026-04-27' AND is_baseline=0 AND name NOT LIKE '%_20260427'.
     - UPDATEs every table storing model_name as text.
  3. Prints before/after counts per table.
  4. --dry-run rolls back the transaction at the end.

Tables migrated (all with TEXT model_name columns):
  - model_registry (PRIMARY KEY name)
  - predictions (FK-like via model_name)
  - paper_trades
  - model_audit
  - calibration_outcomes
  - decay_metrics
  - decay_evaluations
  - calibration_bins
  - calibration_summary (PRIMARY KEY model_name)
  - calibration_map (PRIMARY KEY model_name)
  - model_selection.selected_model_name

NOT migrated (JSON values — would need json_set surgery; flagged separately):
  - committee_weights.weights_json (JSON dict keyed by model_name)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

SUFFIX = "_20260427"
FLEET_VERSION = "2026-04-27"

# (table, column) pairs we will UPDATE.
TABLES_WITH_MODEL_NAME = [
    ("model_registry", "name"),
    ("predictions", "model_name"),
    ("paper_trades", "model_name"),
    ("model_audit", "model_name"),
    ("calibration_outcomes", "model_name"),
    ("decay_metrics", "model_name"),
    ("decay_evaluations", "model_name"),
    ("calibration_bins", "model_name"),
    ("calibration_summary", "model_name"),
    ("calibration_map", "model_name"),
    ("model_selection", "selected_model_name"),
]


def _count(conn: sqlite3.Connection, table: str, col: str, name: str) -> int:
    """Return number of rows where col == name. Returns 0 if table is missing."""
    try:
        cur = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (name,))
        return int(cur.fetchone()[0])
    except sqlite3.OperationalError as e:
        # Table doesn't exist on this DB — treat as zero
        print(f"  WARN {table}.{col}: {e}", file=sys.stderr)
        return 0


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def build_rename_map(conn: sqlite3.Connection) -> dict[str, str]:
    """Find old-fleet model_registry rows missing the suffix; map each to its new name."""
    rows = conn.execute(
        "SELECT name FROM model_registry "
        "WHERE fleet_version = ? "
        "  AND is_baseline = 0 "
        "  AND name NOT LIKE ?",
        (FLEET_VERSION, f"%{SUFFIX}"),
    ).fetchall()
    return {r[0]: r[0] + SUFFIX for r in rows}


def migrate(conn: sqlite3.Connection, *, dry_run: bool) -> None:
    rename_map = build_rename_map(conn)
    if not rename_map:
        print(
            f"Nothing to do: no model_registry rows with fleet_version={FLEET_VERSION!r} "
            f"and name not ending in {SUFFIX!r}."
        )
        return

    print(f"Found {len(rename_map)} model_registry rows to rename:")
    for old, new in sorted(rename_map.items()):
        print(f"  {old}  ->  {new}")
    print()

    # Pre-flight: a target name MUST NOT already exist as a row distinct from
    # the source — otherwise the UPDATE on a PK or unique-indexed column would
    # collide. (Idempotency lets a row already match `new` only when there is
    # no longer any `old` row to rename, but that case is excluded above.)
    collisions = []
    for old, new in rename_map.items():
        row = conn.execute(
            "SELECT 1 FROM model_registry WHERE name = ?", (new,)
        ).fetchone()
        if row is not None:
            collisions.append((old, new))
    if collisions:
        print("ERROR: target names already exist in model_registry:", file=sys.stderr)
        for old, new in collisions:
            print(f"  {old} -> {new} (target exists)", file=sys.stderr)
        print(
            "Resolve the collision (delete or rename the conflicting row) before "
            "re-running the migration.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    # Snapshot per-table row counts BEFORE for the OLD names.
    print("Pre-migration row counts (old names):")
    before: dict[tuple[str, str], int] = {}
    for table, col in TABLES_WITH_MODEL_NAME:
        if not _table_exists(conn, table):
            print(f"  {table}.{col}: SKIPPED (table missing)")
            before[(table, col)] = 0
            continue
        total = 0
        for old in rename_map:
            total += _count(conn, table, col, old)
        before[(table, col)] = total
        print(f"  {table}.{col}: {total}")

    # Apply renames inside one explicit transaction.
    conn.execute("BEGIN")
    try:
        for table, col in TABLES_WITH_MODEL_NAME:
            if not _table_exists(conn, table):
                continue
            for old, new in rename_map.items():
                conn.execute(
                    f"UPDATE {table} SET {col} = ? WHERE {col} = ?",
                    (new, old),
                )

        # Snapshot AFTER counts (under the same transaction so we see staged state).
        print()
        print("Post-migration row counts (new names, pre-commit):")
        for table, col in TABLES_WITH_MODEL_NAME:
            if not _table_exists(conn, table):
                continue
            total = 0
            for old, new in rename_map.items():
                total += _count(conn, table, col, new)
            delta = total - before[(table, col)]
            print(f"  {table}.{col}: {total} (delta={delta:+d})")

        if dry_run:
            print()
            print("--dry-run: rolling back.")
            conn.execute("ROLLBACK")
        else:
            conn.execute("COMMIT")
            print()
            print("Migration committed.")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def main() -> int:
    p = argparse.ArgumentParser(
        description="Append _20260427 suffix to 2026-04-27 fleet model names "
        "across all tables (idempotent)."
    )
    p.add_argument("--db", default="/data/v3.db", help="SQLite database path")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Apply updates in a transaction, then ROLLBACK — for previewing impact.",
    )
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        # Foreign-key-style links between tables are by string match only, not
        # enforced by SQLite FKs; safe to UPDATE in a single transaction.
        conn.execute("PRAGMA foreign_keys = OFF")
        migrate(conn, dry_run=args.dry_run)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
