"""Shared pytest fixtures for v3."""
# Path aliasing: ofi-lab-v3 modules use `from services.X import ...` style
# imports because production runs cwd=ofi-lab-v3/dashboard_api. When pytest
# is run from the project root, those bare imports fail. Inject sys.modules
# aliases so `services`, `ws`, `routers` resolve to dashboard_api.services
# (etc.) without touching every module.
import sys as _sys
import importlib as _importlib
for _alias, _target in (
    ("services", "dashboard_api.services"),
    ("ws", "dashboard_api.ws"),
    ("routers", "dashboard_api.routers"),
):
    if _alias not in _sys.modules:
        try:
            _sys.modules[_alias] = _importlib.import_module(_target)
        except ImportError:
            # Optional alias — skip if target module isn't importable yet.
            # Individual test files can still import the real path directly.
            pass

from pathlib import Path

import numpy as np
import pytest
import lightgbm as lgb
import pyarrow as pa
import pyarrow.parquet as pq

from feature_engineering.feature_contract import FEATURE_COLS


@pytest.fixture(scope="session")
def tiny_model_path(tmp_path_factory) -> str:
    """Train a tiny LightGBM model on synthetic data and return its path.

    Shaped like FEATURE_COLS so PaperTrader's loader can read it.
    Writes both model.lgb and feature_names.json into the same dir.
    """
    out_dir = tmp_path_factory.mktemp("tiny_model")
    out_path = out_dir / "model.lgb"
    rng = np.random.default_rng(seed=42)
    n = 200
    X = rng.normal(size=(n, len(FEATURE_COLS)))
    sc_idx = FEATURE_COLS.index("symbol_cat")
    X[:, sc_idx] = rng.integers(0, 3, size=n)
    y = (X[:, 0] > 0).astype(int)
    train = lgb.Dataset(X, label=y, feature_name=list(FEATURE_COLS))
    params = {"objective": "binary", "verbosity": -1,
              "num_leaves": 7, "learning_rate": 0.1}
    booster = lgb.train(params, train, num_boost_round=20)
    booster.save_model(str(out_path))
    import json
    with open(out_dir / "feature_names.json", "w") as f:
        json.dump(list(FEATURE_COLS), f)
    return str(out_path)


@pytest.fixture(scope="session")
def synthetic_minute_bars_path(tmp_path_factory) -> str:
    """Generate a synthetic minute-bars parquet shaped like FEATURE_COLS.

    10 rows with FEATURE_COLS + ts_ms + mid_price columns.
    Used by replay smoke test.
    """
    out_dir = tmp_path_factory.mktemp("minute_bars")
    out_path = out_dir / "btc_minute_bars.parquet"
    rng = np.random.default_rng(seed=123)
    n = 10
    data = {col: rng.normal(size=n).tolist() for col in FEATURE_COLS}
    sc_idx = FEATURE_COLS.index("symbol_cat")
    data["symbol_cat"] = rng.integers(0, 3, size=n).tolist()
    data["ts_ms"] = [1_735_689_600_000 + i * 300_000 for i in range(n)]
    data["mid_price"] = [60_000.0 + i for i in range(n)]
    table = pa.table(data)
    pq.write_table(table, str(out_path))
    return str(out_path)
