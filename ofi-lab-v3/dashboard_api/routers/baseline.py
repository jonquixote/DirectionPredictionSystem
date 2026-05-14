"""Dashboard cutover baseline — read/write to /data/baseline.json.

Replicates the endpoints from ofi-lab-v3/trading/api_server.py so the
v3 dashboard on port 8081 can serve them directly, without nginx
needing to split-route /api/baseline to port 8080.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter

logger = logging.getLogger("dashboard.baseline")

router = APIRouter(tags=["baseline"])

BASELINE_PATH = Path(os.environ.get("BASELINE_PATH", "/data/baseline.json"))
INITIAL_KALSHI_CENTS = 100_00  # $100 default


def _load_baseline() -> dict:
    default = {
        "cutover_ts_ms": 0,
        "baseline_kalshi_cents": INITIAL_KALSHI_CENTS,
        "baseline_paper_trade_count": 0,
        "set_at_iso": None,
    }
    if not BASELINE_PATH.exists():
        return default
    try:
        data = json.loads(BASELINE_PATH.read_text())
        return {**default, **data}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("baseline read failed: %s — using defaults", e)
        return default


@router.get("/baseline")
def get_baseline():
    """Return current dashboard cutover baseline."""
    return _load_baseline()


@router.post("/baseline")
def set_baseline(body: dict):
    """Snapshot current state as new baseline.

    Body: {"cutover_ts_ms": int} — use 0 to clear (show all history).
    Preserves last-known balances from the previous baseline to avoid
    needing the paper_trader's live Kalshi connection.
    """
    cutover = int(body.get("cutover_ts_ms", 0))
    current = _load_baseline()
    baseline = {
        "cutover_ts_ms": cutover,
        "baseline_kalshi_cents": current.get(
            "baseline_kalshi_cents", INITIAL_KALSHI_CENTS
        ),
        "baseline_paper_trade_count": current.get("baseline_paper_trade_count", 0),
        "set_at_iso": __import__("datetime").datetime.now().isoformat(),
    }
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2))
    logger.warning("DASHBOARD BASELINE RESET: %s", baseline)
    return baseline
