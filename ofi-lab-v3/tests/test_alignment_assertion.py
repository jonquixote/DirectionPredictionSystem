import pytest
import asyncio
import numpy as np
from unittest.mock import MagicMock, patch
from trading.boundary_scorer import BoundaryScorer
from storage.provenance import feature_names_hash, ordered_feature_names_hash

def _run(coro):
    """Run a coroutine in a fresh event loop."""
    prev = None
    try:
        prev = asyncio.get_event_loop()
    except RuntimeError:
        pass
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(prev)

class MockBooster:
    def __init__(self, num_features, feature_names=None):
        self._num_features = num_features
        self._feature_names = feature_names

    def num_feature(self):
        return self._num_features

    def feature_name(self):
        if self._feature_names is None:
            raise Exception("no features")
        return self._feature_names

    def predict(self, feature_vec):
        return np.array([0.55])

# The "canonical" training contract — independent source simulating
# feature_contract.FEATURE_COLS_PER_SYMBOL
CANONICAL = ["f1", "f2", "f3"]

@pytest.fixture
def mock_trader():
    trader = MagicMock()
    trader.models = {}
    trader.feature_names = {}
    trader._trained_num_features = {}
    trader._trained_contract_hash = {}
    trader._trained_contract_order_hash = {}
    trader._contract_source = {}
    trader._canonical_contract = list(CANONICAL)
    trader._prediction_count = 0
    trader._is_in_warmup.return_value = False
    trader._is_model_in_warmup.return_value = False
    trader._model_meta = {}

    # Feature computer setup — bar contains all canonical columns
    trader.feature_computer = MagicMock()
    trader.feature_computer.is_warmed_up.return_value = True
    trader.feature_computer.get_1min_bar.return_value = {
        "mid_price": 60000.0,
        "f1": 1.0,
        "f2": 2.0,
        "f3": 3.0,
        "ts_ms": 1700000000000,
    }

    trader.calibrators = MagicMock()
    # Mock fallback to identity if calibrator KeyError
    trader.calibrators.get.side_effect = KeyError("no calibrator")

    trader.filters = {"confidence_threshold": 0.52, "ev_threshold": 0.0}
    trader._get_meta.side_effect = lambda mn: trader._model_meta[mn]
    trader._tag_regime.return_value = MagicMock()

    # Mock ledger writes
    trader._emit_prediction_rows.return_value = "pred_123"

    return trader

def _setup_aligned_model(trader, name, source="booster_intrinsic"):
    """Set up a model whose served contract matches the canonical contract."""
    booster = MockBooster(3, ["f1", "f2", "f3"] if source == "booster_intrinsic" else ["Column_0", "Column_1", "Column_2"])
    trader.models[name] = booster
    trader.feature_names[name] = list(CANONICAL)  # sidecar matches canonical
    trader._trained_num_features[name] = 3
    # "Trained" hashes from CANONICAL (independent source)
    trader._trained_contract_hash[name] = feature_names_hash(CANONICAL)
    trader._trained_contract_order_hash[name] = ordered_feature_names_hash(CANONICAL)
    trader._contract_source[name] = source
    trader._model_meta[name] = {"symbol": "BTCUSDT", "training_horizon_seconds": 300, "filter_config": {}}


def test_alignment_pass_intrinsic(mock_trader, caplog):
    import logging
    caplog.set_level(logging.DEBUG)

    _setup_aligned_model(mock_trader, "m1", source="booster_intrinsic")

    scorer = BoundaryScorer(trader=mock_trader)
    with patch("trading.boundary_scorer.PREDICTION_SYMBOLS", ["BTCUSDT"]), \
         patch("trading.boundary_scorer.TRADE_SYMBOLS", ["BTCUSDT"]):
        _run(scorer.score_boundary(1700000000000, 1700000000000))

    # Verify predictions were recorded
    assert mock_trader._emit_prediction_rows.call_count == 1
    assert "ALIGNMENT_PASS model=m1 source=booster_intrinsic" in caplog.text


