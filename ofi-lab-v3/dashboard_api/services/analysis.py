"""Analysis service — model+filter optimization.

All heavy lifting for the /api/analysis/* endpoints and the CLI.
Routers and CLI are thin wrappers; logic lives here.

Reuses metrics.py primitives — does NOT re-implement Wilson CI, z-test,
threshold sweep, or NE_t math.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from typing import Any

try:
    from services.metrics import (
        compute_realized_net,
        wilson_ci,
        z_test,
        SYSTEM_FEE,
    )
except ModuleNotFoundError:
    from dashboard_api.services.metrics import (  # type: ignore[no-redef]
        compute_realized_net,
        wilson_ci,
        z_test,
        SYSTEM_FEE,
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALL_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
ALL_WINDOWS = [300, 900, 1800]
DEFAULT_THRESHOLDS = [round(0.50 + i * 0.01, 2) for i in range(21)]  # 0.50–0.70


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_db():
    """Import at call-time to respect monkeypatching in tests."""
    try:
        from services.db import get_db
    except ModuleNotFoundError:
        from dashboard_api.services.db import get_db  # type: ignore[assignment]
    return get_db()


def _load_resolved_predictions(
    db,
    symbol: str | None,
    market_window: int | None,
    since_ms: int | None,
    model: str | None = None,
) -> list[dict]:
    """
    Pull resolved, non-warmup predictions from the DB.

    Returns dicts with at least:
      model_name, symbol, market_window_seconds,
      pred_proba_calibrated, pred_direction, prediction_correct,
      p_market, ts_contract_open_ms, utc_hour, day_of_week,
      relative_spread, p_model_minus_market
    """
    clauses = ["resolved = 1", "prediction_correct IS NOT NULL", "warmup = 0"]
    params: list[Any] = []

    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol)
    if market_window:
        clauses.append("market_window_seconds = ?")
        params.append(market_window)
    if since_ms:
        clauses.append("ts_contract_open_ms >= ?")
        params.append(since_ms)
    if model:
        clauses.append("model_name = ?")
        params.append(model)

    where = " AND ".join(clauses)
    sql = f"""
        SELECT
            model_name, symbol, market_window_seconds,
            pred_proba_calibrated, pred_proba_raw,
            pred_direction, prediction_correct,
            p_market, p_model_minus_market,
            ts_contract_open_ms,
            utc_hour, day_of_week, is_weekend,
            relative_spread,
            ev_estimate
        FROM predictions
        WHERE {where}
        ORDER BY ts_contract_open_ms
    """
    rows = db.execute(sql, params).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["prediction_correct"] = bool(d["prediction_correct"])
        # Compute divergence
        pp = d.get("pred_proba_calibrated") or 0.5
        pm = d.get("p_market") or 0.5
        d["divergence"] = abs(pp - pm)
        result.append(d)
    return result


def _load_filter_config(db, model_name: str) -> dict:
    """Fetch filter_config_json from model_registry for a model."""
    row = db.execute(
        "SELECT filter_config_json FROM model_registry WHERE name = ?",
        (model_name,),
    ).fetchone()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _pnl_series(preds: list[dict]) -> list[float]:
    """Compute per-trade realized NE_t for each prediction (skip if p_market None)."""
    vals = []
    for p in preds:
        pm = p.get("p_market")
        direction = p.get("pred_direction")
        correct = p.get("prediction_correct")
        if pm is None or direction is None or correct is None:
            continue
        ne = compute_realized_net(direction, correct, pm, SYSTEM_FEE)
        if ne is not None:
            vals.append(ne)
    return vals


def _brier_score(preds: list[dict]) -> float | None:
    """Mean squared error between calibrated proba and binary outcome."""
    vals = []
    for p in preds:
        pp = p.get("pred_proba_calibrated")
        correct = p.get("prediction_correct")
        if pp is None or correct is None:
            continue
        # For "up" direction correct → outcome=1; otherwise outcome=0.
        # Brier score on calibrated probability (direction-agnostic side-confidence).
        # Use max(pp, 1-pp) as the "confidence" side, outcome = correct.
        side_p = max(pp, 1 - pp)
        outcome = int(correct)
        vals.append((side_p - outcome) ** 2)
    return round(sum(vals) / len(vals), 6) if vals else None


def _sharpe(pnl_vals: list[float]) -> float | None:
    n = len(pnl_vals)
    if n < 2:
        return None
    mean_pnl = statistics.mean(pnl_vals)
    std_pnl = statistics.stdev(pnl_vals)
    if std_pnl == 0:
        return None
    return round(mean_pnl / std_pnl * math.sqrt(n), 4)


def _model_summary(model_name: str, preds: list[dict], filter_config: dict) -> dict:
    """Compute full leaderboard row for a model's predictions."""
    n = len(preds)
    wins = sum(1 for p in preds if p["prediction_correct"])
    win_rate = wins / n if n > 0 else 0.0
    ci_lo, ci_hi = wilson_ci(wins, n)
    zt = z_test(n, win_rate)

    pnl_vals = _pnl_series(preds)
    n_pnl = len(pnl_vals)
    total_pnl = sum(pnl_vals)
    avg_pnl = total_pnl / n_pnl if n_pnl > 0 else 0.0
    roi_pct = avg_pnl * 100

    sharpe = _sharpe(pnl_vals)
    brier = _brier_score(preds)

    # Trade count — predictions that were trade-eligible (approximate: use all resolved)
    # Total pnl in USDC assumes $1 stake per trade
    return {
        "model_name": model_name,
        "symbol": preds[0]["symbol"] if preds else "",
        "market_window_seconds": preds[0]["market_window_seconds"] if preds else 0,
        "n_samples": n,
        "n_correct": wins,
        "win_rate": round(win_rate, 6),
        "win_rate_ci_lo": round(ci_lo, 6),
        "win_rate_ci_hi": round(ci_hi, 6),
        "p_value_vs_50pct": round(zt["p_value_one_tailed"], 6) if zt["p_value_one_tailed"] is not None else None,
        "brier_score": brier,
        "ev_per_trade": round(avg_pnl, 6),
        "roi_pct": round(roi_pct, 4),
        "sharpe": sharpe,
        "n_resolved_trades": n_pnl,
        "total_pnl_usdc": round(total_pnl, 4),
        "avg_pnl_per_trade": round(avg_pnl, 6),
        "current_filter_config": filter_config,
    }


