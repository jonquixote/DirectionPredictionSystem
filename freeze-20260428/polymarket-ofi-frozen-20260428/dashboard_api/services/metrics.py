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
