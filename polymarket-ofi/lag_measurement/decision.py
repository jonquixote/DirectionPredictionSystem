from __future__ import annotations
"""
LAG_DECISION_RULES evaluator.
Spec v2.6, Section 3.4.

Pre-committed decision rules. These do not change based on measurement outcomes.
"""

import logging

import numpy as np

from config import LAG_DECISION_RULES

logger = logging.getLogger(__name__)


def classify_lag_regime(median_lag_s: float) -> str:
    """Classify median lag into one of the three decision regimes."""
    if median_lag_s < 10:
        return "below_10s"
    elif median_lag_s <= 30:
        return "10_to_30s"
    else:
        return "above_30s"


def evaluate_decision(
    median_lag_s: float,
    lags_by_session: dict[str, list[float]] | None = None,
) -> dict:
    """
    Evaluate the pre-committed decision rules against measured lag data.

    Returns a decision dict with 'regime', 'action', 'details', and
    optionally 'secondary_analysis' for the 10-30s case.
    """
    regime = classify_lag_regime(median_lag_s)
    rule = LAG_DECISION_RULES[regime]

    result = {
        "regime": regime,
        "median_lag_s": median_lag_s,
        "action": rule["action"],
        "rule": rule,
    }

    if regime == "below_10s":
        result["proceed"] = False
        result["message"] = rule["rationale"]

    elif regime == "10_to_30s":
        result["proceed"] = False  # Default until secondary analysis qualifies
        if lags_by_session:
            result["secondary_analysis"] = _stratified_secondary_analysis(
                lags_by_session
            )
            if result["secondary_analysis"]["qualifies"]:
                result["proceed"] = True
                result["message"] = (
                    "Qualified via stratified secondary analysis — "
                    "build session + volatility regime condition into Stage 1 gates"
                )
            else:
                result["message"] = (
                    "Failed stratified secondary analysis — "
                    "treat as below_10s, terminate"
                )
        else:
            result["message"] = (
                "Stratified session data required for secondary analysis"
            )

    elif regime == "above_30s":
        result["proceed"] = True
        result["message"] = rule["validation_required"]

        # Check for stability warning
        if lags_by_session:
            stability = _check_stability(lags_by_session)
            result["stability"] = stability
            if not stability["stable"]:
                result["warning"] = rule["warning"]

    return result


def _stratified_secondary_analysis(
    lags_by_session: dict[str, list[float]],
) -> dict:
    """
    Proceed condition: median lag > 30s in >30% of observations
    in at least ONE session stratum.
    """
    qualifies = False
    session_results = {}

    for session_name, lags_ms in lags_by_session.items():
        if not lags_ms:
            session_results[session_name] = {"n": 0, "pct_above_30s": 0.0}
            continue

        lags_s = np.array(lags_ms) / 1000.0
        n = len(lags_s)
        n_above_30 = int(np.sum(lags_s > 30))
        pct_above_30 = n_above_30 / n if n > 0 else 0.0
        median = float(np.median(lags_s))

        session_results[session_name] = {
            "n": n,
            "median_lag_s": median,
            "n_above_30s": n_above_30,
            "pct_above_30s": pct_above_30,
            "qualifies": pct_above_30 > 0.30 and median > 30,
        }

        if session_results[session_name]["qualifies"]:
            qualifies = True

    return {"qualifies": qualifies, "sessions": session_results}


def _check_stability(
    lags_by_session: dict[str, list[float]],
) -> dict:
    """
    Verify lag stability across sessions.
    A 45s median that collapses to 8s in high-vol is NOT a stable 45s window.
    """
    session_medians = {}
    for session_name, lags_ms in lags_by_session.items():
        if lags_ms:
            lags_s = np.array(lags_ms) / 1000.0
            session_medians[session_name] = float(np.median(lags_s))

    if len(session_medians) < 2:
        return {"stable": False, "reason": "Insufficient session coverage"}

    medians = list(session_medians.values())
    min_median = min(medians)
    max_median = max(medians)

    # Unstable if any session median drops below 10s while others are above 30s
    stable = not (min_median < 10 and max_median > 30)

    return {
        "stable": stable,
        "session_medians": session_medians,
        "min_median_s": min_median,
        "max_median_s": max_median,
    }
