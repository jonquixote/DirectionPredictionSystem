"""Admin API — confirmation token issuance."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from dashboard_api.services.admin_auth import issue_confirmation_token

router = APIRouter(prefix="/admin", tags=["admin"])


class IntentRequest(BaseModel):
    action: str
    target: str
    by: str


@router.post("/confirm_intent")
def confirm_intent(req: IntentRequest):
    return {
        "token": issue_confirmation_token(
            action=req.action, target=req.target, by=req.by,
        )
    }
