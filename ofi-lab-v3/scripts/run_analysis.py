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
  .venv/bin/python3 scripts/run_analysis.py --simulate '{"confidence_threshold":0.55}' --symbol BTCUSDT --window 300
  .venv/bin/python3 scripts/run_analysis.py --grid-search --symbol BTCUSDT --window 300 --top-k 10
  .venv/bin/python3 scripts/run_analysis.py --regime-matrix --symbol BTCUSDT --window 300
  .venv/bin/python3 scripts/run_analysis.py --consensus --symbol BTCUSDT --window 300
  .venv/bin/python3 scripts/run_analysis.py --decay-filter --symbol BTCUSDT --window 300
  .venv/bin/python3 scripts/run_analysis.py --committee-weights --symbol BTCUSDT --window 300
  .venv/bin/python3 scripts/run_analysis.py --walk-forward '{"confidence_threshold":0.55}' --symbol BTCUSDT --window 300
  .venv/bin/python3 scripts/run_analysis.py --train-test '{"confidence_threshold":0.55}' --symbol BTCUSDT --window 300
  .venv/bin/python3 scripts/run_analysis.py --recommend-premium --symbol BTCUSDT --window 300
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


def _import_analysis_v2():
    """Lazy import for v2 premium filter functions."""
    from services.analysis import (
        simulate_filter,
        grid_search,
        regime_matrix,
        consensus_analysis,
        decay_filter_analysis,
        optimize_committee_weights,
        walk_forward_validate,
        train_test_validate,
        recommend_premium_filter,
    )
    return (
        simulate_filter,
        grid_search,
        regime_matrix,
        consensus_analysis,
        decay_filter_analysis,
        optimize_committee_weights,
        walk_forward_validate,
        train_test_validate,
        recommend_premium_filter,
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


# ---------------------------------------------------------------------------
# v2 command functions
# ---------------------------------------------------------------------------

def cmd_simulate(args):
    if not args.symbol or not args.window:
        print("--simulate requires --symbol and --window", file=sys.stderr)
        sys.exit(1)
    try:
        filter_config = json.loads(args.simulate)
    except json.JSONDecodeError as exc:
        print(f"--simulate: invalid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    (simulate_filter, *_) = _import_analysis_v2()
    data = simulate_filter(
        filter_config=filter_config,
        symbol=args.symbol,
        window=args.window,
        since_ms=args.since_ms,
        bootstrap_n=args.bootstrap_n,
    )
    _write_output(data, args.json_out)


def cmd_grid_search(args):
    if not args.symbol or not args.window:
        print("--grid-search requires --symbol and --window", file=sys.stderr)
        sys.exit(1)
    (_, grid_search, *_) = _import_analysis_v2()
    data = grid_search(
        symbol=args.symbol,
        window=args.window,
        since_ms=args.since_ms,
        top_k=args.top_k,
        min_n_passed=args.min_n_passed,
    )
    _write_output(data, args.json_out)


def cmd_regime_matrix(args):
    (_, _, regime_matrix, *_) = _import_analysis_v2()
    data = regime_matrix(
        symbol=args.symbol,
        window=args.window,
        since_ms=args.since_ms,
    )
    _write_output(data, args.json_out)


def cmd_consensus(args):
    if not args.symbol or not args.window:
        print("--consensus requires --symbol and --window", file=sys.stderr)
        sys.exit(1)
    (_, _, _, consensus_analysis, *_) = _import_analysis_v2()
    data = consensus_analysis(
        symbol=args.symbol,
        window=args.window,
        since_ms=args.since_ms,
    )
    _write_output(data, args.json_out)


def cmd_decay_filter(args):
    (_, _, _, _, decay_filter_analysis, *_) = _import_analysis_v2()
    data = decay_filter_analysis(
        symbol=args.symbol,
        window=args.window,
        since_ms=args.since_ms,
    )
    _write_output(data, args.json_out)


def cmd_committee_weights(args):
    if not args.symbol or not args.window:
        print("--committee-weights requires --symbol and --window", file=sys.stderr)
        sys.exit(1)
    (_, _, _, _, _, optimize_committee_weights, *_) = _import_analysis_v2()
    data = optimize_committee_weights(
        symbol=args.symbol,
        window=args.window,
        objective=args.objective,
        since_ms=args.since_ms,
    )
    _write_output(data, args.json_out)


def cmd_walk_forward(args):
    if not args.symbol or not args.window:
        print("--walk-forward requires --symbol and --window", file=sys.stderr)
        sys.exit(1)
    try:
        filter_config = json.loads(args.walk_forward)
    except json.JSONDecodeError as exc:
        print(f"--walk-forward: invalid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    (_, _, _, _, _, _, walk_forward_validate, *_) = _import_analysis_v2()
    data = walk_forward_validate(
        filter_config=filter_config,
        symbol=args.symbol,
        window=args.window,
        n_folds=args.n_folds,
        since_ms=args.since_ms,
    )
    _write_output(data, args.json_out)


def cmd_train_test(args):
    if not args.symbol or not args.window:
        print("--train-test requires --symbol and --window", file=sys.stderr)
        sys.exit(1)
    try:
        filter_config = json.loads(args.train_test)
    except json.JSONDecodeError as exc:
        print(f"--train-test: invalid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    (_, _, _, _, _, _, _, train_test_validate, *_) = _import_analysis_v2()
    data = train_test_validate(
        filter_config=filter_config,
        symbol=args.symbol,
        window=args.window,
        train_frac=args.train_frac,
        since_ms=args.since_ms,
    )
    _write_output(data, args.json_out)


def cmd_recommend_premium(args):
    if not args.symbol or not args.window:
        print("--recommend-premium requires --symbol and --window", file=sys.stderr)
        sys.exit(1)
    (*_, recommend_premium_filter) = _import_analysis_v2()
    data = recommend_premium_filter(
        symbol=args.symbol,
        window=args.window,
        since_ms=args.since_ms,
        mode=getattr(args, "recommend_mode", "strict"),
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
    # v2 modes
    mode.add_argument("--simulate", metavar="FILTER_JSON",
                      help="Simulate a filter config (JSON string) — requires --symbol --window")
    mode.add_argument("--grid-search", dest="grid_search", action="store_true",
                      help="Grid search over filter combos — requires --symbol --window")
    mode.add_argument("--regime-matrix", dest="regime_matrix", action="store_true",
                      help="Regime × model win-rate matrix")
    mode.add_argument("--consensus", action="store_true",
                      help="Consensus vs split boundary analysis — requires --symbol --window")
    mode.add_argument("--decay-filter", dest="decay_filter", action="store_true",
                      help="Decay-state bucketing and threshold recommendation")
    mode.add_argument("--committee-weights", dest="committee_weights", action="store_true",
                      help="Optimize per-model committee weights — requires --symbol --window")
    mode.add_argument("--walk-forward", metavar="FILTER_JSON", dest="walk_forward",
                      help="Walk-forward validation (JSON string) — requires --symbol --window")
    mode.add_argument("--train-test", metavar="FILTER_JSON", dest="train_test",
                      help="Train/test split validation (JSON string) — requires --symbol --window")
    mode.add_argument("--recommend-premium", dest="recommend_premium", action="store_true",
                      help="Full premium filter recommendation — requires --symbol --window")

    p.add_argument("--mode", dest="recommend_mode", choices=["strict", "discovery"],
                   default="strict",
                   help="Gate mode for --recommend-premium: strict (default) or discovery")

    # Shared filter params
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

    # v2 params
    p.add_argument("--bootstrap-n", type=int, default=0, dest="bootstrap_n",
                   help="Bootstrap resamples for CI on simulate (default 0 = disabled)")
    p.add_argument("--top-k", type=int, default=20, dest="top_k",
                   help="Top-k results for grid-search (default 20)")
    p.add_argument("--min-n-passed", type=int, default=100, dest="min_n_passed",
                   help="Min predictions passed for grid-search (default 100)")
    p.add_argument("--objective", type=str, default="sharpe",
                   help="Objective for committee-weights: sharpe|mean|win_rate")
    p.add_argument("--n-folds", type=int, default=5, dest="n_folds",
                   help="Number of folds for walk-forward (default 5)")
    p.add_argument("--train-frac", type=float, default=0.7, dest="train_frac",
                   help="Train fraction for train-test split (default 0.7)")

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
    elif args.simulate is not None:
        cmd_simulate(args)
    elif args.grid_search:
        cmd_grid_search(args)
    elif args.regime_matrix:
        cmd_regime_matrix(args)
    elif args.consensus:
        cmd_consensus(args)
    elif args.decay_filter:
        cmd_decay_filter(args)
    elif args.committee_weights:
        cmd_committee_weights(args)
    elif args.walk_forward is not None:
        cmd_walk_forward(args)
    elif args.train_test is not None:
        cmd_train_test(args)
    elif args.recommend_premium:
        cmd_recommend_premium(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
