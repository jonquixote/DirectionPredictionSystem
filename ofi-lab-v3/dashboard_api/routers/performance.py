"""Performance endpoints — summary, rolling, heatmap, divergence, calibration, funnel, suppression."""
from __future__ import annotations
from fastapi import APIRouter, Query
from services.sqlite_store import get_store
from services.metrics import (
    compute_realized_net, wilson_ci, rolling_accuracy_series,
    calibration_curve, z_test, divergence_bucket_stats,
    compute_execution_funnel, SYSTEM_FEE,
)

router = APIRouter(tags=["performance"])


@router.get("/performance/summary")
async def performance_summary(
    model: str | None = None,
    symbol: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
):
    """Aggregated KPI metrics per model×symbol."""
    store = get_store()
    models = [model] if model else ["h60", "h300", "h60_v3"]
    symbols = [symbol] if symbol else ["BTCUSDT", "SOLUSDT", "ETHUSDT", "XRPUSDT", "ALL"]

    summaries = []
    # Base durations and ALL
    contract_durations = ["ALL", 300, 900, 1800]
    
    for m in models:
        for s in symbols:
            for dur in contract_durations:
                sym_arg = None if s == "ALL" else s
                dur_arg = None if dur == "ALL" else dur
                
                # Fast path: only compute if it's the base "ALL" combinations or if we specifically need it
                # For UI efficiency, we usually only need Symbol=ALL/Dur=split OR Symbol=split/Dur=ALL
                if s != "ALL" and dur != "ALL":
                    continue
                    
                resolved = store.get_resolved_trades(model=m, symbol=sym_arg)
                
                # Duration filter
                if dur_arg is not None:
                    resolved = [t for t in resolved if (t.get("contract_duration_seconds") == dur_arg or t.get("contract_duration") == dur_arg)]

                # Time filter
                if from_ms:
                    resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) >= from_ms]
                if to_ms:
                    resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) <= to_ms]

                n = len(resolved)
                # Only include entries that have at least some data, or if it's the primary "ALL" baseline
                if n == 0 and (s != "ALL" or dur != "ALL"):
                    continue
                    
                wins = sum(1 for t in resolved if t.get("prediction_correct"))
                losses = n - wins

                # Unresolved count
                all_trades, _ = store.get_trades(model=m, page_size=100000)
                if sym_arg:
                    all_trades = [t for t in all_trades if t.get("symbol") == sym_arg]
                if dur_arg is not None:
                    all_trades = [t for t in all_trades if (t.get("contract_duration_seconds") == dur_arg or t.get("contract_duration") == dur_arg)]
                    
                unresolved = sum(1 for t in all_trades if not t.get("resolved") and not t.get("suppressed_reason"))

                acc = wins / n if n > 0 else None
                ci_low, ci_high = wilson_ci(wins, n) if n > 0 else (None, None)
                zt = z_test(n, acc or 0) if n > 0 else {"z_score": None, "p_value_one_tailed": None, "significant_at_05": False}

                # NE_t
                ne_vals = []
                for t in resolved:
                    pm = t.get("p_market")
                    d = t.get("pred_direction")
                    c = t.get("prediction_correct")
                    if pm is not None and d and c is not None:
                        ne = compute_realized_net(d, c, pm)
                        if ne is not None:
                            ne_vals.append(ne)

                ne_total = sum(ne_vals) if ne_vals else None
                ne_per = ne_total / len(ne_vals) if ne_vals else None

                # High divergence subset (top 25%)
                with_div = [t for t in resolved if t.get("p_market") is not None and t.get("pred_proba") is not None]
                if with_div:
                    divs = sorted([abs(t["pred_proba"] - t["p_market"]) for t in with_div])
                    threshold = divs[int(len(divs) * 0.75)] if len(divs) >= 4 else 0
                    high_div = [
                        t for t in with_div
                        if abs(t["pred_proba"] - t["p_market"]) >= threshold
                    ]
                    hd_n = len(high_div)
                    hd_wins = sum(1 for t in high_div if t.get("prediction_correct"))
                    hd_acc = hd_wins / hd_n if hd_n > 0 else None
                    hd_ci = wilson_ci(hd_wins, hd_n) if hd_n > 0 else (None, None)
                else:
                    hd_n, hd_acc, hd_ci = 0, None, (None, None)

                # Direction breakdown
                up = [t for t in resolved if t.get("pred_direction") == "up"]
                down = [t for t in resolved if t.get("pred_direction") == "down"]
                up_wins = sum(1 for t in up if t.get("prediction_correct"))
                down_wins = sum(1 for t in down if t.get("prediction_correct"))

                summaries.append({
                    "model": m,
                    "symbol": s,
                    "contract_duration": str(dur) if dur != "ALL" else "ALL",
                    "total_trades": n,
                    "wins": wins,
                    "losses": losses,
                    "unresolved": unresolved,
                    "accuracy": round(acc, 4) if acc is not None else None,
                    "ci_low": round(ci_low, 4) if ci_low is not None else None,
                    "ci_high": round(ci_high, 4) if ci_high is not None else None,
                    "z_score": zt.get("z_score"),
                    "p_value": zt.get("p_value_one_tailed"),
                    "is_significant": zt.get("significant_at_05"),
                    "realized_net_total": round(ne_total, 4) if ne_total is not None else None,
                    "realized_net_per_trade": round(ne_per, 6) if ne_per is not None else None,
                    "candidates_total": len(all_trades),
                    "gate_pass_rate": round(n / len(all_trades), 4) if all_trades else None,
                    "high_divergence_n": hd_n,
                    "high_divergence_accuracy": round(hd_acc, 4) if hd_acc is not None else None,
                    "high_divergence_ci_low": round(hd_ci[0], 4) if hd_ci[0] is not None else None,
                    "high_divergence_ci_high": round(hd_ci[1], 4) if hd_ci[1] is not None else None,
                    "up_bets_n": len(up),
                    "up_bets_accuracy": round(up_wins / len(up), 4) if up else None,
                    "down_bets_n": len(down),
                    "down_bets_accuracy": round(down_wins / len(down), 4) if down else None,
                })

    return summaries


