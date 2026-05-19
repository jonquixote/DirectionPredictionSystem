import json

import pytest

from storage.db import open_database, init_schema
from trading.overlap_writer import OverlapWriter, ModelScore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Existing tests
# ---------------------------------------------------------------------------

def test_consensus_when_all_models_agree(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    w = OverlapWriter(conn)
    w.record_boundary(
        ts_contract_open_ms=1_700_000_000_000,
        symbol="BTCUSDT",
        market_window_seconds=900,
        registry_load_generation=0,
        scores=[
            ModelScore("900s_btc_v3_20260315", "up", 0.55, weight=0.6),
            ModelScore("60s_btc_v3_20260315", "up", 0.52, weight=0.4),
        ],
    )
    row = conn.execute("SELECT * FROM model_overlap").fetchone()
    assert row["consensus"] == 1
    assert row["consensus_direction"] == "up"
    # Weighted confidence: 0.55 * 0.6 + 0.52 * 0.4 = 0.538
    assert abs(row["weighted_confidence"] - 0.538) < 1e-9


def test_no_consensus_when_models_disagree(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    w = OverlapWriter(conn)
    w.record_boundary(
        ts_contract_open_ms=1_700_000_000_000,
        symbol="BTCUSDT",
        market_window_seconds=900,
        registry_load_generation=0,
        scores=[
            ModelScore("900s_btc_v3_20260315", "up", 0.55, weight=0.5),
            ModelScore("60s_btc_v3_20260315", "down", 0.52, weight=0.5),
        ],
    )
    row = conn.execute("SELECT * FROM model_overlap").fetchone()
    assert row["consensus"] == 0
    assert row["consensus_direction"] is None


def test_models_scored_serialized_as_json_array(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    w = OverlapWriter(conn)
    w.record_boundary(
        ts_contract_open_ms=1_700_000_000_000,
        symbol="BTCUSDT",
        market_window_seconds=900,
        registry_load_generation=0,
        scores=[
            ModelScore("a", "up", 0.55, weight=1.0),
        ],
    )
    row = conn.execute("SELECT models_scored_json, directions_json,"
                       " confidences_json FROM model_overlap").fetchone()
    assert json.loads(row["models_scored_json"]) == ["a"]
    assert json.loads(row["directions_json"]) == {"a": "up"}
    assert json.loads(row["confidences_json"]) == {"a": 0.55}


# ---------------------------------------------------------------------------
# B2 regression tests — ModelScore field names & boundary-loop accumulation
# ---------------------------------------------------------------------------

def test_record_overlap_writes_row(tmp_path):
    """3 models all scoring for the same boundary → exactly 1 row written."""
    conn = _make_conn(tmp_path)
    w = OverlapWriter(conn)
    scores = [
        ModelScore("model_a", "up", 0.60, weight=1.0),
        ModelScore("model_b", "up", 0.55, weight=1.0),
        ModelScore("model_c", "up", 0.50, weight=1.0),
    ]
    w.record_boundary(
        ts_contract_open_ms=1_700_000_000_000,
        symbol="BTCUSDT",
        market_window_seconds=300,
        registry_load_generation=1,
        scores=scores,
    )
    rows = conn.execute("SELECT * FROM model_overlap").fetchall()
    assert len(rows) == 1, "Expected exactly 1 model_overlap row"
    row = rows[0]
    assert row["symbol"] == "BTCUSDT"
    assert row["market_window_seconds"] == 300
    assert json.loads(row["models_scored_json"]) == ["model_a", "model_b", "model_c"]


def test_record_overlap_consensus_flag(tmp_path):
    """All models agree → consensus=1; split vote → consensus=0."""
    conn = _make_conn(tmp_path)
    w = OverlapWriter(conn)

    # All agree → consensus=1
    w.record_boundary(
        ts_contract_open_ms=1_700_000_000_000,
        symbol="BTCUSDT",
        market_window_seconds=900,
        registry_load_generation=1,
        scores=[
            ModelScore("m1", "up", 0.60),
            ModelScore("m2", "up", 0.58),
            ModelScore("m3", "up", 0.55),
        ],
    )
    row = conn.execute(
        "SELECT consensus, consensus_direction FROM model_overlap"
        " WHERE ts_contract_open_ms=1700000000000"
    ).fetchone()
    assert row["consensus"] == 1
    assert row["consensus_direction"] == "up"

    # Split → consensus=0 (different boundary to avoid PK collision)
    w.record_boundary(
        ts_contract_open_ms=1_700_000_300_000,
        symbol="BTCUSDT",
        market_window_seconds=900,
        registry_load_generation=1,
        scores=[
            ModelScore("m1", "up", 0.60),
            ModelScore("m2", "down", 0.58),
        ],
    )
    row2 = conn.execute(
        "SELECT consensus, consensus_direction FROM model_overlap"
        " WHERE ts_contract_open_ms=1700000300000"
    ).fetchone()
    assert row2["consensus"] == 0
    assert row2["consensus_direction"] is None


def test_record_overlap_weighted_confidence(tmp_path):
    """Weighted confidence = sum(conf*w) / sum(w)."""
    conn = _make_conn(tmp_path)
    w = OverlapWriter(conn)
    w.record_boundary(
        ts_contract_open_ms=1_700_000_000_000,
        symbol="ETHUSDT",
        market_window_seconds=300,
        registry_load_generation=0,
        scores=[
            ModelScore("m1", "up", 0.70, weight=2.0),
            ModelScore("m2", "up", 0.60, weight=1.0),
        ],
    )
    row = conn.execute("SELECT weighted_confidence FROM model_overlap").fetchone()
    # (0.70*2 + 0.60*1) / 3 = 2.00/3 ≈ 0.6667
    expected = (0.70 * 2 + 0.60 * 1) / 3
    assert abs(row["weighted_confidence"] - expected) < 1e-9


def test_modelscore_field_names_are_correct():
    """ModelScore must accept model_name/direction/calibrated_confidence — not proba/ev."""
    # This would raise TypeError if wrong field names were used (the B2 root cause)
    score = ModelScore(
        model_name="test_model",
        direction="up",
        calibrated_confidence=0.62,
    )
    assert score.model_name == "test_model"
    assert score.direction == "up"
    assert score.calibrated_confidence == 0.62
    assert score.weight == 1.0  # default


def test_modelscore_wrong_fields_raise():
    """Instantiating ModelScore with proba= or ev= must raise TypeError (not silently succeed)."""
    with pytest.raises(TypeError):
        ModelScore(proba=0.6, direction="up", ev=None)  # type: ignore[call-arg]
