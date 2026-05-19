#!/usr/bin/env python3
"""
Analysis CLI — offline model + filter optimization.

Calls the same service functions as the dashboard API endpoints.
All output is JSON (pretty-printed to stdout or written to --json-out).

Usage examples:
  .venv/bin/python3 scripts/run_analysis.py --leaderboard --metric roi --top 20
  .venv/bin/python3 scripts/run_analysis.py --report --symbol BTCUSDT --window 300
  .venv/bin/python3 scripts/run_analysis.py --report --all --json-out /tmp/report.json
  .venv/bin/python3 scripts/run_analysis.py --threshold-grid --symbol BTCUSDT --window 300
  .venv/bin/python3 scripts/run_analysis.py --committee-sim --symbol BTCUSDT --window 300 --strategy avg
  .venv/bin/python3 scripts/run_analysis.py --skip-conditions --symbol BTCUSDT --window 300
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap — allow running from any cwd
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent  # ofi-lab-v3/
sys.path.insert(0, str(_REPO_ROOT / "dashboard_api"))

# Point STORAGE_DB_PATH to the default location if not already set
if "STORAGE_DB_PATH" not in os.environ:
    default_db = _REPO_ROOT / "data" / "v3.db"
    os.environ["STORAGE_DB_PATH"] = str(default_db)


def _import_analysis():
    """Lazy import so the path bootstrap above takes effect first."""
    from services.analysis import (
        compute_leaderboard,
        compute_threshold_grid,
        compute_committee_sim,
        compute_skip_conditions,
        compute_full_report,
        ALL_SYMBOLS,
        ALL_WINDOWS,
    )
    return (
        compute_leaderboard,
        compute_threshold_grid,
        compute_committee_sim,
        compute_skip_conditions,
        compute_full_report,
        ALL_SYMBOLS,
        ALL_WINDOWS,
    )


def _write_output(data, json_out: str | None):
    text = json.dumps(data, indent=2, default=str)
    if json_out:
        Path(json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(json_out).write_text(text, encoding="utf-8")
        print(f"Written to {json_out}", file=sys.stderr)
    else:
        print(text)


def cmd_leaderboard(args):
    (
        compute_leaderboard, _, _, _, _, _, _
    ) = _import_analysis()
    data = compute_leaderboard(
        symbol=args.symbol,
        market_window=args.window,
        min_samples=args.min_samples,
        metric=args.metric,
        since_ms=args.since_ms,
        limit=args.top,
    )
    _write_output(data, args.json_out)


def cmd_threshold_grid(args):
    if not args.symbol or not args.window:
        print("--threshold-grid requires --symbol and --window", file=sys.stderr)
        sys.exit(1)
    (
        _, compute_threshold_grid, _, _, _, _, _
    ) = _import_analysis()
    data = compute_threshold_grid(
        symbol=args.symbol,
        market_window=args.window,
        min_samples=args.min_samples,
        since_ms=args.since_ms,
    )
    _write_output(data, args.json_out)


def cmd_committee_sim(args):
    if not args.symbol or not args.window:
        print("--committee-sim requires --symbol and --window", file=sys.stderr)
        sys.exit(1)
    (
        _, _, compute_committee_sim, _, _, _, _
    ) = _import_analysis()
    data = compute_committee_sim(
        symbol=args.symbol,
        market_window=args.window,
        strategy=args.strategy,
        since_ms=args.since_ms,
    )
    _write_output(data, args.json_out)


def cmd_skip_conditions(args):
    (
        _, _, _, compute_skip_conditions, _, _, _
    ) = _import_analysis()
    data = compute_skip_conditions(
        symbol=args.symbol,
        market_window=args.window,
        since_ms=args.since_ms,
    )
    _write_output(data, args.json_out)


def cmd_report(args):
    (
        _, _, _, _, compute_full_report, ALL_SYMBOLS, ALL_WINDOWS
    ) = _import_analysis()

    if args.all:
        symbol = None
        window = None
    else:
        symbol = args.symbol
        window = args.window

    data = compute_full_report(
        symbol=symbol,
        market_window=window,
        since_ms=args.since_ms,
    )
    _write_output(data, args.json_out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Direction Prediction System — offline analysis CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Mode flags (mutually exclusive)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--leaderboard", action="store_true", help="Run leaderboard analysis")
    mode.add_argument("--threshold-grid", dest="threshold_grid", action="store_true",
                      help="Run threshold sweep grid")
    mode.add_argument("--committee-sim", dest="committee_sim", action="store_true",
                      help="Run committee simulation")
    mode.add_argument("--skip-conditions", dest="skip_conditions", action="store_true",
                      help="Run skip conditions analysis")
    mode.add_argument("--report", action="store_true",
                      help="Run full composite report")

    # Filter params
    p.add_argument("--symbol", type=str, default=None,
                   help="Symbol filter (e.g. BTCUSDT)")
    p.add_argument("--window", type=int, default=None,
                   help="Market window in seconds: 300, 900, or 1800")
    p.add_argument("--all", action="store_true", default=False,
                   help="Run for all symbols × windows (used with --report)")
    p.add_argument("--min-samples", type=int, default=50, dest="min_samples",
                   help="Minimum resolved predictions per model (default 50)")
    p.add_argument("--since-ms", type=int, default=None, dest="since_ms",
                   help="Only include predictions resolved after this ts_ms")

    # Leaderboard params
    p.add_argument("--metric", type=str, default="win_rate",
                   help="Sort metric: win_rate|roi|ev_per_trade|brier_score|n_samples|sharpe")
    p.add_argument("--top", type=int, default=100, help="Limit results (default 100)")

    # Committee sim params
    p.add_argument("--strategy", type=str, default="avg",
                   help="Committee strategy: avg|vote|weighted_ev")

    # Output
    p.add_argument("--json-out", type=str, default=None, dest="json_out",
                   help="Write JSON output to file instead of stdout")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.leaderboard:
        cmd_leaderboard(args)
    elif args.threshold_grid:
        cmd_threshold_grid(args)
    elif args.committee_sim:
        cmd_committee_sim(args)
    elif args.skip_conditions:
        cmd_skip_conditions(args)
    elif args.report:
        cmd_report(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
