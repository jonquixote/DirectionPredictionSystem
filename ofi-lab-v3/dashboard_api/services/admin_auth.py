"""Confirmation token issuance for high-risk admin actions.

5-minute TTL, single-use, signed with HMAC-SHA256.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from threading import Lock


class ConfirmationError(Exception):
    pass


_SECRET = os.environ.get("V3_ADMIN_SECRET", "").encode() or secrets.token_bytes(32)
_TTL_SECONDS = 300
_USED_TOKENS: set[str] = set()
_LOCK = Lock()


def _sign(payload: bytes) -> str:
    return hmac.new(_SECRET, payload, hashlib.sha256).hexdigest()


def issue_confirmation_token(*, action: str, target: str, by: str) -> str:
    body = {
        "action": action,
        "target": target,
        "by": by,
        "iat": int(time.time()),
        "nonce": secrets.token_hex(8),
    }
    payload = json.dumps(body, sort_keys=True).encode()
    sig = _sign(payload)
    return f"{payload.hex()}.{sig}"


def verify_confirmation_token(token: str, *, action: str, target: str) -> dict:
    try:
        payload_hex, sig = token.split(".")
        payload = bytes.fromhex(payload_hex)
    except ValueError:
        raise ConfirmationError("malformed token")
    if not hmac.compare_digest(sig, _sign(payload)):
        raise ConfirmationError("bad signature")
    body = json.loads(payload)
    if body["action"] != action or body["target"] != target:
        raise ConfirmationError("action/target mismatch")
    if time.time() - body["iat"] > _TTL_SECONDS:
        raise ConfirmationError("expired")
    with _LOCK:
        if token in _USED_TOKENS:
            raise ConfirmationError("token already used")
        _USED_TOKENS.add(token)
    return body


def validate_admin_secret(*, env: str) -> None:
    """Validate that V3_ADMIN_SECRET is set in production mode.
    
    In prod, raise ValueError if not set.
    In dev, log a warning but allow random fallback.
    
    Args:
        env: Environment name ('prod' or other)
        
    Raises:
        ValueError: If env='prod' and V3_ADMIN_SECRET is not set
    """
    secret_is_set = os.environ.get("V3_ADMIN_SECRET") is not None
    
    if env == "prod" and not secret_is_set:
        raise ValueError(
            "V3_ADMIN_SECRET environment variable is not set but V3_ENV=prod. "
            "Use scripts/generate_admin_secret.sh to create one, then set V3_ADMIN_SECRET in /etc/v3/env"
        )
    elif not secret_is_set:
        import logging
        logging.getLogger("admin_auth").warning(
            "V3_ADMIN_SECRET not set — using random tokens (will invalidate on restart)"
        )