@router.get("/performance/rolling")
async def performance_rolling(
    model: str = Query(..., description="Model version"),
    symbol: str | None = None,
    n: int = Query(50, ge=10, le=200),
    from_ms: int | None = None,
    to_ms: int | None = None,
):
    """Rolling accuracy time series with variance context."""
    store = get_store()
    resolved = store.get_resolved_trades(model=model, symbol=symbol)

    if from_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) >= from_ms]
    if to_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) <= to_ms]

    resolved.sort(key=lambda t: t.get("ts_model_ran_ms", 0))
    outcomes = [t.get("prediction_correct", False) for t in resolved]
    timestamps = [t.get("ts_model_ran_ms", 0) for t in resolved]

    series = rolling_accuracy_series(outcomes, n=n)

    # Enrich with timestamps and gate status
    gate_threshold = 0.515
    for point in series:
        idx = point["index"] - 1  # 0-indexed
        if idx < len(timestamps):
            point["timestamp_ms"] = timestamps[idx]
        point["gate_pass"] = point["accuracy"] >= gate_threshold
        point["n_in_window"] = n

    current = series[-1]["accuracy"] if series else None
    gate_status = "insufficient_data"
    if current is not None:
        gate_status = "pass" if current >= gate_threshold else "fail"

    # Trend
    trend = "stable"
    if len(series) >= 3:
        last3 = [s["accuracy"] for s in series[-3:]]
        if last3[-1] > last3[0]:
            trend = "up"
        elif last3[-1] < last3[0]:
            trend = "down"

    # Variance context when gate fails
    variance_context = None
    if gate_status == "fail" and len(outcomes) > 0:
        overall_acc = sum(outcomes) / len(outcomes)
        zt = z_test(n, current, overall_acc)
        variance_context = {
            "overall_accuracy": round(overall_acc, 4),
            "current_rolling": current,
            "z_from_true": zt["z_score"],
            "p_value": zt["p_value_one_tailed"],
            "interpretation": (
                f"{current:.1%} rolling is "
                + ("within expected variance" if (zt["p_value_one_tailed"] or 1) > 0.05 else "statistically significant decline")
                + f" for a {overall_acc:.1%} true accuracy model"
                + f" (p={zt['p_value_one_tailed']:.3f})."
                + (" Not yet evidence of edge erosion." if (zt["p_value_one_tailed"] or 1) > 0.05 else " Edge may be eroding.")
            ),
        }

    return {
        "model": model,
        "symbol": symbol or "ALL",
        "window_n": n,
        "gate_threshold": gate_threshold,
        "series": series,
        "current_value": current,
        "gate_status": gate_status,
        "trend": trend,
        "trend_basis": "last 3 windows",
        "variance_context": variance_context,
    }


