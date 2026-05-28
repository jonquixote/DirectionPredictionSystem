#!/usr/bin/env python3
"""
retention_cron.py — daily DB retention sweep for ofi-lab-v3.

Prunes time-bounded tables that grow without bound.  Intentionally leaves
predictions and paper_trades untouched (highest-value raw evidence layer).

Tables pruned:
  decay_evaluations   ts TEXT (ISO-8601)   keep 14 days
  model_tier_score    ts TEXT (ISO-8601)   keep 30 days
  decision_traces     ts TEXT (ISO-8601)   keep 30 days
  analysis_cache      computed_at_ms INT   keep 24 hours

Usage:
  python -m scripts.retention_cron            # dry-run (default)
  python -m scripts.retention_cron --apply    # live DELETE
  python -m scripts.retention_cron --apply --vacuum   # + VACUUM on 1st of month

Env vars (highest-priority wins):
  V3_DB_PATH         — path recognised by the rest of the ofi-lab-v3 codebase
  STORAGE_DB_PATH    — fallback name specified in the T1-ops brief
Default: /data/v3.db
"""

import argparse
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Logging — structured, timestamped, goes to stdout (systemd captures it).
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logging.Formatter.converter = time.gmtime   # UTC timestamps in log lines
log = logging.getLogger("retention_cron")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_cutoff(days: int) -> str:
    """Return UTC ISO-8601 string N days in the past, matching the stored format."""
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _db_path() -> str:
    return (
        os.environ.get("V3_DB_PATH")
        or os.environ.get("STORAGE_DB_PATH")
        or "/data/v3.db"
    )


def _page_bytes(conn: sqlite3.Connection) -> int:
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    page_size  = conn.execute("PRAGMA page_size").fetchone()[0]
    return page_count * page_size


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def run(dry_run: bool, do_vacuum: bool) -> int:
    """
    Execute the retention sweep.

    Returns the total number of rows deleted (0 in dry-run mode).
    Raises sqlite3.Error on any DB error (causes non-zero exit).
    """
    db = _db_path()
    log.info("retention_cron starting  db=%s  dry_run=%s  vacuum_flag=%s",
             db, dry_run, do_vacuum)

    now_ms = int(time.time() * 1000)

    # Pre-compute cutoffs ────────────────────────────────────────────────────
    cutoff_14d_iso = _iso_cutoff(14)
    cutoff_30d_iso = _iso_cutoff(30)
    cutoff_24h_ms  = now_ms - 86_400_000

    log.info("cutoffs: 14d=%s  30d=%s  24h_ms=%d",
             cutoff_14d_iso, cutoff_30d_iso, cutoff_24h_ms)

    # Table specs: (table_name, ts_column, operator, cutoff_value, label)
    # Using a tuple so we can share count + delete logic cleanly.
    specs = [
        ("decay_evaluations", "ts",            "<",  cutoff_14d_iso, "14d"),
        ("model_tier_score",  "ts",            "<",  cutoff_30d_iso, "30d"),
        ("decision_traces",   "ts",            "<",  cutoff_30d_iso, "30d"),
        ("analysis_cache",    "computed_at_ms","<",  cutoff_24h_ms,  "24h"),
    ]

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")

    total_deleted = 0

    try:
        with conn:                          # single transaction for all DELETEs
            for table, col, op, cutoff, label in specs:
                # COUNT first — always executed so we have visibility in dry-run.
                where = f"{col} {op} ?"
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {where}", (cutoff,)
                ).fetchone()[0]
                log.info("%-22s  eligible to prune: %7d  (cutoff=%s, retention=%s)",
                         table, count, cutoff, label)

                if dry_run:
                    log.info("%-22s  DRY RUN — skipping DELETE", table)
                    continue

                conn.execute(f"DELETE FROM {table} WHERE {where}", (cutoff,))
                log.info("%-22s  deleted: %7d rows", table, count)
                total_deleted += count

        if dry_run:
            log.info("dry-run complete — no rows deleted")
            return 0

        log.info("transaction committed — total rows deleted: %d", total_deleted)

        # VACUUM ─────────────────────────────────────────────────────────────
        today = datetime.now(timezone.utc)
        is_first_of_month = (today.day == 1)

        if do_vacuum and is_first_of_month and total_deleted > 0:
            log.info("VACUUM triggered (1st of month, %d rows deleted)", total_deleted)
            before = _page_bytes(conn)
            conn.execute("VACUUM")
            after  = _page_bytes(conn)
            saved  = before - after
            log.info("VACUUM complete  before=%d bytes  after=%d bytes  reclaimed=%d bytes (%.1f MB)",
                     before, after, saved, saved / 1_048_576)
        elif do_vacuum and not is_first_of_month:
            log.info("--vacuum set but today is not 1st of month (day=%d) — skipping", today.day)
        elif do_vacuum and total_deleted == 0:
            log.info("--vacuum set but no rows were deleted — skipping")

    finally:
        conn.close()

    return total_deleted


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="ofi-lab-v3 DB retention sweep — prunes bounded tables."
    )
    p.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually execute DELETE statements. Default is dry-run.",
    )
    p.add_argument(
        "--vacuum",
        action="store_true",
        default=False,
        help="Run VACUUM after deletes on the 1st of the month when rows were removed.",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    dry_run = not args.apply

    if dry_run:
        log.info("mode=DRY-RUN  (pass --apply to execute deletes)")
    else:
        log.info("mode=APPLY")

    try:
        total = run(dry_run=dry_run, do_vacuum=args.vacuum)
    except sqlite3.Error as exc:
        log.error("DB error: %s", exc)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        log.error("Unexpected error: %s", exc)
        sys.exit(2)

    log.info("retention_cron finished  total_deleted=%d", total)


if __name__ == "__main__":
    main()
