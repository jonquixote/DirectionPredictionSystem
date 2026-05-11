#!/usr/bin/env python3
"""
Weekly report generator for paper trading.

Reads completed predictions/trades from JSONL ledgers and produces
a summary report with:
- Per-model realized accuracy
- Coverage %
- Rolling 50-trade accuracy trend
- Per-symbol breakdown
- Head-to-head H=60 vs H=300 comparison
- Mid-price range check
- Go-live gate check

Usage:
    python -m trading.weekly_report --log-dir /data/logs
"""

from __future__ import annotations

import json
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

import numpy as np

from trading.ledger import Ledger, read_records, merge_predictions_with_resolutions, merge_trades_with_resolutions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("weekly_report")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
MID_PRICE_TRAINING_RANGE = {
    "BTCUSDT": [58_000.0, 110_000.0],
    "ETHUSDT": [1_400.0, 4_200.0],
    "SOLUSDT": [90.0, 220.0],
}

# Go-live gate
GATE_MIN_ACCURACY = 0.515
GATE_MIN_TRADES = 200
GATE_ROLLING_WINDOW = 50


def generate_model_report(model_name: str, log_dir: Path) -> dict:
    """Generate a report for a single model."""
    ledger = Ledger(log_dir, model_name)

    # Load predictions — use raw records for counting (merge overwrites record_type)
    pred_records = read_records(ledger.predictions_path)
    all_preds = [p for p in pred_records if p.get("record_type") == "prediction"]
    warmup_preds = [p for p in all_preds if p.get("warmup", False)]
    live_preds = [p for p in all_preds if not p.get("warmup", False)]

    # Load trades — merge by trade_id (not prediction_id)
    trade_records = read_records(ledger.trades_path)
    trade_merged = merge_trades_with_resolutions(trade_records)

    # Completed trades (with resolution) — exclude warmup
    completed = [t for t in trade_merged
                 if t.get("prediction_correct") is not None
                 and not t.get("warmup", False)]

    # Trade entries (not resolutions)
    trade_entries = [t for t in trade_records if t.get("record_type") == "trade_entry"]

    report = {
        "model": model_name,
        "total_predictions": len(all_preds),
        "warmup_predictions": len(warmup_preds),
        "live_predictions": len(live_preds),
        "total_trades_fired": len(trade_entries),
        "completed_trades": len(completed),
    }

    if not completed:
        report["accuracy"] = None
        report["gate_passed"] = False
        report["gate_reason"] = "No completed trades"
        return report

    # Overall accuracy
    correct = sum(1 for t in completed if t.get("prediction_correct"))
    accuracy = correct / len(completed)
    report["accuracy"] = round(accuracy, 4)

    # Coverage (live predictions only — warmup excluded)
    if live_preds:
        above_threshold = sum(1 for p in live_preds if p.get("above_threshold"))
        report["coverage_pct"] = round(above_threshold / len(live_preds) * 100, 1)
    else:
        report["coverage_pct"] = 0

    # Per-duration accuracy
    by_duration = defaultdict(list)
    for t in completed:
        dur = t.get("contract_duration_seconds", 300)
        by_duration[dur].append(t.get("prediction_correct", False))
    report["by_duration"] = {
        dur: {
            "n": len(vals),
            "accuracy": round(sum(vals) / len(vals), 4) if vals else 0,
        }
        for dur, vals in sorted(by_duration.items())
    }

    # Per-symbol accuracy
    by_symbol = defaultdict(list)
    for t in completed:
        sym = t.get("symbol", "unknown")
        by_symbol[sym].append(t.get("prediction_correct", False))
    report["by_symbol"] = {
        sym: {
            "n": len(vals),
            "accuracy": round(sum(vals) / len(vals), 4) if vals else 0,
        }
        for sym, vals in sorted(by_symbol.items())
    }

    # Rolling 50-trade accuracy
    sorted_trades = sorted(completed, key=lambda t: t.get("ts_model_ran_ms", 0))
    correct_arr = [1 if t.get("prediction_correct") else 0 for t in sorted_trades]

    if len(completed) >= GATE_ROLLING_WINDOW:
        rolling = []
        for i in range(GATE_ROLLING_WINDOW, len(correct_arr) + 1):
            window_acc = sum(correct_arr[i - GATE_ROLLING_WINDOW:i]) / GATE_ROLLING_WINDOW
            rolling.append(round(window_acc, 4))

        report["rolling_50_latest"] = rolling[-1] if rolling else None
        report["rolling_50_min"] = min(rolling) if rolling else None
        report["rolling_50_max"] = max(rolling) if rolling else None

        # Check if declining (last 5 windows trending down)
        if len(rolling) >= 5:
            recent = rolling[-5:]
            declining = all(recent[i] <= recent[i - 1] for i in range(1, len(recent)))
            report["rolling_50_declining"] = declining
        else:
            report["rolling_50_declining"] = False
    else:
        report["rolling_50_latest"] = None
        report["rolling_50_declining"] = False

    # Rolling batch output (per 50 trades, chronological)
    rolling_batches = []
    batch_size = GATE_ROLLING_WINDOW
    for batch_start in range(0, len(correct_arr), batch_size):
        batch = correct_arr[batch_start:batch_start + batch_size]
        if not batch:
            break
        batch_acc = sum(batch) / len(batch)
        rolling_batches.append({
            "batch_num": len(rolling_batches) + 1,
            "start": batch_start + 1,
            "end": batch_start + len(batch),
            "n": len(batch),
            "accuracy": round(batch_acc, 4),
        })
    report["rolling_batches"] = rolling_batches

    # Time-bucketed accuracy (3-hour UTC windows)
    BUCKET_HOURS = 3
    time_buckets: dict[str, list[bool]] = {}
    for t in sorted_trades:
        ts_ms = t.get("ts_model_ran_ms", 0)
        if ts_ms == 0:
            continue
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        bucket_hour = (dt.hour // BUCKET_HOURS) * BUCKET_HOURS
        bucket_key = dt.strftime(f"%Y-%m-%d") + f" {bucket_hour:02d}:00–{bucket_hour + BUCKET_HOURS:02d}:00 UTC"
        if bucket_key not in time_buckets:
            time_buckets[bucket_key] = []
        time_buckets[bucket_key].append(t.get("prediction_correct", False))

    time_bucket_report = []
    cumulative_correct = 0
    cumulative_total = 0
    for bucket_key in sorted(time_buckets.keys()):
        vals = time_buckets[bucket_key]
        n = len(vals)
        correct_in_bucket = sum(1 for v in vals if v)
        cumulative_correct += correct_in_bucket
        cumulative_total += n
        time_bucket_report.append({
            "bucket": bucket_key,
            "n": n,
            "accuracy": round(correct_in_bucket / n, 4) if n else 0,
            "cumulative_accuracy": round(cumulative_correct / cumulative_total, 4),
        })
    report["time_buckets"] = time_bucket_report

    # Net P&L
    total_net_pnl = sum(t.get("net_pnl", 0) for t in completed if t.get("net_pnl") is not None)
    total_gross_pnl = sum(t.get("gross_pnl", 0) for t in completed if t.get("gross_pnl") is not None)
    total_fees = sum(t.get("fee_paid", 0) for t in completed if t.get("fee_paid") is not None)
    report["total_net_pnl"] = round(total_net_pnl, 2)
    report["total_gross_pnl"] = round(total_gross_pnl, 2)
    report["total_fees"] = round(total_fees, 2)

    # Mid-price range check
    out_of_range = []
    for p in all_preds:
        sym = p.get("symbol")
        mid = p.get("price_at_contract_open", 0)
        range_ = MID_PRICE_TRAINING_RANGE.get(sym)
        if range_ and (mid < range_[0] or mid > range_[1]):
            out_of_range.append({"symbol": sym, "mid_price": mid})
    report["out_of_range_count"] = len(out_of_range)
    if out_of_range:
        report["out_of_range_examples"] = out_of_range[:5]
    # p_market divergence analysis (only for predictions that have p_market)
    preds_with_pm = [p for p in all_preds if p.get("p_market") is not None and not p.get("warmup", False)]
    if preds_with_pm:
        divergences = [abs(p.get("p_model_minus_market", 0) or 0) for p in preds_with_pm]
        report["p_market_coverage"] = len(preds_with_pm)
        report["mean_abs_divergence"] = round(np.mean(divergences), 4)

        # Accuracy by divergence quartile (for completed trades with p_market)
        completed_with_pm = [t for t in completed if t.get("p_market") is not None]
        if completed_with_pm:
            div_vals = [abs(t.get("p_model_minus_market", 0) or 0) for t in completed_with_pm]
            if len(div_vals) >= 4:
                q25, q50, q75 = np.percentile(div_vals, [25, 50, 75])
                quartiles = {"Q1 (low div)": [], "Q2": [], "Q3": [], "Q4 (high div)": []}
                for t in completed_with_pm:
                    d = abs(t.get("p_model_minus_market", 0) or 0)
                    correct = t.get("prediction_correct", False)
                    if d <= q25:
                        quartiles["Q1 (low div)"].append(correct)
                    elif d <= q50:
                        quartiles["Q2"].append(correct)
                    elif d <= q75:
                        quartiles["Q3"].append(correct)
                    else:
                        quartiles["Q4 (high div)"].append(correct)
                report["divergence_quartiles"] = {
                    k: {"n": len(v), "accuracy": round(sum(v) / len(v), 4) if v else 0}
                    for k, v in quartiles.items()
                }

        # NE_t per trade: (p_model - p_market) * payout - fee
        # For binary markets: if correct, payout = stake; if wrong, payout = -stake
        # NE_t = (p_model - p_market) * direction_sign * realized_outcome - fee
        ne_values = []
        for t in completed_with_pm:
            pm = t.get("p_market", 0.5)
            p_model = t.get("pred_proba", 0.5)
            fee = t.get("fee_paid", 0)
            correct = t.get("prediction_correct", False)
            gross = t.get("gross_pnl", 0)
            ne_t = gross - fee  # This is the actual realized NE_t
            ne_values.append(ne_t)
        if ne_values:
            report["mean_ne_t"] = round(np.mean(ne_values), 4)
            report["total_ne_t"] = round(sum(ne_values), 2)

    # Go-live gate
    rolling_latest = report.get("rolling_50_latest")
    gate_checks = {
        "accuracy_pass": accuracy >= GATE_MIN_ACCURACY,
        "min_trades_pass": len(completed) >= GATE_MIN_TRADES,
        "rolling_not_declining": not report.get("rolling_50_declining", True),
        "rolling_above_floor": (rolling_latest is not None and rolling_latest >= GATE_MIN_ACCURACY),
    }
    report["gate_checks"] = gate_checks
    report["gate_passed"] = all(gate_checks.values())

    return report


def format_report(reports: list[dict]) -> str:
    """Format reports into readable text."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"PAPER TRADING WEEKLY REPORT — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("=" * 70)

    for r in reports:
        lines.append("")
        lines.append(f"─── Model: {r['model']} {'─' * 50}")
        lines.append(f"  Predictions logged:  {r['total_predictions']} ({r.get('warmup_predictions', 0)} warmup, {r.get('live_predictions', 0)} live)")
        lines.append(f"  Trades fired:        {r['total_trades_fired']} (warmup excluded)")
        lines.append(f"  Completed trades:    {r['completed_trades']}")

        if r.get("accuracy") is not None:
            lines.append(f"  Realized accuracy:   {r['accuracy']:.2%}")
            lines.append(f"  Coverage:            {r.get('coverage_pct', 0):.1f}%")
            lines.append(f"  Net P&L:             ${r['total_net_pnl']:.2f}")
            lines.append(f"  Fees paid:           ${r['total_fees']:.2f}")

            lines.append("")
            lines.append("  Per-duration:")
            for dur, data in r.get("by_duration", {}).items():
                lines.append(f"    {dur}s: {data['accuracy']:.2%} ({data['n']} trades)")

            lines.append("  Per-symbol:")
            for sym, data in r.get("by_symbol", {}).items():
                lines.append(f"    {sym}: {data['accuracy']:.2%} ({data['n']} trades)")

            if r.get("rolling_50_latest") is not None:
                lines.append(f"  Rolling 50: latest={r['rolling_50_latest']:.2%} "
                             f"min={r['rolling_50_min']:.2%} max={r['rolling_50_max']:.2%}")
                if r.get("rolling_50_declining"):
                    lines.append("  ⚠️  Rolling accuracy DECLINING")

            # Rolling batch output (per 50 trades)
            batches = r.get("rolling_batches", [])
            if batches:
                lines.append("")
                lines.append(f"  {r['model']} rolling accuracy (per {GATE_ROLLING_WINDOW} trades):")
                for b in batches:
                    marker = " ← current" if b == batches[-1] else ""
                    lines.append(f"    Batch {b['batch_num']} (trades {b['start']:>3d}–{b['end']:>3d}):  "
                                 f"{b['accuracy']:.1%}{marker}")

            # Time-bucketed accuracy (3-hour windows)
            buckets = r.get("time_buckets", [])
            if buckets:
                lines.append("")
                lines.append(f"  {r['model']} accuracy by time bucket (3-hour windows):")
                for tb in buckets:
                    lines.append(f"    {tb['bucket']}:  n={tb['n']:<3d}  "
                                 f"acc={tb['accuracy']:.1%}   cumulative={tb['cumulative_accuracy']:.1%}")

            if r.get("out_of_range_count", 0) > 0:
                lines.append(f"  ⚠️  {r['out_of_range_count']} predictions outside training price range")

            # p_market divergence analysis
            if r.get("p_market_coverage"):
                lines.append("")
                lines.append("  p_market divergence analysis:")
                lines.append(f"    Predictions with p_market: {r['p_market_coverage']}")
                lines.append(f"    Mean |p_model - p_market|: {r['mean_abs_divergence']:.4f}")
                if r.get("divergence_quartiles"):
                    lines.append("    Accuracy by divergence quartile:")
                    for q, data in r["divergence_quartiles"].items():
                        lines.append(f"      {q}: {data['accuracy']:.1%} (n={data['n']})")
                if r.get("mean_ne_t") is not None:
                    lines.append(f"    Mean NE_t per trade:  ${r['mean_ne_t']:.4f}")
                    lines.append(f"    Total NE_t:           ${r['total_ne_t']:.2f}")

            gate = r.get("gate_checks", {})
            lines.append("")
            lines.append("  Go-live gate:")
            lines.append(f"    Accuracy ≥ 51.5%:     {'✅' if gate.get('accuracy_pass') else '❌'}")
            lines.append(f"    ≥ 200 trades:         {'✅' if gate.get('min_trades_pass') else '❌'}")
            lines.append(f"    Not declining:        {'✅' if gate.get('rolling_not_declining') else '❌'}")
            lines.append(f"    Rolling ≥ 51.5%:      {'✅' if gate.get('rolling_above_floor') else '❌'}"
                         f" (latest={r.get('rolling_50_latest', 0):.1%})")
            lines.append(f"    GATE: {'PASSED ✅' if r['gate_passed'] else 'NOT YET'}")
        else:
            lines.append(f"  Status: {r.get('gate_reason', 'Awaiting data')}")

    # Head-to-head
    if len(reports) == 2 and all(r.get("accuracy") is not None for r in reports):
        lines.append("")
        lines.append("─── Head-to-Head ─────────────────────────────────────────────")
        r0, r1 = reports[0], reports[1]
        delta = r0["accuracy"] - r1["accuracy"]
        winner = r0["model"] if delta > 0 else r1["model"]
        lines.append(f"  {r0['model']}: {r0['accuracy']:.2%}  vs  {r1['model']}: {r1['accuracy']:.2%}")
        lines.append(f"  Delta: {abs(delta):.2%} in favor of {winner}")

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Paper trading weekly report")
    parser.add_argument("--log-dir", type=str, default="/data/logs")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    models = ["h60", "h300"]

    reports = []
    for model in models:
        r = generate_model_report(model, log_dir)
        reports.append(r)

    text = format_report(reports)
    print(text)

    # Save report
    report_path = log_dir / f"weekly_report_{datetime.now(timezone.utc).strftime('%Y%m%d')}.txt"
    with open(report_path, "w") as f:
        f.write(text)
    logger.info("Report saved to %s", report_path)

    # Save JSON for programmatic access
    json_path = log_dir / f"weekly_report_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    with open(json_path, "w") as f:
        json.dump(reports, f, indent=2, default=str)


if __name__ == "__main__":
    main()
