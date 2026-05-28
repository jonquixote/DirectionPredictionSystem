"""Governance actions router — audit feed of tier transitions.

GET /api/governance/actions
  Returns the governance_actions audit log with optional filters.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
import asyncio

logger = logging.getLogger("dashboard.governance")

router = APIRouter(prefix="/governance", tags=["governance"])


def _get_db():
    try:
        from services.db import get_db
    except ModuleNotFoundError:
        from dashboard_api.services.db import get_db  # type: ignore[assignment]
    return get_db()


_SINCE_MAP = {
    "1h": 3600,
    "24h": 86400,
    "7d": 7 * 86400,
}


def _parse_reason(raw: str | None) -> object:
    """Parse reason_json defensively — return None on any error."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _fetch_actions(
    since_seconds: int | None,
    since_ms: int | None,
    limit: int,
    action_filter: str | None,
) -> dict:
    conn = _get_db()

    # Determine cutoff timestamp ISO string
    if since_ms is not None:
        cutoff_dt = datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc)
    elif since_seconds is not None:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(seconds=since_seconds)
    else:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(seconds=86400)

    cutoff_iso = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%S")

    # Build query
    params: list = [cutoff_iso]
    where_extra = ""
    if action_filter:
        where_extra = " AND action = ?"
        params.append(action_filter)

    params.append(min(limit, 500))

    try:
        rows = conn.execute(
            f"""
            SELECT id, ts, model_name, action, from_tier, to_tier,
                   triggered_by, reason_json, cell_key
              FROM governance_actions
             WHERE ts >= ?{where_extra}
             ORDER BY ts DESC
             LIMIT ?
            """,
            params,
        ).fetchall()
    except Exception as exc:
        logger.warning("governance_actions query failed: %s", exc)
        return {"actions": [], "count": 0, "summary": {"promote": 0, "demote": 0, "retire": 0}}

    actions = []
    for r in rows:
        actions.append({
            "ts": r["ts"],
            "model_name": r["model_name"],
            "action": r["action"],
            "from_tier": r["from_tier"],
            "to_tier": r["to_tier"],
            "triggered_by": r["triggered_by"],
            "reason": _parse_reason(r["reason_json"]),
            "cell_key": r["cell_key"],
        })

    # Summary counts for last 24h (always, regardless of the since filter)
    summary_cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=86400)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        summary_rows = conn.execute(
            """
            SELECT action, COUNT(*) as n
              FROM governance_actions
             WHERE ts >= ?
             GROUP BY action
            """,
            (summary_cutoff,),
        ).fetchall()
        summary: dict[str, int] = {"promote": 0, "demote": 0, "retire": 0}
        for sr in summary_rows:
            key = sr["action"].lower()
            if key in summary:
                summary[key] = sr["n"]
    except Exception as exc:
        logger.warning("governance summary query failed: %s", exc)
        summary = {"promote": 0, "demote": 0, "retire": 0}

    return {"actions": actions, "count": len(actions), "summary": summary}


@router.get("/actions")
async def get_governance_actions(
    since: str = Query(default="24h", description="Lookback window: 1h, 24h, 7d"),
    since_ms: int | None = Query(default=None, description="Epoch ms cutoff — overrides `since`"),
    limit: int = Query(default=50, ge=1, le=500),
    action: str | None = Query(default=None, description="Filter: promote|demote|retire"),
):
    since_seconds = _SINCE_MAP.get(since, 86400)
    result = await asyncio.to_thread(
        _fetch_actions, since_seconds, since_ms, limit, action
    )
    return result
