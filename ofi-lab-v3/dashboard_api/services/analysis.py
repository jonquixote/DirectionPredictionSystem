"""Analysis service — model+filter optimization.

All heavy lifting for the /api/analysis/* endpoints and the CLI.
Routers and CLI are thin wrappers; logic lives here.

Reuses metrics.py primitives — does NOT re-implement Wilson CI, z-test,
threshold sweep, or NE_t math.
"""
from __future__ import annotations

import itertools
import json
import math
import random
import statistics
import time as _time
from collections import defaultdict
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Module-level TTL in-memory cache  (T1.1)
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, Any]] = {}  # key → (computed_at_ts, payload)
_CACHE_TTL_SECS = 300  # 5-minute boundary cycle


def _cache_get(key: str) -> Any | None:
    entry = _CACHE.get(key)
    if entry and (_time.time() - entry[0]) < _CACHE_TTL_SECS:
        return entry[1]
    return None


def _cache_put(key: str, payload: Any) -> None:
    _CACHE[key] = (_time.time(), payload)


# ---------------------------------------------------------------------------
# Persistent SQLite cache for slow endpoints  (T1.2)
#
# Survives worker restarts and is shared across uvicorn workers. The
# background loop in dashboard_api.main repopulates these rows every 5 min so
# warm reads return in <50 ms. We reuse _get_db() (the same monkeypatchable
# helper used by analysis fetches) so tests transparently share the test DB.
# ---------------------------------------------------------------------------

_ANALYSIS_CACHE_TABLE_READY: dict[int, bool] = {}  # keyed by id(db) — defensive


def _ensure_analysis_cache_table(db) -> None:
    """Create analysis_cache table on first use if init_schema hasn't run.

    The dashboard API process does not call init_schema (only writers do),
    so this guards against a fresh DB or a DB created before this migration
    landed. Idempotent — uses CREATE TABLE IF NOT EXISTS.
    """
    cache_id = id(db)
    if _ANALYSIS_CACHE_TABLE_READY.get(cache_id):
        return
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS analysis_cache ("
            "  key TEXT PRIMARY KEY,"
            "  payload_json TEXT NOT NULL,"
            "  computed_at_ms INTEGER NOT NULL,"
            "  duration_ms INTEGER"
            ")"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_cache_computed_at"
            " ON analysis_cache(computed_at_ms)"
        )
        try:
            db.commit()
        except Exception:
            pass
        _ANALYSIS_CACHE_TABLE_READY[cache_id] = True
    except Exception:
        # Read-only or contended — fall through; helpers will no-op on failure.
        pass


