import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "v2_logs"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_jsonl_to_sqlite.py"


def test_migration_imports_predictions_and_trades(tmp_path):
    db = tmp_path / "v3.db"
    res = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--db", str(db),
         "--source", str(FIXTURES)],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode == 0, res.stderr
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    preds = conn.execute("SELECT * FROM predictions").fetchall()
    assert len(preds) == 1
    p = preds[0]
    assert p["model_name"] == "900s_btc_v3_20260315"
    assert p["resolution_type"] == "evaluation"
    assert p["resolved"] == 1
    assert p["prediction_correct"] == 1
    assert p["price_at_close"] == 60100.0
    trades = conn.execute("SELECT * FROM paper_trades").fetchall()
    assert len(trades) == 1
    assert trades[0]["resolved"] == 1
    assert abs(trades[0]["net_pnl"] - 7.89) < 1e-9


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "v3.db"
    cmd = [sys.executable, str(SCRIPT), "--db", str(db),
           "--source", str(FIXTURES)]
    subprocess.run(cmd, check=True, capture_output=True)
    res = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    import sqlite3
    conn = sqlite3.connect(str(db))
    n_preds = conn.execute(
        "SELECT count(*) FROM predictions"
    ).fetchone()[0]
    assert n_preds == 1


def test_migration_summary_logged(tmp_path):
    db = tmp_path / "v3.db"
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db),
         "--source", str(FIXTURES)],
        capture_output=True, text=True, check=True,
    )
    assert "predictions imported: 1" in res.stdout
    assert "paper_trades imported: 1" in res.stdout