def test_alignment_pass_sidecar(mock_trader, caplog):
    import logging
    caplog.set_level(logging.DEBUG)

    _setup_aligned_model(mock_trader, "m1", source="sidecar")

    scorer = BoundaryScorer(trader=mock_trader)
    with patch("trading.boundary_scorer.PREDICTION_SYMBOLS", ["BTCUSDT"]), \
         patch("trading.boundary_scorer.TRADE_SYMBOLS", ["BTCUSDT"]):
        _run(scorer.score_boundary(1700000000000, 1700000000000))

    assert mock_trader._emit_prediction_rows.call_count == 1
    assert "ALIGNMENT_PASS model=m1 source=sidecar" in caplog.text


def test_alignment_fail_dimension(mock_trader, caplog):
    import logging
    caplog.set_level(logging.ERROR)

    # Served has 2 cols, booster expects 3
    booster = MockBooster(3, ["f1", "f2", "f3"])
    mock_trader.models["m1"] = booster
    mock_trader.feature_names["m1"] = ["f1", "f2"]
    mock_trader._trained_num_features["m1"] = 3
    mock_trader._trained_contract_hash["m1"] = feature_names_hash(CANONICAL)
    mock_trader._trained_contract_order_hash["m1"] = ordered_feature_names_hash(CANONICAL)
    mock_trader._contract_source["m1"] = "booster_intrinsic"
    mock_trader._model_meta["m1"] = {"symbol": "BTCUSDT", "training_horizon_seconds": 300, "filter_config": {}}

    scorer = BoundaryScorer(trader=mock_trader)
    with patch("trading.boundary_scorer.PREDICTION_SYMBOLS", ["BTCUSDT"]):
        _run(scorer.score_boundary(1700000000000, 1700000000000))

    # Verify prediction is refused (no ledger write)
    assert mock_trader._emit_prediction_rows.call_count == 0
    assert "ALIGNMENT_MISMATCH model=m1 source=booster_intrinsic dim_ok=False" in caplog.text


def test_alignment_fail_order(mock_trader, caplog):
    """Served contract has same columns as canonical but in different order — MUST refuse."""
    import logging
    caplog.set_level(logging.ERROR)

    # Canonical is [f1, f2, f3]. Sidecar resolved to [f2, f1, f3] — reordered.
    booster = MockBooster(3, ["f1", "f2", "f3"])
    mock_trader.models["m1"] = booster
    mock_trader.feature_names["m1"] = ["f2", "f1", "f3"]  # WRONG ORDER
    mock_trader._trained_num_features["m1"] = 3
    # Trained hashes from canonical [f1, f2, f3]
    mock_trader._trained_contract_hash["m1"] = feature_names_hash(CANONICAL)
    mock_trader._trained_contract_order_hash["m1"] = ordered_feature_names_hash(CANONICAL)
    mock_trader._contract_source["m1"] = "booster_intrinsic"
    mock_trader._model_meta["m1"] = {"symbol": "BTCUSDT", "training_horizon_seconds": 300, "filter_config": {}}

    scorer = BoundaryScorer(trader=mock_trader)
    with patch("trading.boundary_scorer.PREDICTION_SYMBOLS", ["BTCUSDT"]):
        _run(scorer.score_boundary(1700000000000, 1700000000000))

    # Should be refused due to order mismatch (set check passes since columns are the same)
    assert mock_trader._emit_prediction_rows.call_count == 0
    assert "ALIGNMENT_MISMATCH model=m1 source=booster_intrinsic" in caplog.text
    assert "order_ok=False" in caplog.text
    assert "set_ok=True" in caplog.text


