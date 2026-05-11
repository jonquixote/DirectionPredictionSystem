"""Test lifecycle FSM periodic call (Task C6)."""
import pytest


def make_trader(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        testnet=True,
        confidence_threshold=0.55,
    )


def test_lifecycle_interval_configured(tmp_path, monkeypatch, tiny_model_path):
    """Verify _lifecycle_interval is initialized to 16."""
    trader = make_trader(tmp_path, monkeypatch, tiny_model_path)
    assert hasattr(trader, "_lifecycle_interval"), "PaperTrader missing _lifecycle_interval"
    assert trader._lifecycle_interval == 16, f"_lifecycle_interval should be 16, got {trader._lifecycle_interval}"


def test_baseline_protection_in_suspend_logic(tmp_path, monkeypatch, tiny_model_path):
    """Verify RegistryState.suspend() respects baseline protection."""
    from storage.db import open_database, init_schema
    from storage.registry_state import RegistryState

    db = open_database(str(tmp_path / "v3.db"))
    init_schema(db)
    registry = RegistryState(db)
    registry.bootstrap_if_empty(reason="test")

    # Ensure baseline model exists in registry
    db.execute(
        "INSERT INTO model_registry (name, is_baseline, paper_active, "
        "live_eligible, lifecycle_state) VALUES (?, ?, ?, ?, ?)",
        ("h300_btc", 1, 1, 1, "active"),
    )
    db.commit()

    # Try to suspend the baseline
    registry.suspend(model="h300_btc", reason="test_suspension")

    # Check baseline is still active (suspend should skip baseline)
    row = db.execute(
        "SELECT lifecycle_state FROM model_registry WHERE name='h300_btc'"
    ).fetchone()

    assert row is not None
    assert row["lifecycle_state"] == "active", "baseline must never suspend"
