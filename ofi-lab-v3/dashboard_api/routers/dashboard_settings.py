"""Server-persisted dashboard preferences.

Provides:
  GET    /api/settings          — all settings as {settings: {key: value, ...}}
  GET    /api/settings/{key}    — single setting or 404
  PUT    /api/settings/{key}    — upsert (JSON-serialize value)
  DELETE /api/settings/{key}    — remove or 404
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from services.db import get_db
except ModuleNotFoundError:
    from dashboard_api.services.db import get_db

router = APIRouter(prefix="/settings", tags=["dashboard-settings"])


def _get_conn():
    return get_db()


def _parse_value(raw: str) -> Any:
    """Parse JSON-encoded value; fall back to raw string on error."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


# ── Request / Response models ────────────────────────────────────

class PutSettingRequest(BaseModel):
    value: Any
    by: Optional[str] = None


# ── Endpoints ────────────────────────────────────────────────────

@router.get("")
def get_all_settings():
    conn = _get_conn()
    rows = conn.execute(
        "SELECT key, value, updated_at, updated_by FROM dashboard_settings"
    ).fetchall()
    settings = {row["key"]: _parse_value(row["value"]) for row in rows}
    return {"settings": settings}


@router.get("/{key}")
def get_setting(key: str):
    conn = _get_conn()
    row = conn.execute(
        "SELECT key, value, updated_at, updated_by FROM dashboard_settings WHERE key = ?",
        (key,),
    ).fetchone()
    if not row:
        raise HTTPException(404, f"setting {key!r} not found")
    return {
        "key": row["key"],
        "value": _parse_value(row["value"]),
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
    }


@router.put("/{key}")
def put_setting(key: str, req: PutSettingRequest):
    conn = _get_conn()
    value_json = json.dumps(req.value)
    conn.execute(
        """INSERT INTO dashboard_settings (key, value, updated_at, updated_by)
           VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?)
           ON CONFLICT(key) DO UPDATE SET
               value      = excluded.value,
               updated_at = excluded.updated_at,
               updated_by = excluded.updated_by""",
        (key, value_json, req.by),
    )
    conn.commit()
    row = conn.execute(
        "SELECT key, value, updated_at, updated_by FROM dashboard_settings WHERE key = ?",
        (key,),
    ).fetchone()
    return {
        "key": row["key"],
        "value": _parse_value(row["value"]),
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
    }


@router.delete("/{key}")
def delete_setting(key: str):
    conn = _get_conn()
    row = conn.execute(
        "SELECT key FROM dashboard_settings WHERE key = ?", (key,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"setting {key!r} not found")
    conn.execute("DELETE FROM dashboard_settings WHERE key = ?", (key,))
    conn.commit()
    return {"deleted": True}
