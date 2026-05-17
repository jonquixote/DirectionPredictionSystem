#!/usr/bin/env python3
"""set_model_filter.py — CLI tool for per-model trade gate configuration.

Updates filter_config_json in model_registry for one or more models.
Writes an audit row to model_audit on each change.

TODO: model_audit write path assumes the table exists (it does per schema.sql).
      If running against an older DB that lacks model_audit, the script logs
      an INFO message and proceeds without auditing.

Exit codes:
  0  — at least one model updated (or --list / --dry-run succeeded)
  1  — no models matched the given --model / --model-pattern
  2  — validation failure
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_confidence_threshold(v: float) -> float:
    if not (0.0 <= v <= 1.0):
        raise ValueError(f"confidence_threshold {v!r} out of range [0.0, 1.0]")
    return v


def _validate_ev_threshold(v: float) -> float:
    if not (-1.0 <= v <= 1.0):
        raise ValueError(f"ev_threshold {v!r} out of range [-1.0, 1.0]")
    return v


def _validate_blackout_hours(raw: str) -> list:
    """Parse comma-separated UTC hours string → sorted deduped list of ints."""
    hours = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            h = int(token)
        except ValueError:
            raise ValueError(f"blackout_hours token {token!r} is not an integer")
        if not (0 <= h <= 23):
            raise ValueError(f"blackout_hours value {h} out of range [0, 23]")
        hours.append(h)
    return sorted(set(hours))


def _validate_warmup_seconds(v: int) -> int:
    if v < 0:
        raise ValueError(f"warmup_seconds {v!r} must be non-negative")
    return v


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _resolve_db_path() -> Path:
    """Resolve DB path: env > /data/v3.db (if writable) > ./v3.db."""
    env_path = os.environ.get("V3_DB_PATH")
    if env_path:
        return Path(env_path)
    candidate = Path("/data/v3.db")
    if candidate.parent.exists() and os.access(candidate.parent, os.W_OK):
        return candidate
    return Path("./v3.db")


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _fetch_models(conn: sqlite3.Connection, model: str | None, pattern: str | None) -> list:
    """Return list of Row objects from model_registry matching criteria."""
    if model is not None:
        rows = conn.execute(
            "SELECT name, symbol, training_horizon_seconds, filter_config_json "
            "FROM model_registry WHERE name = ?",
            (model,),
        ).fetchall()
    else:
        assert pattern is not None, "either model or pattern must be supplied"
        sql_pattern = pattern.replace("*", "%")
        rows = conn.execute(
            "SELECT name, symbol, training_horizon_seconds, filter_config_json "
            "FROM model_registry WHERE name LIKE ?",
            (sql_pattern,),
        ).fetchall()
    return rows


def _write_audit(conn: sqlite3.Connection, model_name: str, before_json: str, after_json: str) -> None:
    detail = json.dumps({"before": before_json, "after": after_json})
    try:
        conn.execute(
            "INSERT INTO model_audit (model_name, action, by_user, detail) "
            "VALUES (?, ?, ?, ?)",
            (model_name, "set_model_filter", "cli", detail),
        )
    except sqlite3.OperationalError as exc:
        # Table may not exist in older schemas — proceed without auditing.
        print(f"[INFO] model_audit write skipped for {model_name}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _build_updates(args: argparse.Namespace) -> dict:
    """Build a dict of keys→values to merge into filter_config_json."""
    updates: dict = {}
    if args.confidence_threshold is not None:
        updates["confidence_threshold"] = args.confidence_threshold
    if args.ev_threshold is not None:
        updates["ev_threshold"] = args.ev_threshold
    if args.blackout_hours is not None:
        updates["blackout_hours"] = args.blackout_hours
    if args.warmup_seconds is not None:
        updates["warmup_seconds"] = args.warmup_seconds
    return updates


def _build_clear_keys(args: argparse.Namespace) -> list:
    if not args.clear_keys:
        return []
    return [k.strip() for k in args.clear_keys.split(",") if k.strip()]


def _merge(current_json: str, updates: dict, clear_keys: list) -> dict:
    """Merge updates into parsed current_json, remove clear_keys, return merged dict."""
    try:
        current = json.loads(current_json or "{}")
    except json.JSONDecodeError:
        current = {}
    merged = dict(current)
    merged.update(updates)
    for k in clear_keys:
        merged.pop(k, None)
    return merged


def _canonical_json(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def _format_change_line(model_name: str, updates: dict, clear_keys: list,
                        before: dict, after: dict, dry_run: bool) -> str:
    """Produce a human-readable change summary line."""
    parts = []
    for key in sorted(set(list(updates.keys()) + clear_keys)):
        old_val = before.get(key, "<absent>")
        new_val = after.get(key, "<removed>")
        if old_val != new_val:
            suffix = "(would-write)" if dry_run else "✓"
            parts.append(f"{key}: {old_val!r} → {new_val!r} {suffix}")
    if not parts:
        return f"[{model_name}] no changes"
    return f"[{model_name}] " + ", ".join(parts)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_list(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT name, symbol, training_horizon_seconds, filter_config_json "
        "FROM model_registry ORDER BY name"
    ).fetchall()
    if not rows:
        print("(no models registered)")
        return 0
    # Header
    print(f"{'model_name':<40} {'symbol':<12} {'horizon_s':<10} filter_config_json")
    print("-" * 100)
    for row in rows:
        fc = row["filter_config_json"] or "{}"
        print(f"{row['name']:<40} {str(row['symbol'] or ''):<12} "
              f"{str(row['training_horizon_seconds'] or ''):<10} {fc}")
    return 0


def cmd_update(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    rows = _fetch_models(conn, getattr(args, "model", None),
                         getattr(args, "model_pattern", None))
    if not rows:
        selector = getattr(args, "model", None) or getattr(args, "model_pattern", None)
        print(f"No models matched: {selector!r}", file=sys.stderr)
        return 1

    updates = _build_updates(args)
    clear_keys = _build_clear_keys(args)
    dry_run = args.dry_run

    changed_count = 0
    for row in rows:
        model_name = row["name"]
        before_json = row["filter_config_json"] or "{}"
        before_dict = json.loads(before_json)
        after_dict = _merge(before_json, updates, clear_keys)
        after_json = _canonical_json(after_dict)

        line = _format_change_line(model_name, updates, clear_keys,
                                   before_dict, after_dict, dry_run)
        print(line)

        if dry_run:
            continue

        if after_dict != before_dict:
            conn.execute(
                "UPDATE model_registry SET filter_config_json = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE name = ?",
                (after_json, model_name),
            )
            _write_audit(conn, model_name, before_json, after_json)
            changed_count += 1

    if dry_run:
        print(f"Dry-run: would update {len(rows)} model(s). No writes performed.")
        return 0

    conn.commit()
    print(f"Updated {changed_count} model(s).")
    return 0


# ---------------------------------------------------------------------------
# Argument parsing + validation
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="set_model_filter.py",
        description="Configure per-model trade gate thresholds in model_registry.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Set confidence + EV threshold for all h60 models
  python3 scripts/set_model_filter.py --model-pattern "h60_*" \\
      --confidence-threshold 0.58 --ev-threshold 0.003

  # Set warmup for one model
  python3 scripts/set_model_filter.py --model h300_btc_30d --warmup-seconds 600

  # Clear keys for a pattern
  python3 scripts/set_model_filter.py --model-pattern "h1800_*" \\
      --clear-keys "blackout_hours,warmup_seconds"

  # Dry-run preview
  python3 scripts/set_model_filter.py --model-pattern "h60_*" \\
      --confidence-threshold 0.58 --dry-run

  # List current config for all models
  python3 scripts/set_model_filter.py --list

NOTE: Validation ranges — confidence_threshold [0.0,1.0], ev_threshold [-1.0,1.0],
      blackout_hours 0–23, warmup_seconds >= 0. The CLI does NOT validate whether
      a threshold is reachable given a model's typical output distribution; that is
      the operator's responsibility.
""",
    )

    # Target selection (mutually exclusive, required unless --list)
    sel = p.add_mutually_exclusive_group()
    sel.add_argument("--model", metavar="NAME",
                     help="Exact model name in model_registry.")
    sel.add_argument("--model-pattern", metavar="PATTERN",
                     help="Glob pattern (* → SQL LIKE %%) matching model_registry.name.")
    sel.add_argument("--list", action="store_true",
                     help="Print table of all models with their filter_config_json and exit.")

    # Gate values
    p.add_argument("--confidence-threshold", type=float, metavar="FLOAT",
                   help="confidence_threshold override [0.0, 1.0].")
    p.add_argument("--ev-threshold", type=float, metavar="FLOAT",
                   help="ev_threshold override [-1.0, 1.0].")
    p.add_argument("--blackout-hours", metavar="CSV",
                   help="Comma-separated UTC hours to blackout, e.g. '21,22,23,0,1,2,3'.")
    p.add_argument("--warmup-seconds", type=int, metavar="INT",
                   help="warmup_seconds override (non-negative integer).")
    p.add_argument("--clear-keys", metavar="CSV",
                   help="Comma-separated keys to remove from filter_config_json.")

    # Flags
    p.add_argument("--dry-run", action="store_true",
                   help="Show planned changes without writing to DB.")
    p.add_argument("--db", metavar="PATH",
                   help="Path to SQLite DB (overrides V3_DB_PATH env / default).")

    return p