@router.get("/performance/heatmap")
async def performance_heatmap(
    model: str | None = None,
    symbol: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
):
    """24×7 UTC accuracy grid."""
    from datetime import datetime, timezone
    store = get_store()
    resolved = store.get_resolved_trades(model=model, symbol=symbol)

    if from_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) >= from_ms]
    if to_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) <= to_ms]

    # Build cells: hour × day_of_week
    from collections import defaultdict
    cells_data = defaultdict(lambda: {"n": 0, "wins": 0})

    overnight_hours = set(range(21, 24)) | set(range(0, 4))
    daytime_n, daytime_wins = 0, 0
    overnight_n, overnight_wins = 0, 0

    for t in resolved:
        ts = t.get("ts_model_ran_ms", 0)
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        h = dt.hour
        dow = dt.weekday()  # 0=Mon … 6=Sun; API wants 0=Sun
        api_dow = (dow + 1) % 7  # convert to 0=Sun

        correct = t.get("prediction_correct", False)
        cells_data[(h, api_dow)]["n"] += 1
        cells_data[(h, api_dow)]["wins"] += int(correct)

        if h in overnight_hours:
            overnight_n += 1
            overnight_wins += int(correct)
        else:
            daytime_n += 1
            daytime_wins += int(correct)

    cells = []
    for (h, d), data in sorted(cells_data.items()):
        cells.append({
            "hour_utc": h,
            "day_of_week": d,
            "n": data["n"],
            "wins": data["wins"],
            "accuracy": round(data["wins"] / data["n"], 4) if data["n"] > 0 else None,
        })

    # Best/worst hours (n >= 5)
    hour_agg = defaultdict(lambda: {"n": 0, "wins": 0})
    for (h, _), data in cells_data.items():
        hour_agg[h]["n"] += data["n"]
        hour_agg[h]["wins"] += data["wins"]

    hour_summaries = [
        {"hour_utc": h, "n": d["n"], "accuracy": round(d["wins"] / d["n"], 4)}
        for h, d in hour_agg.items() if d["n"] >= 5
    ]
    best = sorted(hour_summaries, key=lambda x: x["accuracy"], reverse=True)[:5]
    worst = sorted(hour_summaries, key=lambda x: x["accuracy"])[:5]

    return {
        "model": model or "ALL",
        "symbol": symbol or "ALL",
        "cells": cells,
        "daytime_accuracy": round(daytime_wins / daytime_n, 4) if daytime_n > 0 else None,
        "daytime_n": daytime_n,
        "overnight_accuracy": round(overnight_wins / overnight_n, 4) if overnight_n > 0 else None,
        "overnight_n": overnight_n,
        "best_hours": best,
        "worst_hours": worst,
    }


@router.get("/performance/by-divergence")
async def performance_by_divergence(
    model: str | None = None,
    symbol: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
):
    """Divergence bucket breakdown."""
    store = get_store()
    resolved = store.get_resolved_trades(model=model, symbol=symbol)

    if from_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) >= from_ms]
    if to_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) <= to_ms]

    buckets = divergence_bucket_stats(resolved)

    return {
        "model": model or "ALL",
        "symbol": symbol or "ALL",
        "buckets": buckets,
        "high_divergence_threshold": 0.10,
    }


@router.get("/performance/calibration")
async def performance_calibration(
    model: str | None = None,
    symbol: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
    n_bins: int = Query(10, ge=5, le=20),
):
    """Calibration curve data."""
    store = get_store()
    resolved = store.get_resolved_trades(model=model, symbol=symbol)

    if from_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) >= from_ms]
    if to_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) <= to_ms]

    probas = [t.get("pred_proba", 0.5) for t in resolved]
    outcomes = [bool(t.get("prediction_correct")) for t in resolved]

    model_cal = calibration_curve(probas, outcomes, n_bins)

    # Market calibration (p_market vs outcome)
    market_probas = [t.get("p_market") for t in resolved if t.get("p_market") is not None]
    market_outcomes = [bool(t.get("prediction_correct")) for t in resolved if t.get("p_market") is not None]
    market_cal = calibration_curve(market_probas, market_outcomes, n_bins) if market_probas else []

    return {
        "model_calibration": model_cal,
        "market_calibration": market_cal,
    }


