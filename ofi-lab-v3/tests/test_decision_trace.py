# ofi-lab-v3/tests/test_decision_trace.py
import json

from storage.db import open_database, init_schema
from storage.decision_trace import DecisionTraceWriter, FilterEval


def setup_db(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    return conn


def test_write_emits_one_row_with_all_filters(tmp_path):
    conn = setup_db(tmp_path)
    w = DecisionTraceWriter(conn)
    w.write(
        prediction_id="pid_900_n",
        filters=[
            FilterEval("confidence", threshold=0.55, input_value=0.54, passed=False),
            FilterEval("ev_threshold", threshold=0.0, input_value=-0.001, passed=False),
        ],
        kelly_raw=0.1, kelly_capped=0.05, bankroll_used=200.0,
        per_trade_cap_usdc=5.0,
        fee_model="polymarket", fee_amount=0.018,
        platform_gate={"kalshi_allow_list": False},
        warmup=False, consensus_data=None,
        policy_config_hash="c"*64, calibration_map_hash="d"*64,
        registry_load_generation=0,
    )
    row = conn.execute("SELECT * FROM decision_traces").fetchone()
    parsed = json.loads(row["filters_json"])
    assert {f["name"] for f in parsed} == {"confidence", "ev_threshold"}
    confidence = next(f for f in parsed if f["name"] == "confidence")
    assert confidence["threshold"] == 0.55
    assert confidence["input_value"] == 0.54
    assert confidence["passed"] == 0
    assert row["fee_model"] == "polymarket"
    assert row["kelly_raw"] == 0.1
    assert row["kelly_capped"] == 0.05


def test_write_serializes_consensus_payload(tmp_path):
    conn = setup_db(tmp_path)
    w = DecisionTraceWriter(conn)
    w.write(
        prediction_id="pid_900_n", filters=[],
        kelly_raw=None, kelly_capped=None, bankroll_used=None,
        per_trade_cap_usdc=None, fee_model="kalshi_taker", fee_amount=None,
        platform_gate=None, warmup=True,
        consensus_data={"models": ["a", "b"], "agreement": False},
        policy_config_hash="c"*64, calibration_map_hash="d"*64,
        registry_load_generation=2,
    )
    row = conn.execute("SELECT * FROM decision_traces").fetchone()
    assert json.loads(row["consensus_data_json"])["agreement"] is False
    assert row["registry_load_generation"] == 2
