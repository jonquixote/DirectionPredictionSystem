"""Tests for SIGHUP-driven fleet hot-reload (Phase 6, Part 1).

Covers:
  - _reload_fleet adds newly-registered models
  - _reload_fleet removes deactivated models
  - _reload_fleet updates filter_config for existing models
  - SIGHUP signal sets _sighup_requested flag (no reload executes in handler)
  - POST /reload_fleet API endpoint sets flag and returns {"status": "scheduled"}
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock, patch

import lightgbm as lgb
import numpy as np
import pytest

from storage.db import open_database, init_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dummy_lgb_model(path: Path) -> None:
    """Train a tiny LGB model and save to *path* so lgb.Booster can load it."""
    X = np.random.default_rng(0).random((30, 5)).astype(np.float32)
    y = (X[:, 0] > 0.5).astype(int)
    ds = lgb.Dataset(X, label=y)
    params = {"num_leaves": 4, "n_estimators": 3, "verbose": -1, "objective": "binary"}
    booster = lgb.train(params, ds, num_boost_round=3, valid_sets=[ds],
                        callbacks=[lgb.early_stopping(10, verbose=False),
                                   lgb.log_evaluation(-1)])
    booster.save_model(str(path))
    import json
    with open(path.parent / "feature_names.json", "w") as f:
        json.dump([f"f{i}" for i in range(5)], f)


def _insert_model(conn, name, artifact_path, paper_active=1,
                  lifecycle_state="active",
                  filter_config_json=None,
                  platform_active_json=None):
    conn.execute(
        "INSERT OR REPLACE INTO model_registry "
        "(name, is_baseline, paper_active, lifecycle_state, symbol, "
        "training_horizon_seconds, artifact_path, feature_names_path, "
        "evaluation_windows, filter_config_json, platform_active_json) "
        "VALUES (?,0,?,?,'BTCUSDT',300,?,NULL,'[300,900,1800]',?,?)",
        (
            name,
            paper_active,
            lifecycle_state,
            str(artifact_path),
            filter_config_json or "{}",
            platform_active_json or '{"paper":true,"kalshi":false}',
        ),
    )
    conn.commit()


def _make_minimal_trader(tmp_path, model_names: list[str], db_conn) -> SimpleNamespace:
    """Build the minimal subset of PaperTrader state needed by _reload_fleet.

    Returns a SimpleNamespace that masquerades as a PaperTrader with:
      - .models        dict[name -> lgb.Booster]
      - .feature_names dict[name -> list]
      - ._model_envelopes dict[name -> dict]
      - ._model_meta   dict[name -> dict]
      - ._db_conn      sqlite3.Connection
    and the bound _reload_fleet / _handle_sighup / _reload_model_meta methods.
    """
    models_dir = tmp_path / "models"
    models_dir.mkdir(exist_ok=True)

    models = {}
    feature_names = {}
    model_envelopes = {}
    model_meta = {}

    for name in model_names:
        model_path = models_dir / f"{name}.lgb"
        _make_dummy_lgb_model(model_path)
        models[name] = lgb.Booster(model_file=str(model_path))
        feature_names[name] = [f"f{i}" for i in range(5)]
        model_envelopes[name] = {"model_artifact_hash": "abc", "feature_names_hash": "def"}
        model_meta[name] = {
            "symbol": "BTCUSDT",
            "training_horizon_seconds": 300,
            "feature_version": "v3",
            "train_window_start": "",
            "train_window_end": "",
            "train_cutoff": "",
            "kalshi_dispatch_enabled": False,
            "filter_config": {},
            "lifecycle_state": "active",
            "paper_active": True,
            "live_eligible": False,
        }

    trader = SimpleNamespace(
        models=models,
        feature_names=feature_names,
        _model_envelopes=model_envelopes,
        _model_meta=model_meta,
        _db_conn=db_conn,
        _sighup_requested=False,
    )

    # Bind the actual methods from PaperTrader onto the namespace
    from trading.paper_trader import PaperTrader
    trader._reload_fleet = PaperTrader._reload_fleet.__get__(trader, type(trader))
    trader._handle_sighup = PaperTrader._handle_sighup.__get__(trader, type(trader))
    trader._reload_model_meta = PaperTrader._reload_model_meta.__get__(trader, type(trader))

    return trader


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_conn():
    conn = open_database(":memory:")
    init_schema(conn)
    return conn


@pytest.fixture
def tmp_models(tmp_path):
    """Directory for model artifacts."""
    d = tmp_path / "models"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Test 1: _reload_fleet adds newly-registered models
# ---------------------------------------------------------------------------

def test_reload_fleet_adds_new_models(tmp_path, db_conn, tmp_models):
    """Registry has 3 models; trader starts with 2. Reload should load the 3rd."""
    # Pre-existing models in trader
    m1 = tmp_models / "m1.lgb"
    m2 = tmp_models / "m2.lgb"
    m3 = tmp_models / "m3.lgb"
    _make_dummy_lgb_model(m1)
    _make_dummy_lgb_model(m2)
    _make_dummy_lgb_model(m3)

    _insert_model(db_conn, "m1", m1)
    _insert_model(db_conn, "m2", m2)
    _insert_model(db_conn, "m3", m3)

    trader = _make_minimal_trader(tmp_path, ["m1", "m2"], db_conn)
    assert set(trader.models.keys()) == {"m1", "m2"}

    trader._reload_fleet()

    assert "m3" in trader.models, "m3 should have been added by _reload_fleet"
    assert isinstance(trader.models["m3"], lgb.Booster)
    assert "m3" in trader._model_meta
    assert "m3" in trader._model_envelopes
    assert set(trader.models.keys()) == {"m1", "m2", "m3"}


# ---------------------------------------------------------------------------
# Test 2: _reload_fleet removes deactivated models
# ---------------------------------------------------------------------------

def test_reload_fleet_removes_deactivated_models(tmp_path, db_conn, tmp_models):
    """m3 is in trader.models but paper_active=0 in registry. Should be removed."""
    m1 = tmp_models / "m1.lgb"
    m2 = tmp_models / "m2.lgb"
    m3 = tmp_models / "m3.lgb"
    _make_dummy_lgb_model(m1)
    _make_dummy_lgb_model(m2)
    _make_dummy_lgb_model(m3)

    _insert_model(db_conn, "m1", m1, paper_active=1)
    _insert_model(db_conn, "m2", m2, paper_active=1)
    # m3 deactivated — paper_active=0
    _insert_model(db_conn, "m3", m3, paper_active=0)

    trader = _make_minimal_trader(tmp_path, ["m1", "m2", "m3"], db_conn)
    assert "m3" in trader.models

    trader._reload_fleet()

    assert "m3" not in trader.models, "m3 should be removed (paper_active=0)"
    assert "m3" not in trader._model_meta
    assert "m3" not in trader._model_envelopes
    assert set(trader.models.keys()) == {"m1", "m2"}


def test_reload_fleet_removes_suspended_models(tmp_path, db_conn, tmp_models):
    """m2 has lifecycle_state='suspended' — should be removed even if paper_active=1."""
    m1 = tmp_models / "m1.lgb"
    m2 = tmp_models / "m2.lgb"
    _make_dummy_lgb_model(m1)
    _make_dummy_lgb_model(m2)

    _insert_model(db_conn, "m1", m1, lifecycle_state="active")
    _insert_model(db_conn, "m2", m2, lifecycle_state="suspended")

    trader = _make_minimal_trader(tmp_path, ["m1", "m2"], db_conn)

    trader._reload_fleet()

    assert "m2" not in trader.models
    assert set(trader.models.keys()) == {"m1"}


# ---------------------------------------------------------------------------
# Test 3: _reload_fleet updates filter_config for existing models
# ---------------------------------------------------------------------------

def test_reload_fleet_updates_filter_config(tmp_path, db_conn, tmp_models):
    """Change filter_config_json in DB; reload should update self._model_meta."""
    m1 = tmp_models / "m1.lgb"
    _make_dummy_lgb_model(m1)

    new_filter = json.dumps({"confidence_threshold": 0.58, "ev_threshold": 0.02})
    _insert_model(db_conn, "m1", m1, filter_config_json=new_filter)

    trader = _make_minimal_trader(tmp_path, ["m1"], db_conn)
    # Trader currently has empty filter_config
    assert trader._model_meta["m1"]["filter_config"] == {}

    trader._reload_fleet()

    fc = trader._model_meta["m1"]["filter_config"]
    assert fc.get("confidence_threshold") == 0.58
    assert fc.get("ev_threshold") == 0.02


def test_reload_fleet_updates_lifecycle_flags(tmp_path, db_conn, tmp_models):
    """Change lifecycle_state in DB; reload should update meta in-place."""
    m1 = tmp_models / "m1.lgb"
    _make_dummy_lgb_model(m1)
    _insert_model(db_conn, "m1", m1, lifecycle_state="paper_trading")

    trader = _make_minimal_trader(tmp_path, ["m1"], db_conn)

    trader._reload_fleet()

    assert trader._model_meta["m1"]["lifecycle_state"] == "paper_trading"


# ---------------------------------------------------------------------------
# Test 4: SIGHUP sets _sighup_requested flag (reload does NOT run in handler)
# ---------------------------------------------------------------------------

def test_sighup_sets_flag(tmp_path, db_conn):
    """Sending SIGHUP to the current process sets trader._sighup_requested=True."""
    trader = _make_minimal_trader(tmp_path, [], db_conn)
    assert not trader._sighup_requested

    original_handler = signal.getsignal(signal.SIGHUP)
    try:
        signal.signal(signal.SIGHUP, trader._handle_sighup)
        # Send SIGHUP to self
        os.kill(os.getpid(), signal.SIGHUP)
    finally:
        signal.signal(signal.SIGHUP, original_handler)

    assert trader._sighup_requested is True, (
        "_sighup_requested should be True after SIGHUP"
    )


def test_sighup_handler_does_not_call_reload_fleet(tmp_path, db_conn):
    """_handle_sighup must only set the flag — never call _reload_fleet directly."""
    trader = _make_minimal_trader(tmp_path, [], db_conn)

    reload_called = []
    original_reload = trader._reload_fleet

    def _spy(*a, **kw):
        reload_called.append(True)
        return original_reload(*a, **kw)

    trader._reload_fleet = _spy

    original_handler = signal.getsignal(signal.SIGHUP)
    try:
        signal.signal(signal.SIGHUP, trader._handle_sighup)
        os.kill(os.getpid(), signal.SIGHUP)
    finally:
        signal.signal(signal.SIGHUP, original_handler)

    assert reload_called == [], "_reload_fleet should NOT be called inside the signal handler"
    assert trader._sighup_requested is True


# ---------------------------------------------------------------------------
# Test 5: POST /reload_fleet API endpoint sets flag + returns {"status": "scheduled"}
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run an async coro in a fresh event loop. Matches the pattern in
    test_kalshi_dispatcher.py / test_resolution_checker.py for Python 3.13
    compatibility (asyncio.run() teardown leaves no current loop)."""
    import asyncio
    try:
        prev = asyncio.get_event_loop()
    except RuntimeError:
        prev = None
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(prev)


