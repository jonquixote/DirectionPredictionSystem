from __future__ import annotations
"""
Pilot analysis for propagation lag measurement.
Spec v2.6, Section 3.3.

Stage 1 (Pilot): 40 events (10 per asset) over 1 week
  Compute sigma_pilot = std(operational_lags)
  n_required = (1.96 * sigma_pilot / 5) ** 2

Stage 2 (Main): Collect n_required events, stratified equally across:
  - Asian session:  00:00–08:00 UTC
  - EU session:     08:00–16:00 UTC
  - US session:     16:00–24:00 UTC
"""

import datetime
import logging

import numpy as np

logger = logging.getLogger(__name__)

# Session strata definitions (UTC)
SESSIONS = {
    "asian": (0, 8),
    "eu": (8, 16),
    "us": (16, 24),
}


def classify_session(timestamp_utc: datetime.datetime) -> str:
    """Classify a UTC timestamp into a trading session stratum."""
    hour = timestamp_utc.hour
    for session_name, (start_hour, end_hour) in SESSIONS.items():
        if start_hour <= hour < end_hour:
            return session_name
    return "us"  # Fallback (should not reach)


def compute_pilot_statistics(lags_ms: list[float]) -> dict:
    """
    Compute pilot statistics from the first 40 lag measurements.
    Returns sigma_pilot and n_required per stratum.
    """
    if len(lags_ms) < 10:
        raise ValueError(
            f"Pilot requires at least 10 measurements, got {len(lags_ms)}"
        )

    lags_s = np.array(lags_ms) / 1000.0  # Convert to seconds
    sigma_pilot = float(np.std(lags_s, ddof=1))
    mean_lag = float(np.mean(lags_s))
    median_lag = float(np.median(lags_s))

    # n_required = (1.96 * sigma_pilot / 5) ** 2
    # Margin of error: 5 seconds
    if sigma_pilot == 0:
        n_required = 10  # Minimum sensible value
    else:
        n_required = int(np.ceil((1.96 * sigma_pilot / 5) ** 2))

    n_required = max(n_required, 10)  # Floor at 10

    return {
        "sigma_pilot": sigma_pilot,
        "mean_lag_s": mean_lag,
        "median_lag_s": median_lag,
        "n_required_per_stratum": n_required,
        "n_total_required": n_required * len(SESSIONS),
        "pilot_n": len(lags_ms),
    }


def check_stopping_rule(
    lags_by_session: dict[str, list[float]],
    n_required_per_stratum: int,
) -> dict:
    """
    Stopping rule: n >= n_required PER STRATUM
    AND 95% CI on mean lag excludes both 10s and 30s thresholds.

    Returns dict with 'can_stop', 'reason', and per-session details.
    """
    results = {"can_stop": True, "sessions": {}, "reason": None}

    for session_name, lags_ms in lags_by_session.items():
        lags_s = np.array(lags_ms) / 1000.0 if lags_ms else np.array([])
        n = len(lags_s)

        session_result = {
            "n": n,
            "n_required": n_required_per_stratum,
            "sufficient": n >= n_required_per_stratum,
        }

        if n >= 2:
            mean = float(np.mean(lags_s))
            std = float(np.std(lags_s, ddof=1))
            ci_half = 1.96 * std / np.sqrt(n)
            ci_lower = mean - ci_half
            ci_upper = mean + ci_half

            session_result.update({
                "mean_lag_s": mean,
                "ci_lower": ci_lower,
                "ci_upper": ci_upper,
                "excludes_10s": ci_lower > 10 or ci_upper < 10,
                "excludes_30s": ci_lower > 30 or ci_upper < 30,
            })

            if not session_result["sufficient"]:
                results["can_stop"] = False
                results["reason"] = (
                    f"Insufficient samples in {session_name}: "
                    f"{n}/{n_required_per_stratum}"
                )
            elif not (session_result["excludes_10s"] and session_result["excludes_30s"]):
                results["can_stop"] = False
                results["reason"] = (
                    f"95% CI for {session_name} does not exclude both 10s and 30s "
                    f"thresholds: [{ci_lower:.1f}, {ci_upper:.1f}]"
                )
        else:
            session_result.update({"mean_lag_s": None, "ci_lower": None, "ci_upper": None})
            results["can_stop"] = False
            results["reason"] = f"Insufficient data for {session_name}"

        results["sessions"][session_name] = session_result

    return results
