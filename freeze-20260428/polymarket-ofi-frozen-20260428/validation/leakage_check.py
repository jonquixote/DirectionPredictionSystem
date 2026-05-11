from __future__ import annotations
"""
Leakage check.
Spec v2.6, Section 9.

Reported accuracy > 62%: treat as potential leakage.
The 92.4% in some papers is almost certainly k-fold on time series.
"""

import logging

logger = logging.getLogger(__name__)

LEAKAGE_THRESHOLD = 0.62


def check_leakage(accuracy: float, context: str = "") -> dict:
    """
    Flag accuracy above 62% as potential leakage.

    Returns dict with 'flagged', 'accuracy', 'threshold', and 'message'.
    """
    flagged = accuracy > LEAKAGE_THRESHOLD

    result = {
        "flagged": flagged,
        "accuracy": accuracy,
        "threshold": LEAKAGE_THRESHOLD,
    }

    if flagged:
        result["message"] = (
            f"Accuracy {accuracy:.4f} exceeds leakage threshold {LEAKAGE_THRESHOLD}. "
            "Investigate: temporal leakage via rolling stats, forward-filled features, "
            "or k-fold on time series data. "
            f"Context: {context}" if context else ""
        )
        logger.warning(
            "LEAKAGE CHECK: accuracy=%.4f > %.2f threshold. %s",
            accuracy,
            LEAKAGE_THRESHOLD,
            context,
        )
    else:
        result["message"] = f"Accuracy {accuracy:.4f} within expected range."

    return result
