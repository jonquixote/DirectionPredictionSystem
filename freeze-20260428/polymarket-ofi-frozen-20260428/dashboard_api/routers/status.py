"""GET /api/status — system health snapshot."""
from fastapi import APIRouter
from services.live_state import LiveState

router = APIRouter(tags=["status"])


@router.get("/status")
async def get_status():
    """Full system health snapshot from LiveState."""
    return LiveState.snapshot()
