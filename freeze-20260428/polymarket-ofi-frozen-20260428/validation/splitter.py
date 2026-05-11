from __future__ import annotations
"""
Temporal split and boundary enforcement.
Spec v2.6, Section 9.

Chronological split — never shuffle.
Split at TRAINING_BOUNDARIES (Section 3.1).
"""

import datetime
import logging

import numpy as np
import pandas as pd

from config import TRAINING_BOUNDARIES, CONFIG

logger = logging.getLogger(__name__)


def temporal_split(
    data: pd.DataFrame,
    timestamp_col: str = "timestamp",
    train_frac: float | None = None,
    val_frac: float | None = None,
    test_frac: float | None = None,
    gap_bars: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Chronological temporal split.
    Train: first 70%, Validation: next 15%, Test: final 15%.
    Applies purge gap between train/val and val/test.
    """
    train_frac = train_frac or CONFIG["train_split"]
    val_frac = val_frac or CONFIG["val_split"]
    test_frac = test_frac or CONFIG["test_split"]
    gap_bars = gap_bars or CONFIG["temporal_gap_bars"]

    assert abs(train_frac + val_frac + test_frac - 1.0) < 1e-6, (
        f"Split fractions must sum to 1.0, got {train_frac + val_frac + test_frac}"
    )

    # Sort by timestamp
    data = data.sort_values(timestamp_col).reset_index(drop=True)
    n = len(data)

    train_end = int(n * train_frac)
    val_start = train_end + gap_bars
    val_end = val_start + int(n * val_frac)
    test_start = val_end + gap_bars

    train = data.iloc[:train_end]
    val = data.iloc[val_start:val_end]
    test = data.iloc[test_start:]

    logger.info(
        "Split: train=%d, gap=%d, val=%d, gap=%d, test=%d",
        len(train), gap_bars, len(val), gap_bars, len(test),
    )

    return train, val, test


def enforce_boundaries(
    data: pd.DataFrame,
    timestamp_col: str = "timestamp",
    boundaries: list[str] | None = None,
) -> list[pd.DataFrame]:
    """
    Split data at fee regime boundaries.
    Never train a model across a boundary.
    Returns list of DataFrames, one per regime segment.
    """
    boundaries = boundaries or TRAINING_BOUNDARIES
    boundary_dates = sorted([
        datetime.date.fromisoformat(b) for b in boundaries
    ])

    data = data.sort_values(timestamp_col).reset_index(drop=True)

    # Convert timestamp column to date for comparison
    if pd.api.types.is_datetime64_any_dtype(data[timestamp_col]):
        dates = data[timestamp_col].dt.date
    else:
        dates = pd.to_datetime(data[timestamp_col]).dt.date

    segments = []
    prev_boundary = None

    for boundary in boundary_dates:
        if prev_boundary is None:
            mask = dates < boundary
        else:
            mask = (dates >= prev_boundary) & (dates < boundary)
        segment = data[mask]
        if len(segment) > 0:
            segments.append(segment)
        prev_boundary = boundary

    # Final segment after last boundary
    mask = dates >= boundary_dates[-1]
    segment = data[mask]
    if len(segment) > 0:
        segments.append(segment)

    logger.info(
        "Boundary enforcement: %d segments from %d boundaries",
        len(segments), len(boundary_dates),
    )

    return segments
