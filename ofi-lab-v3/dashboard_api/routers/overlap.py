"""Overlap API — recent model-overlap consensus rows."""
from __future__ import annotations

from fastapi import APIRouter

try:
    from services.db import get_db
except ModuleNotFoundError:
    from dashboard_api.services.db import get_db

router = APIRouter(prefix="/overlap", tags=["overlap"])


@router.get("")
def get_overlap(limit: int = 50, model: str | None = None):
    conn = get_db()
    if model:
        rows = conn.execute(
            "SELECT * FROM model_overlap "
            "ORDER BY ts_contract_open_ms DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM model_overlap "
            "ORDER BY ts_contract_open_ms DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {"rows": [dict(r) for r in rows]}
