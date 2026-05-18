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
  POST /api/models/{name}/filter         (confirmation token if live_eligible=1)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

logger = logging.getLogger("dashboard.models_admin")

try:
    from services.db import get_db
except ModuleNotFoundError:
    from dashboard_api.services.db import get_db

router = APIRouter(prefix="/models", tags=["models-admin"])


# ── Shared helpers ──────────────────────────────────────────────

def _get_conn():
    return get_db()


def _audit(conn, name, action, by, reason, before, after):
    detail = f"before={before}; after={after}"
    if reason:
        detail += f"; reason={reason}"
    conn.execute(
        "INSERT INTO model_audit "
        "(model_name, action, by_user, detail) "
        "VALUES (?, ?, ?, ?)",
        (name, action, by, detail),
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
                                        ORDER BY ts DESC) rn
                FROM decay_metrics
          ) dm ON dm.model_name = mr.name AND dm.rn = 1
         ORDER BY mr.is_baseline DESC, mr.name
    """).fetchall()
    return {"models": [dict(r) for r in rows]}


# ── Model selection endpoints (MUST be before /{name} catch-all) ──

@router.get("/model-selection")
def get_model_selection():
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM model_selection ORDER BY symbol, market_window_seconds"
        ).fetchall()
        return {"selections": [dict(r) for r in rows]}
    except Exception:
        return {"selections": []}


@router.put("/model-selection/{symbol}/{market_window_seconds}")
def set_model_selection(symbol: str, market_window_seconds: int, body: dict):
    import json as _json
    conn = _get_conn()
    strategy = body.get("strategy", "all")
    selected_model_name = body.get("selected_model_name")
    committee_config_json = _json.dumps(body.get("committee_config", {}))
    conn.execute(
        "INSERT OR REPLACE INTO model_selection "
        "(symbol, market_window_seconds, strategy, selected_model_name, "
        "committee_config_json, updated_by) VALUES (?,?,?,?,?,?)",
        (symbol, market_window_seconds, strategy, selected_model_name,
         committee_config_json, "dashboard"),
    )
    conn.commit()
    return {"ok": True}


# ── Model detail (catch-all — must be AFTER specific routes) ──

@router.get("/{name}")
def get_model(name: str):
    conn = _get_conn()
    row = conn.execute("""
        SELECT mr.*,
               dm.recency_weighted_ev as ewma_ev,
               dm.brier_score as ewma_brier,
               dm.calibration_error as psi
          FROM model_registry mr
          LEFT JOIN (
              SELECT model_name, recency_weighted_ev, brier_score, calibration_error,
                     ROW_NUMBER() OVER (PARTITION BY model_name ORDER BY ts DESC) as rn
                FROM decay_metrics
          ) dm ON dm.model_name = mr.name AND dm.rn = 1
         WHERE mr.name = ?
    """, (name,)).fetchone()
    if not row:
        raise HTTPException(404, f"model {name} not found")
    row_dict = dict(row)
    symbol = row_dict.get("symbol")
    overlap = conn.execute(
        "SELECT * FROM model_overlap WHERE symbol = ? "
        "ORDER BY ts_contract_open_ms DESC LIMIT 50",
        (symbol,),
    ).fetchall()
    cal_rows = conn.execute(
        "SELECT bin_lo, bin_hi, observed_freq, n FROM calibration_bins "
        "WHERE model_name = ? ORDER BY bin_lo",
        (name,),
    ).fetchall()
    audit = conn.execute(
        "SELECT * FROM model_audit WHERE model_name = ? "
        "ORDER BY ts DESC LIMIT 25",
        (name,),
    ).fetchall()
    return {
        **row_dict,
        "overlap": [dict(o) for o in overlap],
        "calibration_summary": [dict(r) for r in cal_rows],
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


# ── Filter config (W2) ─────────────────────────────────────────

_TRADER_RELOAD_URL = "http://127.0.0.1:8080/reload_meta"


class FilterRequest(BaseModel):
    by: str
    confidence_threshold: Optional[float] = None
    ev_threshold: Optional[float] = None
    blackout_hours: Optional[List[int]] = None
    warmup_seconds: Optional[int] = None
    clear_keys: Optional[List[str]] = None
    confirmation_token: Optional[str] = None

    @field_validator("confidence_threshold")
    @classmethod
    def _validate_confidence(cls, v):
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence_threshold {v!r} must be in [0.0, 1.0]")
        return v

    @field_validator("ev_threshold")
    @classmethod
    def _validate_ev(cls, v):
        if v is not None and not (-1.0 <= v <= 1.0):
            raise ValueError(f"ev_threshold {v!r} must be in [-1.0, 1.0]")
        return v

    @field_validator("blackout_hours")
    @classmethod
    def _validate_blackout(cls, v):
        if v is not None:
            for h in v:
                if not (0 <= h <= 23):
                    raise ValueError(f"blackout_hours value {h} out of range [0, 23]")
            v = sorted(set(v))
        return v

    @field_validator("warmup_seconds")
    @classmethod
    def _validate_warmup(cls, v):
        if v is not None and v < 0:
            raise ValueError(f"warmup_seconds {v!r} must be non-negative")
        return v


class FilterResponse(BaseModel):
    name: str
    filter_config: dict
    applied_at: str
    trader_reloaded: bool


def _merge_filter(current_json: str, req: FilterRequest) -> dict:
    """Merge supplied fields into current filter config, clear requested keys."""
    try:
        current = json.loads(current_json or "{}")
    except json.JSONDecodeError:
        current = {}
    merged = dict(current)
    if req.confidence_threshold is not None:
        merged["confidence_threshold"] = req.confidence_threshold
    if req.ev_threshold is not None:
        merged["ev_threshold"] = req.ev_threshold
    if req.blackout_hours is not None:
        merged["blackout_hours"] = req.blackout_hours
    if req.warmup_seconds is not None:
        merged["warmup_seconds"] = req.warmup_seconds
    for k in (req.clear_keys or []):
        merged.pop(k, None)
    return merged


def _canonical_json(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def _trigger_reload_meta() -> bool:
    """POST to trader reload endpoint. Returns True if successful."""
    try:
        resp = httpx.post(_TRADER_RELOAD_URL, timeout=3.0)
        return resp.status_code < 300
    except Exception as exc:
        logger.warning("Could not reach %s: %s — auto-refresh will pick it up", _TRADER_RELOAD_URL, exc)
        return False


@router.post("/{name}/filter", response_model=FilterResponse)
def set_filter(name: str, req: FilterRequest):
    from dashboard_api.services.admin_auth import (
        verify_confirmation_token, ConfirmationError,
    )

    conn = _get_conn()
    row = conn.execute(
        "SELECT name, live_eligible, filter_config_json FROM model_registry WHERE name=?",
        (name,),
    ).fetchone()
    if not row:
        raise HTTPException(404, f"model {name!r} not found")

    # Live models require a confirmation token
    if row["live_eligible"]:
        if not req.confirmation_token:
            raise HTTPException(403, "confirmation_token required for live-eligible models")
        try:
            verify_confirmation_token(
                req.confirmation_token, action="set_filter", target=name
            )
        except ConfirmationError as e:
            raise HTTPException(403, f"confirmation failed: {e}")

    before_json = row["filter_config_json"] or "{}"
    after_dict = _merge_filter(before_json, req)
    after_json = _canonical_json(after_dict)

    conn.execute(
        "UPDATE model_registry SET filter_config_json = ?, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
        "WHERE name = ?",
        (after_json, name),
    )
    _audit(conn, name, "set_filter", req.by, None, before_json, after_json)
    conn.commit()

    reloaded = _trigger_reload_meta()
    applied_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return FilterResponse(
        name=name,
        filter_config=after_dict,
        applied_at=applied_at,
        trader_reloaded=reloaded,
    )