def validate_args(args: argparse.Namespace) -> None:
    """Validate parsed args. Raises SystemExit(2) on failure."""
    errors = []

    if args.confidence_threshold is not None:
        try:
            args.confidence_threshold = _validate_confidence_threshold(
                args.confidence_threshold)
        except ValueError as e:
            errors.append(str(e))

    if args.ev_threshold is not None:
        try:
            args.ev_threshold = _validate_ev_threshold(args.ev_threshold)
        except ValueError as e:
            errors.append(str(e))

    if args.blackout_hours is not None:
        try:
            args.blackout_hours = _validate_blackout_hours(args.blackout_hours)
        except ValueError as e:
            errors.append(str(e))

    if args.warmup_seconds is not None:
        try:
            args.warmup_seconds = _validate_warmup_seconds(args.warmup_seconds)
        except ValueError as e:
            errors.append(str(e))

    if errors:
        for err in errors:
            print(f"Validation error: {err}", file=sys.stderr)
        sys.exit(2)

    # Check that --model or --model-pattern is supplied when not --list
    if not args.list and args.model is None and args.model_pattern is None:
        print(
            "error: one of --model, --model-pattern, or --list is required",
            file=sys.stderr,
        )
        sys.exit(2)


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    validate_args(args)

    # Resolve DB path
    if args.db:
        db_path = Path(args.db)
    else:
        db_path = _resolve_db_path()

    conn = _open_db(db_path)

    try:
        if args.list:
            return cmd_list(conn)
        else:
            return cmd_update(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