def test_post_reload_fleet_endpoint_sets_flag(tmp_path, db_conn):
    """POST /reload_fleet sets trader._sighup_requested=True and returns scheduled."""
    from aiohttp.test_utils import TestClient, TestServer

    trader = _make_minimal_trader(tmp_path, [], db_conn)
    trader._sighup_requested = False

    from trading.api_server import create_api_app

    async def _scenario():
        # Patch DASHBOARD_PASSWORD to None so auth is bypassed in test
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DASHBOARD_PASSWORD", None)
            app = create_api_app(trader)
            async with TestClient(TestServer(app)) as client:
                resp = await client.post("/reload_fleet")
                assert resp.status == 200
                body = await resp.json()
                return body

    body = _run_async(_scenario())
    assert body == {"status": "scheduled"}
    assert trader._sighup_requested is True


def test_post_reload_fleet_endpoint_requires_auth(tmp_path, db_conn):
    """POST /reload_fleet returns 401 when DASHBOARD_PASSWORD is set and no token given."""
    from aiohttp.test_utils import TestClient, TestServer

    trader = _make_minimal_trader(tmp_path, [], db_conn)

    from trading.api_server import create_api_app

    async def _scenario():
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "secret"}):
            app = create_api_app(trader)
            async with TestClient(TestServer(app)) as client:
                resp = await client.post("/reload_fleet")
                assert resp.status == 401
            app2 = create_api_app(trader)
            async with TestClient(TestServer(app2)) as client2:
                resp2 = await client2.post(
                    "/reload_fleet",
                    headers={"Authorization": "Bearer secret"},
                )
                assert resp2.status == 200

    _run_async(_scenario())