@router.get("/performance/funnel")
async def performance_funnel(
    model: str | None = None,
    symbol: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
):
    """Execution funnel."""
    store = get_store()
    trades, _ = store.get_trades(model=model, page_size=100000)

    if symbol:
        trades = [t for t in trades if t.get("symbol") == symbol]
    if from_ms:
        trades = [t for t in trades if t.get("ts_model_ran_ms", 0) >= from_ms]
    if to_ms:
        trades = [t for t in trades if t.get("ts_model_ran_ms", 0) <= to_ms]

    funnel = compute_execution_funnel(trades)

    return {
        "model": model or "ALL",
        "symbol": symbol or "ALL",
        "period": {"from_ms": from_ms, "to_ms": to_ms},
        **funnel,
    }


@router.get("/performance/suppression-effectiveness")
async def suppression_effectiveness(
    model: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
):
    """Validate suppression rules by computing hypothetical P&L."""
    store = get_store()

    rules = [
        ("contract_mismatch", "300s Contract Mismatch"),
        ("utc_blackout", "UTC Overnight Blackout"),
    ]

    results = []
    for reason, label in rules:
        # SQL-backed: pulls full history (not the last-hour cache) so
        # /performance/suppression-effectiveness works over arbitrary windows.
        suppressed, _ = store.get_trades(
            model=model,
            from_ms=from_ms,
            to_ms=to_ms,
            suppressed=True,
            suppressed_reason=reason,
            page_size=100000,
        )

        resolved = [t for t in suppressed if t.get("resolved") and t.get("prediction_correct") is not None]

        if len(resolved) < 20:
            results.append({
                "rule": reason,
                "rule_label": label,
                "period": {"from_ms": from_ms, "to_ms": to_ms},
                "suppressed_n": len(suppressed),
                "resolved_n": len(resolved),
                "hypothetical_accuracy": None,
                "hypothetical_net_per_trade": None,
                "hypothetical_net_total": None,
                "net_saved": None,
                "decision": "INSUFFICIENT_DATA",
                "note": f"Only {len(resolved)} resolved — need ≥20 for assessment.",
            })
            continue

        wins = sum(1 for t in resolved if t.get("prediction_correct"))
        hyp_acc = wins / len(resolved)

        ne_vals = []
        for t in resolved:
            pm = t.get("p_market")
            d = t.get("pred_direction")
            c = t.get("prediction_correct")
            if pm is not None and d and c is not None:
                ne = compute_realized_net(d, c, pm)
                if ne is not None:
                    ne_vals.append(ne)

        hyp_total = sum(ne_vals) if ne_vals else 0
        hyp_per = hyp_total / len(ne_vals) if ne_vals else 0

        if hyp_total < -1:
            decision = "CORRECT"
        elif hyp_total > 1:
            decision = "INCORRECT"
        else:
            decision = "NEUTRAL"

        results.append({
            "rule": reason,
            "rule_label": label,
            "period": {"from_ms": from_ms, "to_ms": to_ms},
            "suppressed_n": len(suppressed),
            "resolved_n": len(resolved),
            "hypothetical_accuracy": round(hyp_acc, 4),
            "hypothetical_net_per_trade": round(hyp_per, 4),
            "hypothetical_net_total": round(hyp_total, 2),
            "net_saved": round(abs(hyp_total), 2) if hyp_total < 0 else 0,
            "decision": decision,
            "note": (
                f"{len(resolved)} suppressed trades resolved; "
                f"accuracy was {hyp_acc:.1%} (${hyp_per:.2f}/trade). "
                + (f"Suppression saved ${abs(hyp_total):.2f} NE_t." if hyp_total < 0
                   else f"Suppression blocked ${hyp_total:.2f} of positive NE_t.")
            ),
        })

    return results

