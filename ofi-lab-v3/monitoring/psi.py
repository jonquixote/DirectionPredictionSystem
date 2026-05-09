from __future__ import annotations
"""
Population Stability Index monitoring.
Spec v2.6, Section 11.

compute_psi: Distribution PSI (threshold > 0.25)
compute_predictive_psi: Relationship degradation (threshold > 0.10)
"""

import numpy as np


def compute_psi(
    expected: np.ndarray, actual: np.ndarray, bins: int = 10
) -> float:
    """
    Population Stability Index.
    Standard thresholds:
      < 0.10: stable
      0.10-0.25: monitor
      > 0.25: suspend execution, investigate, retrain

    Raises ValueError on constant series (data pipeline failure).
    """
    min_val = min(min(expected), min(actual))
    max_val = max(max(expected), max(actual))
    if min_val == max_val:
        raise ValueError(
            f"compute_psi: constant series detected (min=max={min_val}). "
            "Data pipeline failure likely — MLOFI should not be constant."
        )
    bin_edges = np.linspace(min_val, max_val, bins + 1)
    exp_counts = np.histogram(expected, bins=bin_edges)[0] + 1e-6
    act_counts = np.histogram(actual, bins=bin_edges)[0] + 1e-6
    exp_pct = exp_counts / exp_counts.sum()
    act_pct = act_counts / act_counts.sum()
    return float(np.sum((act_pct - exp_pct) * np.log(act_pct / exp_pct)))


def compute_predictive_psi(
    mlofi_values: np.ndarray,
    outcomes: np.ndarray,
    training_bucket_win_rates: np.ndarray,
    bins: int = 5,
) -> float:
    """
    Predictive PSI — tighter threshold (> 0.10 triggers alert).
    Detects relationship degradation without distributional shift.

    Handles degenerate cases (duplicate quantile boundaries) gracefully.
    """
    if len(training_bucket_win_rates) != bins:
        raise ValueError(
            f"compute_predictive_psi: training_bucket_win_rates has "
            f"{len(training_bucket_win_rates)} elements but bins={bins}. "
            "Recompute training baseline to match the configured bin count."
        )
    quantiles = np.quantile(mlofi_values, np.linspace(0, 1, bins + 1))
    unique_quantiles = np.unique(quantiles)
    if len(unique_quantiles) < len(quantiles):
        # Degenerate case: low-variance or repeated MLOFI values produce
        # duplicate boundaries. Recompute with fewer bins.
        bins = len(unique_quantiles) - 1
        quantiles = unique_quantiles
        if len(training_bucket_win_rates) != bins:
            raise ValueError(
                f"compute_predictive_psi: quantile deduplication reduced bins to {bins}, "
                f"but training_bucket_win_rates has {len(training_bucket_win_rates)} elements. "
                "Recompute training baseline at the reduced bin count. "
                "Silently truncating would make the PSI comparison apples-to-oranges."
            )
    bucket_win_rates = []
    for i in range(bins):
        # Last bucket uses <= so the maximum value is included.
        upper = (
            (mlofi_values < quantiles[i + 1])
            if i < bins - 1
            else (mlofi_values <= quantiles[i + 1])
        )
        mask = (mlofi_values >= quantiles[i]) & upper
        bucket_win_rates.append(
            np.mean(np.array(outcomes)[mask]) if mask.sum() > 0 else 0.5
        )
    bucket_win_rates = np.array(bucket_win_rates) + 1e-6
    training_rates = np.array(training_bucket_win_rates) + 1e-6
    return float(
        np.sum(
            (bucket_win_rates - training_rates)
            * np.log(bucket_win_rates / training_rates)
        )
    )
