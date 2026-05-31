"""Analysis router — model + filter optimization endpoints.

All heavy logic is in services/analysis.py.
These are thin wrappers: parse query params, call service, return JSON.

T1-be (2026-05-27): every sync compute_* call is wrapped in
``asyncio.to_thread`` so a single heavy /analysis request no longer blocks
the uvicorn event loop. The 5 fast-path endpoints (leaderboard,
threshold-grid, committee-sim, skip-conditions, full-report) accept
``full_history=true`` to opt out of the default 30d / 50k-row bound, and
echo the scope they actually used via ``X-Analysis-*`` response headers
(see _set_analysis_meta_headers below).

Endpoints:
  GET  /api/analysis/leaderboard
  GET  /api/analysis/threshold-grid
  GET  /api/analysis/committee-sim
  GET  /api/analysis/skip-conditions
  GET  /api/analysis/full-report
  POST /api/analysis/simulate
  GET  /api/analysis/grid-search
  GET  /api/analysis/regime-matrix
  GET  /api/analysis/consensus
  GET  /api/analysis/decay-filter
  GET  /api/analysis/committee-weights
  POST /api/analysis/walk-forward
  POST /api/analysis/train-test
  POST /api/analysis/recommend-premium
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, HTTPException, Response
from pydantic import BaseModel


def _set_analysis_meta_headers(response: Response, meta: dict) -> None:
    """Surface the bounded-fetch metadata on the HTTP response.

    Headers are read by the frontend (T1-fe) to render scope indicators —
    a "scanning N rows…" badge, rollup-lag warning, or "showing last 30d"
    chip on the analysis page. Strings are required by Starlette's header
    type.
    """
    path = meta.get("path")
    if path:
        response.headers["X-Analysis-Path"] = str(path)
    response.headers["X-Analysis-Truncated"] = "1" if meta.get("truncated") else "0"
    sm = meta.get("since_ms_used")
    if sm is not None:
        response.headers["X-Analysis-Since-Ms-Used"] = str(int(sm))
    rc = meta.get("row_count_scanned")
    if rc is not None:
        response.headers["X-Analysis-Row-Count-Scanned"] = str(int(rc))
    response.headers["X-Analysis-Full-History"] = "1" if meta.get("full_history") else "0"

try:
    from services.analysis import (
        compute_leaderboard,
        compute_threshold_grid,
        compute_committee_sim,
        compute_skip_conditions,
        compute_full_report,
        DEFAULT_THRESHOLDS,
        simulate_filter,
        grid_search,
        regime_matrix,
        consensus_analysis,
        decay_filter_analysis,
        optimize_committee_weights,
        walk_forward_validate,
        train_test_validate,
        recommend_premium_filter,
    )
except ModuleNotFoundError:
    from dashboard_api.services.analysis import (  # type: ignore[no-redef]
        compute_leaderboard,
        compute_threshold_grid,
        compute_committee_sim,
        compute_skip_conditions,
        compute_full_report,
        DEFAULT_THRESHOLDS,
        simulate_filter,
        grid_search,
        regime_matrix,
        consensus_analysis,
        decay_filter_analysis,
        optimize_committee_weights,
        walk_forward_validate,
        train_test_validate,
        recommend_premium_filter,
    )


# ---------------------------------------------------------------------------
# Pydantic request models for POST endpoints
# ---------------------------------------------------------------------------

class SimulateRequest(BaseModel):
    filter_config: dict
    symbol: str
    window: int
    since_ms: int | None = None
    bootstrap_n: int = 0
    model: str | None = None


class WalkForwardRequest(BaseModel):
    filter_config: dict
    symbol: str
    window: int
    n_folds: int = 5
    since_ms: int | None = None


class TrainTestRequest(BaseModel):
    filter_config: dict
    symbol: str
    window: int
    train_frac: float = 0.7
    since_ms: int | None = None


class RecommendPremiumRequest(BaseModel):
    symbol: str
    window: int
    since_ms: int | None = None
    mode: str = "strict"

router = APIRouter(tags=["analysis"])


@router.get("/analysis/leaderboard")
async def leaderboard(
    response: Response,
    symbol: str | None = Query(None),
    market_window: int | None = Query(None, alias="window"),
    min_samples: int = Query(50, ge=1),
    metric: str = Query("win_rate", description="win_rate|roi|ev_per_trade|brier_score|n_samples|sharpe"),
    since_ms: int | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    full_history: bool = Query(False, description="Bypass 30d/50k-row default bound and scan all resolved predictions. Slow."),
):
    """Ranked model leaderboard by symbol × window."""
    valid_metrics = {"win_rate", "roi", "ev_per_trade", "brier_score", "n_samples", "sharpe"}
    if metric not in valid_metrics:
        raise HTTPException(status_code=422, detail=f"metric must be one of {sorted(valid_metrics)}")
    meta: dict = {}
    result = await asyncio.to_thread(
        compute_leaderboard,
        symbol=symbol,
        market_window=market_window,
        min_samples=min_samples,
        metric=metric,
        since_ms=since_ms,
        limit=limit,
        full_history=full_history,
        meta=meta,
    )
    _set_analysis_meta_headers(response, meta)
    return result


@router.get("/analysis/threshold-grid")
async def threshold_grid(
    response: Response,
    symbol: str = Query(...),
    window: int = Query(...),
    min_samples: int = Query(50, ge=1),
    since_ms: int | None = Query(None),
    limit_models: int = Query(30, ge=1, le=100),
    full_history: bool = Query(False),
):
    """Per-model threshold sweep for (symbol, window)."""
    meta: dict = {}
    result = await asyncio.to_thread(
        compute_threshold_grid,
        symbol=symbol,
        market_window=window,
        min_samples=min_samples,
        since_ms=since_ms,
        limit_models=limit_models,
        thresholds=DEFAULT_THRESHOLDS,
        full_history=full_history,
        meta=meta,
    )
    _set_analysis_meta_headers(response, meta)
    return result


@router.get("/analysis/committee-sim")
async def committee_sim(
    response: Response,
    symbol: str = Query(...),
    window: int = Query(...),
    strategy: str = Query("avg", description="avg|vote|weighted_ev"),
    since_ms: int | None = Query(None),
    full_history: bool = Query(False),
):
    """Committee decision simulation vs best single model."""
    valid = {"avg", "vote", "weighted_ev"}
    if strategy not in valid:
        raise HTTPException(status_code=422, detail=f"strategy must be one of {sorted(valid)}")
    meta: dict = {}
    result = await asyncio.to_thread(
        compute_committee_sim,
        symbol=symbol,
        market_window=window,
        strategy=strategy,
        since_ms=since_ms,
        full_history=full_history,
        meta=meta,
    )
    _set_analysis_meta_headers(response, meta)
    return result


@router.get("/analysis/skip-conditions")
async def skip_conditions(
    response: Response,
    symbol: str | None = Query(None),
    window: int | None = Query(None),
    min_bucket_size: int = Query(30, ge=1),
    since_ms: int | None = Query(None),
    full_history: bool = Query(False),
    model: str | None = Query(None, description="Scope buckets to a single model_name."),
):
    """Surface time/regime buckets where win rate < 50% (skip candidates)."""
    meta: dict = {}
    result = await asyncio.to_thread(
        compute_skip_conditions,
        symbol=symbol,
        market_window=window,
        min_bucket_size=min_bucket_size,
        since_ms=since_ms,
        full_history=full_history,
        meta=meta,
        model=model,
    )
    _set_analysis_meta_headers(response, meta)
    return result


@router.get("/analysis/full-report")
async def full_report(
    response: Response,
    symbol: str | None = Query(None),
    window: int | None = Query(None),
    since_ms: int | None = Query(None),
    full_history: bool = Query(False),
):
    """Composite A+B+C+D report for all (symbol, window) combinations with recommendations."""
    meta: dict = {}
    result = await asyncio.to_thread(
        compute_full_report,
        symbol=symbol,
        market_window=window,
        since_ms=since_ms,
        full_history=full_history,
        meta=meta,
    )
    _set_analysis_meta_headers(response, meta)
    return result


# ---------------------------------------------------------------------------
# v2 Premium Filter Discovery endpoints
# ---------------------------------------------------------------------------

@router.post("/analysis/simulate")
async def simulate(body: SimulateRequest):
    """Apply a filter_config to historical resolved predictions and return metrics."""
    return await asyncio.to_thread(
        simulate_filter,
        filter_config=body.filter_config,
        symbol=body.symbol,
        window=body.window,
        since_ms=body.since_ms,
        model=body.model,
        bootstrap_n=body.bootstrap_n,
    )


@router.get("/analysis/grid-search")
async def api_grid_search(
    symbol: str = Query(...),
    window: int = Query(...),
    top_k: int = Query(20, ge=1, le=200),
    since_ms: int | None = Query(None),
    min_n_passed: int = Query(100, ge=1),
    apply_fdr: bool = Query(True),
):
    """Sweep filter configs over Cartesian grid with BH-FDR correction."""
    return await asyncio.to_thread(
        grid_search,
        symbol=symbol,
        window=window,
        since_ms=since_ms,
        top_k=top_k,
        min_n_passed=min_n_passed,
        apply_fdr=apply_fdr,
    )


@router.get("/analysis/regime-matrix")
async def api_regime_matrix(
    symbol: str | None = Query(None),
    window: int | None = Query(None),
    since_ms: int | None = Query(None),
    min_cell_n: int = Query(30, ge=1),
):
    """Per (model, regime) win-rate cell matrix."""
    return await asyncio.to_thread(
        regime_matrix,
        symbol=symbol,
        window=window,
        since_ms=since_ms,
        min_cell_n=min_cell_n,
    )


@router.get("/analysis/consensus")
async def api_consensus(
    symbol: str = Query(...),
    window: int = Query(...),
    since_ms: int | None = Query(None),
):
    """Compare boundaries where models agree (consensus) vs split."""
    return await asyncio.to_thread(
        consensus_analysis,
        symbol=symbol,
        window=window,
        since_ms=since_ms,
    )


@router.get("/analysis/decay-filter")
async def api_decay_filter(
    symbol: str | None = Query(None),
    window: int | None = Query(None),
    since_ms: int | None = Query(None),
    min_bucket_n: int = Query(30, ge=1),
):
    """Bucket predictions by decay state at prediction time; recommend threshold."""
    return await asyncio.to_thread(
        decay_filter_analysis,
        symbol=symbol,
        window=window,
        since_ms=since_ms,
        min_bucket_n=min_bucket_n,
    )


@router.get("/analysis/observation-status")
async def api_observation_status():
    """Live monitoring of consensus-gated paper-observation models.

    Returns the 6 currently-gated models (3 ETH 300s + 3 BTC 1800s) plus
    fresh paper-trade win rates and the 7-day decision gate readout
    (ship / observe / kill).
    """
    try:
        from services.analysis import observation_status
    except ModuleNotFoundError:
        from dashboard_api.services.analysis import observation_status  # type: ignore
    return await asyncio.to_thread(observation_status)


@router.get("/analysis/committee-weights")
async def api_committee_weights(
    symbol: str = Query(...),
    window: int = Query(...),
    objective: str = Query("sharpe", description="sharpe|mean|win_rate"),
    since_ms: int | None = Query(None),
):
    """Find per-model weights that maximize the chosen objective."""
    valid_objectives = {"sharpe", "mean", "win_rate"}
    if objective not in valid_objectives:
        raise HTTPException(
            status_code=422,
            detail=f"objective must be one of {sorted(valid_objectives)}",
        )
    return await asyncio.to_thread(
        optimize_committee_weights,
        symbol=symbol,
        window=window,
        objective=objective,
        since_ms=since_ms,
    )


@router.post("/analysis/walk-forward")
async def api_walk_forward(body: WalkForwardRequest):
    """Walk-forward validation of a filter_config over chronological folds."""
    return await asyncio.to_thread(
        walk_forward_validate,
        filter_config=body.filter_config,
        symbol=body.symbol,
        window=body.window,
        n_folds=body.n_folds,
        since_ms=body.since_ms,
    )


@router.post("/analysis/train-test")
async def api_train_test(body: TrainTestRequest):
    """Chronological train/test split to check filter stability."""
    return await asyncio.to_thread(
        train_test_validate,
        filter_config=body.filter_config,
        symbol=body.symbol,
        window=body.window,
        train_frac=body.train_frac,
        since_ms=body.since_ms,
    )


@router.post("/analysis/recommend-premium")
async def api_recommend_premium(body: RecommendPremiumRequest):
    """Full top-down recommendation: grid search → validate → pick winner."""
    return await asyncio.to_thread(
        recommend_premium_filter,
        symbol=body.symbol,
        window=body.window,
        since_ms=body.since_ms,
        mode=body.mode,
    )


# ---------------------------------------------------------------------------
# Lineage analytics — Tier 1 of the model-lineage spec
# (docs/2026-05-31-model-lineage-analytics-spec.md)
# ---------------------------------------------------------------------------

try:
    from services.lineage import (  # type: ignore
        compute_best_per_cell,
        compute_cell_history,
        compute_cell_regime_breakdown,
        compute_context_outcome_correlation,
        compute_cell_hourly_series,
        list_cells,
        LEADERBOARD_METRICS,
    )
except ModuleNotFoundError:
    from dashboard_api.services.lineage import (  # type: ignore[no-redef]
        compute_best_per_cell,
        compute_cell_history,
        compute_cell_regime_breakdown,
        compute_context_outcome_correlation,
        compute_cell_hourly_series,
        list_cells,
        LEADERBOARD_METRICS,
    )


@router.get("/analysis/best-per-cell")
async def api_best_per_cell(
    metric: str = Query("composite", description="composite|roi|win_rate|sharpe"),
    since_ms: int | None = Query(None, description="Default = now - 14d"),
    min_n_samples: int = Query(50, ge=1, description="Drop models with fewer than N rollup samples"),
    top_k_runners: int = Query(3, ge=0, le=10, description="Runners-up returned per cell"),
):
    """Best model per (symbol, market_window) — 12 cells (4 symbols × 3 windows)."""
    if metric not in LEADERBOARD_METRICS:
        raise HTTPException(status_code=422, detail=f"metric must be one of {sorted(LEADERBOARD_METRICS)}")
    return await asyncio.to_thread(
        compute_best_per_cell,
        metric=metric,
        since_ms=since_ms,
        min_n_samples=min_n_samples,
        top_k_runners=top_k_runners,
    )


@router.get("/analysis/cell-history")
async def api_cell_history(
    cell_key: str = Query(..., description="SYMBOL_HORIZON_TRAININGDAYS (e.g. BTCUSDT_300_179)"),
    since_ms: int | None = Query(None, description="Default = oldest train_window_end in cell"),
):
    """Per-fleet history for one cell, with daily series per market window."""
    try:
        return await asyncio.to_thread(
            compute_cell_history,
            cell_key=cell_key,
            since_ms=since_ms,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/analysis/cell-list")
async def api_cell_list():
    """All registered cells with their current incumbent.

    Pure index. Lets the frontend populate dropdowns and the matrix grid
    without fetching the heavier best-per-cell payload first.
    """
    return await asyncio.to_thread(list_cells)


@router.get("/analysis/cell-regime-breakdown")
async def api_cell_regime_breakdown(
    cell_key: str = Query(..., description="SYMBOL_HORIZON_TRAININGDAYS"),
    market_window: int | None = Query(None, description="300 | 900 | 1800. Default = all three."),
    since_ms: int | None = Query(None, description="Default = now - 30d"),
    min_n: int = Query(10, ge=1, description="Drop regime buckets with fewer than N predictions"),
):
    """Per-(regime_volatility, regime_liquidity, regime_trend) performance per model in the cell.

    Slice predictions by the regime triplet captured at prediction time.
    Reveals which models work in which market conditions and how that
    rotates across fleet generations.
    """
    try:
        return await asyncio.to_thread(
            compute_cell_regime_breakdown,
            cell_key=cell_key,
            market_window=market_window,
            since_ms=since_ms,
            min_n=min_n,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/analysis/cell-context-correlation")
async def api_cell_context_correlation(
    cell_key: str = Query(..., description="SYMBOL_HORIZON_TRAININGDAYS"),
    market_window: int | None = Query(None, description="300 | 900 | 1800. Default = all three."),
    since_ms: int | None = Query(None, description="Default = now - 30d"),
    min_n: int = Query(5, ge=1, description="Drop heatmap cells with fewer than N predictions"),
):
    """3-way correlation: pre-prediction market state x model output x outcome.

    Returns two heatmaps per model:
      A. p_market_bucket x divergence_bucket (what the market thought vs
         what the model thought, vs whether the model was right).
      B. regime_volatility x utc_hour_bucket (does the edge survive
         different times of day under different vol).
    """
    try:
        return await asyncio.to_thread(
            compute_context_outcome_correlation,
            cell_key=cell_key,
            market_window=market_window,
            since_ms=since_ms,
            min_n=min_n,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/analysis/cell-hourly-series")
async def api_cell_hourly_series(
    cell_key: str = Query(..., description="SYMBOL_HORIZON_TRAININGDAYS"),
    market_window: int = Query(300, description="300 | 900 | 1800"),
    since_ms: int | None = Query(None, description="Default = now - 14d"),
):
    """Hourly win-rate per fleet model. Daily rollups are too coarse for 5-min markets."""
    try:
        return await asyncio.to_thread(
            compute_cell_hourly_series,
            cell_key=cell_key,
            market_window=market_window,
            since_ms=since_ms,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/analysis/decay-alerts")
async def api_decay_alerts(
    since_ms: int | None = Query(None, description="Only return alerts after this timestamp_ms"),
    triggered: int = Query(1, description="1 = only triggered alerts (default), 0 = all"),
    eval_type: str | None = Query(None, description="Filter by eval_type: rwev_drop|brier_rise|calibration_drift"),
    limit: int = Query(200, ge=1, le=2000),
):
    """Recent decay_evaluations rows. Returns list of {ts, model_name, symbol, market_window_seconds, eval_type, metric_value, threshold, triggered, detail_json}."""
    try:
        from services.db import get_db  # type: ignore
    except ModuleNotFoundError:
        from dashboard_api.services.db import get_db  # type: ignore
    conn = get_db()
    try:
        sql = (
            "SELECT ts, model_name, symbol, market_window_seconds,"
            " eval_type, metric_value, threshold, triggered, detail_json"
            " FROM decay_evaluations"
            " WHERE 1=1"
        )
        params: list = []
        if since_ms is not None:
            # ts is ISO format; convert since_ms to ISO for comparison
            from datetime import datetime, timezone
            iso = datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            sql += " AND ts >= ?"
            params.append(iso)
        if triggered == 1:
            sql += " AND triggered = 1"
        if eval_type is not None:
            sql += " AND eval_type = ?"
            params.append(eval_type)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, tuple(params)).fetchall()
        out = []
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else {
                "ts": r[0], "model_name": r[1], "symbol": r[2],
                "market_window_seconds": r[3], "eval_type": r[4],
                "metric_value": r[5], "threshold": r[6],
                "triggered": r[7], "detail_json": r[8],
            }
            out.append(d)
        return {"alerts": out, "count": len(out)}
    finally:
        try:
            conn.close()
        except Exception:
            pass
