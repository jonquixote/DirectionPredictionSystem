# ofi-lab-v3/tests/test_policy_snapshot.py
from storage.db import open_database, init_schema
from storage.policy_snapshot import PolicySnapshot, policy_canonical_form


SAMPLE = {
    "confidence_threshold": 0.55,
    "kelly_fraction": 0.25,
    "ev_threshold": 0.001,
    "circuit_breaker_drawdown": 50.0,
    "clob_divergence_min_edge": 0.02,
    "per_symbol_confidence": {"BTCUSDT": 0.55, "SOLUSDT": 0.58},
    "blackout_hours_utc": [21, 22, 23, 0, 1, 2, 3],
}


def test_canonical_form_round_trips():
    canon = policy_canonical_form(SAMPLE)
    assert canon["per_symbol_confidence"] == {"BTCUSDT": 0.55, "SOLUSDT": 0.58}
    # Lists of times/hours preserved in source order
    assert canon["blackout_hours_utc"] == [21, 22, 23, 0, 1, 2, 3]


def test_first_capture_writes_version_zero(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    ps = PolicySnapshot(conn)
    version, hashv = ps.capture(SAMPLE, initiated_by="bootstrap")
    assert version == 0
    assert len(hashv) == 64
    rows = conn.execute("SELECT * FROM policy_audit").fetchall()
    assert len(rows) == 1
    assert rows[0]["decision_policy_version"] == 0


def test_repeated_identical_capture_does_not_increment(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    ps = PolicySnapshot(conn)
    v1, h1 = ps.capture(SAMPLE, initiated_by="boot")
    v2, h2 = ps.capture(SAMPLE, initiated_by="boot")
    assert v1 == v2 == 0
    assert h1 == h2


def test_changed_value_increments_version(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    ps = PolicySnapshot(conn)
    v1, h1 = ps.capture(SAMPLE, initiated_by="boot")
    altered = dict(SAMPLE, confidence_threshold=0.56)
    v2, h2 = ps.capture(altered, initiated_by="api")
    assert v2 == v1 + 1
    assert h2 != h1


def test_current_returns_latest(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    ps = PolicySnapshot(conn)
    ps.capture(SAMPLE)
    altered = dict(SAMPLE, kelly_fraction=0.5)
    v, h = ps.capture(altered)
    assert ps.current() == (v, h)
