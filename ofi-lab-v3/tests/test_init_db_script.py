# ofi-lab-v3/tests/test_init_db_script.py
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "init_db.py"


def test_init_db_creates_schema(tmp_path):
    db = tmp_path / "v3.db"
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db),
         "--bootstrap-reason", "test"],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode == 0, res.stderr
    assert db.exists()
    import sqlite3
    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    for t in ("predictions", "paper_trades", "decision_traces",
              "calibration_outcomes", "registry_audit", "policy_audit"):
        assert t in tables
    audit = conn.execute(
        "SELECT generation, reason FROM registry_audit"
    ).fetchall()
    assert audit == [(0, "test")]
    conn.close()


def test_init_db_idempotent(tmp_path):
    db = tmp_path / "v3.db"
    cmd = [sys.executable, str(SCRIPT), "--db", str(db)]
    subprocess.run(cmd, check=True, capture_output=True)
    res = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
