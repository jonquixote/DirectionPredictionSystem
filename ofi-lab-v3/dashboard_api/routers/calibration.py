"""Calibration API — model calibration bins and summary metrics."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

try:
    from services.db import get_db
except ModuleNotFoundError:
    from dashboard_api.services.db import get_db

router = APIRouter(prefix="/calibration", tags=["calibration"])


@router.get("/{model_name}")
def get_calibration(model_name: str):
    """Get calibration curve bins and summary metrics for a model.

    Returns:
        {
            "model_name": "h300_btc",
            "bins": [
                {"bin_lo": 0.0, "bin_hi": 0.1, "observed_freq": 0.05, "n": 42},
                ...
            ],
            "brier": 0.18,
            "log_loss": 0.42,
            "n_obs": 1234
        }
    """
    conn = get_db()

    # Get calibration bins ordered by bin_lo
    bins = conn.execute(
        "SELECT bin_lo, bin_hi, observed_freq, n "
        "FROM calibration_bins WHERE model_name=? ORDER BY bin_lo",
        (model_name,),
    ).fetchall()

    if not bins:
        raise HTTPException(404, detail=f"No calibration data for {model_name}")

    # Get summary metrics
    summary = conn.execute(
        "SELECT brier, log_loss, n_obs FROM calibration_summary WHERE model_name=?",
        (model_name,),
    ).fetchone()

    return {
        "model_name": model_name,
        "bins": [dict(b) for b in bins],
        "brier": summary["brier"] if summary else None,
        "log_loss": summary["log_loss"] if summary else None,
        "n_obs": summary["n_obs"] if summary else 0,
    }