def test_alignment_fail_set(mock_trader, caplog):
    import logging
    caplog.set_level(logging.ERROR)

    # Canonical is [f1, f2, f3]. Sidecar resolved to [f1, f2, f4] — wrong column.
    booster = MockBooster(3, ["f1", "f2", "f3"])
    mock_trader.models["m1"] = booster
    mock_trader.feature_names["m1"] = ["f1", "f2", "f4"]
    mock_trader._trained_num_features["m1"] = 3
    mock_trader._trained_contract_hash["m1"] = feature_names_hash(CANONICAL)
    mock_trader._trained_contract_order_hash["m1"] = ordered_feature_names_hash(CANONICAL)
    mock_trader._contract_source["m1"] = "booster_intrinsic"
    mock_trader._model_meta["m1"] = {"symbol": "BTCUSDT", "training_horizon_seconds": 300, "filter_config": {}}

    scorer = BoundaryScorer(trader=mock_trader)
    with patch("trading.boundary_scorer.PREDICTION_SYMBOLS", ["BTCUSDT"]):
        _run(scorer.score_boundary(1700000000000, 1700000000000))

    # Should be refused
    assert mock_trader._emit_prediction_rows.call_count == 0
    assert "ALIGNMENT_MISMATCH model=m1 source=booster_intrinsic" in caplog.text
    assert "set_ok=False" in caplog.text


def test_columns_missing_guard(mock_trader, caplog):
    """If a contract column doesn't exist in bar, refuse — don't zero-fill."""
    import logging
    caplog.set_level(logging.ERROR)

    # Contract says [f1, f2, f3], bar has f1, f2 but NOT f3
    _setup_aligned_model(mock_trader, "m1")
    mock_trader.feature_computer.get_1min_bar.return_value = {
        "mid_price": 60000.0,
        "f1": 1.0,
        "f2": 2.0,
        # f3 missing from bar!
        "ts_ms": 1700000000000,
    }

    scorer = BoundaryScorer(trader=mock_trader)
    with patch("trading.boundary_scorer.PREDICTION_SYMBOLS", ["BTCUSDT"]):
        _run(scorer.score_boundary(1700000000000, 1700000000000))

    assert mock_trader._emit_prediction_rows.call_count == 0
    assert "COLUMNS_MISSING model=m1 missing=['f3']" in caplog.text


def test_alignment_tautology_impossible(mock_trader, caplog):
    """Prove the assertion is NOT tautological: even when sidecar matches
    the served contract perfectly, a reordered canonical catches it."""
    import logging
    caplog.set_level(logging.ERROR)

    # Sidecar resolved to [f1, f2, f3] — set in feature_names
    booster = MockBooster(3, ["Column_0", "Column_1", "Column_2"])
    mock_trader.models["m1"] = booster
    mock_trader.feature_names["m1"] = ["f1", "f2", "f3"]  # sidecar
    mock_trader._trained_num_features["m1"] = 3
    mock_trader._contract_source["m1"] = "sidecar"
    mock_trader._model_meta["m1"] = {"symbol": "BTCUSDT", "training_horizon_seconds": 300, "filter_config": {}}

    # But canonical says [f3, f1, f2] — a different order.
    # This simulates the case where sidecar is stale/wrong relative to the
    # actual training contract.
    different_canonical = ["f3", "f1", "f2"]
    mock_trader._canonical_contract = different_canonical
    mock_trader._trained_contract_hash["m1"] = feature_names_hash(different_canonical)
    mock_trader._trained_contract_order_hash["m1"] = ordered_feature_names_hash(different_canonical)

    scorer = BoundaryScorer(trader=mock_trader)
    with patch("trading.boundary_scorer.PREDICTION_SYMBOLS", ["BTCUSDT"]):
        _run(scorer.score_boundary(1700000000000, 1700000000000))

    # Must refuse — sidecar != canonical (order differs)
    assert mock_trader._emit_prediction_rows.call_count == 0
    assert "ALIGNMENT_MISMATCH model=m1 source=sidecar" in caplog.text
    assert "order_ok=False" in caplog.text
    assert "set_ok=True" in caplog.text  # same columns, different order
