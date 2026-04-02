"""
Authentication service — HTTP Basic Auth.

Credentials from env vars DASHBOARD_USER / DASHBOARD_PASS.
If either is unset, auth is skipped (dev mode).
"""
import os
import logging
import secrets
import base64
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

logger = logging.getLogger("dashboard.auth")

security = HTTPBasic(auto_error=False)

_USER = os.environ.get("DASHBOARD_USER")
_PASS = os.environ.get("DASHBOARD_PASS")

if not _USER or not _PASS:
    logger.warning("DASHBOARD_USER / DASHBOARD_PASS not set — auth DISABLED (dev mode)")


def verify_credentials(
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str:
    """Verify HTTP Basic credentials. Returns username on success."""
    if not _USER or not _PASS:
        return "dev"

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    correct_user = secrets.compare_digest(credentials.username.encode(), _USER.encode())
    correct_pass = secrets.compare_digest(credentials.password.encode(), _PASS.encode())

    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username


def verify_ws_token(token: str | None) -> bool:
    """Verify WebSocket token (base64-encoded user:pass)."""
    if not _USER or not _PASS:
        return True
    if not token:
        return False
    try:
        decoded = base64.b64decode(token).decode()
        user, passwd = decoded.split(":", 1)
        return (
            secrets.compare_digest(user.encode(), _USER.encode())
            and secrets.compare_digest(passwd.encode(), _PASS.encode())
        )
    except Exception:
        return False
