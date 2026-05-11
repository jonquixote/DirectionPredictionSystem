from __future__ import annotations
"""
MI-based feature selection.
Spec v2.6, Section 4.

Use mutual information to select top 64 features for Track A.
Fit on training fold only. Never fit on validation or test.
"""

import logging

import numpy as np
from sklearn.feature_selection import mutual_info_classif

logger = logging.getLogger(__name__)


class MIFeatureSelector:
    """
    Mutual information–based feature selector.
    Selects top-k features from training data only.
    """

    def __init__(self, k: int = 64, random_state: int = 42):
        self.k = k
        self.random_state = random_state
        self.selected_indices: np.ndarray | None = None
        self.selected_names: list[str] | None = None
        self.mi_scores: np.ndarray | None = None
        self.is_fitted = False

    def fit(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        feature_names: list[str] | None = None,
    ) -> "MIFeatureSelector":
        """
        Compute MI scores and select top-k features.
        Fit on training fold only — never on validation or test.
        """
        n_features = X_train.shape[1]
        k = min(self.k, n_features)

        self.mi_scores = mutual_info_classif(
            X_train, y_train, random_state=self.random_state
        )
        self.selected_indices = np.argsort(self.mi_scores)[-k:][::-1]

        if feature_names is not None:
            self.selected_names = [feature_names[i] for i in self.selected_indices]
        else:
            self.selected_names = [f"feature_{i}" for i in self.selected_indices]

        self.is_fitted = True
        logger.info(
            "Selected %d features. Top 5 MI scores: %s",
            k,
            list(zip(self.selected_names[:5], self.mi_scores[self.selected_indices[:5]])),
        )
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        """Select the fitted feature columns from X."""
        assert self.is_fitted, "Call fit() before transform()"
        return X[:, self.selected_indices]

    def fit_transform(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        feature_names: list[str] | None = None,
    ) -> np.ndarray:
        """Fit and transform in one call."""
        self.fit(X_train, y_train, feature_names)
        return self.transform(X_train)
