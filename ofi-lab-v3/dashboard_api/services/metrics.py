"""
Metrics service — single source of truth for NE_t, accuracy, CI, calibration.

All NE_t uses the realized formula. Never the theoretical EV formula.
"""
from __future__ import annotations
import math
from scipy import stats


SYSTEM_FEE = 0.009  # platform fee


def compute_realized_net(
    direction: str, correct: bool, p_market: float, fee: float = SYSTEM_FEE
) -> float | None:
    """
    Realized P&L per $1 stake for a resolved trade.

    direction: "up" or "down"
    correct:   whether the prediction was right
    p_market:  Polymarket implied probability at trade time
    fee:       platform fee

    Returns None for unresolved trades (caller must check).
    """
    if direction == "up":
        return +(1 - p_market - fee) if correct else -(p_market + fee)
    else:  # down
        return +(p_market - fee) if correct else -((1 - p_market) + fee)


def realized_net_breakdown(
    direction: str, correct: bool, p_market: float, fee: float = SYSTEM_FEE
) -> dict | None:
    """Full decomposition for NE_t tooltip in the UI."""
    result = compute_realized_net(direction, correct, p_market, fee)
    if result is None:
        return None

    if direction == "up":
        if correct:
            formula = f"+(1 − {p_market:.3f} − {fee}) = {result:+.3f}"
        else:
            formula = f"−({p_market:.3f} + {fee}) = {result:+.3f}"
    else:
        if correct:
            formula = f"+({p_market:.3f} − {fee}) = {result:+.3f}"
        else:
            formula = f"−((1 − {p_market:.3f}) + {fee}) = {result:+.3f}"

    return {
        "direction": direction,
        "correct": correct,
        "p_market": p_market,
        "fee": fee,
        "formula": formula,
        "result": round(result, 6),
    }


