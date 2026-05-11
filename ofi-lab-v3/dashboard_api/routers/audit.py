"""Audit trail API — recent registry_audit entries."""
from __future__ import annotations

from fastapi import APIRouter

try:
    from services.db import get_db
except ModuleNotFoundError:
    from dashboard_api.services.db import get_db

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def audit(limit: int = 50, model: str | None = None, action: str | None = None):
    conn = get_db()
    sql = "SELECT * FROM registry_audit WHERE 1=1"
    params: list = []
    if model:
        sql += " AND model_name=?"
        params.append(model)
    if action:
        sql += " AND action=?"
        params.append(action)
    sql += " ORDER BY ts_ms DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return {"entries": [dict(r) for r in rows]}