# ---------------------------------------------------------------------------
# Endpoint A: Leaderboard
# ---------------------------------------------------------------------------

SORT_KEY_MAP = {
    "win_rate": "win_rate",
    "roi": "roi_pct",
    "ev_per_trade": "ev_per_trade",
    "brier_score": "brier_score",   # ascending (lower is better)
    "n_samples": "n_samples",
    "sharpe": "sharpe",
}


def compute_leaderboard(
    symbol: str | None = None,
    market_window: int | None = None,
    min_samples: int = 50,
    metric: str = "win_rate",
    since_ms: int | None = None,
    limit: int = 100,
) -> list[dict]:
    db = _get_db()
    preds = _load_resolved_predictions(db, symbol, market_window, since_ms)

    # Group by (model, symbol, window)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for p in preds:
        key = (p["model_name"], p["symbol"], p["market_window_seconds"])
        groups[key].append(p)

    rows = []
    for (model_name, sym, window), group_preds in groups.items():
        if len(group_preds) < min_samples:
            continue
        fc = _load_filter_config(db, model_name)
        row = _model_summary(model_name, group_preds, fc)
        row["symbol"] = sym
        row["market_window_seconds"] = window
        rows.append(row)

    # Sort
    ascending = metric == "brier_score"
    sort_key = SORT_KEY_MAP.get(metric, "win_rate")

    def _sort_val(r):
        v = r.get(sort_key)
        if v is None:
            return float("inf") if ascending else float("-inf")
        return v

    rows.sort(key=_sort_val, reverse=(not ascending))
    return rows[:limit]


# ---------------------------------------------------------------------------
# Endpoint B: Threshold Grid
# ---------------------------------------------------------------------------

