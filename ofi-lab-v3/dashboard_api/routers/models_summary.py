"""Models summary router — fleet-level aggregates for HealthHeader.

GET /api/models_registry/summary
  Returns active model counts, fleet breakdown, and per-window tier counts.

Kept separate from models_registry.py (which is a filesystem stub) to
avoid mixing filesystem and DB concerns.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter

logger = logging.getLogger("dashboard.models_summary")

router = APIRouter(prefix="/models_registry", tags=["models-summary"])


def _get_db():
    try:
        from services.db import get_db  # type: ignore
    except ModuleNotFoundError:
        from dashboard_api.services.db import get_db  # type: ignore
    return get_db()


def _fetch_summary() -> dict:
    conn = _get_db()

    # Active (paper_active=1, not baseline)
    try:
        row = conn.execute(
            "SELECT COUNT(*) as n FROM model_registry "
            "WHERE paper_active=1 AND COALESCE(is_baseline,0)=0"
        ).fetchone()
        active_models = int(row["n"]) if row else 0
    except Exception as exc:
        logger.warning("active_models count failed: %s", exc)
        active_models = 0

    # Total models
    try:
        row = conn.execute("SELECT COUNT(*) as n FROM model_registry").fetchone()
        total_models = int(row["n"]) if row else 0
    except Exception:
        total_models = 0

    # Distinct fleets + per-fleet model count + latest tier assigned at
    fleets: list[dict] = []
    distinct_fleets = 0
    try:
        rows = conn.execute(
            """
            SELECT fleet_version,
                   COUNT(*) as model_count,
                   MAX(
                       COALESCE(
                           (SELECT MAX(mwt.tier_assigned_at)
                              FROM model_window_tier mwt
                             WHERE mwt.model_name = mr.name),
                           mr.created_at
                       )
                   ) as latest_tier_assigned_at
              FROM model_registry mr
             WHERE COALESCE(is_baseline,0)=0
             GROUP BY fleet_version
             ORDER BY latest_tier_assigned_at DESC NULLS LAST
            """
        ).fetchall()
        distinct_fleets = len(rows)
        for r in rows:
            fleets.append({
                "fleet_version": r["fleet_version"],
                "model_count": r["model_count"],
                "latest_tier_assigned_at": r["latest_tier_assigned_at"],
            })
    except Exception as exc:
        logger.warning("fleet query failed: %s", exc)

    # Last retrain timestamp: MAX(train_window_end) across non-baseline models
    try:
        row = conn.execute(
            "SELECT MAX(train_window_end) as last_retrain_ts "
            "FROM model_registry WHERE COALESCE(is_baseline,0)=0"
        ).fetchone()
        last_retrain_ts = row["last_retrain_ts"] if row else None
    except Exception:
        last_retrain_ts = None

    # Tier counts by window from model_window_tier
    tier_counts_by_window: dict[str, dict[str, int]] = {}
    try:
        rows = conn.execute(
            """
            SELECT market_window_seconds, tier, COUNT(*) as n
              FROM model_window_tier
             GROUP BY market_window_seconds, tier
            """
        ).fetchall()
        for r in rows:
            win_key = str(r["market_window_seconds"])
            if win_key not in tier_counts_by_window:
                tier_counts_by_window[win_key] = {
                    "gold": 0, "silver": 0, "watch": 0, "retired": 0
                }
            tier = (r["tier"] or "watch").lower()
            if tier in tier_counts_by_window[win_key]:
                tier_counts_by_window[win_key][tier] = r["n"]
            else:
                tier_counts_by_window[win_key][tier] = r["n"]
    except Exception as exc:
        logger.warning("tier_counts_by_window query failed: %s", exc)

    # Ensure standard windows always present
    for win in ("300", "900", "1800"):
        if win not in tier_counts_by_window:
            tier_counts_by_window[win] = {"gold": 0, "silver": 0, "watch": 0, "retired": 0}

    return {
        "active_models": active_models,
        "total_models": total_models,
        "distinct_fleets": distinct_fleets,
        "fleets": fleets,
        "last_retrain_ts": last_retrain_ts,
        "tier_counts_by_window": tier_counts_by_window,
    }


@router.get("/summary")
async def get_models_registry_summary():
    """Fleet-level aggregate: active counts, fleets, and per-window tier distribution."""
    return await asyncio.to_thread(_fetch_summary)
