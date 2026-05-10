"""Quartile-based regime tagger.

Three independent regime axes computed from existing v3 features:
  - volatility: low / medium / high   (vwap_dev_30s_std + mlofi_60s_std)
  - liquidity:  thin / normal / deep  (relative_spread + spread_5m_pct)
  - trend:      trending / choppy / mean_reverting (mlofi_momentum +
                vwap_2m_deviation sign persistence)

Thresholds come from regime_thresholds.json (auto-refreshed by
threshold_updater.py from the rolling 30-day distribution).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class RegimeTags:
    volatility: str
    liquidity: str
    trend: str


def _bucket(value: float, thresholds: dict) -> str:
    """Map a value to low/medium/high based on p25/p50/p75."""
    if value < thresholds["p25"]:
        return "low"
    if value < thresholds["p75"]:
        return "medium"
    return "high"


def _avg_bucket(b1: str, b2: str) -> str:
    """Combine two single-axis buckets into a regime label."""
    score = {"low": 0, "medium": 1, "high": 2}.get(b1, 1) + \
            {"low": 0, "medium": 1, "high": 2}.get(b2, 1)
    if score <= 1:
        return "low"
    if score <= 2:
        return "medium"
    return "high"


def _liquidity_bucket(rel_spread: float, spread_5m: float, t: dict) -> str:
    a = _bucket(rel_spread, t["relative_spread"])
    b = _bucket(spread_5m, t["spread_5m_pct"])
    combined = _avg_bucket(a, b)
    return {"low": "deep", "medium": "normal", "high": "thin"}[combined]


def _trend_bucket(momentum: float, vwap_dev: float, t: dict) -> str:
    m_bucket = _bucket(momentum, t["mlofi_momentum"])
    v_bucket = _bucket(vwap_dev, t["vwap_2m_deviation"])
    if m_bucket == "high" and v_bucket == "high":
        return "trending"
    if m_bucket == "low" and v_bucket == "low":
        return "mean_reverting"
    return "choppy"


def compute_regime(
    symbol: str, feature_row: Dict[str, float], thresholds: Dict[str, dict],
) -> RegimeTags:
    sym_t = thresholds.get(symbol)
    if sym_t is None:
        return RegimeTags(volatility="unknown", liquidity="unknown",
                          trend="unknown")
    vol = _avg_bucket(
        _bucket(feature_row.get("vwap_dev_30s_std", 0.0), sym_t["vwap_dev_30s_std"]),
        _bucket(feature_row.get("mlofi_60s_std", 0.0), sym_t["mlofi_60s_std"]),
    )
    liq = _liquidity_bucket(
        feature_row.get("relative_spread", 0.0),
        feature_row.get("spread_5m_pct", 0.0),
        sym_t,
    )
    tr = _trend_bucket(
        feature_row.get("mlofi_momentum", 0.0),
        feature_row.get("vwap_2m_deviation", 0.0),
        sym_t,
    )
    return RegimeTags(volatility=vol, liquidity=liq, trend=tr)