def _sweep_for_preds(preds: list[dict], thresholds: list[float], min_samples: int) -> dict:
    """For a set of preds, sweep thresholds and return rows + best_threshold."""
    rows = []
    for t_val in thresholds:
        # Keep predictions where the calibrated proba is beyond the threshold
        # i.e. max(pp, 1-pp) >= t_val  (symmetrical — above OR below)
        filtered = [
            p for p in preds
            if max(p.get("pred_proba_calibrated", 0.5),
                   1 - p.get("pred_proba_calibrated", 0.5)) >= t_val
        ]
        n = len(filtered)
        if n == 0:
            rows.append({
                "threshold": round(t_val, 2),
                "n": 0,
                "win_rate": None,
                "roi_pct": None,
                "total_pnl": None,
            })
            continue
        wins = sum(1 for p in filtered if p["prediction_correct"])
        wr = wins / n
        pnl_vals = _pnl_series(filtered)
        n_pnl = len(pnl_vals)
        total_pnl = sum(pnl_vals)
        roi_pct = (total_pnl / n_pnl) * 100 if n_pnl > 0 else 0.0
        rows.append({
            "threshold": round(t_val, 2),
            "n": n,
            "win_rate": round(wr, 6),
            "roi_pct": round(roi_pct, 4),
            "total_pnl": round(total_pnl, 4),
        })

    # Best threshold = max ROI where n >= min_samples
    eligible = [r for r in rows if r["n"] >= min_samples and r["roi_pct"] is not None]
    if not eligible:
        # Fallback: max win_rate where n >= min_samples / 2
        eligible2 = [r for r in rows if r["n"] >= max(1, min_samples // 2) and r["win_rate"] is not None]
        best = max(eligible2, key=lambda r: r["win_rate"]) if eligible2 else (rows[0] if rows else None)
    else:
        best = max(eligible, key=lambda r: r["roi_pct"])

    return {"rows": rows, "best": best}


def compute_threshold_grid(
    symbol: str,
    market_window: int,
    min_samples: int = 50,
    since_ms: int | None = None,
    limit_models: int = 30,
    thresholds: list[float] | None = None,
) -> dict:
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS

    db = _get_db()
    preds = _load_resolved_predictions(db, symbol, market_window, since_ms)

    # Group by model
    by_model: dict[str, list[dict]] = defaultdict(list)
    for p in preds:
        by_model[p["model_name"]].append(p)

    models_output = []
    for model_name, model_preds in list(by_model.items())[:limit_models]:
        if len(model_preds) < min_samples:
            continue
        sweep_result = _sweep_for_preds(model_preds, thresholds, min_samples)
        best = sweep_result["best"]
        models_output.append({
            "model_name": model_name,
            "rows": sweep_result["rows"],
            "best_threshold": {
                "threshold": best["threshold"],
                "win_rate": best["win_rate"],
                "roi_pct": best["roi_pct"],
                "n": best["n"],
            } if best else None,
        })

    return {
        "symbol": symbol,
        "market_window_seconds": market_window,
        "thresholds": thresholds,
        "models": models_output,
    }


# ---------------------------------------------------------------------------
# Endpoint C: Committee Sim
# ---------------------------------------------------------------------------

def compute_committee_sim(
    symbol: str,
    market_window: int,
    strategy: str = "avg",
    since_ms: int | None = None,
) -> dict:
    db = _get_db()
    preds = _load_resolved_predictions(db, symbol, market_window, since_ms)

    # Group by boundary
    by_boundary: dict[int, list[dict]] = defaultdict(list)
    for p in preds:
        by_boundary[p["ts_contract_open_ms"]].append(p)

    n_boundaries = 0
    committee_wins = 0
    committee_pnl: list[float] = []

    for boundary_ts, group in by_boundary.items():
        if len(group) < 1:
            continue

        # Determine committee direction
        if strategy == "avg":
            avg_pp = sum(p["pred_proba_calibrated"] for p in group) / len(group)
            # avg_pp > 0.5 → up, else → down
            committee_direction = "up" if avg_pp >= 0.5 else "down"
            # Use avg confidence for NE_t
            effective_pp = avg_pp
        elif strategy == "vote":
            up_votes = sum(1 for p in group if p["pred_direction"] == "up")
            down_votes = len(group) - up_votes
            if up_votes == down_votes:
                continue  # tie → skip
            committee_direction = "up" if up_votes > down_votes else "down"
            effective_pp = max(p["pred_proba_calibrated"] for p in group if p["pred_direction"] == committee_direction)
        elif strategy == "weighted_ev":
            # Weight by ev_estimate if available, else equal weight
            weights = []
            for p in group:
                ev = p.get("ev_estimate")
                weights.append(ev if ev is not None and ev > 0 else 1.0)
            total_w = sum(weights) or 1.0
            weighted_pp = sum(p["pred_proba_calibrated"] * w for p, w in zip(group, weights)) / total_w
            committee_direction = "up" if weighted_pp >= 0.5 else "down"
            effective_pp = weighted_pp
        else:
            avg_pp = sum(p["pred_proba_calibrated"] for p in group) / len(group)
            committee_direction = "up" if avg_pp >= 0.5 else "down"
            effective_pp = avg_pp

        # Determine actual outcome: all predictions in a boundary share the same result
        # Use first prediction's outcome
        # For committee: correct if committee_direction matches contract_result
        # We infer from prediction_correct + pred_direction
        ref_pred = group[0]
        actual_up_won = (
            ref_pred["prediction_correct"] == (ref_pred["pred_direction"] == "up")
        )
        # actual_up_won: True means "up" was correct
        committee_correct = (committee_direction == "up") == actual_up_won

        # Compute p_market as average across models for this boundary
        p_markets = [p["p_market"] for p in group if p["p_market"] is not None]
        p_market = sum(p_markets) / len(p_markets) if p_markets else 0.5

        ne = compute_realized_net(committee_direction, committee_correct, p_market)
        if ne is not None:
            committee_pnl.append(ne)

        n_boundaries += 1
        if committee_correct:
            committee_wins += 1

    committee_wr = committee_wins / n_boundaries if n_boundaries > 0 else 0.0
    pnl_n = len(committee_pnl)
    committee_roi = (sum(committee_pnl) / pnl_n) * 100 if pnl_n > 0 else 0.0

    # Find best single model for comparison
    by_model: dict[str, list[dict]] = defaultdict(list)
    for p in preds:
        by_model[p["model_name"]].append(p)

    best_single: dict | None = None
    for model_name, model_preds in by_model.items():
        if len(model_preds) < 10:
            continue
        n = len(model_preds)
        wins = sum(1 for p in model_preds if p["prediction_correct"])
        wr = wins / n
        pnl_vals = _pnl_series(model_preds)
        n_pnl = len(pnl_vals)
        roi = (sum(pnl_vals) / n_pnl) * 100 if n_pnl > 0 else 0.0
        if best_single is None or wr > best_single["win_rate"]:
            best_single = {"model_name": model_name, "win_rate": round(wr, 6), "roi_pct": round(roi, 4)}

    # Recommendation
    if best_single is not None:
        delta = committee_wr - best_single["win_rate"]
        recommendation = "committee" if delta > 0 else "single_model"
    else:
        delta = 0.0
        recommendation = "committee" if n_boundaries > 0 else "no_data"

    return {
        "symbol": symbol,
        "market_window_seconds": market_window,
        "strategy": strategy,
        "n_boundaries": n_boundaries,
        "committee_win_rate": round(committee_wr, 6),
        "committee_roi_pct": round(committee_roi, 4),
        "best_single_model": best_single,
        "recommendation": recommendation,
        "delta_pct_points": round(delta * 100, 4),
    }


# ---------------------------------------------------------------------------
# Endpoint D: Skip Conditions
# ---------------------------------------------------------------------------

def _quartile_label(value: float, sorted_vals: list[float]) -> int:
    """Return 1-4 quartile bucket for value."""
    n = len(sorted_vals)
    if n == 0:
        return 1
    pct = sum(1 for v in sorted_vals if v <= value) / n
    if pct <= 0.25:
        return 1
    elif pct <= 0.5:
        return 2
    elif pct <= 0.75:
        return 3
    return 4


def compute_skip_conditions(
    symbol: str | None = None,
    market_window: int | None = None,
    min_bucket_size: int = 30,
    since_ms: int | None = None,
) -> dict:
    db = _get_db()
    preds = _load_resolved_predictions(db, symbol, market_window, since_ms)

    if not preds:
        return {
            "symbol": symbol or "ALL",
            "window": market_window or "ALL",
            "buckets": {},
            "skip_recommendations": {},
        }

    # Pre-compute quartile breakpoints for spread and divergence
    spreads = sorted(p["relative_spread"] for p in preds if p.get("relative_spread") is not None)
    divs = sorted(p["divergence"] for p in preds if p.get("divergence") is not None)

    # --- utc_hour ---
    hour_buckets: dict[int, list[bool]] = defaultdict(list)
    for p in preds:
        h = p.get("utc_hour")
        if h is not None:
            hour_buckets[h].append(p["prediction_correct"])

    utc_hour_rows = []
    for h in range(24):
        outcomes = hour_buckets.get(h, [])
        n = len(outcomes)
        wins = sum(outcomes)
        wr = wins / n if n > 0 else None
        utc_hour_rows.append({
            "bucket": h,
            "n": n,
            "win_rate": round(wr, 4) if wr is not None else None,
            "skip_recommended": (n >= min_bucket_size and wr is not None and wr < 0.50),
        })

    # --- day_of_week ---
    dow_buckets: dict[int, list[bool]] = defaultdict(list)
    for p in preds:
        d = p.get("day_of_week")
        if d is not None:
            dow_buckets[d].append(p["prediction_correct"])

    dow_rows = []
    for d in range(7):
        outcomes = dow_buckets.get(d, [])
        n = len(outcomes)
        wins = sum(outcomes)
        wr = wins / n if n > 0 else None
        dow_rows.append({
            "bucket": d,
            "n": n,
            "win_rate": round(wr, 4) if wr is not None else None,
            "skip_recommended": (n >= min_bucket_size and wr is not None and wr < 0.50),
        })

    # --- is_weekend ---
    weekend_buckets: dict[bool, list[bool]] = defaultdict(list)
    for p in preds:
        iw = p.get("is_weekend")
        if iw is not None:
            weekend_buckets[bool(iw)].append(p["prediction_correct"])

    weekend_rows = []
    for val in [False, True]:
        outcomes = weekend_buckets.get(val, [])
        n = len(outcomes)
        wins = sum(outcomes)
        wr = wins / n if n > 0 else None
        weekend_rows.append({
            "bucket": val,
            "n": n,
            "win_rate": round(wr, 4) if wr is not None else None,
            "skip_recommended": (n >= min_bucket_size and wr is not None and wr < 0.50),
        })

    # --- relative_spread quartile ---
    spread_q_buckets: dict[int, list[bool]] = defaultdict(list)
    for p in preds:
        rs = p.get("relative_spread")
        if rs is not None:
            q = _quartile_label(rs, spreads)
            spread_q_buckets[q].append(p["prediction_correct"])

    spread_rows = []
    for q in range(1, 5):
        outcomes = spread_q_buckets.get(q, [])
        n = len(outcomes)
        wins = sum(outcomes)
        wr = wins / n if n > 0 else None
        spread_rows.append({
            "bucket": q,
            "n": n,
            "win_rate": round(wr, 4) if wr is not None else None,
            "skip_recommended": (n >= min_bucket_size and wr is not None and wr < 0.50),
        })

    # --- divergence bucket (|pred_proba - p_market|) ---
    div_q_buckets: dict[int, list[bool]] = defaultdict(list)
    for p in preds:
        div = p.get("divergence")
        if div is not None:
            q = _quartile_label(div, divs)
            div_q_buckets[q].append(p["prediction_correct"])

    div_rows = []
    for q in range(1, 5):
        outcomes = div_q_buckets.get(q, [])
        n = len(outcomes)
        wins = sum(outcomes)
        wr = wins / n if n > 0 else None
        div_rows.append({
            "bucket": q,
            "n": n,
            "win_rate": round(wr, 4) if wr is not None else None,
            "skip_recommended": (n >= min_bucket_size and wr is not None and wr < 0.50),
        })

    # --- calibrated_p_range (0.50-0.55, 0.55-0.60, ...) ---
    p_range_labels = ["0.50-0.55", "0.55-0.60", "0.60-0.65", "0.65-0.70", "0.70+"]
    p_range_bounds = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.01)]
    p_range_buckets: dict[str, list[bool]] = defaultdict(list)
    for p in preds:
        pp = p.get("pred_proba_calibrated")
        if pp is None:
            continue
        side = max(pp, 1 - pp)
        for label, (lo, hi) in zip(p_range_labels, p_range_bounds):
            if lo <= side < hi:
                p_range_buckets[label].append(p["prediction_correct"])
                break

    p_range_rows = []
    for label in p_range_labels:
        outcomes = p_range_buckets.get(label, [])
        n = len(outcomes)
        wins = sum(outcomes)
        wr = wins / n if n > 0 else None
        p_range_rows.append({
            "bucket": label,
            "n": n,
            "win_rate": round(wr, 4) if wr is not None else None,
            "skip_recommended": (n >= min_bucket_size and wr is not None and wr < 0.50),
        })

    # --- Aggregate skip_recommendations ---
    blackout_hours = [
        r["bucket"] for r in utc_hour_rows if r["skip_recommended"]
    ]

    # Max spread quartile to skip: if Q4 is bad, skip_top_spread_quartile=True
    skip_top_spread = any(
        r["skip_recommended"] for r in spread_rows if r["bucket"] == 4
    )
    max_spread_quartile = 3 if skip_top_spread else None

    # Skip high divergence if Q4 div bucket has win_rate < 0.50
    skip_high_div = any(
        r["skip_recommended"] for r in div_rows if r["bucket"] == 4
    )

    return {
        "symbol": symbol or "ALL",
        "window": market_window or "ALL",
        "buckets": {
            "utc_hour": utc_hour_rows,
            "relative_spread_quartile": spread_rows,
            "divergence_bucket": div_rows,
            "calibrated_p_range": p_range_rows,
            "day_of_week": dow_rows,
            "is_weekend": weekend_rows,
        },
        "skip_recommendations": {
            "blackout_hours": sorted(blackout_hours),
            "max_spread_quartile": max_spread_quartile,
            "skip_high_divergence": skip_high_div,
        },
    }


