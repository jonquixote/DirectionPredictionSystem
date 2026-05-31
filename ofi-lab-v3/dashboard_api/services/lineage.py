"""Model lineage analytics.

Powers the cell-history / best-per-cell views. Every read is cheap:
- predictions_daily_rollup for per-(model, symbol, window, date) aggregates
- model_tier_score for the canonical composite score
- model_registry for lineage / fleet metadata
- decay_evaluations for per-trigger events

A "training cell" is the (symbol, training_horizon_seconds, train_days)
triple — already keyed in cell_governance + retrain_queue as
``f"{symbol}_{horizon}_{train_days}"``. A "fleet generation" is a
fleet_version (e.g. ``2026-05-12``). Lineage walks across generations
via model_registry.parent_model_name (legacy rows where parent is NULL
are resolved by matching (symbol, training_horizon, train_days) and
ordering by train_window_end).

Tier 1 of the lineage-analytics spec at
docs/2026-05-31-model-lineage-analytics-spec.md.
"""
from __future__ import annotations

import time as _time
from typing import Any


def _get_db():
    """Same import dance as services/analysis.py for path compatibility."""
    try:
        from services.db import get_db  # type: ignore
    except ModuleNotFoundError:
        from dashboard_api.services.db import get_db  # type: ignore[assignment]
    return get_db()


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
MARKET_WINDOWS = (300, 900, 1800)
COMPOSITE_METRIC = "composite"
LEADERBOARD_METRICS = ("composite", "roi", "win_rate", "sharpe")

# Regime dimensions captured at prediction time. The trader writes these
# alongside every row in `predictions`, so we can slice cell performance by
# any subset without joining to regime_features_latest.
REGIME_DIMENSIONS = ("regime_volatility", "regime_liquidity", "regime_trend")

# Buckets for pre-prediction market state proxies. p_market is what the
# Kalshi book implied at the moment the model fired; p_model_minus_market
# is the signed divergence (positive = model thinks the side is more likely
# than market does). These two axes are the cleanest "what was the market
# saying BEFORE the model predicted" signal we already store.
P_MARKET_BUCKETS = (
    ("near_0", 0.0, 0.15),
    ("0.15_0.35", 0.15, 0.35),
    ("0.35_0.50", 0.35, 0.50),
    ("0.50_0.65", 0.50, 0.65),
    ("0.65_0.85", 0.65, 0.85),
    ("near_1", 0.85, 1.01),
)
DIVERGENCE_BUCKETS = (
    ("strong_against", -1.01, -0.10),
    ("mild_against", -0.10, -0.02),
    ("aligned", -0.02, 0.02),
    ("mild_with", 0.02, 0.10),
    ("strong_with", 0.10, 1.01),
)


def _wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float] | tuple[None, None]:
    """Wilson 95% lower + upper bound for a binomial proportion."""
    if n <= 0:
        return (None, None)
    p = wins / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    spread = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5
    lo = (center - spread) / denom
    hi = (center + spread) / denom
    return (max(0.0, lo), min(1.0, hi))


def _parse_cell_key(cell_key: str) -> tuple[str, int, int]:
    """``BTCUSDT_300_179`` → (``BTCUSDT``, 300, 179)."""
    parts = cell_key.split("_")
    if len(parts) != 3:
        raise ValueError(f"cell_key must be SYMBOL_HORIZON_TRAINDAYS, got {cell_key!r}")
    symbol, horizon_s, train_days_s = parts
    try:
        return symbol, int(horizon_s), int(train_days_s)
    except (TypeError, ValueError) as e:
        raise ValueError(f"cell_key parse failed: {cell_key!r}: {e}") from e


def _cell_key(symbol: str, horizon: int, train_days: int) -> str:
    return f"{symbol}_{horizon}_{train_days}"


