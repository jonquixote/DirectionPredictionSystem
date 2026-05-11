"""Regime API — volatility/liquidity/trend tagging and thresholds."""
from __future__ import annotations

import json
from fastapi import APIRouter, HTTPException

try:
    from services.db import get_db
except ModuleNotFoundError:
    from dashboard_api.services.db import get_db

try:
    from regime.tagger import compute_regime
except ModuleNotFoundError:
    from ofi_lab_v3.regime.tagger import compute_regime

router = APIRouter(prefix="/regime", tags=["regime"])


@router.get("/current")
def get_regime_current(symbol: str):
    """Get current regime tags for a symbol (volatility, liquidity, trend).

    Returns:
        {
            "symbol": "BTCUSDT",
            "volatility": "low|medium|high",
            "liquidity": "deep|normal|thin",
            "trend": "trending|choppy|mean_reverting"
        }
    """
    conn = get_db()

    # Get latest features for symbol
    row = conn.execute(
        "SELECT * FROM regime_features_latest WHERE symbol=?",
        (symbol,),
    ).fetchone()

    if not row:
        raise HTTPException(404, f"No regime data for {symbol}")

    # Get thresholds for symbol
    th_row = conn.execute(
        "SELECT thresholds_json FROM regime_thresholds WHERE symbol=?",
        (symbol,),
    ).fetchone()

    thresholds = json.loads(th_row["thresholds_json"]) if th_row else {}

    # Compute regime tags
    feature_dict = dict(row) if row else {}
    # Remove non-numeric columns
    feature_dict.pop("symbol", None)
    feature_dict.pop("ts_updated_ms", None)
    feature_dict.pop("updated_at", None)

    tags = compute_regime(symbol, feature_dict, {symbol: thresholds})

    return {
        "symbol": symbol,
        "volatility": tags.volatility,
        "liquidity": tags.liquidity,
        "trend": tags.trend,
    }


@router.get("/thresholds")
def get_regime_thresholds():
    """Get all per-symbol regime thresholds.

    Returns:
        {
            "thresholds": {
                "BTCUSDT": {
                    "vwap_dev_30s_std": {"p25": 0.1, "p50": 0.2, "p75": 0.3},
                    "mlofi_60s_std": {...},
                    ...
                },
                ...
            }
        }
    """
    conn = get_db()

    rows = conn.execute(
        "SELECT symbol, thresholds_json FROM regime_thresholds"
    ).fetchall()

    thresholds_dict = {}
    for r in rows:
        thresholds_dict[r["symbol"]] = json.loads(r["thresholds_json"])

    return {"thresholds": thresholds_dict}
