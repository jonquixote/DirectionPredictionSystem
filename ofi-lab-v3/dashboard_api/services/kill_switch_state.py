"""Persistent kill switch state.

Engaged state persists to ``/data/kill_switch.json`` and survives
restarts.  After restart, the state remains "killed" until an operator
hits ``confirm_resume`` with a fresh confirmation token (Steering 10l).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import Lock

STATE_PATH = Path(os.environ.get("V3_KILL_SWITCH_PATH", "/data/kill_switch.json"))
_LOCK = Lock()


def is_engaged() -> bool:
    return STATE_PATH.exists()


def read_state() -> dict | None:
    if not STATE_PATH.exists():
        return None
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return None


def engage(*, reason: str, by: str) -> None:
    with _LOCK:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({
            "engaged_at_ms": int(time.time() * 1000),
            "reason": reason,
            "by": by,
        }))


def disengage(*, by: str) -> None:
    with _LOCK:
        if STATE_PATH.exists():
            archive = STATE_PATH.with_suffix(
                f".resumed.{int(time.time())}.json"
            )
            STATE_PATH.rename(archive)
