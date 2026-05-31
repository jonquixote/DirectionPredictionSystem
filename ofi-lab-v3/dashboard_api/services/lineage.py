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
