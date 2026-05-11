"""Model admin API — read + mutation endpoints.

Provides:
  GET  /api/models/list        — all models with latest decay metrics
  GET  /api/models/{name}      — detail with overlap, calibration, audit
  POST /api/models/{name}/enable_paper   (no confirmation needed)
  POST /api/models/{name}/disable_paper  (no confirmation needed)
  POST /api/models/{name}/enable_live    (confirmation token required)
  POST /api/models/{name}/disable_live   (no confirmation needed — always safe)
  POST /api/models/{name}/reload         (confirmation token required)
  POST /api/models/{name}/rollback       (confirmation token required)
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

try:
    from services.db import get_db
except ModuleNotFoundError:
    from dashboard_api.services.db import get_db

router = APIRouter(prefix="/models", tags=["models-admin"])


# ── Shared helpers ──────────────────────────────────────────────

def _get_conn():
    return get_db()


def _audit(conn, name, action, by, reason, before, after):
    conn.execute(
        "INSERT INTO registry_audit "
        "(ts_ms, model_name, action, actor, reason, before_state, after_state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(time.time() * 1000), name, action, by, reason or "", str(before), str(after)),
    )
    conn.commit()


# ── Request models ──────────────────────────────────────────────

class ToggleRequest(BaseModel):
    by: str
    reason: str | None = None


class LiveToggleRequest(ToggleRequest):
    confirmation_token: str | None = None


class RollbackRequest(LiveToggleRequest):
    target_generation: int | None = None


# ── Read endpoints ──────────────────────────────────────────────

@router.get("/list")
def list_models():
    conn = _get_conn()
    rows = conn.execute("""
        SELECT mr.name, mr.is_baseline, mr.lifecycle_state,
               mr.paper_active, mr.live_eligible,
               mr.symbol, mr.training_horizon_seconds as horizon,
               mr.generation, mr.created_at,
               dm.recency_weighted_ev as ewma_ev,
               dm.brier_score as ewma_brier,
               dm.calibration_error as psi
          FROM model_registry mr
          LEFT JOIN (
              SELECT model_name,
                     recency_weighted_ev, brier_score, calibration_error,
                     ROW_NUMBER() OVER (PARTITION BY model_name
                                        ORDER BY ts_ms DESC) rn
                FROM decay_metrics
          ) dm ON dm.model_name = mr.name AND dm.rn = 1
         ORDER BY mr.is_baseline DESC, mr.name
    """).fetchall()
    return {"models": [dict(r) for r in rows]}


@router.get("/{name}")
def get_model(name: str):
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM model_registry WHERE name = ?", (name,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"model {name} not found")
    overlap = conn.execute(
        "SELECT * FROM model_overlap "
        "ORDER BY ts_contract_open_ms DESC LIMIT 50"
    ).fetchall()
    audit = conn.execute(
        "SELECT * FROM registry_audit WHERE model_name = ? "
        "ORDER BY ts_ms DESC LIMIT 25",
        (name,),
    ).fetchall()
    return {
        **dict(row),
        "overlap": [dict(o) for o in overlap],
        "calibration_summary": [],
        "recent_audit": [dict(a) for a in audit],
    }


# ── Paper toggles (low-risk, no confirmation) ──────────────────

@router.post("/{name}/enable_paper")
def enable_paper(name: str, req: ToggleRequest):
    conn = _get_conn()
    row = conn.execute(
        "SELECT paper_active FROM model_registry WHERE name=?", (name,)
    ).fetchone()
    if not row:
        raise HTTPException(404)
    conn.execute(
        "UPDATE model_registry SET paper_active=1 WHERE name=?", (name,)
    )
    _audit(conn, name, "enable_paper", req.by, req.reason, row["paper_active"], 1)
    return {"name": name, "paper_active": True}


@router.post("/{name}/disable_paper")
def disable_paper(name: str, req: ToggleRequest):
    conn = _get_conn()
    row = conn.execute(
        "SELECT paper_active FROM model_registry WHERE name=?", (name,)
    ).fetchone()
    if not row:
        raise HTTPException(404)
    conn.execute(
        "UPDATE model_registry SET paper_active=0 WHERE name=?", (name,)
    )
    _audit(conn, name, "disable_paper", req.by, req.reason, row["paper_active"], 0)
    return {"name": name, "paper_active": False}


# ── Live toggles (high-risk, require confirmation) ─────────────

@router.post("/{name}/enable_live")
def enable_live(name: str, req: LiveToggleRequest):
    from dashboard_api.services.admin_auth import (
        verify_confirmation_token, ConfirmationError,
    )
    if not req.confirmation_token:
        raise HTTPException(400, "confirmation_token required for live toggles")
    try:
        verify_confirmation_token(
            req.confirmation_token, action="enable_live", target=name
        )
    except ConfirmationError as e:
        raise HTTPException(400, f"confirmation failed: {e}")
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM model_registry WHERE name=?", (name,)
    ).fetchone()
    if not row:
        raise HTTPException(404)
    conn.execute(
        "UPDATE model_registry SET live_eligible=1 WHERE name=?", (name,)
    )
    _audit(conn, name, "enable_live", req.by, req.reason, row["live_eligible"], 1)
    return {"name": name, "live_eligible": True}


@router.post("/{name}/disable_live")
def disable_live(name: str, req: LiveToggleRequest):
    conn = _get_conn()
    row = conn.execute(
        "SELECT live_eligible FROM model_registry WHERE name=?", (name,)
    ).fetchone()
    if not row:
        raise HTTPException(404)
    conn.execute(
        "UPDATE model_registry SET live_eligible=0 WHERE name=?", (name,)
    )
    _audit(conn, name, "disable_live", req.by, req.reason, row["live_eligible"], 0)
    return {"name": name, "live_eligible": False}


# ── Reload (bumps generation) ──────────────────────────────────

@router.post("/{name}/reload")
def reload_model(name: str, req: LiveToggleRequest):
    from dashboard_api.services.admin_auth import (
        verify_confirmation_token, ConfirmationError,
    )
    if not req.confirmation_token:
        raise HTTPException(400, "confirmation_token required for reload")
    try:
        verify_confirmation_token(
            req.confirmation_token, action="reload", target=name
        )
    except ConfirmationError as e:
        raise HTTPException(400, str(e))
    conn = _get_conn()
    row = conn.execute(
        "SELECT generation FROM model_registry WHERE name=?", (name,)
    ).fetchone()
    if not row:
        raise HTTPException(404)
    new_gen = row["generation"] + 1
    conn.execute(
        "UPDATE model_registry SET generation=? WHERE name=?", (new_gen, name)
    )
    _audit(conn, name, "reload", req.by, req.reason, row["generation"], new_gen)
    conn.commit()
    return {"name": name, "generation": new_gen}


# ── Rollback (Steering 10k) ────────────────────────────────────

@router.post("/{name}/rollback")
def rollback(name: str, req: RollbackRequest):
    from dashboard_api.services.admin_auth import (
        verify_confirmation_token, ConfirmationError,
    )
    if not req.confirmation_token:
        raise HTTPException(400, "confirmation_token required for rollback")
    try:
        verify_confirmation_token(
            req.confirmation_token, action="rollback", target=name
        )
    except ConfirmationError as e:
        raise HTTPException(400, str(e))
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM model_registry WHERE name=?", (name,)
    ).fetchone()
    if not row:
        raise HTTPException(404)
    if row["is_baseline"]:
        raise HTTPException(412, "baseline models cannot be rolled back")
    target_gen = req.target_generation
    if target_gen is None or target_gen < 0:
        target_gen = max(row["generation"] - 1, 0)
    conn.execute(
        "UPDATE model_registry SET generation=? WHERE name=?",
        (target_gen, name),
    )
    _audit(
        conn, name, "rollback", req.by,
        req.reason or f"rollback to gen {target_gen}",
        row["generation"], target_gen,
    )
    return {"name": name, "generation": target_gen}
