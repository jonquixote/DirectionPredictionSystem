# ofi-lab-v3/tests/test_registry_state.py
from storage.db import open_database, init_schema
from storage.registry_state import RegistryState


def test_first_bootstrap_writes_generation_zero(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    rs = RegistryState(conn)
    rs.bootstrap_if_empty(reason="initial v3 boot")
    assert rs.current_generation() == 0
    rows = conn.execute("SELECT * FROM registry_audit").fetchall()
    assert len(rows) == 1
    assert rows[0]["generation"] == 0
    assert rows[0]["reason"] == "initial v3 boot"


def test_increment_persists_new_generation(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    rs = RegistryState(conn)
    rs.bootstrap_if_empty()
    rs.increment(reason="hot reload", detail={"models_added": ["x"]})
    assert rs.current_generation() == 1
    rs.increment(reason="hot reload")
    assert rs.current_generation() == 2
    rows = conn.execute(
        "SELECT generation, reason FROM registry_audit ORDER BY generation"
    ).fetchall()
    assert [r["generation"] for r in rows] == [0, 1, 2]


def test_reload_after_restart_recovers_current_generation(tmp_path):
    db = str(tmp_path / "v3.db")
    conn = open_database(db)
    init_schema(conn)
    rs = RegistryState(conn)
    rs.bootstrap_if_empty()
    rs.increment(reason="r1")
    rs.increment(reason="r2")
    conn.close()

    conn2 = open_database(db)
    rs2 = RegistryState(conn2)
    assert rs2.current_generation() == 2
