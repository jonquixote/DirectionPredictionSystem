#!/usr/bin/env python3
"""
Drift Watchdog — detects model performance decay.

Reads the JSONL trade ledgers and computes rolling win rates.
Fires alerts when:
  1. Rolling-50 win rate drops below 52% (coin-flip territory)
  2. Daily net P&L is negative for 3 consecutive days
  3. Consecutive loss streak exceeds 8 trades

Designed to run as a cron job (e.g., every 6 hours):
  0 */6 * * * cd /app/DirectionPredictionSystem/polymarket-ofi && python -m monitoring.drift_watchdog

Does NOT modify any model behavior. Read-only monitoring only.

Usage:
    python -m monitoring.drift_watchdog [--ledger /data/logs/paper_trades_h300.jsonl]
    python -m monitoring.drift_watchdog --all
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("drift_watchdog")

# ── Thresholds (read-only monitoring — these do NOT gate trading) ──────

ROLLING_WINDOW = 50          # trades for rolling win rate
WIN_RATE_ALERT = 0.52        # below this = alert
CONSECUTIVE_LOSS_ALERT = 8   # streak length
NEGATIVE_DAYS_ALERT = 3      # consecutive days with net < 0


def load_resolutions(ledger_path: str) -> list[dict]:
    """Load trade_resolution records from a JSONL ledger."""
    resolutions = []
    with open(ledger_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("record_type") == "trade_resolution":
                    resolutions.append(r)
            except json.JSONDecodeError:
                continue
    return resolutions


def analyze_rolling_winrate(resolutions: list[dict], window: int = ROLLING_WINDOW) -> dict:
    """Compute rolling win rate over the last `window` trades."""
    if len(resolutions) < window:
        return {
            "enough_data": False,
            "total_trades": len(resolutions),
            "window": window,
        }

    recent = resolutions[-window:]
    wins = sum(1 for r in recent if r.get("trade_result") == "win")
    wr = wins / window

    return {
        "enough_data": True,
        "window": window,
        "wins": wins,
        "losses": window - wins,
        "win_rate": round(wr, 4),
        "alert": wr < WIN_RATE_ALERT,
        "threshold": WIN_RATE_ALERT,
    }


def analyze_consecutive_losses(resolutions: list[dict]) -> dict:
    """Find the current and max consecutive loss streaks."""
    if not resolutions:
        return {"current_streak": 0, "max_streak": 0, "alert": False}

    current = 0
    max_streak = 0

    for r in resolutions:
        if r.get("trade_result") == "loss":
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0

    return {
        "current_streak": current,
        "max_streak": max_streak,
        "alert": current >= CONSECUTIVE_LOSS_ALERT,
        "threshold": CONSECUTIVE_LOSS_ALERT,
    }


def analyze_daily_pnl(resolutions: list[dict]) -> dict:
    """Check for consecutive negative P&L days."""
    if not resolutions:
        return {"negative_streak": 0, "alert": False, "daily_pnl": {}}

    # Group by UTC date
    daily: dict[str, float] = defaultdict(float)
    for r in resolutions:
        ts_ms = r.get("ts_contract_close_ms", 0)
        if ts_ms == 0:
            continue
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        day_str = dt.strftime("%Y-%m-%d")
        daily[day_str] += r.get("net_pnl", 0)

    # Check last N days for consecutive negatives
    sorted_days = sorted(daily.keys(), reverse=True)
    negative_streak = 0
    for day in sorted_days:
        if daily[day] < 0:
            negative_streak += 1
        else:
            break

    # Last 7 days for display
    recent_7 = {d: round(daily[d], 2) for d in sorted_days[:7]}

    return {
        "negative_streak": negative_streak,
        "alert": negative_streak >= NEGATIVE_DAYS_ALERT,
        "threshold": NEGATIVE_DAYS_ALERT,
        "last_7_days": recent_7,
    }


def analyze_weekly_trend(resolutions: list[dict]) -> dict:
    """Win rate by week for trend analysis."""
    if not resolutions:
        return {"weeks": {}}

    weekly: dict[str, dict] = defaultdict(lambda: {"w": 0, "l": 0})
    for r in resolutions:
        ts_ms = r.get("ts_contract_close_ms", 0)
        if ts_ms == 0:
            continue
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        # ISO week
        week_str = "W%02d (%s)" % (dt.isocalendar()[1], dt.strftime("%b %d"))
        if r.get("trade_result") == "win":
            weekly[week_str]["w"] += 1
        else:
            weekly[week_str]["l"] += 1

    result = {}
    for week in sorted(weekly.keys()):
        d = weekly[week]
        total = d["w"] + d["l"]
        result[week] = {
            "wins": d["w"],
            "losses": d["l"],
            "total": total,
            "win_rate": round(d["w"] / total, 3) if total > 0 else 0,
        }
    return {"weeks": result}


def run_watchdog(ledger_path: str, model_name: str = "") -> dict:
    """Run all drift checks on a single ledger file."""
    if not os.path.exists(ledger_path):
        logger.warning("Ledger not found: %s", ledger_path)
        return {"error": "file_not_found", "path": ledger_path}

    resolutions = load_resolutions(ledger_path)
    logger.info("Loaded %d trade resolutions from %s", len(resolutions), ledger_path)

    rolling = analyze_rolling_winrate(resolutions)
    streaks = analyze_consecutive_losses(resolutions)
    daily = analyze_daily_pnl(resolutions)
    weekly = analyze_weekly_trend(resolutions)

    # Aggregate alert status
    any_alert = rolling.get("alert", False) or \
                streaks.get("alert", False) or \
                daily.get("alert", False)

    result = {
        "model": model_name or os.path.basename(ledger_path),
        "total_resolutions": len(resolutions),
        "any_alert": any_alert,
        "rolling_winrate": rolling,
        "consecutive_losses": streaks,
        "daily_pnl": daily,
        "weekly_trend": weekly,
    }

    # Print human-readable summary
    print()
    print("=" * 60)
    print("  DRIFT WATCHDOG: %s" % result["model"])
    print("=" * 60)
    print("  Total resolved trades: %d" % len(resolutions))

    if rolling.get("enough_data"):
        status = "🔴 ALERT" if rolling["alert"] else "🟢 OK"
        print("  Rolling-%d win rate: %.1f%% %s (threshold: %.0f%%)" % (
            rolling["window"], rolling["win_rate"] * 100,
            status, rolling["threshold"] * 100))
    else:
        print("  Rolling win rate: insufficient data (%d/%d)" % (
            rolling["total_trades"], rolling["window"]))

    streak_status = "🔴 ALERT" if streaks["alert"] else "🟢 OK"
    print("  Current loss streak: %d %s (threshold: %d)" % (
        streaks["current_streak"], streak_status, streaks["threshold"]))
    print("  Max loss streak: %d" % streaks["max_streak"])

    daily_status = "🔴 ALERT" if daily["alert"] else "🟢 OK"
    print("  Consecutive negative days: %d %s (threshold: %d)" % (
        daily["negative_streak"], daily_status, daily["threshold"]))

    if daily.get("last_7_days"):
        print("  Last 7 days P&L:")
        for day, pnl in daily["last_7_days"].items():
            emoji = "📈" if pnl >= 0 else "📉"
            print("    %s: $%+.2f %s" % (day, pnl, emoji))

    if weekly.get("weeks"):
        print("  Weekly trend:")
        for week, d in weekly["weeks"].items():
            bar = "█" * int(d["win_rate"] * 20) + "░" * (20 - int(d["win_rate"] * 20))
            print("    %s: %s %.0f%% (%dW/%dL)" % (
                week, bar, d["win_rate"] * 100, d["wins"], d["losses"]))

    if any_alert:
        print()
        print("  ⚠️  ONE OR MORE ALERTS FIRED — REVIEW MODEL PERFORMANCE")
    else:
        print()
        print("  ✅ All checks passed")

    print("=" * 60)

    return result


def main():
    parser = argparse.ArgumentParser(description="Drift Watchdog — model performance monitoring")
    parser.add_argument("--ledger", type=str, default="",
                        help="Path to a specific JSONL ledger file")
    parser.add_argument("--all", action="store_true",
                        help="Check all known ledger files in /data/logs/")
    parser.add_argument("--log-dir", type=str, default="/data/logs",
                        help="Directory containing JSONL ledger files")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON (for programmatic use)")
    args = parser.parse_args()

    results = []

    if args.ledger:
        results.append(run_watchdog(args.ledger))
    elif args.all:
        log_dir = Path(args.log_dir)
        ledger_files = {
            "h300 (900s)": log_dir / "paper_trades_h300.jsonl",
            "h60": log_dir / "paper_trades_h60.jsonl",
            "h60_v3": log_dir / "paper_trades_h60_v3.jsonl",
            "h60_v2 (compounder)": log_dir / "h60_v2_paper_trader.jsonl",
        }
        for name, path in ledger_files.items():
            if path.exists():
                results.append(run_watchdog(str(path), model_name=name))
            else:
                logger.info("Skipping %s — not found", path)
    else:
        # Default: check h300 (the primary model)
        default = Path(args.log_dir) / "paper_trades_h300.jsonl"
        if default.exists():
            results.append(run_watchdog(str(default), model_name="h300 (default)"))
        else:
            logger.error("No ledger specified and default not found: %s", default)
            sys.exit(1)

    if args.json:
        print(json.dumps(results, indent=2, default=str))

    # Exit code: 1 if any alerts fired
    if any(r.get("any_alert") for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