# ---------------------------------------------------------------------------
# Test 6: Edge case — model artifact missing on disk
# ---------------------------------------------------------------------------

def test_reload_fleet_skips_model_with_missing_artifact(tmp_path, db_conn):
    """When a new model's artifact file doesn't exist, skip it gracefully."""
    missing_path = tmp_path / "ghost.lgb"  # intentionally not created

    _insert_model(db_conn, "ghost", missing_path, paper_active=1)

    trader = _make_minimal_trader(tmp_path, [], db_conn)

    trader._reload_fleet()  # Must not raise

    assert "ghost" not in trader.models, "Model with missing artifact should be skipped"


# ---------------------------------------------------------------------------
# Test 7: Idempotency — reload with no registry changes has no effect
# ---------------------------------------------------------------------------

def test_reload_fleet_idempotent(tmp_path, db_conn, tmp_models):
    """Calling _reload_fleet twice in a row with no DB changes is a no-op."""
    m1 = tmp_models / "m1.lgb"
    _make_dummy_lgb_model(m1)
    _insert_model(db_conn, "m1", m1)

    trader = _make_minimal_trader(tmp_path, ["m1"], db_conn)
    original_booster = trader.models["m1"]

    trader._reload_fleet()
    trader._reload_fleet()

    # Same booster object (not reloaded unnecessarily)
    assert trader.models["m1"] is original_booster
    assert len(trader.models) == 1
