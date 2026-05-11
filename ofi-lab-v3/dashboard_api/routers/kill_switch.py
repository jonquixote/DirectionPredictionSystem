"""Kill switch API — engage, status, confirm_resume."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dashboard_api.services import kill_switch_state as ks
from dashboard_api.services.admin_auth import (
    verify_confirmation_token,
    ConfirmationError,
)

router = APIRouter(prefix="/kill_switch", tags=["kill-switch"])


class EngageReq(BaseModel):
    reason: str
    by: str


class ResumeReq(BaseModel):
    by: str
    confirmation_token: str | None = None


@router.get("")
def status():
    return {"engaged": ks.is_engaged(), "state": ks.read_state()}


@router.post("")
def engage(req: EngageReq):
    ks.engage(reason=req.reason, by=req.by)
    return {"engaged": True, "reason": req.reason}


@router.post("/confirm_resume")
def confirm_resume(req: ResumeReq):
    if not req.confirmation_token:
        raise HTTPException(400, "confirmation_token required")
    try:
        verify_confirmation_token(
            req.confirmation_token,
            action="kill_switch_resume",
            target="global",
        )
    except ConfirmationError as e:
        raise HTTPException(400, str(e))
    ks.disengage(by=req.by)
    return {"engaged": False}