def wilson_ci(wins: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """
    Wilson score interval — correct behavior at small n and extreme p.
    Never use normal approximation.
    """
    if n == 0:
        return (0.0, 0.0)
    z = stats.norm.ppf((1 + confidence) / 2)
    p = wins / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def rolling_accuracy_series(
    outcomes: list[bool], n: int = 50
) -> list[dict]:
    """
    outcomes: list of bool (True=correct), chronological order.
    Returns: [{index, accuracy, ci_low, ci_high}]
    Only includes positions where at least n trades have occurred.
    """
    if len(outcomes) < n:
        return []

    series = []
    for i in range(n, len(outcomes) + 1):
        window = outcomes[i - n : i]
        wins = sum(window)
        acc = wins / n
        ci_low, ci_high = wilson_ci(wins, n)
        series.append({
            "index": i,
            "accuracy": round(acc, 4),
            "ci_low": round(ci_low, 4),
            "ci_high": round(ci_high, 4),
        })

    return series


def calibration_curve(
    proba_values: list[float], outcomes: list[bool], n_bins: int = 10
) -> list[dict]:
    """
    Bucket p_model values into n_bins equally spaced between 0 and 1.
    For each bucket: mean_predicted_proba, actual_win_rate, count.
    """
    if not proba_values:
        return []

    bins = []
    bin_width = 1.0 / n_bins
    for i in range(n_bins):
        low = i * bin_width
        high = (i + 1) * bin_width
        center = (low + high) / 2

        indices = [
            j for j, p in enumerate(proba_values)
            if low <= p < high or (i == n_bins - 1 and p == high)
        ]

        if not indices:
            bins.append({
                "bin_center": round(center, 3),
                "bin_low": round(low, 3),
                "bin_high": round(high, 3),
                "predicted": round(center, 4),
                "actual": None,
                "n": 0,
                "deviation": None,
            })
            continue

        mean_pred = sum(proba_values[j] for j in indices) / len(indices)
        actual_rate = sum(outcomes[j] for j in indices) / len(indices)

        bins.append({
            "bin_center": round(center, 3),
            "bin_low": round(low, 3),
            "bin_high": round(high, 3),
            "predicted": round(mean_pred, 4),
            "actual": round(actual_rate, 4),
            "n": len(indices),
            "deviation": round(actual_rate - mean_pred, 4),
        })

    return bins


def z_test(
    n: int, observed_accuracy: float, null_accuracy: float = 0.50
) -> dict:
    """One-tailed z-test against null hypothesis."""
    if n == 0:
        return {"z_score": None, "p_value_one_tailed": None, "significant_at_05": False}

    se = math.sqrt(null_accuracy * (1 - null_accuracy) / n)
    if se == 0:
        return {"z_score": None, "p_value_one_tailed": None, "significant_at_05": False}

    z = float((observed_accuracy - null_accuracy) / se)
    p = float(1 - stats.norm.cdf(z))

    return {
        "z_score": round(z, 4),
        "p_value_one_tailed": round(p, 6),
        "significant_at_05": bool(p < 0.05),
    }


def compute_execution_funnel(trades: list[dict]) -> dict:
    """
    Input: all trade records (including suppressed/excluded).
    Returns funnel stage counts.
    """
    total = len(trades)
    # For now, without full gate pipeline data, we approximate:
    # predictions_fired = total records
    # executed = records without suppressed_reason
    executed = sum(1 for t in trades if not t.get("suppressed_reason"))
    suppressed = total - executed

    return {
        "predictions_fired": total,
        "executed": executed,
        "suppressed": suppressed,
        "suppression_rate": round(suppressed / total, 4) if total > 0 else 0,
    }


def divergence_bucket_stats(trades: list[dict], fee: float = SYSTEM_FEE) -> list[dict]:
    """
    Bucket resolved trades by |p_model − p_market|.
    Buckets: 0–0.02, 0.02–0.05, 0.05–0.10, 0.10+
    """
    bucket_defs = [
        ("0.00–0.02", 0.00, 0.02),
        ("0.02–0.05", 0.02, 0.05),
        ("0.05–0.10", 0.05, 0.10),
        ("0.10+", 0.10, None),
    ]

    results = []
    for label, low, high in bucket_defs:
        bucket_trades = []
        for t in trades:
            pm = t.get("p_market")
            pp = t.get("pred_proba")
            if pm is None or pp is None:
                continue
            div = abs(pp - pm)
            if high is not None:
                if low <= div < high:
                    bucket_trades.append(t)
            else:
                if div >= low:
                    bucket_trades.append(t)

        n = len(bucket_trades)
        wins = sum(1 for t in bucket_trades if t.get("prediction_correct"))
        acc = wins / n if n > 0 else None
        ci_low, ci_high = wilson_ci(wins, n) if n > 0 else (None, None)

        # Realized NE_t
        ne_vals = []
        for t in bucket_trades:
            if t.get("prediction_correct") is not None and t.get("p_market") is not None:
                ne = compute_realized_net(
                    t["pred_direction"], t["prediction_correct"], t["p_market"], fee
                )
                if ne is not None:
                    ne_vals.append(ne)

        avg_ne = sum(ne_vals) / len(ne_vals) if ne_vals else None

        results.append({
            "label": label,
            "range_low": low,
            "range_high": high,
            "is_high_divergence": low >= 0.10,
            "n": n,
            "wins": wins,
            "accuracy": round(acc, 4) if acc is not None else None,
            "ci_low": round(ci_low, 4) if ci_low is not None else None,
            "ci_high": round(ci_high, 4) if ci_high is not None else None,
            "realized_net_per_trade": round(avg_ne, 6) if avg_ne is not None else None,
        })

    return results


def portfolio_metrics(trades: list[dict], fee: float = SYSTEM_FEE) -> dict:
    """Compute portfolio-level performance metrics from resolved trades."""
    if not trades:
        return {
            "total_trades": 0, "total_ne": 0.0, "total_roi_pct": 0.0,
            "profit_factor": 0.0, "max_drawdown": 0.0, "sharpe_ratio": None,
            "sortino_ratio": None, "win_rate": 0.0, "avg_trade_ne": 0.0,
        }
    net_values = []
    for t in trades:
        direction = t.get("pred_direction", "up")
        correct = t.get("prediction_correct")
        p_market = t.get("p_market") or 0.5
        if correct is None or p_market is None:
            continue
        ne = compute_realized_net(direction, correct, p_market, fee)
        if ne is not None:
            net_values.append(ne)

    n = len(net_values)
    if n == 0:
        return {
            "total_trades": 0, "total_ne": 0.0, "total_roi_pct": 0.0,
            "profit_factor": 0.0, "max_drawdown": 0.0, "sharpe_ratio": None,
            "sortino_ratio": None, "win_rate": 0.0, "avg_trade_ne": 0.0,
        }

    total_ne = sum(net_values)
    roi_pct = (total_ne / n) * 100

    wins_sum = sum(v for v in net_values if v > 0)
    losses_sum = sum(abs(v) for v in net_values if v < 0)
    profit_factor = wins_sum / losses_sum if losses_sum > 0 else (float('inf') if wins_sum > 0 else 0.0)

    cumulative = []
    running = 0.0
    for v in net_values:
        running += v
        cumulative.append(running)
    peak = 0.0
    max_dd = 0.0
    for c in cumulative:
        if c > peak:
            peak = c
        dd = peak - c
        if dd > max_dd:
            max_dd = dd

    import numpy as np
    sharpe = None
    sortino = None
    if n > 1:
        mean_ne = float(np.mean(net_values))
        std_ne = float(np.std(net_values, ddof=1))
        trades_per_year = 105120
        sharpe = (mean_ne / std_ne) * math.sqrt(trades_per_year) if std_ne > 0 else None
        downside = [v for v in net_values if v < 0]
        downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else std_ne
        sortino = (mean_ne / downside_std) * math.sqrt(trades_per_year) if downside_std > 0 else None

    win_count = sum(1 for v in net_values if v > 0)
    return {
        "total_trades": n,
        "total_ne": round(total_ne, 6),
        "total_roi_pct": round(roi_pct, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float('inf') else None,
        "max_drawdown": round(max_dd, 6),
        "sharpe_ratio": round(sharpe, 4) if sharpe is not None else None,
        "sortino_ratio": round(sortino, 4) if sortino is not None else None,
        "win_rate": round(win_count / n, 4) if n > 0 else 0.0,
        "avg_trade_ne": round(total_ne / n, 6) if n > 0 else 0.0,
    }


def threshold_sweep(
    trades: list[dict],
    thresholds: list[float] | None = None,
    fee: float = SYSTEM_FEE,
) -> list[dict]:
    """Compute win_rate and ROI at each confidence threshold."""
    if thresholds is None:
        thresholds = [0.50 + i * 0.01 for i in range(31)]  # 0.50 to 0.80
    results = []
    for t_val in thresholds:
        filtered = [
            tr for tr in trades
            if tr.get("prediction_correct") is not None and
               max(tr.get("pred_proba_calibrated", tr.get("pred_proba", 0.5)),
                   1 - tr.get("pred_proba_calibrated", tr.get("pred_proba", 0.5))) >= t_val
        ]
        if not filtered:
            results.append({"threshold": t_val, "win_rate": None, "roi_pct": None, "n_trades": 0})
            continue
        net_values = []
        wins = 0
        for tr in filtered:
            direction = tr.get("pred_direction", "up")
            correct = tr.get("prediction_correct")
            p_market = tr.get("p_market") or 0.5
            if p_market is None:
                continue
            ne = compute_realized_net(direction, correct, p_market, fee)
            if ne is not None:
                net_values.append(ne)
                if ne > 0:
                    wins += 1
        n = len(net_values)
        wr = wins / n if n > 0 else 0.0
        roi = (sum(net_values) / n) * 100 if n > 0 else 0.0
        results.append({
            "threshold": round(t_val, 2),
            "win_rate": round(wr, 4),
            "roi_pct": round(roi, 4),
            "n_trades": n,
        })
    return results


def pareto_frontier(sweep_results: list[dict]) -> list[dict]:
    """Extract non-dominated points from threshold sweep results."""
    valid = [p for p in sweep_results if p["n_trades"] > 0 and p["win_rate"] is not None]
    if not valid:
        return []

    frontier = []
    for p in valid:
        dominated = False
        for q in valid:
            if q is p:
                continue
            q_wr = q["win_rate"] or 0
            q_roi = q.get("roi_pct", 0) or 0
            p_wr = p["win_rate"] or 0
            p_roi = p.get("roi_pct", 0) or 0
            if q_wr >= p_wr and q_roi >= p_roi and (q_wr > p_wr or q_roi > p_roi):
                dominated = True
                break
        if not dominated:
            frontier.append(p)

    frontier.sort(key=lambda p: (p["win_rate"] or 0))
    return frontier

