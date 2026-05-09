from fastapi import APIRouter, Depends, Query
from typing import Optional
from services.auth import verify_credentials
from services.live_state import LiveState

router = APIRouter(tags=["alerts"], dependencies=[Depends(verify_credentials)])

@router.get("/alerts")
async def list_alerts(
    severity: Optional[str] = None,
    type: Optional[str] = None,
    from_ms: int = Query(0),
    to_ms: int = Query(2000000000000),
    resolved: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500)
):
    """Paginated alert log from LiveState."""
    items = LiveState.alert_queue
    
    # Filter
    filtered = []
    for item in items:
        ts = item.get("timestamp_ms", 0)
        if ts < from_ms or ts > to_ms: continue
        if severity and item.get("severity") != severity: continue
        if type and item.get("type") != type: continue
        if resolved is not None and item.get("resolved") != resolved: continue
        
        filtered.append(item)
    
    # Sort descending
    filtered.sort(key=lambda x: x.get("timestamp_ms", 0), reverse=True)
    
    total = len(filtered)
    start = (page - 1) * page_size
    page_items = filtered[start:start+page_size]
    
    return {
        "data": page_items,
        "meta": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "filters_applied": {
                "severity": severity,
                "type": type,
                "from_ms": from_ms,
                "to_ms": to_ms
            }
        }
    }
