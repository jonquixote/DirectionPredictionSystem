"""Test paper-active vs live-status independence (Task C7)."""
import pytest


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    from storage.db import open_database, init_schema
    from storage.registry_state import RegistryState
    db_conn = open_database(str(tmp_path / "v3.db"))
    init_schema(db_conn)
    reg = RegistryState(db_conn)
    reg.bootstrap_if_empty(reason="test")

    # Create test model entries
    db_conn.execute(
        "INSERT INTO model_registry (name, is_baseline, paper_active, live_eligible) "
        "VALUES (?, ?, ?, ?)",
        ("h60_xrp", 0, 0, 0),
    )
    db_conn.commit()

    return reg


def test_disabling_live_does_not_disable_paper(registry):
    """Verify disabling live does not auto-disable paper_active."""
    registry.set_live(model="h60_xrp", enabled=True, by="op")
    registry.set_paper(model="h60_xrp", enabled=True, by="op")
    registry.set_live(model="h60_xrp", enabled=False, by="op")

    row = registry.get("h60_xrp")
    assert bool(row["paper_active"]) is True, "disabling live should not disable paper"
    assert bool(row["live_eligible"]) is False


def test_disabling_paper_does_not_disable_live(registry):
    """Verify disabling paper does not auto-disable live_eligible."""
    registry.set_paper(model="h60_xrp", enabled=True, by="op")
    registry.set_live(model="h60_xrp", enabled=True, by="op")
    registry.set_paper(model="h60_xrp", enabled=False, by="op")

    row = registry.get("h60_xrp")
    assert bool(row["live_eligible"]) is True, "disabling paper should not disable live"
    assert bool(row["paper_active"]) is False


def test_lifecycle_suspend_disables_both(registry):
    """Verify lifecycle suspend disables both paper and live."""
    registry.set_paper(model="h60_xrp", enabled=True, by="op")
    registry.set_live(model="h60_xrp", enabled=True, by="op")
    registry.suspend(model="h60_xrp", reason="ev_below_threshold")

    row = registry.get("h60_xrp")
    assert bool(row["paper_active"]) is False, "suspend should disable paper"
    assert bool(row["live_eligible"]) is False, "suspend should disable live"
