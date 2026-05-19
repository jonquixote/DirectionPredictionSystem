"""Analysis router — model + filter optimization endpoints.

All heavy logic is in services/analysis.py.
These are thin wrappers: parse query params, call service, return JSON.

Endpoints:
  GET /api/analysis/leaderboard
  GET /api/analysis/threshold-grid
  GET /api/analysis/committee-sim
  GET /api/analysis/skip-conditions
  GET /api/analysis/full-report
"""
from __future__ import annotations

from fastapi import APIRouter, Query, HTTPException

try:
    from services.analysis import (
        compute_leaderboard,
        compute_threshold_grid,
        compute_committee_sim,
        compute_skip_conditions,
        compute_full_report,
        DEFAULT_THRESHOLDS,
    )
except ModuleNotFoundError:
    from dashboard_api.services.analysis import (  # type: ignore[no-redef]
        compute_leaderboard,
        compute_threshold_grid,
        compute_committee_sim,
        compute_skip_conditions,
        compute_full_report,
        DEFAULT_THRESHOLDS,
    )

router = APIRouter(tags=["analysis"])


@router.get("/analysis/leaderboard")
async def leaderboard(
    symbol: str | None = Query(None),
    market_window: int | None = Query(None, alias="window"),
    min_samples: int = Query(50, ge=1),
    metric: str = Query("win_rate", description="win_rate|roi|ev_per_trade|brier_score|n_samples|sharpe"),
    since_ms: int | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """Ranked model leaderboard by symbol × window."""
    valid_metrics = {"win_rate", "roi", "ev_per_trade", "brier_score", "n_samples", "sharpe"}
    if metric not in valid_metrics:
        raise HTTPException(status_code=422, detail=f"metric must be one of {sorted(valid_metrics)}")
    return compute_leaderboard(
        symbol=symbol,
        market_window=market_window,
        min_samples=min_samples,
        metric=metric,
        since_ms=since_ms,
        limit=limit,
    )


@router.get("/analysis/threshold-grid")
async def threshold_grid(
    symbol: str = Query(...),
    window: int = Query(...),
    min_samples: int = Query(50, ge=1),
    since_ms: int | None = Query(None),
    limit_models: int = Query(30, ge=1, le=100),
):
    """Per-model threshold sweep for (symbol, window)."""
    return compute_threshold_grid(
        symbol=symbol,
        market_window=window,
        min_samples=min_samples,
        since_ms=since_ms,
        limit_models=limit_models,
        thresholds=DEFAULT_THRESHOLDS,
    )


@router.get("/analysis/committee-sim")
async def committee_sim(
    symbol: str = Query(...),
    window: int = Query(...),
    strategy: str = Query("avg", description="avg|vote|weighted_ev"),
    since_ms: int | None = Query(None),
):
    """Committee decision simulation vs best single model."""
    valid = {"avg", "vote", "weighted_ev"}
    if strategy not in valid:
        raise HTTPException(status_code=422, detail=f"strategy must be one of {sorted(valid)}")
    return compute_committee_sim(
        symbol=symbol,
        market_window=window,
        strategy=strategy,
        since_ms=since_ms,
    )


@router.get("/analysis/skip-conditions")
async def skip_conditions(
    symbol: str | None = Query(None),
    window: int | None = Query(None),
    min_bucket_size: int = Query(30, ge=1),
    since_ms: int | None = Query(None),
):
    """Surface time/regime buckets where win rate < 50% (skip candidates)."""
    return compute_skip_conditions(
        symbol=symbol,
        market_window=window,
        min_bucket_size=min_bucket_size,
        since_ms=since_ms,
    )


@router.get("/analysis/full-report")
async def full_report(
    symbol: str | None = Query(None),
    window: int | None = Query(None),
    since_ms: int | None = Query(None),
):
    """Composite A+B+C+D report for all (symbol, window) combinations with recommendations."""
    return compute_full_report(
        symbol=symbol,
        market_window=window,
        since_ms=since_ms,
    )
