import json
from pathlib import Path

import pytest

from storage.model_registry import (
    ModelRegistry, ModelEntry, BaselineRemovalError,
)


SAMPLE_REGISTRY = {
    "models": {
        "900s_btc_v3_20260315": {
            "path": "/data/models/run_20260315/model.lgb",
            "feature_names_path": "/data/models/run_20260315/feature_names.json",
            "horizon_seconds": 900,
            "symbol": "BTCUSDT",
            "feature_version": "v3",
            "train_window_start": "2025-04-01",
            "train_window_end": "2026-03-15",
            "train_cutoff": "2026-03-15",
            "is_baseline": True,
            "paper_trading_enabled": True,
            "kalshi_live_enabled": True,
            "lifecycle_state": "live_active",
            "min_markets_for_kalshi": 200,
            "min_days_for_kalshi": 14,
        },
        "60s_btc_v3_20260315": {
            "path": "/data/models/run_20260316/model.lgb",
            "feature_names_path": "/data/models/run_20260316/feature_names.json",
            "horizon_seconds": 60,
            "symbol": "BTCUSDT",
            "feature_version": "v3",
            "train_window_start": "2025-04-01",
            "train_window_end": "2026-03-15",
            "train_cutoff": "2026-03-15",
            "is_baseline": False,
            "paper_trading_enabled": False,
            "kalshi_live_enabled": False,
            "lifecycle_state": "prediction_only",
            "min_markets_for_kalshi": 200,
            "min_days_for_kalshi": 14,
        },
    }
}


def _write_registry(tmp_path, body):
    p = tmp_path / "model_registry.json"
    p.write_text(json.dumps(body))
    return p


def test_load_returns_entries_keyed_by_name(tmp_path):
    p = _write_registry(tmp_path, SAMPLE_REGISTRY)
    reg = ModelRegistry(str(p))
    reg.load()
    names = sorted(reg.entries().keys())
    assert names == ["60s_btc_v3_20260315", "900s_btc_v3_20260315"]
    e = reg.entries()["900s_btc_v3_20260315"]
    assert isinstance(e, ModelEntry)
    assert e.is_baseline is True
    assert e.horizon_seconds == 900


def test_active_models_filters_paper_disabled(tmp_path):
    p = _write_registry(tmp_path, SAMPLE_REGISTRY)
    reg = ModelRegistry(str(p))
    reg.load()
    active = reg.active_paper_models()
    assert list(active.keys()) == ["900s_btc_v3_20260315"]


def test_baseline_lookup(tmp_path):
    p = _write_registry(tmp_path, SAMPLE_REGISTRY)
    reg = ModelRegistry(str(p))
    reg.load()
    assert reg.baseline_name() == "900s_btc_v3_20260315"


def test_missing_baseline_raises(tmp_path):
    body = {"models": {"x": dict(SAMPLE_REGISTRY["models"]["60s_btc_v3_20260315"])}}
    p = _write_registry(tmp_path, body)
    reg = ModelRegistry(str(p))
    with pytest.raises(BaselineRemovalError):
        reg.load()


def test_lifecycle_state_default_is_prediction_only(tmp_path):
    body = json.loads(json.dumps(SAMPLE_REGISTRY))
    body["models"]["60s_btc_v3_20260315"].pop("lifecycle_state", None)
    p = _write_registry(tmp_path, body)
    reg = ModelRegistry(str(p))
    reg.load()
    e = reg.entries()["60s_btc_v3_20260315"]
    assert e.lifecycle_state == "prediction_only"


# --- Task 3: Hot reload tests ---

def test_reload_increments_generation_and_appends_audit(tmp_path):
    from storage.db import open_database, init_schema
    from storage.registry_state import RegistryState

    p = _write_registry(tmp_path, SAMPLE_REGISTRY)
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    rs = RegistryState(conn)
    rs.bootstrap_if_empty()

    reg = ModelRegistry(str(p))
    reg.load()
    gen0 = rs.current_generation()
    new_gen = reg.reload(rs, reason="test reload")
    assert new_gen == gen0 + 1
    rows = conn.execute(
        "SELECT generation, reason FROM registry_audit ORDER BY generation"
    ).fetchall()
    assert rows[-1]["generation"] == new_gen
    assert rows[-1]["reason"] == "test reload"


def test_reload_with_missing_baseline_does_not_increment(tmp_path):
    from storage.db import open_database, init_schema
    from storage.registry_state import RegistryState

    p = _write_registry(tmp_path, SAMPLE_REGISTRY)
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    rs = RegistryState(conn)
    rs.bootstrap_if_empty()

    reg = ModelRegistry(str(p))
    reg.load()
    gen0 = rs.current_generation()

    # Rewrite registry without baseline
    bad = {"models": {"x": dict(SAMPLE_REGISTRY["models"]["60s_btc_v3_20260315"])}}
    p.write_text(json.dumps(bad))

    with pytest.raises(BaselineRemovalError):
        reg.reload(rs, reason="bad")

    # Generation must NOT have advanced
    assert rs.current_generation() == gen0
    # In-memory entries unchanged (still hold the previous baseline)
    assert "900s_btc_v3_20260315" in reg.entries()


# --- Task 4: Pending-queue cleanup on reload ---

def test_reload_prunes_pending_queue_for_removed_models(tmp_path):
    from storage.db import open_database, init_schema
    from storage.registry_state import RegistryState
    from storage.pending_queue import PendingResolutionQueue, PendingEntry

    p = _write_registry(tmp_path, SAMPLE_REGISTRY)
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    rs = RegistryState(conn)
    rs.bootstrap_if_empty()

    queue_path = tmp_path / "pending.json"
    queue = PendingResolutionQueue(queue_path)
    # Enqueue entries for both models
    for name in ("900s_btc_v3_20260315", "60s_btc_v3_20260315"):
        queue.enqueue(PendingEntry(
            prediction_id=f"{name}_p", boundary_ms=1_000_000,
            model_name=name, symbol="BTCUSDT", market_window_seconds=900,
            registry_load_generation=0, ts_resolve_at_ms=1_900_000,
            resolution_type="native", price_at_open=60_000.0,
        ))
    # Old generation entry from a now-removed model
    queue.enqueue(PendingEntry(
        prediction_id="ghost_p", boundary_ms=1_000_000,
        model_name="ghost_model", symbol="BTCUSDT", market_window_seconds=900,
        registry_load_generation=0, ts_resolve_at_ms=1_900_000,
        resolution_type="native", price_at_open=60_000.0,
    ))
    queue.persist()

    reg = ModelRegistry(str(p))
    reg.load()
    pruned, new_gen = reg.reload_with_queue_cleanup(
        rs, queue, reason="hot reload",
    )
    assert pruned == 1  # only ghost_model removed
    assert new_gen == 1
    remaining = sorted(e.prediction_id for e in queue.iter_all())
    assert remaining == ["60s_btc_v3_20260315_p", "900s_btc_v3_20260315_p"]
