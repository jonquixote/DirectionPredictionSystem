from __future__ import annotations
"""
Alert routing and threshold constants.
Spec v2.6, Section 11.

Integrates PSI monitors with execution decisions.
"""

import logging

from monitoring.psi import compute_psi, compute_predictive_psi
from config import CONFIG

logger = logging.getLogger(__name__)

# Alert thresholds
THRESHOLDS = {
    "distribution_psi": CONFIG["psi_distribution_alert"],   # 0.25
    "predictive_psi": CONFIG["psi_predictive_alert"],        # 0.10
}

# Alert levels
ALERT_STABLE = "stable"
ALERT_MONITOR = "monitor"
ALERT_SUSPEND = "suspend"


def classify_distribution_psi(psi_value: float) -> str:
    """Classify distribution PSI into alert level."""
    if psi_value < 0.10:
        return ALERT_STABLE
    elif psi_value < THRESHOLDS["distribution_psi"]:
        return ALERT_MONITOR
    else:
        return ALERT_SUSPEND


def classify_predictive_psi(psi_value: float) -> str:
    """Classify predictive PSI into alert level."""
    if psi_value < THRESHOLDS["predictive_psi"]:
        return ALERT_STABLE
    else:
        return ALERT_SUSPEND


def check_feature_stability(
    expected_mlofi,
    actual_mlofi,
    mlofi_values,
    outcomes,
    training_bucket_win_rates,
) -> dict:
    """
    Run both PSI checks and return combined alert status.
    Returns dict with alert_level, distribution_psi, predictive_psi,
    and recommended action.
    """
    result = {
        "distribution_psi": None,
        "predictive_psi": None,
        "distribution_alert": ALERT_STABLE,
        "predictive_alert": ALERT_STABLE,
        "overall_alert": ALERT_STABLE,
        "action": "continue",
    }

    try:
        dist_psi = compute_psi(expected_mlofi, actual_mlofi)
        result["distribution_psi"] = dist_psi
        result["distribution_alert"] = classify_distribution_psi(dist_psi)
    except ValueError as e:
        logger.error("Distribution PSI computation failed: %s", e)
        result["distribution_alert"] = ALERT_SUSPEND
        result["action"] = "investigate_pipeline"
        result["overall_alert"] = ALERT_SUSPEND
        return result

    try:
        pred_psi = compute_predictive_psi(
            mlofi_values, outcomes, training_bucket_win_rates
        )
        result["predictive_psi"] = pred_psi
        result["predictive_alert"] = classify_predictive_psi(pred_psi)
    except ValueError as e:
        logger.warning("Predictive PSI computation failed: %s", e)

    # Overall alert is the more severe of the two
    if ALERT_SUSPEND in (result["distribution_alert"], result["predictive_alert"]):
        result["overall_alert"] = ALERT_SUSPEND
        result["action"] = "suspend_and_investigate"
    elif ALERT_MONITOR in (result["distribution_alert"], result["predictive_alert"]):
        result["overall_alert"] = ALERT_MONITOR
        result["action"] = "monitor"

    if result["distribution_alert"] == ALERT_SUSPEND:
        logger.warning(
            "Distribution PSI > %.2f: %.4f — refit adverse selection model",
            THRESHOLDS["distribution_psi"],
            dist_psi,
        )

    if result["predictive_alert"] == ALERT_SUSPEND:
        logger.warning(
            "Predictive PSI > %.2f: %.4f — relationship degradation detected",
            THRESHOLDS["predictive_psi"],
            pred_psi,
        )

    return result
