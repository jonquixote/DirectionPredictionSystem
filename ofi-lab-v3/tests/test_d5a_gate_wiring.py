"""D.5a — Per-model gate wiring tests.

Covers:
- Per-model confidence_threshold / ev_threshold / blackout_hours / warmup_seconds
  overrides from filter_config_json (resolved via _model_fc in _run_predictions)
- _is_model_in_warmup helper
- _reload_model_meta picks up SQL changes + skips invalid JSON
- log_paper_trade called for each CONTRACT_DURATION when prediction passes all gates
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_trader(tmp_path, monkeypatch, tiny_model_path, model_name="h300_btc_30d"):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={model_name: tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.52,
    )


def _set_filter_config(trader, model_name: str, fc: dict) -> None:
    """Update the in-memory filter_config for a model (bypasses SQL)."""
    if model_name in trader._model_meta:
        trader._model_meta[model_name]["filter_config"] = fc
    else:
        trader._model_meta[model_name] = {"filter_config": fc}


# ── 1. _is_model_in_warmup ────────────────────────────────────────────────────

class TestIsModelInWarmup:
    def test_returns_true_when_no_data_yet(self, tmp_path, monkeypatch, tiny_model_path):
        t = _make_trader(tmp_path, monkeypatch, tiny_model_path)
        t._first_data_time_ms = None
        assert t._is_model_in_warmup(1800) is True

    def test_returns_true_when_within_warmup_window(self, tmp_path, monkeypatch, tiny_model_path):
        t = _make_trader(tmp_path, monkeypatch, tiny_model_path)
        # 5 seconds ago → still in a 10s warmup
        t._first_data_time_ms = int(time.time() * 1000) - 5_000
        assert t._is_model_in_warmup(10) is True

    def test_returns_false_when_warmup_expired(self, tmp_path, monkeypatch, tiny_model_path):
        t = _make_trader(tmp_path, monkeypatch, tiny_model_path)
        # 20 seconds ago → past a 10s warmup
        t._first_data_time_ms = int(time.time() * 1000) - 20_000
        assert t._is_model_in_warmup(10) is False

    def test_per_model_warmup_seconds_override(self, tmp_path, monkeypatch, tiny_model_path):
        """A model with warmup_seconds=5 should exit warmup faster than the global 1800s."""
        t = _make_trader(tmp_path, monkeypatch, tiny_model_path)
        t._first_data_time_ms = int(time.time() * 1000) - 11_000  # 11s ago
        # Global 1800s → still in warmup; per-model 10s → expired
        assert t._is_in_warmup() is True
        assert t._is_model_in_warmup(10) is False


# ── 2. _reload_model_meta ─────────────────────────────────────────────────────

def _seed_model_in_registry(db_conn, model_name: str, fc: dict | None = None) -> None:
    """Insert or update a model_registry row so _reload_model_meta has something to read."""
    fc_json = json.dumps(fc or {})
    db_conn.execute(
        "INSERT OR REPLACE INTO model_registry "
        "(name, symbol, training_horizon_seconds, feature_version, "
        " platform_active_json, filter_config_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (model_name, "BTCUSDT", 300, "v3", "{}", fc_json),
    )
    db_conn.commit()


class TestReloadModelMeta:
    def test_picks_up_new_filter_config_from_db(self, tmp_path, monkeypatch, tiny_model_path):
        t = _make_trader(tmp_path, monkeypatch, tiny_model_path, model_name="h300_btc_30d")
        # Confirm model has an entry (may be empty filter_config)
        assert "h300_btc_30d" in t._model_meta

        # Seed registry row with initial empty config, then set in-memory to match
        _seed_model_in_registry(t._db_conn, "h300_btc_30d", {})
        t._model_meta["h300_btc_30d"]["filter_config"] = {}

        # Now update DB to new value
        new_fc = {"confidence_threshold": 0.77, "ev_threshold": 0.01}
        t._db_conn.execute(
            "UPDATE model_registry SET filter_config_json=? WHERE name=?",
            (json.dumps(new_fc), "h300_btc_30d"),
        )
        t._db_conn.commit()

        t._reload_model_meta()

        assert t._model_meta["h300_btc_30d"]["filter_config"] == new_fc

    def test_skips_models_not_in_meta(self, tmp_path, monkeypatch, tiny_model_path):
        """SQL rows for unknown models should not raise."""
        t = _make_trader(tmp_path, monkeypatch, tiny_model_path)
        # Insert a row for a model not loaded
        _seed_model_in_registry(t._db_conn, "ghost_model", {"confidence_threshold": 0.9})
        # Should not raise
        t._reload_model_meta()
        # ghost_model should NOT appear in _model_meta
        assert "ghost_model" not in t._model_meta

    def test_skips_invalid_json_and_logs_warning(self, tmp_path, monkeypatch, tiny_model_path, caplog):
        t = _make_trader(tmp_path, monkeypatch, tiny_model_path, model_name="h300_btc_30d")
        # Seed valid row, set in-memory state, then corrupt the DB row
        _seed_model_in_registry(t._db_conn, "h300_btc_30d", {})
        t._model_meta["h300_btc_30d"]["filter_config"] = {}
        t._db_conn.execute(
            "UPDATE model_registry SET filter_config_json=? WHERE name=?",
            ("not valid json{{", "h300_btc_30d"),
        )
        t._db_conn.commit()

        import logging
        with caplog.at_level(logging.WARNING, logger="paper_trader"):
            t._reload_model_meta()  # must not raise

        assert any("invalid filter_config_json" in r.message for r in caplog.records)

    def test_no_crash_when_db_conn_is_none(self, tmp_path, monkeypatch, tiny_model_path):
        t = _make_trader(tmp_path, monkeypatch, tiny_model_path)
        t._db_conn = None
        t._reload_model_meta()  # must not raise


# ── 3. per-model confidence_threshold gate ────────────────────────────────────

class TestPerModelConfidenceThreshold:
    def test_filter_config_confidence_threshold_blocks_low_proba(
        self, tmp_path, monkeypatch, tiny_model_path
    ):
        """Model with confidence_threshold=0.99 should block a 0.5-ish prediction."""
        t = _make_trader(tmp_path, monkeypatch, tiny_model_path, model_name="h300_btc_30d")
        _set_filter_config(t, "h300_btc_30d", {"confidence_threshold": 0.99})

        # Build a filter_ctx with pred_proba = 0.5 (not above 0.99)
        filter_ctx = {
            "prediction_id": "test_pred_001",
            "model_name": "h300_btc_30d",
            "symbol": "BTCUSDT",
            "boundary_ms": 1_735_689_600_000,
            "pred_proba": 0.5,
            "pred_proba_calibrated": 0.5,
            "pred_direction": "up",
            "above_threshold": False,  # 0.5 < (1 - 0.99) = 0.01 fails; also < 0.99
            "warmup": False,
            "confidence_threshold": 0.99,
            "ev_threshold": 0.0,
            "active_filter_mode": "confidence_gate",
            "p_market": 0.5,
            "regime_features": {},
            "calibrated_p": 0.5,
            "ev": 0.0,
            "utc_hour": 12,
            "blackout_hours": [],
            "model_conflict": False,
            "book_age_seconds": 1.0,
            "book_has_quotes": True,
        }
        verdict = t._evaluate_paper_filters(filter_ctx)
        assert not verdict.passed

    def test_global_confidence_threshold_used_when_no_override(
        self, tmp_path, monkeypatch, tiny_model_path
    ):
        """When filter_config is empty, global threshold (0.52) should apply."""
        t = _make_trader(tmp_path, monkeypatch, tiny_model_path, model_name="h300_btc_30d")
        _set_filter_config(t, "h300_btc_30d", {})

        # proba = 0.55 → above global 0.52
        filter_ctx = {
            "prediction_id": "test_pred_002",
            "model_name": "h300_btc_30d",
            "symbol": "BTCUSDT",
            "boundary_ms": 1_735_689_600_000,
            "pred_proba": 0.55,
            "pred_proba_calibrated": 0.55,
            "pred_direction": "up",
            "above_threshold": True,
            "warmup": False,
            "confidence_threshold": 0.52,
            "ev_threshold": 0.0,
            "active_filter_mode": "confidence_gate",
            "p_market": 0.5,
            "regime_features": {},
            "calibrated_p": 0.55,
            "ev": 0.0,
            "utc_hour": 12,
            "blackout_hours": [],
            "model_conflict": False,
            "book_age_seconds": 1.0,
            "book_has_quotes": True,
        }
        verdict = t._evaluate_paper_filters(filter_ctx)
        assert verdict.passed


# ── 4. log_paper_trade called for each duration when above threshold ──────────

class TestLogPaperTradeCalledPerDuration:
    def test_log_paper_trade_called_for_each_contract_duration(
        self, tmp_path, monkeypatch, tiny_model_path
    ):
        """When a prediction passes all gates, sqlite_ledger.log_paper_trade
        must be called once per CONTRACT_DURATION."""
        import asyncio
        from trading import paper_trader as pt_module

        monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
        monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
        from trading.paper_trader import PaperTrader, CONTRACT_DURATIONS, PREDICTION_SYMBOLS, TRADE_SYMBOLS

        t = PaperTrader(
            model_paths={"h300_btc_30d": tiny_model_path},
            log_dir=str(tmp_path / "logs"),
            confidence_threshold=0.52,
        )

        # Ensure BTCUSDT is both predicted and trade-eligible
        assert "BTCUSDT" in PREDICTION_SYMBOLS
        assert "BTCUSDT" in TRADE_SYMBOLS

        # Patch sqlite_ledger.log_paper_trade to capture calls
        t.sqlite_ledger = MagicMock()
        t.sqlite_ledger.log_paper_trade = MagicMock()

        # Set up feature computer to appear warmed up
        t.feature_computer = MagicMock()
        t.feature_computer.is_warmed_up = MagicMock(return_value=True)
        # bar ts_ms must be "fresh" (within max_book_age_seconds=30) relative to now
        _now_ms = int(time.time() * 1000)
        _bar = {col: 0.0 for col in t.feature_names.get("h300_btc_30d", [])}
        _bar["mid_price"] = 60_000.0
        _bar["has_quotes"] = True
        _bar["ts_ms"] = _now_ms - 2_000  # 2 seconds ago — fresh
        t.feature_computer.get_1min_bar = MagicMock(return_value=_bar)

        # Mark warmup as over
        t._first_data_time_ms = int(time.time() * 1000) - 3_600_000  # 1h ago

        # Force the model to always predict 0.7 (above 0.52 threshold)
        t.models["h300_btc_30d"] = MagicMock()
        t.models["h300_btc_30d"].predict = MagicMock(return_value=[0.7])

        # Stub calibrators: return a calibrator mock that passes through proba unchanged
        _cal_mock = MagicMock()
        _cal_mock.calibrate = MagicMock(side_effect=lambda p: p)
        t.calibrators = MagicMock()
        t.calibrators.get = MagicMock(return_value=_cal_mock)

        # Stub _http_session so p_market lookup is skipped
        t._http_session = None

        # Set filter_config for this model to permissive values
        _set_filter_config(t, "h300_btc_30d", {
            "confidence_threshold": 0.52,
            "ev_threshold": 0.0,
            "blackout_hours": [],
        })

        # Run _run_predictions in the event loop
        boundary_ms = 1_735_689_600_000
        asyncio.run(t._run_predictions(boundary_ms, boundary_ms))

        # Expect log_paper_trade called once per CONTRACT_DURATION
        assert t.sqlite_ledger.log_paper_trade.call_count == len(CONTRACT_DURATIONS), (
            f"Expected {len(CONTRACT_DURATIONS)} calls, "
            f"got {t.sqlite_ledger.log_paper_trade.call_count}"
        )