# ---------------------------------------------------------------------------
# Endpoint E: Full Report
# ---------------------------------------------------------------------------

_TRADES_PER_DAY_APPROX = {300: 288, 900: 96, 1800: 48}


def compute_full_report(
    symbol: str | None = None,
    market_window: int | None = None,
    since_ms: int | None = None,
) -> dict:
    symbols = [symbol] if symbol else ALL_SYMBOLS
    windows = [market_window] if market_window else ALL_WINDOWS

    results = []
    recommended_configs = []

    for sym in symbols:
        for win in windows:
            leaderboard = compute_leaderboard(
                symbol=sym,
                market_window=win,
                min_samples=50,
                metric="win_rate",
                since_ms=since_ms,
                limit=10,
            )
            threshold_grid = compute_threshold_grid(
                symbol=sym,
                market_window=win,
                min_samples=50,
                since_ms=since_ms,
                limit_models=10,
            )
            committee = compute_committee_sim(
                symbol=sym,
                market_window=win,
                strategy="avg",
                since_ms=since_ms,
            )
            skip = compute_skip_conditions(
                symbol=sym,
                market_window=win,
                since_ms=since_ms,
            )

            results.append({
                "symbol": sym,
                "market_window_seconds": win,
                "leaderboard": leaderboard,
                "threshold_grid": threshold_grid,
                "committee_sim": committee,
                "skip_conditions": skip,
            })

            # --- Build recommended_config ---
            if not leaderboard:
                recommended_configs.append({
                    "symbol": sym,
                    "window": win,
                    "recommended_strategy": "no_data",
                    "recommended_model": None,
                    "recommended_filter_config": {},
                    "expected_win_rate": None,
                    "expected_roi_pct": None,
                    "expected_n_trades_per_day": None,
                })
                continue

            top_model = leaderboard[0]
            use_committee = committee["recommendation"] == "committee"
            strategy = "committee" if use_committee else "single_model"

            # Best threshold for top model from grid
            best_conf_threshold = 0.55  # default
            best_roi = top_model["roi_pct"]
            best_wr = top_model["win_rate"]
            for m in threshold_grid["models"]:
                if m["model_name"] == top_model["model_name"] and m["best_threshold"]:
                    best_conf_threshold = m["best_threshold"]["threshold"]
                    best_roi = m["best_threshold"]["roi_pct"] or best_roi
                    best_wr = m["best_threshold"]["win_rate"] or best_wr
                    break

            # Estimate n_trades_per_day: fraction of predictions passing threshold × daily boundary count
            approx_daily = _TRADES_PER_DAY_APPROX.get(win, 96)
            n_at_best = None
            for m in threshold_grid["models"]:
                if m["model_name"] == top_model["model_name"] and m["best_threshold"]:
                    n_at_best = m["best_threshold"]["n"]
                    break
            total_preds = top_model["n_samples"] or 1
            frac = n_at_best / total_preds if n_at_best else 0.5
            expected_n = max(1, round(approx_daily * frac))

            blackout = skip["skip_recommendations"].get("blackout_hours", [])

            recommended_configs.append({
                "symbol": sym,
                "window": win,
                "recommended_strategy": strategy,
                "recommended_model": top_model["model_name"] if not use_committee else None,
                "recommended_filter_config": {
                    "confidence_threshold": best_conf_threshold,
                    "ev_threshold": 0.003,
                    "blackout_hours": blackout,
                    "warmup_seconds": 1800,
                },
                "expected_win_rate": round(best_wr, 4) if best_wr else None,
                "expected_roi_pct": round(best_roi, 4) if best_roi else None,
                "expected_n_trades_per_day": expected_n,
            })

    return {
        "results": results,
        "recommended_configs": recommended_configs,
    }