def list_cells() -> list[dict]:
    """All registered cells with their current incumbent.

    Order: symbol asc, training_horizon_seconds asc, train_days asc.
    A cell is "registered" if any model_registry row exists for it.
    """
    db = _get_db()
    rows = db.execute(
        """
        SELECT
            symbol,
            training_horizon_seconds AS horizon,
            train_days,
            COUNT(*) AS n_models,
            MAX(fleet_version) AS latest_fleet
        FROM model_registry
        WHERE symbol IS NOT NULL AND training_horizon_seconds IS NOT NULL AND train_days IS NOT NULL
        GROUP BY symbol, training_horizon_seconds, train_days
        ORDER BY symbol, training_horizon_seconds, train_days
        """
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        sym = r["symbol"]
        h = int(r["horizon"])
        td = int(r["train_days"])
        ck = _cell_key(sym, h, td)
        # Current incumbent = newest paper_active=1 row for this cell, falling
        # back to newest fleet's row if none is active.
        incum = db.execute(
            """
            SELECT name, fleet_version
            FROM model_registry
            WHERE symbol=? AND training_horizon_seconds=? AND train_days=?
              AND paper_active=1
            ORDER BY fleet_version DESC LIMIT 1
            """,
            (sym, h, td),
        ).fetchone()
        if incum is None:
            incum = db.execute(
                """
                SELECT name, fleet_version
                FROM model_registry
                WHERE symbol=? AND training_horizon_seconds=? AND train_days=?
                ORDER BY fleet_version DESC LIMIT 1
                """,
                (sym, h, td),
            ).fetchone()
        out.append({
            "cell_key": ck,
            "symbol": sym,
            "training_horizon_seconds": h,
            "train_days": td,
            "n_models": int(r["n_models"]),
            "latest_fleet": r["latest_fleet"],
            "incumbent_model_name": incum["name"] if incum else None,
            "incumbent_fleet_version": incum["fleet_version"] if incum else None,
        })
    return out


def _aggregate_rollup_for(
    db,
    model_name: str | None,
    symbol: str,
    market_window: int,
    since_date: str,
) -> dict | None:
    """Sum predictions_daily_rollup rows for one (model, symbol, window) over a window.

    If ``model_name`` is None, aggregates across ALL models for that (sym, win).
    Returns None when no rows match. Wraps the standard derived metrics
    (win_rate, roi_pct, ev_per_trade, brier, sharpe) plus Wilson CI on win_rate.
    """
    clauses = ["symbol = ?", "market_window_seconds = ?", "date_utc >= ?"]
    params: list[Any] = [symbol, market_window, since_date]
    if model_name is not None:
        clauses.append("model_name = ?")
        params.append(model_name)
    where = " AND ".join(clauses)
    row = db.execute(
        f"""
        SELECT
            SUM(n)                 AS tot_n,
            SUM(n_correct)         AS tot_correct,
            SUM(sum_pnl)           AS tot_pnl,
            SUM(sum_pnl_sq)        AS tot_pnl_sq,
            SUM(n_resolved_trades) AS tot_resolved,
            SUM(sum_brier_terms)   AS tot_brier,
            COUNT(*)               AS n_days,
            MAX(updated_at)        AS max_updated_at
        FROM predictions_daily_rollup
        WHERE {where}
        """,
        params,
    ).fetchone()
    if not row or not row["tot_n"]:
        return None
    n = int(row["tot_n"])
    nc = int(row["tot_correct"] or 0)
    tot_pnl = float(row["tot_pnl"] or 0.0)
    tot_sq = float(row["tot_pnl_sq"] or 0.0)
    tot_resolved = int(row["tot_resolved"] or 0)
    tot_brier = float(row["tot_brier"] or 0.0)
    win_rate = nc / n if n else None
    wr_lo, wr_hi = _wilson_ci(nc, n) if n else (None, None)
    ev = (tot_pnl / tot_resolved) if tot_resolved else None
    sharpe: float | None = None
    if tot_resolved >= 2:
        mean_pnl = tot_pnl / tot_resolved
        var = (tot_resolved * tot_sq - tot_pnl * tot_pnl) / (tot_resolved * (tot_resolved - 1))
        if var > 0:
            sd = var ** 0.5
            sharpe = (mean_pnl / sd) * (tot_resolved ** 0.5) if sd else None
    brier = tot_brier / n if n else None
    return {
        "n_samples": n,
        "n_correct": nc,
        "n_resolved_trades": tot_resolved,
        "win_rate": round(win_rate, 6) if win_rate is not None else None,
        "win_rate_ci_lo": round(wr_lo, 6) if wr_lo is not None else None,
        "win_rate_ci_hi": round(wr_hi, 6) if wr_hi is not None else None,
        "total_pnl_usdc": round(tot_pnl, 4),
        "ev_per_trade": round(ev, 6) if ev is not None else None,
        "roi_pct": round((ev or 0.0) * 100, 4) if ev is not None else None,
        "brier_score": round(brier, 6) if brier is not None else None,
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "n_days": int(row["n_days"] or 0),
        "max_updated_at": row["max_updated_at"],
    }


def _latest_composite(db, model_name: str, symbol: str, market_window: int) -> float | None:
    """Most recent composite_score from model_tier_score for one (model, sym, win)."""
    row = db.execute(
        """
        SELECT composite_score
        FROM model_tier_score
        WHERE model_name=? AND symbol=? AND market_window_seconds=?
        ORDER BY ts DESC
        LIMIT 1
        """,
        (model_name, symbol, market_window),
    ).fetchone()
    if row is None:
        return None
    v = row["composite_score"]
    return float(v) if v is not None else None


def compute_best_per_cell(
    metric: str = COMPOSITE_METRIC,
    since_ms: int | None = None,
    min_n_samples: int = 50,
    top_k_runners: int = 3,
) -> dict:
    """Best model per (symbol, market_window) — 12 rows.

    ``metric`` ∈ {composite, roi, win_rate, sharpe}. Composite reads from
    model_tier_score; the others come from rollup-aggregated metrics over
    the requested window. Runners-up are sorted by the same metric.
    """
    if metric not in LEADERBOARD_METRICS:
        raise ValueError(f"metric must be one of {LEADERBOARD_METRICS}, got {metric!r}")
    if since_ms is None:
        since_ms = int(_time.time() * 1000) - 14 * 86_400_000
    since_date = _time.strftime("%Y-%m-%d", _time.gmtime(since_ms / 1000))

    db = _get_db()
    cells: list[dict] = []
    for sym in SYMBOLS:
        for mw in MARKET_WINDOWS:
            rows = db.execute(
                """
                SELECT
                    model_name,
                    SUM(n)                  AS tot_n,
                    SUM(n_correct)          AS tot_correct,
                    SUM(sum_pnl)            AS tot_pnl,
                    SUM(sum_pnl_sq)         AS tot_pnl_sq,
                    SUM(n_resolved_trades)  AS tot_resolved,
                    SUM(sum_brier_terms)    AS tot_brier
                FROM predictions_daily_rollup
                WHERE symbol=? AND market_window_seconds=? AND date_utc >= ?
                GROUP BY model_name
                HAVING SUM(n) >= ?
                """,
                (sym, mw, since_date, min_n_samples),
            ).fetchall()
            if not rows:
                cells.append({
                    "symbol": sym,
                    "market_window_seconds": mw,
                    "best_model": None,
                    "best_metric_value": None,
                    "n_candidates": 0,
                    "reason": "no models meeting min_n_samples in window",
                    "runners_up": [],
                })
                continue
            candidates: list[dict] = []
            for r in rows:
                mn = r["model_name"]
                n = int(r["tot_n"])
                nc = int(r["tot_correct"] or 0)
                tot_pnl = float(r["tot_pnl"] or 0.0)
                tot_sq = float(r["tot_pnl_sq"] or 0.0)
                tot_resolved = int(r["tot_resolved"] or 0)
                tot_brier = float(r["tot_brier"] or 0.0)
                win_rate = nc / n if n else 0.0
                wr_lo, wr_hi = _wilson_ci(nc, n)
                ev = (tot_pnl / tot_resolved) if tot_resolved else None
                sharpe: float | None = None
                if tot_resolved >= 2:
                    mean_pnl = tot_pnl / tot_resolved
                    var = (tot_resolved * tot_sq - tot_pnl * tot_pnl) / (
                        tot_resolved * (tot_resolved - 1)
                    )
                    if var > 0:
                        sd = var ** 0.5
                        sharpe = (mean_pnl / sd) * (tot_resolved ** 0.5) if sd else None
                composite = _latest_composite(db, mn, sym, mw) if metric == COMPOSITE_METRIC else None
                if metric == COMPOSITE_METRIC:
                    sort_key = composite if composite is not None else float("-inf")
                elif metric == "roi":
                    sort_key = (ev or 0.0)
                elif metric == "win_rate":
                    # Wilson lower bound — penalizes small-n flukes
                    sort_key = wr_lo if wr_lo is not None else 0.0
                else:  # sharpe
                    sort_key = sharpe if sharpe is not None else float("-inf")
                candidates.append({
                    "model_name": mn,
                    "n_samples": n,
                    "n_resolved_trades": tot_resolved,
                    "win_rate": round(win_rate, 6),
                    "win_rate_ci_lo": round(wr_lo, 6) if wr_lo is not None else None,
                    "win_rate_ci_hi": round(wr_hi, 6) if wr_hi is not None else None,
                    "total_pnl_usdc": round(tot_pnl, 4),
                    "ev_per_trade": round(ev, 6) if ev is not None else None,
                    "roi_pct": round((ev or 0.0) * 100, 4) if ev is not None else None,
                    "brier_score": round(tot_brier / n, 6) if n else None,
                    "sharpe": round(sharpe, 4) if sharpe is not None else None,
                    "composite_score": round(composite, 6) if composite is not None else None,
                    "_sort_key": sort_key,
                })
            candidates.sort(key=lambda c: c["_sort_key"], reverse=True)
            winner = candidates[0]
            # Enrich winner with lineage from model_registry
            reg = db.execute(
                """
                SELECT training_horizon_seconds, train_days, fleet_version,
                       parent_model_name, train_window_start, train_window_end,
                       paper_active, tier
                FROM model_registry WHERE name=?
                """,
                (winner["model_name"],),
            ).fetchone()
            for c in candidates:
                c.pop("_sort_key", None)
            cells.append({
                "symbol": sym,
                "market_window_seconds": mw,
                "best_model": winner["model_name"],
                "best_metric_value": winner.get({
                    "composite": "composite_score",
                    "roi": "roi_pct",
                    "win_rate": "win_rate_ci_lo",
                    "sharpe": "sharpe",
                }[metric]),
                "training_horizon_seconds": reg["training_horizon_seconds"] if reg else None,
                "train_days": reg["train_days"] if reg else None,
                "fleet_version": reg["fleet_version"] if reg else None,
                "paper_active": bool(reg["paper_active"]) if reg else None,
                "tier": reg["tier"] if reg else None,
                "winner_metrics": winner,
                "n_candidates": len(candidates),
                "runners_up": candidates[1: 1 + top_k_runners],
            })
    return {
        "cells": cells,
        "metadata": {
            "metric": metric,
            "since_ms": since_ms,
            "since_date": since_date,
            "min_n_samples": min_n_samples,
            "computed_at_ms": int(_time.time() * 1000),
        },
    }


def compute_cell_history(cell_key: str, since_ms: int | None = None) -> dict:
    """Per-fleet performance history for one cell, across all 3 market windows.

    Returns one entry per fleet_version that has ever held a model in this cell.
    Includes retired/archived fleets (the whole point — historical lineage).
    """
    symbol, horizon, train_days = _parse_cell_key(cell_key)
    db = _get_db()

    # All models that have ever sat in this cell, newest first
    reg_rows = db.execute(
        """
        SELECT name, fleet_version, parent_model_name, train_window_start, train_window_end,
               training_horizon_seconds, train_days, paper_active, live_eligible,
               tier, tier_assigned_at, created_at, updated_at
        FROM model_registry
        WHERE symbol=? AND training_horizon_seconds=? AND train_days=?
        ORDER BY fleet_version DESC, created_at DESC
        """,
        (symbol, horizon, train_days),
    ).fetchall()
    if not reg_rows:
        return {
            "cell_key": cell_key,
            "symbol": symbol,
            "training_horizon_seconds": horizon,
            "train_days": train_days,
            "fleets": [],
            "metadata": {"computed_at_ms": int(_time.time() * 1000)},
        }

    # since_ms: default to the earliest train_window_end across all models
    if since_ms is None:
        twe = [r["train_window_end"] for r in reg_rows if r["train_window_end"]]
        twe.sort()
        if twe:
            try:
                since_ms = int(_time.mktime(_time.strptime(twe[0][:10], "%Y-%m-%d"))) * 1000
            except Exception:
                since_ms = 0
        else:
            since_ms = 0
    since_date = _time.strftime("%Y-%m-%d", _time.gmtime(since_ms / 1000))

    fleets: list[dict] = []
    for r in reg_rows:
        mn = r["name"]
        fleet_block: dict[str, Any] = {
            "fleet_version": r["fleet_version"],
            "model_name": mn,
            "parent_model_name": r["parent_model_name"],
            "train_window_start": r["train_window_start"],
            "train_window_end": r["train_window_end"],
            "paper_active": bool(r["paper_active"]),
            "live_eligible": bool(r["live_eligible"]),
            "tier": r["tier"],
            "tier_assigned_at": r["tier_assigned_at"],
            "created_at": r["created_at"],
            "by_market_window": {},
        }
        for mw in MARKET_WINDOWS:
            agg = _aggregate_rollup_for(db, mn, symbol, mw, since_date)
            composite_latest = _latest_composite(db, mn, symbol, mw)
            # Daily series — newest 30 days max so payload stays bounded
            daily = db.execute(
                """
                SELECT date_utc, n, n_correct, sum_pnl, sum_pnl_sq, n_resolved_trades, sum_brier_terms
                FROM predictions_daily_rollup
                WHERE model_name=? AND symbol=? AND market_window_seconds=? AND date_utc >= ?
                ORDER BY date_utc ASC
                LIMIT 60
                """,
                (mn, symbol, mw, since_date),
            ).fetchall()
            daily_series = []
            for d in daily:
                n_d = int(d["n"] or 0)
                nc_d = int(d["n_correct"] or 0)
                tot_r = int(d["n_resolved_trades"] or 0)
                pnl_d = float(d["sum_pnl"] or 0.0)
                daily_series.append({
                    "date": d["date_utc"],
                    "n": n_d,
                    "win_rate": round(nc_d / n_d, 6) if n_d else None,
                    "n_resolved_trades": tot_r,
                    "total_pnl_usdc": round(pnl_d, 4),
                    "ev_per_trade": round(pnl_d / tot_r, 6) if tot_r else None,
                    "brier_score": round(float(d["sum_brier_terms"] or 0.0) / n_d, 6) if n_d else None,
                })
            # Decay events
            decay_rows = db.execute(
                """
                SELECT ts, eval_type, metric_value, threshold, triggered
                FROM decay_evaluations
                WHERE model_name=? AND symbol=? AND market_window_seconds=?
                ORDER BY ts DESC LIMIT 20
                """,
                (mn, symbol, mw),
            ).fetchall()
            fleet_block["by_market_window"][str(mw)] = {
                "lifetime": agg,
                "composite_score_latest": round(composite_latest, 6) if composite_latest is not None else None,
                "daily_series": daily_series,
                "decay_events": [
                    {
                        "ts": d["ts"],
                        "eval_type": d["eval_type"],
                        "metric_value": float(d["metric_value"]) if d["metric_value"] is not None else None,
                        "threshold": float(d["threshold"]) if d["threshold"] is not None else None,
                        "triggered": bool(d["triggered"]),
                    }
                    for d in decay_rows
                ],
            }
        fleets.append(fleet_block)

    return {
        "cell_key": cell_key,
        "symbol": symbol,
        "training_horizon_seconds": horizon,
        "train_days": train_days,
        "fleets": fleets,
        "metadata": {
            "computed_at_ms": int(_time.time() * 1000),
            "since_ms": since_ms,
            "since_date": since_date,
            "n_fleets": len(fleets),
        },
    }


# ===========================================================================
# Regime + market-context analytics (Tier 2)
#
# Slice cell performance by:
#   - regime_volatility × regime_liquidity × regime_trend triplet
#   - pre-prediction market state proxies (p_market bucket, p_model_minus_market bucket)
#   - hour-of-day / day-of-week
#
# All three regime dims + p_market + p_model_minus_market are columns on
# `predictions`, captured at the moment the model fired. So the joins are
# none — we scan the predictions table directly with WHERE filters. With
# typical cell scope (one model_name, one market_window, ~30d of 5-min
# boundaries = ~8k rows) the scan is comfortably under 100ms.
# ===========================================================================


def _wilson_lo(wins: int, n: int) -> float | None:
    if n <= 0:
        return None
    return _wilson_ci(wins, n)[0]


def _fleet_models_for_cell(db, symbol: str, horizon: int, train_days: int) -> list[dict]:
    """All models in this cell, newest fleet first. Includes retired fleets."""
    rows = db.execute(
        """
        SELECT name, fleet_version, paper_active
        FROM model_registry
        WHERE symbol=? AND training_horizon_seconds=? AND train_days=?
        ORDER BY fleet_version DESC, created_at DESC
        """,
        (symbol, horizon, train_days),
    ).fetchall()
    return [dict(r) for r in rows]


def compute_cell_regime_breakdown(
    cell_key: str,
    market_window: int | None = None,
    since_ms: int | None = None,
    min_n: int = 10,
) -> dict:
    """Per-regime performance for every model in this cell.

    For each (fleet's model) × (market_window) × (regime triplet), reports
    n, win_rate (with Wilson 95% CI), and avg PnL realized.

    Aggregated client-side from `predictions` rather than the daily rollup
    because the rollup throws away the regime columns.
    """
    symbol, horizon, train_days = _parse_cell_key(cell_key)
    if since_ms is None:
        since_ms = int(_time.time() * 1000) - 30 * 86_400_000
    market_windows = [market_window] if market_window else list(MARKET_WINDOWS)

    db = _get_db()
    models = _fleet_models_for_cell(db, symbol, horizon, train_days)
    if not models:
        return {
            "cell_key": cell_key,
            "symbol": symbol,
            "training_horizon_seconds": horizon,
            "train_days": train_days,
            "since_ms": since_ms,
            "market_windows": market_windows,
            "models": [],
            "metadata": {"computed_at_ms": int(_time.time() * 1000)},
        }

    placeholder_names = ",".join("?" for _ in models)
    name_params = [m["name"] for m in models]

    out_models: list[dict] = []
    for mw in market_windows:
        # One round-trip per market window, grouped by (model_name, regime triplet)
        rows = db.execute(
            f"""
            SELECT
                model_name,
                COALESCE(regime_volatility, '?')  AS rv,
                COALESCE(regime_liquidity, '?')   AS rl,
                COALESCE(regime_trend, '?')       AS rt,
                COUNT(*)                          AS n,
                SUM(prediction_correct)           AS n_correct,
                AVG(p_market)                     AS avg_p_market,
                AVG(pred_proba_calibrated)        AS avg_p_model,
                AVG(p_model_minus_market)         AS avg_divergence,
                AVG(CASE WHEN price_at_close IS NOT NULL AND price_at_open IS NOT NULL AND price_at_open > 0
                    THEN (price_at_close - price_at_open) / price_at_open ELSE NULL END) AS avg_price_move
            FROM predictions
            WHERE model_name IN ({placeholder_names})
              AND symbol = ? AND market_window_seconds = ?
              AND resolved = 1 AND warmup = 0
              AND ts_contract_open_ms >= ?
            GROUP BY model_name, rv, rl, rt
            HAVING n >= ?
            ORDER BY n DESC
            """,
            (*name_params, symbol, mw, since_ms, min_n),
        ).fetchall()
        by_model: dict[str, list[dict]] = {}
        for r in rows:
            mn = r["model_name"]
            n = int(r["n"])
            nc = int(r["n_correct"] or 0)
            wr_lo, wr_hi = _wilson_ci(nc, n)
            entry = {
                "regime_volatility": r["rv"],
                "regime_liquidity": r["rl"],
                "regime_trend": r["rt"],
                "n": n,
                "n_correct": nc,
                "win_rate": round(nc / n, 6) if n else None,
                "win_rate_ci_lo": round(wr_lo, 6) if wr_lo is not None else None,
                "win_rate_ci_hi": round(wr_hi, 6) if wr_hi is not None else None,
                "avg_p_market": round(r["avg_p_market"], 4) if r["avg_p_market"] is not None else None,
                "avg_p_model": round(r["avg_p_model"], 4) if r["avg_p_model"] is not None else None,
                "avg_divergence": round(r["avg_divergence"], 4) if r["avg_divergence"] is not None else None,
                "avg_price_move_pct": round((r["avg_price_move"] or 0) * 100, 4) if r["avg_price_move"] is not None else None,
            }
            by_model.setdefault(mn, []).append(entry)
        for mn, buckets in by_model.items():
            out_models.append({
                "model_name": mn,
                "market_window_seconds": mw,
                "fleet_version": next((m["fleet_version"] for m in models if m["name"] == mn), None),
                "n_regime_buckets": len(buckets),
                "regime_buckets": buckets,
            })
    return {
        "cell_key": cell_key,
        "symbol": symbol,
        "training_horizon_seconds": horizon,
        "train_days": train_days,
        "since_ms": since_ms,
        "market_windows": market_windows,
        "models": out_models,
        "metadata": {
            "computed_at_ms": int(_time.time() * 1000),
            "min_n": min_n,
            "n_models": len(models),
        },
    }


def _bucket_value(v: float | None, buckets) -> str | None:
    if v is None:
        return None
    for label, lo, hi in buckets:
        if lo <= v < hi:
            return label
    return None


def compute_context_outcome_correlation(
    cell_key: str,
    market_window: int | None = None,
    since_ms: int | None = None,
    min_n: int = 5,
) -> dict:
    """3-way correlation: pre-prediction market state × model output × outcome.

    For each (model, market_window) we compute two 2-D heatmaps:

      (A) p_market_bucket × divergence_bucket -> {n, win_rate, avg_price_move}
          "When the market said X and our model diverged by Y, did we win?"

      (B) regime_volatility × utc_hour_bucket -> {n, win_rate}
          "Does our edge survive different times of day under different vol?"

    Heatmap (A) is the closer match to what the user asked for — it
    directly answers "what was the market doing right before we predicted,
    and did our prediction correlate with the outcome?" p_market is the
    Kalshi book's implied probability AT the boundary, captured by the
    trader inside the prediction row. p_model_minus_market is the signed
    divergence (positive = model thought side was more likely than market).
    """
    symbol, horizon, train_days = _parse_cell_key(cell_key)
    if since_ms is None:
        since_ms = int(_time.time() * 1000) - 30 * 86_400_000
    market_windows = [market_window] if market_window else list(MARKET_WINDOWS)

    db = _get_db()
    models = _fleet_models_for_cell(db, symbol, horizon, train_days)
    if not models:
        return {
            "cell_key": cell_key,
            "symbol": symbol,
            "training_horizon_seconds": horizon,
            "train_days": train_days,
            "since_ms": since_ms,
            "market_windows": market_windows,
            "models": [],
            "metadata": {"computed_at_ms": int(_time.time() * 1000)},
        }
    placeholder_names = ",".join("?" for _ in models)
    name_params = [m["name"] for m in models]

    out_models: list[dict] = []
    for mw in market_windows:
        rows = db.execute(
            f"""
            SELECT
                model_name,
                p_market,
                p_model_minus_market,
                COALESCE(regime_volatility, '?') AS rv,
                utc_hour,
                prediction_correct,
                price_at_open,
                price_at_close
            FROM predictions
            WHERE model_name IN ({placeholder_names})
              AND symbol = ? AND market_window_seconds = ?
              AND resolved = 1 AND warmup = 0
              AND ts_contract_open_ms >= ?
            """,
            (*name_params, symbol, mw, since_ms),
        ).fetchall()

        # Bucket client-side so we can express bucket ranges compactly.
        per_model: dict[str, dict] = {}
        for r in rows:
            mn = r["model_name"]
            pm = r["p_market"]
            div = r["p_model_minus_market"]
            rv = r["rv"]
            uh = r["utc_hour"]
            correct = bool(r["prediction_correct"])
            price_open = r["price_at_open"]
            price_close = r["price_at_close"]
            move_pct = None
            if (price_open is not None and price_close is not None
                    and price_open > 0):
                move_pct = (price_close - price_open) / price_open

            pm_bucket = _bucket_value(pm, P_MARKET_BUCKETS)
            div_bucket = _bucket_value(div, DIVERGENCE_BUCKETS)
            # 4-hour utc buckets: 0-3, 4-7, 8-11, 12-15, 16-19, 20-23
            uh_bucket = f"{(uh // 4) * 4:02d}-{((uh // 4) * 4) + 3:02d}" if uh is not None else None

            cell_a = per_model.setdefault(mn, {
                "matrix_a": {},
                "matrix_b": {},
                "n_total": 0,
                "n_correct": 0,
                "sum_price_move_pct": 0.0,
                "n_price_move_seen": 0,
            })
            cell_a["n_total"] += 1
            if correct:
                cell_a["n_correct"] += 1
            if move_pct is not None:
                cell_a["sum_price_move_pct"] += move_pct
                cell_a["n_price_move_seen"] += 1

            if pm_bucket and div_bucket:
                key = (pm_bucket, div_bucket)
                a = cell_a["matrix_a"].setdefault(key, {"n": 0, "n_correct": 0, "sum_move": 0.0, "n_move": 0})
                a["n"] += 1
                if correct:
                    a["n_correct"] += 1
                if move_pct is not None:
                    a["sum_move"] += move_pct
                    a["n_move"] += 1

            if uh_bucket:
                bkey = (rv, uh_bucket)
                b = cell_a["matrix_b"].setdefault(bkey, {"n": 0, "n_correct": 0})
                b["n"] += 1
                if correct:
                    b["n_correct"] += 1

        for mn, data in per_model.items():
            matrix_a_rows = []
            for (pm_bucket, div_bucket), agg in data["matrix_a"].items():
                if agg["n"] < min_n:
                    continue
                wr_lo, _ = _wilson_ci(agg["n_correct"], agg["n"])
                matrix_a_rows.append({
                    "p_market_bucket": pm_bucket,
                    "divergence_bucket": div_bucket,
                    "n": agg["n"],
                    "win_rate": round(agg["n_correct"] / agg["n"], 6),
                    "win_rate_lo_95": round(wr_lo, 6) if wr_lo is not None else None,
                    "avg_price_move_pct": (
                        round((agg["sum_move"] / agg["n_move"]) * 100, 4)
                        if agg["n_move"] else None
                    ),
                })
            matrix_b_rows = []
            for (rv, uh_bucket), agg in data["matrix_b"].items():
                if agg["n"] < min_n:
                    continue
                wr_lo, _ = _wilson_ci(agg["n_correct"], agg["n"])
                matrix_b_rows.append({
                    "regime_volatility": rv,
                    "utc_hour_bucket": uh_bucket,
                    "n": agg["n"],
                    "win_rate": round(agg["n_correct"] / agg["n"], 6),
                    "win_rate_lo_95": round(wr_lo, 6) if wr_lo is not None else None,
                })
            # Sort for stable rendering
            matrix_a_rows.sort(key=lambda x: (x["p_market_bucket"], x["divergence_bucket"]))
            matrix_b_rows.sort(key=lambda x: (x["regime_volatility"], x["utc_hour_bucket"]))
            out_models.append({
                "model_name": mn,
                "market_window_seconds": mw,
                "fleet_version": next((m["fleet_version"] for m in models if m["name"] == mn), None),
                "n_total": data["n_total"],
                "win_rate_overall": (
                    round(data["n_correct"] / data["n_total"], 6)
                    if data["n_total"] else None
                ),
                "avg_price_move_pct": (
                    round((data["sum_price_move_pct"] / data["n_price_move_seen"]) * 100, 4)
                    if data["n_price_move_seen"] else None
                ),
                "matrix_p_market_x_divergence": matrix_a_rows,
                "matrix_volatility_x_utc_hour": matrix_b_rows,
            })

    return {
        "cell_key": cell_key,
        "symbol": symbol,
        "training_horizon_seconds": horizon,
        "train_days": train_days,
        "since_ms": since_ms,
        "market_windows": market_windows,
        "models": out_models,
        "metadata": {
            "computed_at_ms": int(_time.time() * 1000),
            "min_n": min_n,
            "p_market_buckets": [b[0] for b in P_MARKET_BUCKETS],
            "divergence_buckets": [b[0] for b in DIVERGENCE_BUCKETS],
        },
    }


def compute_cell_hourly_series(
    cell_key: str,
    market_window: int = 300,
    since_ms: int | None = None,
) -> dict:
    """Hourly win-rate per fleet model over time.

    Daily rollups are too coarse for 5-min markets — at 288 boundaries/day
    you can lose a regime shift in the noise. This gives one row per
    (model_name, hour_utc) so the UI can plot fine-grained trend lines.
    """
    symbol, horizon, train_days = _parse_cell_key(cell_key)
    if since_ms is None:
        since_ms = int(_time.time() * 1000) - 14 * 86_400_000

    db = _get_db()
    models = _fleet_models_for_cell(db, symbol, horizon, train_days)
    placeholder_names = ",".join("?" for _ in models) if models else "''"
    name_params = [m["name"] for m in models]

    rows = db.execute(
        f"""
        SELECT
            model_name,
            strftime('%Y-%m-%dT%H', datetime(ts_contract_open_ms/1000, 'unixepoch')) AS hour_utc,
            COUNT(*) AS n,
            SUM(prediction_correct) AS n_correct,
            AVG(p_market) AS avg_p_market,
            AVG(p_model_minus_market) AS avg_divergence
        FROM predictions
        WHERE model_name IN ({placeholder_names})
          AND symbol = ? AND market_window_seconds = ?
          AND resolved = 1 AND warmup = 0
          AND ts_contract_open_ms >= ?
        GROUP BY model_name, hour_utc
        ORDER BY hour_utc
        """,
        (*name_params, symbol, market_window, since_ms),
    ).fetchall() if models else []

    by_model: dict[str, list[dict]] = {}
    for r in rows:
        mn = r["model_name"]
        n = int(r["n"])
        nc = int(r["n_correct"] or 0)
        wr_lo, _ = _wilson_ci(nc, n)
        by_model.setdefault(mn, []).append({
            "hour_utc": r["hour_utc"],
            "n": n,
            "win_rate": round(nc / n, 6) if n else None,
            "win_rate_lo_95": round(wr_lo, 6) if wr_lo is not None else None,
            "avg_p_market": round(r["avg_p_market"], 4) if r["avg_p_market"] is not None else None,
            "avg_divergence": round(r["avg_divergence"], 4) if r["avg_divergence"] is not None else None,
        })
    return {
        "cell_key": cell_key,
        "symbol": symbol,
        "training_horizon_seconds": horizon,
        "train_days": train_days,
        "market_window_seconds": market_window,
        "since_ms": since_ms,
        "models": [
            {
                "model_name": mn,
                "fleet_version": next((m["fleet_version"] for m in models if m["name"] == mn), None),
                "series": series,
            }
            for mn, series in by_model.items()
        ],
        "metadata": {
            "computed_at_ms": int(_time.time() * 1000),
        },
    }
