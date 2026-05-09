"""GET /api/predictions — paginated, filtered prediction records."""
from __future__ import annotations
from fastapi import APIRouter, Query
from services.jsonl_reader import get_store
from services.metrics import compute_realized_net, SYSTEM_FEE

router = APIRouter(tags=["predictions"])


@router.get("/predictions")
async def list_predictions(
    model: str | None = None,
    symbol: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
    direction: str | None = None,
    suppressed: bool | None = None,
    warmup: bool | None = None,
    outcome: str | None = None,
    divergence_min: float | None = None,
    divergence_max: float | None = None,
    pmodel_min: float | None = None,
    pmodel_max: float | None = None,
    prediction_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str = "ts_model_ran_ms",
    order: str = "desc",
):
    """Paginated, filtered prediction records."""
    store = get_store()

    # Exact match shortcut
    if prediction_id:
        match = [
            p for p in store.predictions
            if p.get("prediction_id") == prediction_id
        ]
        items = [_enrich_prediction(p) for p in match]
        return {
            "data": items,
            "meta": {
                "total": len(items),
                "page": 1,
                "page_size": page_size,
                "from_ms": from_ms,
                "to_ms": to_ms,
                "filters_applied": {"prediction_id": prediction_id},
            },
            "warnings": [],
        }

    items, total = store.get_predictions(
        model=model, symbol=symbol, from_ms=from_ms, to_ms=to_ms,
        direction=direction, suppressed=suppressed, warmup=warmup,
        outcome=outcome, page=page, page_size=page_size,
        sort=sort, order=order,
    )

    # Post-filters for divergence/pmodel ranges
    if divergence_min is not None:
        items = [p for p in items if (p.get("divergence") or 0) >= divergence_min]
    if divergence_max is not None:
        items = [p for p in items if (p.get("divergence") or 0) <= divergence_max]
    if pmodel_min is not None:
        items = [p for p in items if (p.get("pred_proba") or 0) >= pmodel_min]
    if pmodel_max is not None:
        items = [p for p in items if (p.get("pred_proba") or 0) <= pmodel_max]

    enriched = [_enrich_prediction(p) for p in items]

    return {
        "data": enriched,
        "meta": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "from_ms": from_ms,
            "to_ms": to_ms,
            "filters_applied": {
                k: v for k, v in {
                    "model": model, "symbol": symbol, "direction": direction,
                    "suppressed": suppressed, "warmup": warmup, "outcome": outcome,
                }.items() if v is not None
            },
        },
        "warnings": [],
    }


def _enrich_prediction(p: dict) -> dict:
    """Add realized_net to a prediction record."""
    realized_net = None
    correct = p.get("prediction_correct")
    pm = p.get("p_market")
    direction = p.get("pred_direction")

    if correct is not None and pm is not None and direction is not None:
        realized_net = compute_realized_net(direction, correct, pm, SYSTEM_FEE)
        if realized_net is not None:
            realized_net = round(realized_net, 6)

    return {
        "prediction_id": p.get("prediction_id"),
        "ts_model_ran_ms": p.get("ts_model_ran_ms"),
        "symbol": p.get("symbol"),
        "model": p.get("model"),
        "pred_direction": p.get("pred_direction"),
        "pred_proba": p.get("pred_proba"),
        "p_market": p.get("p_market"),
        "divergence": p.get("divergence"),
        "signed_divergence": p.get("signed_divergence"),
        "warmup": p.get("warmup", False),
        "suppressed_reason": p.get("suppressed_reason"),
        "features": p.get("features", {}),
        "trade_id": p.get("trade_id"),
        "outcome": p.get("outcome"),
        "realized_net": realized_net,
    }