@router.get("/performance/cross-symbol-correlation")
async def cross_symbol_correlation(
    model: str = Query(...),
    from_ms: int | None = None,
    to_ms: int | None = None,
):
    """Pairwise outcome correlation between symbols."""
    import numpy as np
    from scipy import stats
    from itertools import combinations
    
    store = get_store()
    resolved = store.get_resolved_trades(model=model)
    
    if from_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) >= from_ms]
    if to_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) <= to_ms]

    # Group outcomes by prediction window
    windows = {}
    for t in resolved:
        ts = t.get("ts_model_ran_ms", 0)
        sym = t.get("symbol")
        correct = 1 if t.get("prediction_correct") else 0
        
        # Snap to 60s windows
        window = ts // 60000
        if window not in windows:
            windows[window] = {}
        windows[window][sym] = correct

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    pairs = list(combinations(symbols, 2))
    
    results = []
    for s1, s2 in pairs:
        vec1 = []
        vec2 = []
        for w in windows.values():
            if s1 in w and s2 in w:
                vec1.append(w[s1])
                vec2.append(w[s2])
        
        if len(vec1) > 2:
            try:
                # scipy pearsonr does not return a dict, but (statistic, pvalue) in old scipy or an object in newer
                res = stats.pearsonr(vec1, vec2)
                corr = float(res[0])
                p_val = float(res[1])
            except:
                corr, p_val = 0.0, 1.0
        else:
            corr, p_val = 0.0, 1.0
            
        results.append({
            "sym_a": s1,
            "sym_b": s2,
            "n_matched": len(vec1),
            "correlation": round(corr, 4),
            "p_value": round(p_val, 4)
        })
        
    return {"pairs": results}

@router.get("/performance/timeline")
async def performance_timeline(
    from_ms: int | None = None,
    to_ms: int | None = None,
):
    """Cumulative Realized NE_t over time for all recorded models."""
    from collections import defaultdict
    from datetime import datetime, timezone
    
    store = get_store()
    resolved = store.get_resolved_trades()
    
    if from_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) >= from_ms]
    if to_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) <= to_ms]

    # Sort explicitly by time
    resolved.sort(key=lambda x: x.get("ts_model_ran_ms", 0))
    
    cuml = defaultdict(float)
    daily_snapshots = {}
    
    for t in resolved:
        mod = str(t.get("model", "UNKNOWN")).upper()
        if mod == "NONE": mod = "UNKNOWN"
        ts = t.get("ts_model_ran_ms", 0)
        d = t.get("pred_direction")
        c = t.get("prediction_correct")
        pm = t.get("p_market")
        
        if d and c is not None and pm is not None:
            net = compute_realized_net(d, c, pm)
            if net is not None:
                cuml[mod] += net
                
        # Group by UTC day
        dt = datetime.fromtimestamp(ts/1000.0, tz=timezone.utc)
        day_str = dt.strftime("%Y-%m-%d")
        
        # Overwrite means we keep the END of day snapshot
        item = {
            "name": dt.strftime("%b %d"),
            "timestamp_ms": ts,
        }
        for m, v in cuml.items():
            item[m] = round(v, 4)
            
        daily_snapshots[day_str] = item
            
    # Convert dict to sorted list
    timeline = []
    for day_str in sorted(daily_snapshots.keys()):
        timeline.append(daily_snapshots[day_str])
        
    return {"data": timeline}


@router.get("/performance/portfolio")
async def portfolio_performance(
    model: str | None = None,
    symbol: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
):
    """Aggregate portfolio-level performance metrics."""
    from services.metrics import portfolio_metrics
    store = get_store()
    resolved = store.get_resolved_trades(model=model, symbol=symbol)
    if from_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) >= from_ms]
    if to_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) <= to_ms]
    return portfolio_metrics(resolved)


@router.get("/performance/threshold-sweep")
async def threshold_sweep_performance(
    model: str | None = None,
    symbol: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
):
    """Win rate and ROI at each confidence threshold level."""
    from services.metrics import threshold_sweep
    store = get_store()
    resolved = store.get_resolved_trades(model=model, symbol=symbol)
    if from_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) >= from_ms]
    if to_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) <= to_ms]
    return {"sweep": threshold_sweep(resolved)}


@router.get("/performance/pareto")
async def pareto_frontier_endpoint(
    model: str | None = None,
    symbol: str | None = None,
):
    """Efficient frontier (non-dominated threshold/win-rate/ROI points)."""
    from services.metrics import threshold_sweep, pareto_frontier
    store = get_store()
    resolved = store.get_resolved_trades(model=model, symbol=symbol)
    sweep = threshold_sweep(resolved)
    return {"frontier": pareto_frontier(sweep)}

