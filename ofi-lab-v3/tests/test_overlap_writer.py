import json

from storage.db import open_database, init_schema
from trading.overlap_writer import OverlapWriter, ModelScore


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
