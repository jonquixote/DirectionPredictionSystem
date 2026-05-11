import json
import os
import sqlite3
import pytest
from unittest.mock import MagicMock

from trading.paper_trader import PaperTrader
from storage.db import init_schema
from regime.tagger import RegimeTags


# Canonical threshold schema matching regime/tagger.py expectations
_THRESHOLD_BTCUSDT = {
    "vwap_dev_30s_std": {"p25": 0.001, "p75": 0.005},
    "mlofi_60s_std":     {"p25": 10, "p75": 50},
    "relative_spread":   {"p25": 0.0001, "p75": 0.0005},
    "spread_5m_pct":     {"p25": 0.0002, "p75": 0.001},
    "mlofi_momentum":    {"p25": -5, "p75": 5},
    "vwap_2m_deviation": {"p25": -0.001, "p75": 0.001},
}


@pytest.fixture
def mock_trader(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    thresholds_path = tmp_path / "regime.json"
    thresholds_path.write_text(json.dumps({"BTCUSDT": _THRESHOLD_BTCUSDT}))
    monkeypatch.setenv("REGIME_THRESHOLDS_PATH", str(thresholds_path))
    trader = PaperTrader(model_paths={}, log_dir=str(tmp_path / "logs"),
                         confidence_threshold=0.55)
    return trader


def test_paper_trader_tag_regime_uses_thresholds(mock_trader):
    # Low volatility: both inputs well below p25
    tags = mock_trader._tag_regime("BTCUSDT", {
        "vwap_dev_30s_std": 0.0005,
        "mlofi_60s_std": 5,
    })
    assert tags.volatility == "low"

    # High volatility: both inputs above p75
    tags = mock_trader._tag_regime("BTCUSDT", {
        "vwap_dev_30s_std": 0.010,
        "mlofi_60s_std": 100,
    })
    assert tags.volatility == "high"


def test_paper_trader_emit_prediction_rows_includes_regime(mock_trader):
    import config
    # Mock dependencies inside the trader
    mock_trader._build_envelope = MagicMock()
    mock_trader._build_envelope.return_value = MagicMock(
        model_name="test_model",
        model_artifact_hash="abc",
        feature_names_hash="def",
        feature_version="1",
        training_horizon_seconds=60,
        train_window_start="2020-01-01",
        train_window_end="2020-01-02",
        train_cutoff="2020-01-02",
        registry_load_generation=1,
        policy_config_hash="ghi",
        decision_policy_version="1",
        calibration_map_hash="jkl",
    )

    original_meta = getattr(config, "PAPER_TRADING", {}).get("model_metadata", {})
    config.PAPER_TRADING = {"model_metadata": {"test_model": {
        "training_horizon_seconds": 60, "feature_version": "1",
        "symbol": "BTCUSDT",
        "train_window_start": "2020-01-01",
        "train_window_end": "2020-01-02",
        "train_cutoff": "2020-01-02",
    }}}

    pid = mock_trader._emit_prediction_rows(
        model_name="test_model",
        symbol="BTCUSDT",
        boundary_ms=1000000,
        ts_model_ran_ms=1000005,
        pred_proba_raw=0.6,
        pred_proba_calibrated=0.55,
        pred_direction="up",
        above_threshold=True,
        warmup=False,
        platform="kalshi",
        price_at_open=50000.0,
        regime_features={
            "vwap_dev_30s_std": 0.010,
            "mlofi_60s_std": 100,
        },
    )

    conn = sqlite3.connect(os.environ["STORAGE_DB_PATH"])
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT regime_volatility FROM predictions WHERE prediction_id = ?",
        (pid,),
    ).fetchone()

    assert row["regime_volatility"] == "high"

    # restore config
    config.PAPER_TRADING["model_metadata"] = original_meta
