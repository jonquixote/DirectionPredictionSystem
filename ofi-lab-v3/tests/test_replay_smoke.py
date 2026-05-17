# ofi-lab-v3/tests/test_replay_smoke.py
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_replay_writes_well_formed_rows(tmp_path, synthetic_minute_bars_path,
                                          tiny_model_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "replay_v2_features.py"
    db = tmp_path / "v3.db"
    res = subprocess.run(
        [sys.executable, str(script),
        "--features-parquet", synthetic_minute_bars_path,
        "--model-path", tiny_model_path,
        "--db", str(db),
        "--max-rows", "10"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    n = conn.execute("SELECT count(*) FROM predictions").fetchone()[0]
    # Alignment gate: only windows that evenly divide the boundary emit rows.
    # 10 × 5-min boundaries: 2×3 + 2×2 + 6×1 = 16 evaluation rows
    assert n == 16
    natives = conn.execute(
        "SELECT count(*) FROM predictions WHERE resolution_type='native'"
    ).fetchone()[0]
    assert natives == 0, "native rows should not be emitted post-refactor"
    evaluations = conn.execute(
        "SELECT count(*) FROM predictions WHERE resolution_type='evaluation'"
    ).fetchone()[0]
    assert evaluations == 16
    rows = conn.execute(
        "SELECT model_artifact_hash, feature_names_hash,"
        " policy_config_hash, calibration_map_hash FROM predictions"
    ).fetchall()
    for r in rows:
        assert len(r["model_artifact_hash"]) == 64
        assert len(r["feature_names_hash"]) == 64
        assert len(r["policy_config_hash"]) == 64
        assert len(r["calibration_map_hash"]) == 64
