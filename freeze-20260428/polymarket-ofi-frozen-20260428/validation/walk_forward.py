from __future__ import annotations
"""
Walk-forward validation with rolling retraining.
Spec v2.6, Section 9.

Temporal gap between train and val >= prediction horizon in bars.
Prevents leakage from rolling normalisation stats and forward-filled features.
"""

import logging

import numpy as np
import pandas as pd

from config import CONFIG, ENSEMBLE_SEEDS

logger = logging.getLogger(__name__)


def walk_forward_splits(
    data: pd.DataFrame,
    timestamp_col: str = "timestamp",
    train_window: int | None = None,
    val_window: int | None = None,
    step_size: int | None = None,
    gap_bars: int | None = None,
) -> list[dict]:
    """
    Generate walk-forward train/val/test splits.

    Each split:
      - train: [i, i + train_window)
      - gap:   [i + train_window, i + train_window + gap_bars)
      - val:   [i + train_window + gap_bars, i + train_window + gap_bars + val_window)

    Returns list of dicts with 'train_idx', 'val_idx' index arrays.
    """
    gap_bars = gap_bars or CONFIG["temporal_gap_bars"]
    n = len(data)

    # Defaults: train=70%, val=15% of total
    if train_window is None:
        train_window = int(n * CONFIG["train_split"])
    if val_window is None:
        val_window = int(n * CONFIG["val_split"])
    if step_size is None:
        step_size = val_window  # Non-overlapping by default

    splits = []
    i = 0
    while i + train_window + gap_bars + val_window <= n:
        train_start = i
        train_end = i + train_window
        val_start = train_end + gap_bars
        val_end = val_start + val_window

        splits.append({
            "fold": len(splits),
            "train_idx": np.arange(train_start, train_end),
            "val_idx": np.arange(val_start, val_end),
            "train_start": train_start,
            "train_end": train_end,
            "val_start": val_start,
            "val_end": val_end,
        })
        i += step_size

    logger.info("Walk-forward: %d folds, gap=%d bars", len(splits), gap_bars)
    return splits


class WalkForwardValidator:
    """
    Orchestrates walk-forward validation with multi-seed ensemble.
    Seeds: [42, 137, 256, 512, 1024]
    """

    def __init__(
        self,
        model_factory,
        train_fn,
        predict_fn,
        seeds: list[int] | None = None,
    ):
        """
        model_factory: callable() -> model instance
        train_fn: callable(model, X_train, y_train, seed) -> trained_model
        predict_fn: callable(model, X) -> probabilities
        """
        self.model_factory = model_factory
        self.train_fn = train_fn
        self.predict_fn = predict_fn
        self.seeds = seeds or ENSEMBLE_SEEDS

    def run(
        self,
        X: np.ndarray,
        y: np.ndarray,
        splits: list[dict],
    ) -> list[dict]:
        """
        Run walk-forward validation across all splits and seeds.
        Returns list of fold results with predictions and metrics.
        """
        results = []

        for split in splits:
            X_train = X[split["train_idx"]]
            y_train = y[split["train_idx"]]
            X_val = X[split["val_idx"]]
            y_val = y[split["val_idx"]]

            # Multi-seed ensemble
            seed_predictions = []
            for seed in self.seeds:
                model = self.model_factory()
                trained = self.train_fn(model, X_train, y_train, seed)
                preds = self.predict_fn(trained, X_val)
                seed_predictions.append(preds)

            # Average probability outputs across seeds
            ensemble_preds = np.mean(seed_predictions, axis=0)

            # Compute accuracy
            predicted_labels = (ensemble_preds > 0.5).astype(int)
            accuracy = float(np.mean(predicted_labels == y_val))

            fold_result = {
                "fold": split["fold"],
                "train_size": len(X_train),
                "val_size": len(X_val),
                "accuracy": accuracy,
                "predictions": ensemble_preds,
                "y_val": y_val,
                "n_seeds": len(self.seeds),
            }
            results.append(fold_result)

            logger.info(
                "Fold %d: accuracy=%.4f (train=%d, val=%d, seeds=%d)",
                split["fold"],
                accuracy,
                len(X_train),
                len(X_val),
                len(self.seeds),
            )

        return results
