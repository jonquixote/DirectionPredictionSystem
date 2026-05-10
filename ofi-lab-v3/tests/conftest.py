"""Shared pytest fixtures for v3."""
from pathlib import Path

import numpy as np
import pytest
import lightgbm as lgb

from trading.live_features import V3_FEATURE_COLS


@pytest.fixture(scope="session")
def tiny_model_path(tmp_path_factory) -> str:
    """Train a tiny LightGBM model on synthetic data and return its path.

    Shaped like V3_FEATURE_COLS so PaperTrader's loader can read it.
    Writes both model.lgb and feature_names.json into the same dir.
    """
    out_dir = tmp_path_factory.mktemp("tiny_model")
    out_path = out_dir / "model.lgb"
    rng = np.random.default_rng(seed=42)
    n = 200
    X = rng.normal(size=(n, len(V3_FEATURE_COLS)))
    sc_idx = V3_FEATURE_COLS.index("symbol_cat")
    X[:, sc_idx] = rng.integers(0, 3, size=n)
    y = (X[:, 0] > 0).astype(int)
    train = lgb.Dataset(X, label=y, feature_name=list(V3_FEATURE_COLS))
    params = {"objective": "binary", "verbosity": -1,
              "num_leaves": 7, "learning_rate": 0.1}
    booster = lgb.train(params, train, num_boost_round=20)
    booster.save_model(str(out_path))
    import json
    with open(out_dir / "feature_names.json", "w") as f:
        json.dump(list(V3_FEATURE_COLS), f)
    return str(out_path)
