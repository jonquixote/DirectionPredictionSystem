"""GET /api/trades, GET /api/resolutions — paper trade records."""
from __future__ import annotations
from fastapi import APIRouter, Query
from services.jsonl_reader import get_store
from services.metrics import (
    compute_realized_net, realized_net_breakdown, SYSTEM_FEE,
)

router = APIRouter(tags=["trades"])


@router.get("/trades")
async def list_trades(
    model: str | None = None,
    symbol: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
    direction: str | None = None,
    suppressed: bool | None = None,
    outcome: str | None = None,
    contract_duration: int | None = None,
    net_sign: str | None = None,
    settled: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str = "ts_model_ran_ms",
    order: str = "desc",
):
    """Paginated, filtered paper trade records with realized NE_t."""
    store = get_store()

    items, total = store.get_trades(
        model=model, symbol=symbol, from_ms=from_ms, to_ms=to_ms,
        direction=direction, suppressed=suppressed, outcome=outcome,
        contract_duration=contract_duration, settled=settled,
        page=page, page_size=page_size, sort=sort, order=order,
    )

    enriched = [_enrich_trade(t) for t in items]

    # Post-filter by net_sign
    if net_sign == "positive":
        enriched = [t for t in enriched if t.get("realized_net") is not None and t["realized_net"] > 0]
    elif net_sign == "negative":
        enriched = [t for t in enriched if t.get("realized_net") is not None and t["realized_net"] < 0]

    # Open trades
    open_trades = store.get_open_trades()

    return {
        "data": enriched,
        "open_trades": open_trades,
        "meta": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "from_ms": from_ms,
            "to_ms": to_ms,
            "filters_applied": {
                k: v for k, v in {
                    "model": model, "symbol": symbol, "direction": direction,
                    "contract_duration": contract_duration, "settled": settled,
                    "suppressed": suppressed, "outcome": outcome, "net_sign": net_sign,
                }.items() if v is not None
            },
        },
        "warnings": [],
    }


@router.get("/resolutions")
async def list_resolutions(
    model: str | None = None,
    symbol: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str = "ts_model_ran_ms",
    order: str = "desc",
):
    """Paginated resolution records (resolved trades only)."""
    store = get_store()

    items, total = store.get_trades(
        model=model, symbol=symbol, from_ms=from_ms, to_ms=to_ms,
        settled=True, page=page, page_size=page_size,
        sort=sort, order=order,
    )

    resolutions = []
    for t in items:
        res = {
            "trade_id": t.get("trade_id"),
            "resolved_at_ms": t.get("ts_contract_close_ms"),
            "settlement_price": t.get("price_at_contract_close"),
            "correct": t.get("prediction_correct"),
            "model": t.get("model"),
            "symbol": t.get("symbol"),
            "direction": t.get("pred_direction"),
            "p_market": t.get("p_market"),
        }
        resolutions.append(res)

    return {
        "data": resolutions,
        "meta": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "from_ms": from_ms,
            "to_ms": to_ms,
            "filters_applied": {
                k: v for k, v in {"model": model, "symbol": symbol}.items()
                if v is not None
            },
        },
        "warnings": [],
    }


def _enrich_trade(t: dict) -> dict:
    """Add realized NE_t and breakdown to a trade record."""
    realized_net = None
    breakdown = None
    correct = t.get("prediction_correct")
    pm = t.get("p_market")
    direction = t.get("pred_direction")

    if correct is not None and pm is not None and direction is not None:
        realized_net = compute_realized_net(direction, correct, pm, SYSTEM_FEE)
        if realized_net is not None:
            realized_net = round(realized_net, 6)
        breakdown = realized_net_breakdown(direction, correct, pm, SYSTEM_FEE)

    return {
        "id": t.get("trade_id"),
        "prediction_id": t.get("prediction_id"),
        "timestamp_ms": t.get("ts_model_ran_ms"),
        "symbol": t.get("symbol"),
        "model": t.get("model"),
        "contract_duration": t.get("contract_duration_seconds") or t.get("contract_duration"),
        "direction": t.get("pred_direction"),
        "simulated_stake_usdc": t.get("simulated_stake_usdc", 10.0),
        "p_market": pm,
        "price_at_open": t.get("price_at_contract_open"),
        "price_at_close": t.get("price_at_contract_close"),
        "outcome": t.get("outcome"),
        "correct": correct,
        "realized_net": realized_net,
        "realized_net_breakdown": breakdown,
        "suppressed_reason": t.get("suppressed_reason"),
        "resolved": t.get("resolved", False),
        "resolved_at_ms": t.get("ts_contract_close_ms"),
        "pred_proba": t.get("pred_proba"),
    }
