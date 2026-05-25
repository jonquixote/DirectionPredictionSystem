"""Analysis router — model + filter optimization endpoints.

All heavy logic is in services/analysis.py.
These are thin wrappers: parse query params, call service, return JSON.

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

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

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


# ---------------------------------------------------------------------------
# v2 Premium Filter Discovery endpoints
# ---------------------------------------------------------------------------

@router.post("/analysis/simulate")
async def simulate(body: SimulateRequest):
    """Apply a filter_config to historical resolved predictions and return metrics."""
    return simulate_filter(
        filter_config=body.filter_config,
        symbol=body.symbol,
        window=body.window,
        since_ms=body.since_ms,
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
    return grid_search(
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
    return regime_matrix(
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
    return consensus_analysis(
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
    return decay_filter_analysis(
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
    return observation_status()


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
    return optimize_committee_weights(
        symbol=symbol,
        window=window,
        objective=objective,
        since_ms=since_ms,
    )


@router.post("/analysis/walk-forward")
async def api_walk_forward(body: WalkForwardRequest):
    """Walk-forward validation of a filter_config over chronological folds."""
    return walk_forward_validate(
        filter_config=body.filter_config,
        symbol=body.symbol,
        window=body.window,
        n_folds=body.n_folds,
        since_ms=body.since_ms,
    )


@router.post("/analysis/train-test")
async def api_train_test(body: TrainTestRequest):
    """Chronological train/test split to check filter stability."""
    return train_test_validate(
        filter_config=body.filter_config,
        symbol=body.symbol,
        window=body.window,
        train_frac=body.train_frac,
        since_ms=body.since_ms,
    )


@router.post("/analysis/recommend-premium")
async def api_recommend_premium(body: RecommendPremiumRequest):
    """Full top-down recommendation: grid search → validate → pick winner."""
    return recommend_premium_filter(
        symbol=body.symbol,
        window=body.window,
        since_ms=body.since_ms,
        mode=body.mode,
    )


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
