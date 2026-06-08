from __future__ import annotations
import json
import logging
from unittest.mock import MagicMock
import pytest

from feature_engineering.feature_contract import (
    FEATURE_COLS,
    FEATURE_COLS_PER_SYMBOL,
    resolve_model_feature_contract,
)


def test_resolve_model_feature_contract_from_booster():
    """Booster with real names (no Column_ prefix) should return those names directly."""
    booster = MagicMock()
    booster.feature_name.return_value = ["mlofi", "ofi", "mid_price"]
    
    names = resolve_model_feature_contract(booster, None)
    assert names == ["mlofi", "ofi", "mid_price"]


def test_resolve_model_feature_contract_from_sidecar(tmp_path, caplog):
    """Booster with generic Column_* names, sidecar exists -> returns sidecar names + logs WARNING."""
    booster = MagicMock()
    booster.feature_name.return_value = ["Column_0", "Column_1"]
    
    sidecar_path = tmp_path / "feature_names.json"
    sidecar_cols = ["ofi", "mid_price"]
    with open(sidecar_path, "w") as f:
        json.dump(sidecar_cols, f)
        
    with caplog.at_level(logging.WARNING):
        names = resolve_model_feature_contract(booster, sidecar_path)
        
    assert names == sidecar_cols
    assert any("generic Column_*" in record.message for record in caplog.records)


def test_resolve_model_feature_contract_no_source_raises(tmp_path):
    """No sidecar, generic booster -> raises RuntimeError."""
    booster = MagicMock()
    booster.feature_name.return_value = ["Column_0", "Column_1"]
    
    sidecar_path = tmp_path / "non_existent.json"
    
    with pytest.raises(RuntimeError) as excinfo:
        resolve_model_feature_contract(booster, sidecar_path)
    assert "Could not resolve feature contract" in str(excinfo.value)


def test_feature_cols_per_symbol_excludes_symbol_cat():
    """FEATURE_COLS_PER_SYMBOL must exclude 'symbol_cat'."""
    assert "symbol_cat" in FEATURE_COLS
    assert "symbol_cat" not in FEATURE_COLS_PER_SYMBOL


def test_feature_cols_per_symbol_order_matches_feature_cols():
    """FEATURE_COLS_PER_SYMBOL order must match FEATURE_COLS minus symbol_cat."""
    expected = [c for c in FEATURE_COLS if c != "symbol_cat"]
    assert FEATURE_COLS_PER_SYMBOL == expected
