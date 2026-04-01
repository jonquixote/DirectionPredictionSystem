#!/usr/bin/env python3
"""
Daily NE_t check for H300.

Computes NE_t/trade for the last 24 hours. If below zero, flags immediately.
Run via cron: 0 */6 * * * cd /app/DirectionPredictionSystem/polymarket-ofi && python -m monitoring.daily_net 2>&1 >> /data/logs/daily_net.log

Usage:
    python -m monitoring.daily_net [--hours 24] [--log-dir /data/logs]
"""
import json
import argparse
import logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("daily_net")

FEE = 0.009
STAKE = 10.0


def compute_ne_t(log_path: str, hours: int = 24) -> dict:
    """Compute realized NE_t for resolved trades in the last N hours.

    Per-trade P&L (for $1 stake):
      - Buying "up" at p_market:   Win: +(1 - p_market - fee)  Loss: -(p_market + fee)
      - Buying "down" at 1-p_market: Win: +(p_market - fee)    Loss: -((1-p_market) + fee)
    """
    try:
        records = [json.loads(l) for l in open(log_path)]
    except FileNotFoundError:
        return {"error": f"File not found: {log_path}"}

    # Merge prediction + resolution by prediction_id
    by_id = {}
    for r in records:
        pid = r.get("prediction_id")
        if not pid:
            continue
        if pid not in by_id:
            by_id[pid] = {}
        by_id[pid].update(r)

    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)

    all_resolved = [
        v for v in by_id.values()
        if v.get("prediction_correct") is not None
        and not v.get("warmup", False)
        and v.get("ts_model_ran_ms", 0) >= cutoff_ms
    ]

    # Separate: with p_market vs without
    resolved = [v for v in all_resolved if v.get("p_market") is not None]
    excluded = len(all_resolved) - len(resolved)

    if not resolved:
        return {"n": 0, "excluded": excluded, "ne_t_total": 0, "ne_t_per_trade": 0, "accuracy": 0, "flagged": False}

    n = len(resolved)
    correct = sum(1 for p in resolved if p["prediction_correct"])
    accuracy = correct / n

    ne_t_vals = []
    for p in resolved:
        mkt = p["p_market"]
        won = p["prediction_correct"]

        if p["pred_direction"] == "up":
            # Bought "up" contract at p_market
            if won:
                pnl = (1 - mkt - FEE) * STAKE
            else:
                pnl = -(mkt + FEE) * STAKE
        else:
            # Bought "down" contract at (1 - p_market)
            if won:
                pnl = (mkt - FEE) * STAKE
            else:
                pnl = -((1 - mkt) + FEE) * STAKE

        ne_t_vals.append(pnl)

    ne_t_total = sum(ne_t_vals)
    ne_t_per_trade = ne_t_total / n

    flagged = ne_t_per_trade < 0

    return {
        "n": n,
        "excluded": excluded,
        "accuracy": round(accuracy, 4),
        "ne_t_total": round(ne_t_total, 2),
        "ne_t_per_trade": round(ne_t_per_trade, 4),
        "flagged": flagged,
    }


def main():
    parser = argparse.ArgumentParser(description="Daily NE_t check")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--log-dir", type=str, default="/data/logs")
    args = parser.parse_args()

    models = {
        "h60": f"{args.log_dir}/predictions_h60.jsonl",
        "h300": f"{args.log_dir}/predictions_h300.jsonl",
        "h60_v3": f"{args.log_dir}/predictions_h60_v3.jsonl",
    }

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info("=" * 50)
    logger.info("Daily NE_t Check — %s (last %dh)", now, args.hours)
    logger.info("=" * 50)

    for model, path in models.items():
        result = compute_ne_t(path, args.hours)

        if "error" in result:
            logger.warning("  %s: %s", model, result["error"])
            continue

        n = result["n"]
        if n == 0:
            logger.info("  %s: no resolved trades in last %dh", model, args.hours)
            continue

        flag = "🚨 NE_t NEGATIVE" if result["flagged"] else "✅"
        excluded = result.get("excluded", 0)
        excl_str = f" ({excluded} excluded, no p_market)" if excluded else ""
        logger.info(
            "  %s: n=%d%s acc=%.1f%% NE_t=$%.2f ($/trade=$%.4f) %s",
            model, n, excl_str, result["accuracy"] * 100,
            result["ne_t_total"], result["ne_t_per_trade"], flag,
        )

        if result["flagged"]:
            logger.warning(
                "  ⚠️  %s NE_t/trade is NEGATIVE ($%.4f) — edge may have disappeared",
                model, result["ne_t_per_trade"],
            )

    logger.info("=" * 50)


if __name__ == "__main__":
    main()
