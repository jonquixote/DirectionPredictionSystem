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
