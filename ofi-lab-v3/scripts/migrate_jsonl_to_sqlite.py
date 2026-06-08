"""Migrate v2 JSONL ledger files into v3 SQLite.

One-shot importer. Idempotent — uses pre-insert SELECT checks.
Provenance fields are backfilled per Plan A T25 rules.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage.db import open_database, init_schema, DEFAULT_DB_PATH
from storage.provenance import (
    ProvenanceEnvelope, sha256_canonical_json, feature_names_hash,
    calibration_map_hash,
)
from storage.registry_state import RegistryState
from trading.ledger import (
    read_records, merge_predictions_with_resolutions,
    merge_trades_with_resolutions,
)
from feature_engineering.feature_contract import FEATURE_COLS

LEGACY_NAME_MAP = {
    "h300":   "900s_btc_v3_20260315",
    "h60":    "60s_btc_v3_20260315",
    "h60_v3": "60s_btc_v3d_20260315",
}

LEGACY_FEATURE_VERSION = {
    "h300": "v3", "h60": "v3", "h60_v3": "v3d",
}

LEGACY_HORIZON_SECONDS = {"h300": 900, "h60": 60, "h60_v3": 60}


def _legacy_envelope(legacy_name, calibration_path, policy_path):
    feat_hash = feature_names_hash(FEATURE_COLS)
    if Path(calibration_path).exists():
        cal = json.loads(Path(calibration_path).read_text())
        cal_hash = calibration_map_hash(cal)
    else:
        cal_hash = "0" * 64
    if Path(policy_path).exists():
        snap = json.loads(Path(policy_path).read_text())
        policy_hash = sha256_canonical_json(snap)
    else:
        policy_hash = "0" * 64
    artifact_hash = "v2legacy" + "0" * (64 - len("v2legacy"))
    return ProvenanceEnvelope(
        model_name=LEGACY_NAME_MAP[legacy_name],
        model_artifact_hash=artifact_hash,
        feature_names_hash=feat_hash,
        feature_version=LEGACY_FEATURE_VERSION[legacy_name],
        training_horizon_seconds=LEGACY_HORIZON_SECONDS[legacy_name],
        train_window_start="2025-04-01",
        train_window_end="2026-03-15",
        train_cutoff="2026-03-15",
        registry_load_generation=0,
        policy_config_hash=policy_hash,
        decision_policy_version=0,
        calibration_map_hash=cal_hash,
        platform="paper",
    )


def _import_predictions(conn, source_dir, legacy_name, env, summary):
    path = source_dir / f"predictions_{legacy_name}.jsonl"
    if not path.exists():
        return
    merged = merge_predictions_with_resolutions(read_records(path))
    n = 0
    for rec in merged:
        pid = rec["prediction_id"]
        existing = conn.execute(
            "SELECT 1 FROM predictions WHERE prediction_id = ?", (pid,)
        ).fetchone()
        if existing:
            continue
        ts_open = int(rec["ts_contract_open_ms"])
        ts_resolve = ts_open + 900_000  # legacy was always 900s
        conn.execute(
            "INSERT INTO predictions ("
            " prediction_id, model_name, model_artifact_hash, feature_names_hash,"
            " feature_version, training_horizon_seconds, train_window_start,"
            " train_window_end, train_cutoff, registry_load_generation,"
            " policy_config_hash, decision_policy_version, calibration_map_hash,"
            " symbol, market_window_seconds, resolution_type,"
            " ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms,"
            " pred_proba_raw, pred_proba_calibrated, pred_direction,"
            " above_threshold, warmup, platform,"
            " price_at_open, price_at_close, contract_result,"
            " prediction_correct, resolved, ts_resolved_ms"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                pid, env.model_name, env.model_artifact_hash, env.feature_names_hash,
                env.feature_version, env.training_horizon_seconds,
                env.train_window_start, env.train_window_end, env.train_cutoff,
                env.registry_load_generation, env.policy_config_hash,
                env.decision_policy_version, env.calibration_map_hash,
        rec.get("symbol", "BTCUSDT"), 900, "evaluation",
        int(rec["ts_model_ran_ms"]), ts_open, ts_resolve,
        float(rec["pred_proba"]), float(rec["pred_proba"]),
        rec["pred_direction"], int(rec.get("above_threshold", 0)),
                0, "paper",
                rec.get("price_at_open"), rec.get("price_at_close"),
                rec.get("contract_result"),
                int(rec["prediction_correct"]) if rec.get("prediction_correct") is not None else None,
                1 if rec.get("contract_result") is not None else 0,
                rec.get("ts_contract_close_ms"),
            ),
        )
        n += 1
    summary["predictions"] += n


def _import_paper_trades(conn, source_dir, legacy_name, env, summary):
    path = source_dir / f"paper_trades_{legacy_name}.jsonl"
    if not path.exists():
        return
    merged = merge_trades_with_resolutions(read_records(path))
    n = 0
    for rec in merged:
        tid = rec["trade_id"]
        existing = conn.execute(
            "SELECT 1 FROM paper_trades WHERE trade_id = ?", (tid,)
        ).fetchone()
        if existing:
            continue
        ts_open = int(rec["ts_contract_open_ms"])
        ts_resolve = ts_open + 900_000
        conn.execute(
            "INSERT INTO paper_trades ("
            " trade_id, prediction_id,"
            " model_name, model_artifact_hash, policy_config_hash,"
            " decision_policy_version, calibration_map_hash,"
            " registry_load_generation, feature_version,"
            " training_horizon_seconds,"
            " symbol, market_window_seconds, resolution_type,"
            " ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms,"
            " pred_proba_raw, pred_proba_calibrated, pred_direction,"
            " confidence_threshold_used, simulated_stake_usdc,"
            " warmup, platform,"
            " decision_outcome,"
            " price_at_open, price_at_close, contract_result,"
            " prediction_correct, gross_pnl, fee_paid, net_pnl,"
            " trade_result, pnl_method, resolved, ts_resolved_ms"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tid, rec["prediction_id"],
                env.model_name, env.model_artifact_hash,
                env.policy_config_hash, env.decision_policy_version,
                env.calibration_map_hash, env.registry_load_generation,
                env.feature_version, env.training_horizon_seconds,
        rec.get("symbol", "BTCUSDT"), 900, "evaluation",
        int(rec["ts_model_ran_ms"]), ts_open, ts_resolve,
        float(rec["pred_proba"]), float(rec["pred_proba"]),
        rec["pred_direction"],
                float(rec.get("confidence_threshold", 0.52)),
                float(rec.get("simulated_stake_usdc", 10.0)),
                0, "paper",
                "executed",
                rec.get("price_at_open"), rec.get("price_at_close"),
                rec.get("contract_result"),
                int(rec["prediction_correct"]) if rec.get("prediction_correct") is not None else None,
                rec.get("gross_pnl"), rec.get("fee_paid"), rec.get("net_pnl"),
                rec.get("trade_result"), rec.get("pnl_method"),
                1 if rec.get("contract_result") is not None else 0,
                rec.get("ts_contract_close_ms"),
            ),
        )
        n += 1
    summary["paper_trades"] += n


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=DEFAULT_DB_PATH)
    p.add_argument("--source", required=True,
                   help="Directory containing v2 JSONL files.")
    p.add_argument("--calibration-path", default="/data/calibration.json")
    p.add_argument("--policy-path", default="/data/policy_v2_final.json")
    args = p.parse_args()

    conn = open_database(args.db)
    init_schema(conn)
    RegistryState(conn).bootstrap_if_empty(reason="migration import")
    src = Path(args.source)
    summary = {"predictions": 0, "paper_trades": 0}
    for legacy in ("h300", "h60", "h60_v3"):
        env = _legacy_envelope(legacy, args.calibration_path, args.policy_path)
        _import_predictions(conn, src, legacy, env, summary)
        _import_paper_trades(conn, src, legacy, env, summary)
    print(f"predictions imported: {summary['predictions']}")
    print(f"paper_trades imported: {summary['paper_trades']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