def _persistent_cache_get(key: str, max_age_ms: int = 300_000) -> dict | None:
    """Read cached blob if computed within max_age_ms; else None.

    Returns the decoded JSON payload (dict) on hit. Never raises — any
    error (missing table, malformed JSON, DB unavailable) is swallowed so
    the caller falls through to live computation.
    """
    try:
        db = _get_db()
    except Exception:
        return None
    try:
        _ensure_analysis_cache_table(db)
        now_ms = int(_time.time() * 1000)
        row = db.execute(
            "SELECT payload_json, computed_at_ms FROM analysis_cache WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        computed_at_ms = row[1] if not hasattr(row, "keys") else row["computed_at_ms"]
        if (now_ms - int(computed_at_ms)) > max_age_ms:
            return None
        payload_json = row[0] if not hasattr(row, "keys") else row["payload_json"]
        return json.loads(payload_json)
    except Exception:
        return None


def _persistent_cache_put(key: str, payload: dict, duration_ms: int) -> None:
    """Upsert cache row via INSERT OR REPLACE. Best-effort — never raises."""
    try:
        db = _get_db()
    except Exception:
        return
    try:
        _ensure_analysis_cache_table(db)
        payload_json = json.dumps(payload, default=str)
        now_ms = int(_time.time() * 1000)
        db.execute(
            "INSERT OR REPLACE INTO analysis_cache"
            " (key, payload_json, computed_at_ms, duration_ms)"
            " VALUES (?, ?, ?, ?)",
            (key, payload_json, now_ms, int(duration_ms)),
        )
        try:
            db.commit()
        except Exception:
            pass
    except Exception:
        return

try:
    import numpy as np
    from scipy.optimize import minimize
    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False

try:
    from services.metrics import (
        compute_realized_net,
        wilson_ci,
        z_test,
        pareto_frontier,
        SYSTEM_FEE,
    )
except ModuleNotFoundError:
    from dashboard_api.services.metrics import (  # type: ignore[no-redef]
        compute_realized_net,
        wilson_ci,
        z_test,
        pareto_frontier,
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
    # T1.1 — in-memory TTL cache for the most-called DB fetch
    _cache_key = f"_load_resolved:{symbol}:{market_window}:{since_ms}:{model}"
    _cached = _cache_get(_cache_key)
    if _cached is not None:
        return _cached

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
    _cache_put(_cache_key, result)
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
    # T1.1 outer cache
    _ck = f"committee_sim:{symbol}:{market_window}:{strategy}:{since_ms}"
    _hit = _cache_get(_ck)
    if _hit is not None:
        return _hit
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

    _result = {
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
    _cache_put(_ck, _result)
    return _result


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
    # T1.1 outer cache (in-memory, per-worker)
    _ck = f"skip_conditions:{symbol or '_all'}:{market_window or '_all'}:{min_bucket_size}:{since_ms or '_all'}"
    _hit = _cache_get(_ck)
    if _hit is not None:
        return _hit
    # T1.2 persistent cache (shared across workers, repopulated by bg loop)
    _persisted = _persistent_cache_get(_ck)
    if _persisted is not None:
        _cache_put(_ck, _persisted)
        return _persisted
    _t0 = _time.time()
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

    _result = {
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
    _cache_put(_ck, _result)
    _persistent_cache_put(_ck, _result, int((_time.time() - _t0) * 1000))
    return _result


# ---------------------------------------------------------------------------
# Endpoint E: Full Report
# ---------------------------------------------------------------------------

_TRADES_PER_DAY_APPROX = {300: 288, 900: 96, 1800: 48}


def compute_full_report(
    symbol: str | None = None,
    market_window: int | None = None,
    since_ms: int | None = None,
) -> dict:
    # T1.1 outer cache — most expensive endpoint
    _ck = f"full_report:{symbol or '_all'}:{market_window or '_all'}:{since_ms or '_all'}"
    _hit = _cache_get(_ck)
    if _hit is not None:
        return _hit
    # T1.2 persistent cache (shared across workers, repopulated by bg loop)
    _persisted = _persistent_cache_get(_ck)
    if _persisted is not None:
        _cache_put(_ck, _persisted)
        return _persisted
    _t0 = _time.time()
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

    # Merge results + recommended_configs into a `pairs` array (one per
    # symbol+window). Match frontend types (Analysis.tsx FullReport).
    from datetime import datetime, timezone as _tz
    pairs = []
    rec_by_key = {(rc["symbol"], rc["window"]): rc for rc in recommended_configs}
    for r in results:
        key = (r["symbol"], r["market_window_seconds"])
        pairs.append({
            "symbol": r["symbol"],
            "window": r["market_window_seconds"],
            "leaderboard": r["leaderboard"],
            "threshold_grid": r["threshold_grid"],
            # Frontend expects committee_sims as array — give them just the
            # `avg` strategy result for now (the other strategies are still
            # available via /api/analysis/committee-sim with ?strategy=).
            "committee_sims": [r["committee_sim"]] if r["committee_sim"] else [],
            "skip_conditions": r["skip_conditions"],
            "recommended_config": rec_by_key.get(key, {}),
        })
    _result = {
        "generated_at": datetime.now(_tz.utc).isoformat(),
        "pairs": pairs,
        # Back-compat aliases for existing CLI + tests that read the legacy keys.
        "results": results,
        "recommended_configs": recommended_configs,
    }
    _cache_put(_ck, _result)
    _persistent_cache_put(_ck, _result, int((_time.time() - _t0) * 1000))
    return _result


# ===========================================================================
# v2 — Premium Filter Discovery
#
# Adds 8 new public functions + supporting helpers. All return the strict
# envelope {status, message, metadata, result, warnings} (H3). Uses BH-FDR
# correction (H1), strict-< temporal join for decay (H2), unsafe-column
# guard (H2), power-adequacy flag (H6), and explicit convergence handling
# for scipy.optimize (H8).
# ===========================================================================

from datetime import datetime as _dt, timezone as _tzv2  # noqa: E402

UNSAFE_FILTER_COLUMNS = {
    "prediction_correct", "contract_result", "price_at_close",
    "ts_resolved_ms", "resolved", "trade_result",
}

SUPPORTED_FILTER_KEYS = {
    "confidence_threshold", "ev_threshold", "blackout_hours",
    "max_relative_spread", "regime_volatility", "regime_liquidity",
    "regime_trend", "consensus_required", "max_book_age_seconds",
    "min_recency_weighted_ev", "min_p_market_edge",
    "skip_high_divergence_quartile",
    "regime_gates",
}


def _envelope(
    status: str = "ok",
    message: str | None = None,
    metadata: dict | None = None,
    result: Any = None,
    warnings: list | None = None,
) -> dict:
    """H3 — strict envelope contract for every v2 endpoint."""
    return {
        "status": status,
        "message": message,
        "metadata": metadata or {},
        "result": result,
        "warnings": warnings or [],
    }


def _now_iso() -> str:
    return _dt.now(_tzv2.utc).isoformat()


def _validate_filter_config(filter_config: dict) -> None:
    """H2 — raise if filter uses any post-resolution column. Fail loudly."""
    keys = set(filter_config or {})
    leaky = keys & UNSAFE_FILTER_COLUMNS
    if leaky:
        raise AssertionError(
            f"filter_config contains post-resolution column(s): {sorted(leaky)}. "
            f"These are NOT known at prediction time and would leak future info."
        )
    unknown = keys - SUPPORTED_FILTER_KEYS
    if unknown:
        # Unknown keys allowed (forward-compat) but warned
        return list(sorted(unknown))
    return None


def _power_adequate(n: int, lift_pp: float = 5.0) -> bool:
    """H6 — n required for 80% power to detect `lift_pp` lift vs 50% at α=0.05.

    Approx via normal approximation. 5pp lift → ~304 samples; 8pp → ~208.
    """
    if lift_pp >= 10:
        return n >= 144
    if lift_pp >= 8:
        return n >= 208
    if lift_pp >= 5:
        return n >= 304
    return n >= 500


def _load_decay_state_for_predictions(db, preds: list[dict]) -> dict:
    """H2 — for each (model, symbol, window), find the LATEST decay_metrics
    row whose ts_ms < prediction.ts_contract_open_ms (strict <).

    Returns: {(model, symbol, window, prediction_ts_ms): {recency_weighted_ev,
              rolling_win_rate, ...}} for use as a per-prediction decay state.

    Uses denormalized `ts_ms` column added in H2 migration. If the column is
    NULL on legacy rows, falls back to strftime conversion.
    """
    if not preds:
        return {}
    # Group predictions by (model, symbol, window)
    keys = set()
    for p in preds:
        keys.add((p["model_name"], p["symbol"], p["market_window_seconds"]))
    if not keys:
        return {}
    # Pre-load all decay history for these keys, sorted ascending by ts_ms
    decay_by_key: dict = {}
    for (mn, sym, win) in keys:
        rows = db.execute("""
            SELECT
              model_name, symbol, market_window_seconds,
              COALESCE(ts_ms,
                       CAST(strftime('%s', ts) AS INTEGER) * 1000
              ) AS effective_ts_ms,
              COALESCE(computed_for_max_ts_ms, 0) AS sentinel_ts_ms,
              recency_weighted_ev, rolling_win_rate,
              rolling_ev, brier_score, calibration_error, sample_count
            FROM decay_metrics
            WHERE model_name = ? AND symbol = ? AND market_window_seconds = ?
            ORDER BY effective_ts_ms ASC
        """, (mn, sym, win)).fetchall()
        decay_by_key[(mn, sym, win)] = [dict(r) for r in rows]
    # For each prediction, bisect to find latest decay snapshot with strict <
    out = {}
    for p in preds:
        key = (p["model_name"], p["symbol"], p["market_window_seconds"])
        history = decay_by_key.get(key, [])
        pred_ts = p["ts_contract_open_ms"]
        latest = None
        for row in history:
            if row["effective_ts_ms"] >= pred_ts:
                break
            # H2 defensive cross-check: sentinel must also be strictly before
            if row["sentinel_ts_ms"] >= pred_ts:
                continue
            latest = row
        if latest is not None:
            out[(key[0], key[1], key[2], pred_ts)] = latest
    return out


def _load_consensus_for_predictions(db, preds: list[dict]) -> dict:
    """Group predictions' boundaries and load model_overlap rows. Returns
    {(symbol, window, ts_contract_open_ms): {consensus, weighted_confidence}}.
    """
    if not preds:
        return {}
    keys = set()
    for p in preds:
        keys.add((p["symbol"], p["market_window_seconds"], p["ts_contract_open_ms"]))
    if not keys:
        return {}
    # Single query — pull all overlap rows for these symbol-window pairs
    sym_win = {(k[0], k[1]) for k in keys}
    out = {}
    for (sym, win) in sym_win:
        rows = db.execute("""
            SELECT ts_contract_open_ms, symbol, market_window_seconds,
                   consensus, consensus_direction, weighted_confidence
            FROM model_overlap
            WHERE symbol = ? AND market_window_seconds = ?
        """, (sym, win)).fetchall()
        for r in rows:
            out[(sym, win, r["ts_contract_open_ms"])] = dict(r)
    return out


def _apply_filter_row(p: dict, filter_config: dict,
                     decay_lookup: dict, consensus_lookup: dict,
                     divergence_q3: float | None = None) -> bool:
    """Returns True if prediction passes ALL configured filters."""
    fc = filter_config

    # Resolve per-regime threshold overrides (shared with live trader).
    try:
        from filters.regime_gate import resolve_thresholds
    except ModuleNotFoundError:
        from ofi_lab_v3.filters.regime_gate import resolve_thresholds  # type: ignore
    regime_for_row = {
        "volatility": p.get("regime_volatility"),
        "liquidity": p.get("regime_liquidity"),
        "trend": p.get("regime_trend"),
    }
    ct, et = resolve_thresholds(
        fc, regime_for_row, fc.get("confidence_threshold"), fc.get("ev_threshold"),
    )

    # Confidence threshold (side-confidence: max(p, 1-p))
    if ct is not None:
        pp = p.get("pred_proba_calibrated") or 0.5
        side = max(pp, 1.0 - pp)
        if side < ct:
            return False

    # EV threshold
    if et is not None:
        ev = p.get("ev_estimate")
        if ev is None or ev < et:
            return False

    # Blackout hours
    blackout = fc.get("blackout_hours")
    if blackout:
        utc_hour = p.get("utc_hour")
        if utc_hour is not None and utc_hour in blackout:
            return False

    # Max relative spread
    msp = fc.get("max_relative_spread")
    if msp is not None:
        rs = p.get("relative_spread")
        if rs is not None and rs > msp:
            return False

    # Regime allow-lists (None = all OK)
    rvol = fc.get("regime_volatility")
    if rvol:
        if (p.get("regime_volatility") or "unknown") not in rvol:
            return False
    rliq = fc.get("regime_liquidity")
    if rliq:
        if (p.get("regime_liquidity") or "unknown") not in rliq:
            return False
    rtrend = fc.get("regime_trend")
    if rtrend:
        if (p.get("regime_trend") or "unknown") not in rtrend:
            return False

    # Min p_market edge
    edge = fc.get("min_p_market_edge")
    if edge is not None:
        pm = p.get("p_market")
        pp = p.get("pred_proba_calibrated")
        if pm is None or pp is None or abs(pp - pm) < edge:
            return False

    # Skip top divergence quartile
    if fc.get("skip_high_divergence_quartile"):
        if divergence_q3 is None:
            return False
        if p.get("divergence", 0.0) >= divergence_q3:
            return False

    # Consensus required
    if fc.get("consensus_required"):
        ckey = (p["symbol"], p["market_window_seconds"], p["ts_contract_open_ms"])
        ov = consensus_lookup.get(ckey)
        if not ov or not ov.get("consensus"):
            return False

    # Min recency_weighted_ev (decay state)
    min_rwev = fc.get("min_recency_weighted_ev")
    if min_rwev is not None:
        dkey = (p["model_name"], p["symbol"], p["market_window_seconds"],
                p["ts_contract_open_ms"])
        state = decay_lookup.get(dkey)
        if not state:
            return False  # No decay info available → conservative skip
        rwev = state.get("recency_weighted_ev")
        if rwev is None or rwev < min_rwev:
            return False

    return True


def _max_drawdown(pnls: list[float]) -> float:
    """Cumulative peak-to-trough max drawdown."""
    if not pnls:
        return 0.0
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for v in pnls:
        cum += v
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 4)


def _metrics_from_preds(passed: list[dict]) -> dict:
    """Compute win/CI/p/PnL metrics from a filtered prediction list."""
    n = len(passed)
    if n == 0:
        return {
            "n_passed": 0, "win_rate": None,
            "wilson_ci": None, "p_value": None,
            "mean_pnl_per_trade": None, "roi_pct": None,
            "sharpe": None, "total_pnl": None, "max_drawdown": None,
        }
    wins = sum(1 for x in passed if x.get("prediction_correct"))
    win_rate = wins / n
    ci_lo, ci_hi = wilson_ci(wins, n)
    zt = z_test(n, win_rate)
    p_value = zt.get("p_value_one_tailed")
    pnls = _pnl_series(passed)
    n_pnl = len(pnls)
    mean_pnl = sum(pnls) / n_pnl if n_pnl else None
    sharpe = _sharpe(pnls)
    return {
        "n_passed": n,
        "win_rate": round(win_rate, 6),
        "wilson_ci": [round(ci_lo, 6), round(ci_hi, 6)],
        "p_value": round(p_value, 6) if p_value is not None else None,
        "mean_pnl_per_trade": round(mean_pnl, 6) if mean_pnl is not None else None,
        "roi_pct": round(mean_pnl * 100, 4) if mean_pnl is not None else None,
        "sharpe": sharpe,
        "total_pnl": round(sum(pnls), 4) if pnls else None,
        "max_drawdown": _max_drawdown(pnls),
    }


# ---------------------------------------------------------------------------
# v2 endpoint A: simulate_filter
# ---------------------------------------------------------------------------

def simulate_filter(
    filter_config: dict,
    symbol: str,
    window: int,
    since_ms: int | None = None,
    bootstrap_n: int = 0,
    _preloaded_preds: list[dict] | None = None,
    _preloaded_decay: dict | None = None,
    _preloaded_consensus: dict | None = None,
) -> dict:
    """Apply filter_config to historical resolved predictions.

    Returns envelope. H2 — validates filter_config keys; AssertionError if
    any unsafe (post-resolution) column appears. H3 — strict envelope.
    """
    started = _now_iso()
    warnings: list[str] = []

    # H2 — safety check
    unknown_keys = _validate_filter_config(filter_config)
    if unknown_keys:
        warnings.append(f"unknown filter keys (ignored): {unknown_keys}")

    db = _get_db()
    if _preloaded_preds is None:
        preds = _load_resolved_predictions(db, symbol, window, since_ms)
    else:
        preds = _preloaded_preds
    n_in = len(preds)

    if n_in == 0:
        return _envelope(
            status="no_data",
            message=f"no resolved predictions for {symbol}/{window}",
            metadata={"symbol": symbol, "window": window,
                      "n_input": 0, "since_ms": since_ms,
                      "computed_at_utc": started},
            warnings=warnings,
        )

    decay_lookup = _preloaded_decay
    if decay_lookup is None and filter_config.get("min_recency_weighted_ev") is not None:
        decay_lookup = _load_decay_state_for_predictions(db, preds)
    decay_lookup = decay_lookup or {}

    consensus_lookup = _preloaded_consensus
    if consensus_lookup is None and filter_config.get("consensus_required"):
        consensus_lookup = _load_consensus_for_predictions(db, preds)
    consensus_lookup = consensus_lookup or {}

    # Precompute divergence Q3 if needed
    div_q3 = None
    if filter_config.get("skip_high_divergence_quartile"):
        divs = sorted(p.get("divergence", 0.0) for p in preds)
        if divs:
            div_q3 = divs[int(len(divs) * 0.75)]

    passed = [p for p in preds if _apply_filter_row(
        p, filter_config, decay_lookup, consensus_lookup, div_q3)]

    metrics = _metrics_from_preds(passed)

    # Bootstrap CI on win_rate
    bootstrap_ci = None
    if bootstrap_n > 0 and len(passed) >= 5:
        wins = sum(1 for p in passed if p.get("prediction_correct"))
        n = len(passed)
        # Simple bootstrap: resample wins from binomial
        rng = __import__("random").Random(42)
        boot_rates = []
        for _ in range(bootstrap_n):
            resampled_wins = sum(1 for _ in range(n) if rng.random() < (wins / n))
            boot_rates.append(resampled_wins / n)
        boot_rates.sort()
        lo_idx = int(0.025 * bootstrap_n)
        hi_idx = int(0.975 * bootstrap_n)
        bootstrap_ci = [round(boot_rates[lo_idx], 6), round(boot_rates[hi_idx], 6)]

    status = "ok"
    if metrics["n_passed"] == 0:
        status = "no_data"
        warnings.append("filter rejected all predictions")
    elif metrics["n_passed"] < 5:
        status = "insufficient_samples"
        warnings.append(f"only {metrics['n_passed']} passed — metrics weak")

    result = {
        "filter_config": filter_config,
        **metrics,
        "bootstrap_ci": bootstrap_ci,
        "power_adequate_5pp": _power_adequate(metrics["n_passed"], 5.0),
        "power_adequate_8pp": _power_adequate(metrics["n_passed"], 8.0),
    }

    return _envelope(
        status=status, message=None,
        metadata={"symbol": symbol, "window": window,
                  "n_input": n_in, "n_passed": metrics["n_passed"],
                  "since_ms": since_ms, "computed_at_utc": started},
        result=result, warnings=warnings,
    )


# ---------------------------------------------------------------------------
# v2 endpoint B: grid_search with BH-FDR correction (H1)
# ---------------------------------------------------------------------------

DEFAULT_GRID = {
    "confidence_threshold": [0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.65],
    "ev_threshold": [None, 0.0, 0.002, 0.005, 0.01],
    "regime_volatility": [None, ["low"], ["medium", "high"]],
    "consensus_required": [False, True],
    "min_recency_weighted_ev": [None, 0.0, 0.01],
    "blackout_hours": [
        None,
        list(range(21, 24)) + list(range(0, 4)),  # current H60
        list(range(0, 6)),
        list(range(18, 24)) + list(range(0, 6)),
    ],
}


def grid_search(
    symbol: str,
    window: int,
    grid: dict | None = None,
    since_ms: int | None = None,
    top_k: int = 20,
    min_n_passed: int = 100,
    max_p_value: float = 0.05,
    apply_fdr: bool = True,
) -> dict:
    """Sweep filter configs over Cartesian grid. Apply BH-FDR (H1).

    Returns envelope. result.top = top_k ranked by ROI subject to
    survives_fdr_q05=True AND n_passed>=min_n_passed.
    """
    started = _now_iso()
    g = grid or DEFAULT_GRID
    db = _get_db()
    preds = _load_resolved_predictions(db, symbol, window, since_ms)
    n_in = len(preds)

    if n_in == 0:
        return _envelope(
            status="no_data",
            message="no resolved predictions",
            metadata={"symbol": symbol, "window": window,
                      "n_input": 0, "computed_at_utc": started},
        )

    # Pre-load decay + consensus lookups ONCE (used across all combos that need them)
    decay_lookup = _load_decay_state_for_predictions(db, preds) \
        if any(v is not None for v in g.get("min_recency_weighted_ev", [None]) if v is not None) \
        else {}
    consensus_lookup = _load_consensus_for_predictions(db, preds) \
        if any(g.get("consensus_required", [False])) else {}

    # Precompute divergence Q3
    divs = sorted(p.get("divergence", 0.0) for p in preds)
    div_q3 = divs[int(len(divs) * 0.75)] if divs else None

    # Cartesian product
    keys = list(g.keys())
    value_lists = [g[k] for k in keys]
    combos: list[dict] = []
    from itertools import product
    for combo in product(*value_lists):
        fc = {k: v for k, v in zip(keys, combo) if v is not None or k == "consensus_required"}
        # consensus_required can be False (a valid value), keep it
        if "consensus_required" in fc and not fc["consensus_required"]:
            del fc["consensus_required"]
        combos.append(fc)

    rows = []
    for fc in combos:
        passed = [p for p in preds if _apply_filter_row(
            p, fc, decay_lookup, consensus_lookup, div_q3)]
        m = _metrics_from_preds(passed)
        row = {"filter_config": fc, **m}
        row["power_adequate_5pp"] = _power_adequate(m["n_passed"], 5.0)
        rows.append(row)

    # H1 — BH-FDR correction
    n_combos = len(rows)
    n_n_gate = sum(1 for r in rows if r["n_passed"] >= min_n_passed)
    n_sig_uncorrected = 0
    n_sig_fdr = 0

    if apply_fdr and rows:
        try:
            from statsmodels.stats.multitest import multipletests
            p_vals = [r["p_value"] if r["p_value"] is not None else 1.0 for r in rows]
            reject, p_adj, _, _ = multipletests(p_vals, alpha=0.05, method="fdr_bh")
            for i, r in enumerate(rows):
                r["p_value_raw"] = r["p_value"]
                r["p_value_bh_adjusted"] = round(float(p_adj[i]), 6)
                r["survives_fdr_q05"] = bool(reject[i])
                if r["p_value"] is not None and r["p_value"] < 0.05:
                    n_sig_uncorrected += 1
                if r["survives_fdr_q05"]:
                    n_sig_fdr += 1
        except ImportError:
            for r in rows:
                r["p_value_raw"] = r["p_value"]
                r["p_value_bh_adjusted"] = None
                r["survives_fdr_q05"] = (r["p_value"] is not None
                                          and r["p_value"] < max_p_value)
                if r["survives_fdr_q05"]:
                    n_sig_fdr += 1
    else:
        for r in rows:
            r["p_value_raw"] = r["p_value"]
            r["p_value_bh_adjusted"] = None
            r["survives_fdr_q05"] = (r["p_value"] is not None
                                      and r["p_value"] < max_p_value)
            if r["survives_fdr_q05"]:
                n_sig_fdr += 1

    # Recommendation gate: n_passed >= min_n + survives FDR + roi positive
    qualifying = [
        r for r in rows
        if r["n_passed"] >= min_n_passed
        and r["survives_fdr_q05"]
        and (r["roi_pct"] is not None)
    ]
    qualifying.sort(
        key=lambda r: (r["roi_pct"] or float("-inf"), r["win_rate"] or 0),
        reverse=True,
    )
    top = qualifying[:top_k]

    # Pareto frontier on (win_rate, roi_pct) of FDR-surviving combos
    pareto_input = [
        (r.get("win_rate") or 0, r.get("roi_pct") or 0, r)
        for r in qualifying
    ]
    pareto = []
    for i, (wr_i, roi_i, r_i) in enumerate(pareto_input):
        dominated = False
        for j, (wr_j, roi_j, _) in enumerate(pareto_input):
            if i == j:
                continue
            if wr_j >= wr_i and roi_j >= roi_i and (wr_j > wr_i or roi_j > roi_i):
                dominated = True
                break
        if not dominated:
            pareto.append(r_i)

    status = "ok"
    if not qualifying:
        status = "no_data"
        msg = (f"no combo passed: {n_n_gate} met n>={min_n_passed}; "
               f"{n_sig_uncorrected} significant raw; {n_sig_fdr} survived BH-FDR")
    else:
        msg = None

    return _envelope(
        status=status, message=msg,
        metadata={
            "symbol": symbol, "window": window,
            "n_input": n_in, "since_ms": since_ms,
            "computed_at_utc": started,
            "n_combos_tried": n_combos,
            "n_combos_passing_n_gate": n_n_gate,
            "n_combos_passing_raw_significance": n_sig_uncorrected,
            "n_combos_passing_fdr_significance": n_sig_fdr,
        },
        result={"top": top, "pareto": pareto},
    )


# ---------------------------------------------------------------------------
# v2 endpoint C: regime_matrix
# ---------------------------------------------------------------------------

def regime_matrix(
    symbol: str | None = None,
    window: int | None = None,
    since_ms: int | None = None,
    min_cell_n: int = 30,
) -> dict:
    """Per (model, regime_vol, regime_liq, regime_trend): win rate cells.

    Returns envelope.result.models list with per-model cells.
    """
    started = _now_iso()
    db = _get_db()
    preds = _load_resolved_predictions(db, symbol, window, since_ms)
    n_in = len(preds)
    if n_in == 0:
        return _envelope(
            status="no_data",
            message=f"no resolved predictions for {symbol}/{window}",
            metadata={"symbol": symbol, "window": window,
                      "n_input": 0, "computed_at_utc": started},
        )
    # Group by (model, symbol, window, rv, rl, rt)
    groups: dict = defaultdict(list)
    for p in preds:
        rv = p.get("regime_volatility") or "unknown"
        rl = p.get("regime_liquidity") or "unknown"
        rt = p.get("regime_trend") or "unknown"
        key = (p["model_name"], p["symbol"], p["market_window_seconds"], rv, rl, rt)
        groups[key].append(p)
    # Note: predictions table may not have regime_* columns populated for older rows
    # Aggregate per model
    by_model: dict = defaultdict(list)
    for (mn, sym, win, rv, rl, rt), rows in groups.items():
        n = len(rows)
        wins = sum(1 for x in rows if x.get("prediction_correct"))
        wr = wins / n if n else 0.0
        ci_lo, ci_hi = wilson_ci(wins, n)
        zt = z_test(n, wr)
        pv = zt.get("p_value_one_tailed")
        pnls = _pnl_series(rows)
        mean_pnl = (sum(pnls) / len(pnls)) if pnls else None
        recommended = (wr > 0.55 and n >= min_cell_n and pv is not None and pv < 0.05)
        cell = {
            "regime": {"volatility": rv, "liquidity": rl, "trend": rt},
            "n": n,
            "win_rate": round(wr, 6),
            "wilson_ci": [round(ci_lo, 6), round(ci_hi, 6)],
            "p_value": round(pv, 6) if pv is not None else None,
            "mean_pnl": round(mean_pnl, 6) if mean_pnl is not None else None,
            "recommended": recommended,
            "power_adequate_5pp": _power_adequate(n, 5.0),
        }
        by_model[(mn, sym, win)].append(cell)
    models = []
    for (mn, sym, win), cells in sorted(by_model.items()):
        cells.sort(key=lambda c: c["win_rate"], reverse=True)
        models.append({
            "model_name": mn, "symbol": sym, "window": win,
            "cells": cells,
            "best_cell": cells[0] if cells else None,
        })
    return _envelope(
        status="ok",
        metadata={"symbol": symbol, "window": window,
                  "n_input": n_in, "computed_at_utc": started},
        result={"models": models},
    )


# ---------------------------------------------------------------------------
# v2 endpoint D: consensus_analysis
# ---------------------------------------------------------------------------

def consensus_analysis(symbol: str, window: int,
                       since_ms: int | None = None) -> dict:
    """Compare boundaries where models agree (consensus=1) vs split."""
    started = _now_iso()
    db = _get_db()
    preds = _load_resolved_predictions(db, symbol, window, since_ms)
    if not preds:
        return _envelope(
            status="no_data",
            message="no resolved predictions",
            metadata={"symbol": symbol, "window": window,
                      "n_input": 0, "computed_at_utc": started},
        )
    # Join with overlap
    overlap = _load_consensus_for_predictions(db, preds)
    if not overlap:
        return _envelope(
            status="no_data",
            message="no model_overlap rows for this (symbol, window)",
            metadata={"symbol": symbol, "window": window,
                      "n_input": len(preds), "computed_at_utc": started},
        )
    # Build per-boundary outcome (use any one prediction's correctness; all
    # models at the same boundary share the same underlying truth).
    boundary_outcomes: dict = {}
    boundary_overlap: dict = {}
    for p in preds:
        b = p["ts_contract_open_ms"]
        if b not in boundary_outcomes:
            boundary_outcomes[b] = p.get("prediction_correct")
        ov = overlap.get((symbol, window, b))
        if ov:
            boundary_overlap[b] = ov
    # Categorize
    consensus_outcomes = []
    split_outcomes = []
    wc_values = []  # (weighted_confidence, correct)
    for b, correct in boundary_outcomes.items():
        ov = boundary_overlap.get(b)
        if ov is None or correct is None:
            continue
        wc_values.append((ov.get("weighted_confidence") or 0.5, correct))
        if ov.get("consensus"):
            consensus_outcomes.append(correct)
        else:
            split_outcomes.append(correct)

    def _stats(outs):
        n = len(outs)
        if n == 0:
            return {"n": 0, "win_rate": None, "wilson_ci": None, "p_value": None}
        wins = sum(1 for x in outs if x)
        wr = wins / n
        ci = wilson_ci(wins, n)
        pv = z_test(n, wr).get("p_value_one_tailed")
        return {
            "n": n, "win_rate": round(wr, 6),
            "wilson_ci": [round(ci[0], 6), round(ci[1], 6)],
            "p_value": round(pv, 6) if pv is not None else None,
        }

    cs = _stats(consensus_outcomes)
    ss = _stats(split_outcomes)
    delta_pp = None
    if cs["win_rate"] is not None and ss["win_rate"] is not None:
        delta_pp = round((cs["win_rate"] - ss["win_rate"]) * 100, 4)

    # Weighted-confidence quartile breakdown
    wc_values.sort()
    quartiles = []
    if wc_values:
        n_wc = len(wc_values)
        for q in range(4):
            lo_idx = (q * n_wc) // 4
            hi_idx = ((q + 1) * n_wc) // 4
            chunk = wc_values[lo_idx:hi_idx]
            if chunk:
                wins = sum(1 for _, c in chunk if c)
                quartiles.append({
                    "quartile": q + 1,
                    "wc_range": [round(chunk[0][0], 4), round(chunk[-1][0], 4)],
                    "n": len(chunk),
                    "win_rate": round(wins / len(chunk), 6),
                })

    return _envelope(
        status="ok",
        metadata={"symbol": symbol, "window": window,
                  "n_input": len(preds),
                  "computed_at_utc": started},
        result={
            "n_total_boundaries": len(boundary_outcomes),
            "n_consensus": cs["n"], "n_split": ss["n"],
            "consensus": cs, "split": ss,
            "delta_pct_points": delta_pp,
            "weighted_confidence_quartiles": quartiles,
        },
    )


# ---------------------------------------------------------------------------
# v2 endpoint E: decay_filter_analysis
# ---------------------------------------------------------------------------

DECAY_BUCKETS = [
    (float("-inf"), 0.0),
    (0.0, 0.005),
    (0.005, 0.01),
    (0.01, 0.02),
    (0.02, float("inf")),
]


def decay_filter_analysis(symbol: str | None = None,
                          window: int | None = None,
                          since_ms: int | None = None,
                          min_bucket_n: int = 30) -> dict:
    """Bucket predictions by their model's recency_weighted_ev (decay state)
    at the time of prediction; report win_rate per bucket; recommend
    min_recency_weighted_ev threshold per model.
    """
    started = _now_iso()
    db = _get_db()
    preds = _load_resolved_predictions(db, symbol, window, since_ms)
    if not preds:
        return _envelope(
            status="no_data",
            message="no resolved predictions",
            metadata={"symbol": symbol, "window": window,
                      "n_input": 0, "computed_at_utc": started},
        )
    decay_lookup = _load_decay_state_for_predictions(db, preds)
    # Group by (model, symbol, window)
    grouped: dict = defaultdict(list)
    for p in preds:
        dkey = (p["model_name"], p["symbol"], p["market_window_seconds"],
                p["ts_contract_open_ms"])
        state = decay_lookup.get(dkey)
        rwev = state.get("recency_weighted_ev") if state else None
        if rwev is None:
            continue
        key = (p["model_name"], p["symbol"], p["market_window_seconds"])
        grouped[key].append((rwev, p))

    models_out = []
    for (mn, sym, win), entries in sorted(grouped.items()):
        buckets = []
        recommended_thresh = None
        for (lo, hi) in DECAY_BUCKETS:
            sub = [p for (rwev, p) in entries if lo <= rwev < hi]
            n = len(sub)
            if n == 0:
                buckets.append({"range": [lo if lo != float("-inf") else None,
                                          hi if hi != float("inf") else None],
                                "n": 0, "win_rate": None})
                continue
            wins = sum(1 for x in sub if x.get("prediction_correct"))
            wr = wins / n
            buckets.append({
                "range": [lo if lo != float("-inf") else None,
                          hi if hi != float("inf") else None],
                "n": n, "win_rate": round(wr, 6),
            })
            if recommended_thresh is None and wr >= 0.52 and n >= min_bucket_n:
                recommended_thresh = lo if lo != float("-inf") else 0.0
        models_out.append({
            "model_name": mn, "symbol": sym, "window": win,
            "buckets": buckets,
            "recommended_min_recency_weighted_ev": recommended_thresh,
            "n_total": sum(b["n"] for b in buckets),
        })

    status = "ok" if models_out else "no_data"
    return _envelope(
        status=status,
        message=None if models_out else "no decay snapshots intersect resolved predictions",
        metadata={"symbol": symbol, "window": window,
                  "n_input": len(preds), "computed_at_utc": started},
        result={"models": models_out},
    )


# ---------------------------------------------------------------------------
# v2 endpoint F: optimize_committee_weights (scipy.optimize)
# ---------------------------------------------------------------------------

def optimize_committee_weights(symbol: str, window: int,
                               objective: str = "sharpe",
                               since_ms: int | None = None,
                               min_boundaries: int = 50) -> dict:
    """Find per-model weights that maximize Sharpe/mean_pnl/win_rate of the
    weighted ensemble. Writes to committee_weights table on success.
    """
    started = _now_iso()
    db = _get_db()
    preds = _load_resolved_predictions(db, symbol, window, since_ms)
    if not preds:
        return _envelope(
            status="no_data",
            message="no resolved predictions",
            metadata={"symbol": symbol, "window": window,
                      "n_input": 0, "computed_at_utc": started},
        )
    # Group by boundary
    boundaries: dict = defaultdict(list)
    for p in preds:
        boundaries[p["ts_contract_open_ms"]].append(p)
    # Only keep boundaries where the SAME set of models is present (intersection)
    model_counts: dict = defaultdict(int)
    for rows in boundaries.values():
        for r in rows:
            model_counts[r["model_name"]] += 1
    total_b = len(boundaries)
    # Models present in ≥80% of boundaries
    common_models = [m for m, c in model_counts.items() if c >= 0.8 * total_b]
    common_models.sort()
    if len(common_models) < 2:
        return _envelope(
            status="insufficient_samples",
            message=f"only {len(common_models)} models common across boundaries",
            metadata={"symbol": symbol, "window": window,
                      "n_input": len(preds), "computed_at_utc": started},
        )
    # Build matrix
    boundary_rows = []
    for b_ts in sorted(boundaries.keys()):
        rows = {r["model_name"]: r for r in boundaries[b_ts]}
        if not all(m in rows for m in common_models):
            continue
        outcome = rows[common_models[0]].get("prediction_correct")
        p_market = rows[common_models[0]].get("p_market")
        if outcome is None or p_market is None:
            continue
        proba_vec = [rows[m]["pred_proba_calibrated"] for m in common_models]
        boundary_rows.append({
            "probas": proba_vec,
            "outcome": outcome,
            "p_market": p_market,
        })
    n_boundaries = len(boundary_rows)
    if n_boundaries < min_boundaries:
        return _envelope(
            status="insufficient_samples",
            message=f"{n_boundaries} aligned boundaries < min {min_boundaries}",
            metadata={"symbol": symbol, "window": window,
                      "n_input": len(preds), "computed_at_utc": started},
        )

    # Try scipy.optimize
    converged = True
    warnings_list: list = []
    try:
        import numpy as _np
        from scipy.optimize import minimize as _minimize
        P = _np.array([br["probas"] for br in boundary_rows])  # (B, M)
        outcomes = _np.array([1.0 if br["outcome"] else 0.0 for br in boundary_rows])
        p_markets = _np.array([br["p_market"] for br in boundary_rows])

        def _pnl_array(weights: _np.ndarray) -> _np.ndarray:
            committee_p = P @ weights
            # Up if > 0.5, down otherwise
            directions = (committee_p > 0.5).astype(int)
            correct = (directions == outcomes.astype(int))
            # Realized net per trade: side bought at p_market, won pays (1 - p_market) - fee
            pnl = _np.where(correct,
                            _np.where(directions == 1, 1 - p_markets, p_markets) - SYSTEM_FEE,
                            -1.0 * _np.where(directions == 1, p_markets, 1 - p_markets) - SYSTEM_FEE)
            return pnl

        def _neg_objective(w):
            w = _np.clip(w, 0, None)
            s = w.sum()
            if s <= 1e-9:
                return 1e6
            w = w / s
            pnl = _pnl_array(w)
            if objective == "sharpe":
                if len(pnl) < 2 or pnl.std() == 0:
                    return 1e6
                return float(-(pnl.mean() / pnl.std() * (len(pnl) ** 0.5)))
            elif objective == "mean":
                return float(-pnl.mean())
            elif objective == "win_rate":
                wr = ((P @ w > 0.5) == outcomes.astype(int)).mean()
                return float(-wr)
            return 1e6

        n_m = len(common_models)
        x0 = _np.full(n_m, 1.0 / n_m)
        bounds = [(0.0, 1.0)] * n_m
        constraints = ({"type": "eq", "fun": lambda w: w.sum() - 1.0},)
        res = _minimize(_neg_objective, x0, method="SLSQP",
                       bounds=bounds, constraints=constraints,
                       options={"maxiter": 200, "ftol": 1e-6})
        if not res.success:
            converged = False
            warnings_list.append(f"scipy.optimize.minimize did not converge: {res.message}")
            weights = x0  # fallback uniform
        else:
            weights = _np.clip(res.x, 0, None)
            weights = weights / weights.sum()

        committee_pnl = _pnl_array(weights)
        committee_p = P @ weights
        committee_dirs = (committee_p > 0.5).astype(int)
        win_rate = float(((committee_dirs == outcomes.astype(int)).mean()))
        mean_pnl = float(committee_pnl.mean())
        sharpe_val = (float(committee_pnl.mean() / committee_pnl.std() * (len(committee_pnl) ** 0.5))
                      if committee_pnl.std() > 0 else None)

        # Baselines
        uniform_w = _np.full(n_m, 1.0 / n_m)
        uniform_pnl = _pnl_array(uniform_w)
        uniform_wr = float(((P @ uniform_w > 0.5) == outcomes.astype(int)).mean())
        uniform_mean = float(uniform_pnl.mean())
        uniform_sharpe = (float(uniform_pnl.mean() / uniform_pnl.std() * (len(uniform_pnl) ** 0.5))
                         if uniform_pnl.std() > 0 else None)

        best_single = {"model_name": None, "win_rate": -1.0,
                       "mean_pnl": None, "sharpe": None, "roi_pct": None}
        for i, m in enumerate(common_models):
            w_single = _np.zeros(n_m)
            w_single[i] = 1.0
            s_pnl = _pnl_array(w_single)
            s_wr = float(((P @ w_single > 0.5) == outcomes.astype(int)).mean())
            if s_wr > best_single["win_rate"]:
                s_mean = float(s_pnl.mean())
                s_sharpe = (float(s_pnl.mean() / s_pnl.std() * (len(s_pnl) ** 0.5))
                            if s_pnl.std() > 0 else None)
                best_single = {"model_name": m, "win_rate": round(s_wr, 6),
                                "mean_pnl": round(s_mean, 6),
                                "sharpe": round(s_sharpe, 4) if s_sharpe else None,
                                "roi_pct": round(s_mean * 100, 4)}

        weights_dict = {m: round(float(w), 6) for m, w in zip(common_models, weights)}

        # Persist to committee_weights table
        try:
            db.execute("""
                INSERT INTO committee_weights
                  (symbol, market_window_seconds, objective, weights_json,
                   n_boundaries, expected_sharpe, expected_win_rate,
                   expected_roi_pct, converged, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                ON CONFLICT(symbol, market_window_seconds, objective) DO UPDATE SET
                  weights_json = excluded.weights_json,
                  n_boundaries = excluded.n_boundaries,
                  expected_sharpe = excluded.expected_sharpe,
                  expected_win_rate = excluded.expected_win_rate,
                  expected_roi_pct = excluded.expected_roi_pct,
                  converged = excluded.converged,
                  computed_at = excluded.computed_at
            """, (symbol, window, objective, json.dumps(weights_dict, sort_keys=True),
                  n_boundaries, sharpe_val, win_rate, mean_pnl * 100,
                  1 if converged else 0))
            try:
                db.commit()
            except Exception:
                pass
        except Exception as e:
            warnings_list.append(f"could not persist committee_weights: {e}")

        return _envelope(
            status="ok" if converged else "convergence_failed",
            message=None if converged else "scipy did not converge; uniform fallback",
            metadata={"symbol": symbol, "window": window,
                      "n_input": len(preds), "n_boundaries": n_boundaries,
                      "n_models": n_m,
                      "computed_at_utc": started},
            result={
                "objective": objective,
                "converged": converged,
                "models": common_models,
                "weights": weights_dict,
                "sharpe": round(sharpe_val, 4) if sharpe_val else None,
                "win_rate": round(win_rate, 6),
                "mean_pnl": round(mean_pnl, 6),
                "roi_pct": round(mean_pnl * 100, 4),
                "baseline_uniform": {
                    "sharpe": round(uniform_sharpe, 4) if uniform_sharpe else None,
                    "win_rate": round(uniform_wr, 6),
                    "mean_pnl": round(uniform_mean, 6),
                    "roi_pct": round(uniform_mean * 100, 4),
                },
                "baseline_best_single": best_single,
                "delta_vs_uniform_pct_points": round((win_rate - uniform_wr) * 100, 4),
                "delta_vs_best_single_pct_points": round(
                    (win_rate - best_single["win_rate"]) * 100, 4)
                    if best_single["win_rate"] >= 0 else None,
            },
            warnings=warnings_list,
        )
    except ImportError as e:
        return _envelope(
            status="error",
            message=f"scipy/numpy not available: {e}",
            metadata={"symbol": symbol, "window": window,
                      "n_input": len(preds), "computed_at_utc": started},
        )


# ---------------------------------------------------------------------------
# v2 endpoint G + H: walk_forward_validate + train_test_validate
# ---------------------------------------------------------------------------

def train_test_validate(
    filter_config: dict, symbol: str, window: int,
    train_frac: float = 0.7, since_ms: int | None = None,
) -> dict:
    """Split resolved predictions chronologically train/test; apply filter
    to each half; return per-half metrics + delta.
    """
    started = _now_iso()
    _validate_filter_config(filter_config)
    db = _get_db()
    preds = _load_resolved_predictions(db, symbol, window, since_ms)
    n_in = len(preds)
    if n_in < 10:
        return _envelope(
            status="insufficient_samples",
            message=f"only {n_in} predictions; need >= 10",
            metadata={"symbol": symbol, "window": window,
                      "n_input": n_in, "computed_at_utc": started},
        )
    preds.sort(key=lambda p: p["ts_contract_open_ms"])
    split = int(n_in * train_frac)
    train_p = preds[:split]
    test_p = preds[split:]

    # Pre-load lookups for filter (apply only to preds being filtered)
    needs_decay = filter_config.get("min_recency_weighted_ev") is not None
    needs_consensus = filter_config.get("consensus_required") is True
    decay_lookup = _load_decay_state_for_predictions(db, preds) if needs_decay else {}
    consensus_lookup = _load_consensus_for_predictions(db, preds) if needs_consensus else {}
    divs = sorted(p.get("divergence", 0.0) for p in preds)
    div_q3 = divs[int(len(divs) * 0.75)] if divs else None

    def _run(subset):
        passed = [p for p in subset if _apply_filter_row(
            p, filter_config, decay_lookup, consensus_lookup, div_q3)]
        return _metrics_from_preds(passed)

    train_m = _run(train_p)
    test_m = _run(test_p)
    delta = None
    if train_m["win_rate"] is not None and test_m["win_rate"] is not None:
        delta = round(train_m["win_rate"] - test_m["win_rate"], 6)
    return _envelope(
        status="ok",
        metadata={"symbol": symbol, "window": window,
                  "n_input": n_in, "split_at": split,
                  "computed_at_utc": started},
        result={
            "filter_config": filter_config,
            "train": train_m,
            "test": test_m,
            "win_rate_delta": delta,
        },
    )


def walk_forward_validate(
    filter_config: dict, symbol: str, window: int,
    n_folds: int = 5, since_ms: int | None = None,
) -> dict:
    """Apply filter to each of n_folds chronological holdout slices.
    Returns per-fold metrics + robustness flag (mean - 1.96*std/sqrt(n) > 0.5).
    """
    started = _now_iso()
    _validate_filter_config(filter_config)
    db = _get_db()
    preds = _load_resolved_predictions(db, symbol, window, since_ms)
    n_in = len(preds)
    if n_in < 50:
        return _envelope(
            status="insufficient_samples",
            message=f"only {n_in} predictions; walk-forward needs >= 50",
            metadata={"symbol": symbol, "window": window,
                      "n_input": n_in, "computed_at_utc": started},
        )
    preds.sort(key=lambda p: p["ts_contract_open_ms"])
    fold_size = n_in // n_folds
    needs_decay = filter_config.get("min_recency_weighted_ev") is not None
    needs_consensus = filter_config.get("consensus_required") is True
    decay_lookup = _load_decay_state_for_predictions(db, preds) if needs_decay else {}
    consensus_lookup = _load_consensus_for_predictions(db, preds) if needs_consensus else {}
    divs = sorted(p.get("divergence", 0.0) for p in preds)
    div_q3 = divs[int(len(divs) * 0.75)] if divs else None

    fold_rows = []
    warnings_list = []
    for i in range(n_folds):
        lo = i * fold_size
        hi = (i + 1) * fold_size if i < n_folds - 1 else n_in
        chunk = preds[lo:hi]
        passed = [p for p in chunk if _apply_filter_row(
            p, filter_config, decay_lookup, consensus_lookup, div_q3)]
        m = _metrics_from_preds(passed)
        if m["n_passed"] == 0:
            warnings_list.append(f"fold {i}: 0 passed predictions")
        fold_rows.append({"fold": i, **m})

    # Aggregate
    valid_wr = [r["win_rate"] for r in fold_rows if r["win_rate"] is not None]
    valid_roi = [r["roi_pct"] for r in fold_rows if r["roi_pct"] is not None]
    mean_wr = statistics.mean(valid_wr) if valid_wr else None
    std_wr = statistics.stdev(valid_wr) if len(valid_wr) > 1 else 0.0
    mean_roi = statistics.mean(valid_roi) if valid_roi else None
    std_roi = statistics.stdev(valid_roi) if len(valid_roi) > 1 else 0.0
    robust = False
    if mean_wr is not None and len(valid_wr) >= 2:
        se = std_wr / (len(valid_wr) ** 0.5)
        robust = (mean_wr - 1.96 * se) > 0.50
    return _envelope(
        status="ok",
        metadata={"symbol": symbol, "window": window,
                  "n_input": n_in, "n_folds": n_folds,
                  "computed_at_utc": started},
        result={
            "filter_config": filter_config,
            "folds": fold_rows,
            "mean_win_rate": round(mean_wr, 6) if mean_wr is not None else None,
            "std_win_rate": round(std_wr, 6),
            "mean_roi_pct": round(mean_roi, 4) if mean_roi is not None else None,
            "std_roi_pct": round(std_roi, 4),
            "robust": robust,
            "robust_threshold": 0.50,
        },
        warnings=warnings_list,
    )


# ---------------------------------------------------------------------------
# v2 endpoint I: recommend_premium_filter
# ---------------------------------------------------------------------------

def recommend_premium_filter(
    symbol: str,
    window: int,
    since_ms: int | None = None,
    mode: str = "strict",
) -> dict:
    """Top-down recommendation: grid search → walk_forward + train_test on
    top 5 → return first config that survives all gates.

    mode="strict"    (default): FDR + walk-forward robust + test_wr >= 0.55 + n >= 100
    mode="discovery": walk-forward robust + test_wr >= 0.55 + n >= 50 + p_raw < 0.05;
                      FDR is surfaced but not gating.
    """
    if mode not in {"strict", "discovery"}:
        raise ValueError(f"mode must be 'strict' or 'discovery', got {mode!r}")

    started = _now_iso()
    warnings_list = []
    errors_list = []

    # Always run FDR for transparency — it populates survives_fdr_q05 on every candidate
    gs = grid_search(symbol, window, since_ms=since_ms, top_k=5, apply_fdr=True)
    if gs["status"] != "ok":
        return _envelope(
            status=gs["status"],
            message=f"grid_search: {gs.get('message')}",
            metadata={"symbol": symbol, "window": window,
                      "computed_at_utc": started, "mode": mode},
            result={"winner": None,
                    "reason": "grid_search returned no candidates",
                    "candidates_evaluated": 0},
            warnings=gs.get("warnings", []),
        )

    candidates = gs["result"]["top"]
    if not candidates:
        return _envelope(
            status="no_data",
            message="grid_search top is empty",
            metadata={"symbol": symbol, "window": window,
                      "computed_at_utc": started, "mode": mode},
            result={"winner": None,
                    "reason": "no FDR-surviving candidates",
                    "candidates_evaluated": 0,
                    "grid_metadata": gs["metadata"]},
            warnings=warnings_list,
        )

    # In discovery mode we consider all top candidates regardless of FDR flag
    # (grid_search top_k=5 uses apply_fdr=True but returns all top-k rows, not
    #  just FDR survivors, so we already have the full set)
    evaluated = []
    winner = None
    for cand in candidates[:5]:
        fc = cand["filter_config"]
        try:
            wf = walk_forward_validate(fc, symbol, window, n_folds=5, since_ms=since_ms)
        except Exception as e:
            errors_list.append(f"walk_forward({fc}): {e}")
            continue
        try:
            tt = train_test_validate(fc, symbol, window, since_ms=since_ms)
        except Exception as e:
            errors_list.append(f"train_test({fc}): {e}")
            continue

        wf_ok = wf["status"] == "ok"
        tt_ok = tt["status"] == "ok"
        wf_robust = wf_ok and wf["result"].get("robust")
        tt_win_rate = tt["result"]["test"]["win_rate"] if (tt_ok and tt["result"]) else None
        wf_mean_wr = (wf["result"].get("mean_win_rate") if wf_ok else None) or 0.0
        p_raw = cand.get("p_value_raw", 1.0)  # raw (uncorrected) p-value from grid

        # Gate sets differ by mode
        if mode == "strict":
            gates_pass = (
                wf_robust
                and tt_win_rate is not None
                and tt_win_rate >= 0.55
                and cand.get("survives_fdr_q05", False)
                and cand["n_passed"] >= 100
            )
            gates_detail = {
                "survives_fdr_q05": cand.get("survives_fdr_q05", False),
                "wf_robust": wf_robust,
                "test_win_rate_ge_55pct": tt_win_rate is not None and tt_win_rate >= 0.55,
                "n_passed_ge_100": cand["n_passed"] >= 100,
            }
        else:  # discovery
            gates_pass = (
                wf_robust
                and tt_win_rate is not None
                and tt_win_rate >= 0.55
                and cand["n_passed"] >= 50
                and (p_raw is not None and p_raw < 0.05)
            )
            gates_detail = {
                "wf_robust": wf_robust,
                "test_win_rate_ge_55pct": tt_win_rate is not None and tt_win_rate >= 0.55,
                "n_passed_ge_50": cand["n_passed"] >= 50,
                "p_raw_lt_05": p_raw is not None and p_raw < 0.05,
                # informative only
                "survives_fdr_q05_informative": cand.get("survives_fdr_q05", False),
            }

        # Composite score used for sorting runners-up (conservative cross-validated WR)
        composite = min(
            tt_win_rate if tt_win_rate is not None else 0.0,
            wf_mean_wr if wf_mean_wr else 0.0,
        )

        entry = {
            "filter_config": fc,
            "grid_metrics": cand,
            "walk_forward": wf["result"] if wf_ok else {"status": wf["status"], "message": wf.get("message")},
            "train_test": tt["result"] if tt_ok else {"status": tt["status"], "message": tt.get("message")},
            "gates_passed": gates_detail,
            "applicable": gates_pass,
            "mode_used": mode,
            "composite_score": round(composite, 6),
            "confidence": "high" if gates_pass else (
                "medium" if (wf_robust or (tt_win_rate and tt_win_rate >= 0.55)) else "low"
            ),
        }
        evaluated.append(entry)
        if gates_pass and winner is None:
            winner = entry

    # Sort runners-up by composite_score descending so best runner-up is first
    runners = sorted(
        [e for e in evaluated if e is not winner],
        key=lambda x: x["composite_score"],
        reverse=True,
    )

    if mode == "strict":
        no_winner_reason = "no candidate satisfied (FDR + walk-forward robust + test win_rate >= 0.55 + n >= 100)"
    else:
        no_winner_reason = "no candidate satisfied (walk-forward robust + test win_rate >= 0.55 + n >= 50 + p_raw < 0.05)"

    if winner is None:
        return _envelope(
            status="ok",
            message="no candidate passed all gates; returning highest-confidence runner-up",
            metadata={"symbol": symbol, "window": window,
                      "computed_at_utc": started, "mode": mode},
            result={
                "winner": None,
                "reason": no_winner_reason,
                "runners_up": runners,
                "candidates_evaluated": len(evaluated),
            },
            warnings=warnings_list,
        )

    return _envelope(
        status="ok",
        metadata={"symbol": symbol, "window": window,
                  "computed_at_utc": started, "mode": mode},
        result={
            "winner": winner,
            "runners_up": runners,
            "candidates_evaluated": len(evaluated),
        },
        warnings=warnings_list,
    )


# ---------------------------------------------------------------------------
# observation_status — consensus-gated model monitoring dashboard
# ---------------------------------------------------------------------------

_OBSERVE_DIR = "/data/observe"
_SHIP_THRESHOLD = 0.65
_OBSERVE_THRESHOLD = 0.55
_GATE_DAYS = 7
_MIN_N_FOR_RATE = 10


def _wilson_ci_safe(wins: int, n: int) -> list[float] | None:
    """Return [lo, hi] Wilson CI or None if n is 0."""
    if n == 0:
        return None
    lo, hi = wilson_ci(wins, n)
    return [round(lo, 4), round(hi, 4)]


def _gate_applied_at_for_model(db, model_name: str) -> str | None:
    """Return ISO timestamp when consensus_required was first set for this model.

    Strategy: scan model_audit for action='set_filter' rows where after_json
    contains 'consensus_required' in the detail string. Return earliest match.
    Falls back to committee_weights.computed_at for the model's (symbol, window).
    """
    rows = db.execute(
        """
        SELECT ts FROM model_audit
        WHERE model_name = ? AND action = 'set_filter'
              AND detail LIKE '%consensus_required%true%'
        ORDER BY ts ASC
        LIMIT 1
        """,
        (model_name,),
    ).fetchall()
    if rows:
        return rows[0]["ts"]
    return None


def _gate_applied_at_for_pair(db, symbol: str, window: int, models: list[str]) -> str | None:
    """Return the earliest gate timestamp across all models in the pair."""
    earliest = None
    for m in models:
        t = _gate_applied_at_for_model(db, m)
        if t and (earliest is None or t < earliest):
            earliest = t
    if earliest:
        return earliest
    # Fallback: committee_weights computed_at
    row = db.execute(
        """
        SELECT computed_at FROM committee_weights
        WHERE symbol = ? AND market_window_seconds = ?
        ORDER BY computed_at DESC LIMIT 1
        """,
        (symbol, window),
    ).fetchone()
    if row and row["computed_at"]:
        return row["computed_at"]
    return None


def _fresh_paper_trade_stats(db, models: list[str], symbol: str, window: int,
                              gate_ts: str | None) -> dict:
    """Compute live win-rate stats from paper_trades since gate was applied.

    Counts:
    - n_total: all paper_trades rows for models in (symbol, window) since gate_ts
    - n_consensus_deferred: rows with decision_outcome='suppressed' AND reason='consensus_required'
    - n_executed: total - deferred (trades that actually executed)
    - n_resolved: executed trades with resolved=1
    - win_rate: wins / n_resolved (None if < _MIN_N_FOR_RATE)
    """
    warnings: list[str] = []

    if not models:
        return {
            "n_total": 0, "n_consensus_deferred": 0, "n_executed": 0,
            "n_resolved": 0, "win_rate": None, "wilson_ci": None,
        }

    placeholders = ",".join("?" * len(models))
    gate_ms: int | None = None
    if gate_ts:
        try:
            from datetime import datetime, timezone
            # Parse ISO string (with or without Z suffix)
            ts_clean = gate_ts.rstrip("Z").replace("+00:00", "")
            dt = datetime.fromisoformat(ts_clean).replace(tzinfo=timezone.utc)
            gate_ms = int(dt.timestamp() * 1000)
        except Exception:
            pass

    # Fetch all paper_trades rows for these models in this (symbol, window)
    since_clause = ""
    params_base: list = list(models) + [symbol, window]
    if gate_ms is not None:
        since_clause = "AND pt.ts_contract_open_ms >= ?"
        params_base.append(gate_ms)

    rows = db.execute(
        f"""
        SELECT pt.resolved, pt.prediction_correct, pt.decision_outcome, pt.suppressed_reason,
               pt.decision_reason
        FROM paper_trades pt
        WHERE pt.model_name IN ({placeholders})
          AND pt.symbol = ?
          AND pt.market_window_seconds = ?
          {since_clause}
        """,
        params_base,
    ).fetchall()

    n_total = len(rows)
    n_deferred = sum(
        1 for r in rows
        if (r["decision_outcome"] == "suppressed"
            and r["decision_reason"] == "consensus_required")
    )
    # Executed = wrote a trade (not consensus-suppressed)
    executed = [r for r in rows if not (
        r["decision_outcome"] == "suppressed"
        and r["decision_reason"] == "consensus_required"
    )]
    n_executed = len(executed)
    resolved = [r for r in executed if r["resolved"] == 1]
    n_resolved = len(resolved)
    wins = sum(1 for r in resolved if r["prediction_correct"] == 1)

    win_rate: float | None = None
    ci: list[float] | None = None
    if n_resolved >= _MIN_N_FOR_RATE:
        win_rate = round(wins / n_resolved, 4)
        ci = _wilson_ci_safe(wins, n_resolved)
    else:
        warnings.append(
            f"only {n_resolved} resolved trades since gate; win_rate suppressed (min={_MIN_N_FOR_RATE})"
        )

    return {
        "n_total": n_total,
        "n_consensus_deferred": n_deferred,
        "n_executed": n_executed,
        "n_resolved": n_resolved,
        "win_rate": win_rate,
        "wilson_ci": ci,
        "_warnings": warnings,
    }


def _decision_gate(win_rate: float | None, gate_ts: str | None) -> dict:
    """Compute ship/observe/kill status and days remaining."""
    import math as _math
    from datetime import datetime, timezone

    days_observed: float = 0.0
    if gate_ts:
        try:
            ts_clean = gate_ts.rstrip("Z").replace("+00:00", "")
            dt = datetime.fromisoformat(ts_clean).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            days_observed = max(0.0, (now - dt).total_seconds() / 86400.0)
        except Exception:
            pass

    days_remaining = max(0.0, round(_GATE_DAYS - days_observed, 2))
    days_observed_r = round(days_observed, 2)

    if win_rate is None:
        status = "observe"
    elif win_rate >= _SHIP_THRESHOLD:
        status = "ship"
    elif win_rate >= _OBSERVE_THRESHOLD:
        status = "observe"
    else:
        status = "kill"

    return {
        "ship_threshold": _SHIP_THRESHOLD,
        "observe_threshold": _OBSERVE_THRESHOLD,
        "current_status": status,
        "days_remaining_to_decision": days_remaining,
    }, days_observed_r


def _read_snapshots(observe_dir: str, limit: int = 14) -> list[dict]:
    """Read JSON snapshot files from observe_dir; return last `limit` by date desc."""
    import os
    import json as _json

    snapshots: list[dict] = []
    try:
        entries = os.listdir(observe_dir)
    except FileNotFoundError:
        return []
    except PermissionError:
        return []

    json_files = sorted(
        [e for e in entries if e.endswith(".json")],
        reverse=True,
    )[:limit]

    for fname in json_files:
        fpath = os.path.join(observe_dir, fname)
        # Expected format: YYYY-MM-DD_SYMBOL_WINDOW.json
        snap: dict = {"path": fpath}
        parts = fname[:-5].split("_")  # strip .json
        if len(parts) >= 3:
            snap["date"] = parts[0]
            snap["symbol"] = parts[1]
            try:
                snap["window"] = int(parts[2])
            except ValueError:
                snap["window"] = None
        else:
            snap["date"] = None
            snap["symbol"] = None
            snap["window"] = None

        try:
            with open(fpath) as fh:
                data = _json.load(fh)
            snap["recommend_premium_winner"] = data.get("winner") or data.get("recommend_premium_winner")
        except Exception:
            snap["recommend_premium_winner"] = None

        snapshots.append(snap)

    return snapshots


def observation_status(observe_dir: str = _OBSERVE_DIR) -> dict:
    """Summarize consensus-gated model observation state.

    Returns an envelope with:
    - observation_pairs: grouped by (symbol, window)
    - snapshots: last 14 /data/observe/*.json files
    """
    started = _now_iso()
    db = _get_db()
    warnings_list: list[str] = []

    # 1. Query model_registry for consensus_required models
    rows = db.execute(
        """
        SELECT name, symbol, training_horizon_seconds, filter_config_json
        FROM model_registry
        WHERE filter_config_json LIKE '%consensus_required%'
          AND paper_active = 1
        """
    ).fetchall()

    # Filter: only rows where consensus_required is actually true
    gated_models: list[dict] = []
    for row in rows:
        try:
            import json as _json
            fc = _json.loads(row["filter_config_json"] or "{}")
        except Exception:
            fc = {}
        if fc.get("consensus_required") is True:
            gated_models.append({
                "name": row["name"],
                "symbol": row["symbol"],
                "window": row["training_horizon_seconds"],
                "filter_config": fc,
            })

    # 2. Group by (symbol, window)
    from collections import defaultdict as _dd
    pairs: dict[tuple, list] = _dd(list)
    filter_configs: dict[tuple, dict] = {}
    for m in gated_models:
        key = (m["symbol"], m["window"])
        pairs[key].append(m["name"])
        filter_configs[key] = m["filter_config"]

    # 3. Build observation_pairs
    observation_pairs: list[dict] = []
    for (symbol, window), models in sorted(pairs.items()):
        gate_ts = _gate_applied_at_for_pair(db, symbol, window, models)
        trade_stats = _fresh_paper_trade_stats(db, models, symbol, window, gate_ts)
        extra_warnings = trade_stats.pop("_warnings", [])
        warnings_list.extend(extra_warnings)

        gate_dict, days_observed = _decision_gate(trade_stats["win_rate"], gate_ts)

        observation_pairs.append({
            "symbol": symbol,
            "window": window,
            "models": sorted(models),
            "filter_config": filter_configs[(symbol, window)],
            "gate_applied_at": gate_ts,
            "days_observed": days_observed,
            "fresh_paper_trades": trade_stats,
            "decision_gate": gate_dict,
        })

    # 4. Read snapshots
    snapshots = _read_snapshots(observe_dir)

    return {
        "status": "ok",
        "metadata": {
            "computed_at_utc": started,
            "n_observation_pairs": len(observation_pairs),
        },
        "result": {
            "observation_pairs": observation_pairs,
            "snapshots": snapshots,
        },
        "warnings": warnings_list,
    }
