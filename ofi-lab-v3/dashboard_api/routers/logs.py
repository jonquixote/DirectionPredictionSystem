from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional
from services.auth import verify_credentials
from services.jsonl_reader import get_store

router = APIRouter(tags=["logs"], dependencies=[Depends(verify_credentials)])

@router.get("/logs/raw")
async def raw_log_lookup(
    prediction_id: str = Query(...)
):
    """
    Fetch raw JSON record chain by prediction_id.
    Returns: { "prediction": {}, "trade": {}, "resolution": {} }
    """
    store = get_store()
    
    # Simple linear scan for MVP. In production, binary search or index.
    pred_doc = next((p for p in store.predictions if p.get("prediction_id") == prediction_id), None)
    trade_doc = next((t for t in store.trades if t.get("prediction_id") == prediction_id), None)
    
    return {
        "prediction": pred_doc,
        "trade": trade_doc,
        "resolution": None # We don't have separate a resolution log yet, it's patched into trades
    }

@router.get("/logs/recent")
async def recent_logs(limit: int = 100):
    """
    Fetch the most recent N logs (predictions and trades combined).
    """
    store = get_store()
    
    # Format predictions
    logs = []
    
    # We will grab up to `limit` from the end of both lists
    recent_preds = store.predictions[-limit:] if len(store.predictions) > limit else store.predictions
    for p in recent_preds:
        logs.append({
            "id": p.get("prediction_id", f"p_{p.get('ts_model_ran_ms')}"),
            "timestamp_ms": p.get("ts_model_ran_ms", 0),
            "level": "INFO",
            "component": "PredictionEngine",
            "message": f"Generated prediction for {p.get('symbol')} with {p.get('model')}",
            "data": p
        })
        
    recent_trades = store.trades[-limit:] if len(store.trades) > limit else store.trades
    for t in recent_trades:
        status = "EXECUTED" if t.get("executed") else "SUPPRESSED"
        logs.append({
            "id": t.get("trade_id", t.get("prediction_id", f"t_{t.get('ts_model_ran_ms')}")),
            "timestamp_ms": t.get("ts_model_ran_ms", 0) + 1, # Offset slightly
            "level": "INFO" if t.get("executed") else "WARN",
            "component": "ExecutionEngine",
            "message": f"Trade eval: {status} for {t.get('symbol')} ({t.get('suppressed_reason', 'Passed')})",
            "data": t
        })
        
    # Sort descending
    logs.sort(key=lambda x: x["timestamp_ms"], reverse=True)
    
    return {"data": logs[:limit]}
