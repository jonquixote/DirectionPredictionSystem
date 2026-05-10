# ofi-lab-v3/scripts/replay_v2_features.py
"""Feed minute-bar feature vectors directly into v3 PaperTrader scoring.

Skips the WebSocket layer. Deterministic offline harness for plan-A
correctness checks.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Parse args early to get db path for env setup
parser = argparse.ArgumentParser()
parser.add_argument("--features-parquet", required=True)
parser.add_argument("--model-path", required=True)
parser.add_argument("--db", default="/tmp/v3_replay.db")
parser.add_argument("--max-rows", type=int, default=60)
early_args, _ = parser.parse_known_args()
os.environ["STORAGE_DB_PATH"] = early_args.db

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import polars as pl

from trading.paper_trader import PaperTrader
from storage.db import open_database, init_schema


def main() -> int:
    args = early_args

    df = pl.read_parquet(args.features_parquet).head(args.max_rows)
    trader = PaperTrader(
        model_paths={"900s_btc_v3_20260315": args.model_path},
        log_dir="/tmp/v3_replay_logs",
        confidence_threshold=0.55,
    )
    # Reuse trader's existing connection or rebind to specific db
    trader._db_conn.close()
    trader._db_conn = open_database(args.db)
    init_schema(trader._db_conn)
    # Rebuild storage objects bound to new conn
    from storage.sqlite_ledger import SQLiteLedger
    from storage.registry_state import RegistryState
    from storage.policy_snapshot import PolicySnapshot
    trader.sqlite_ledger = SQLiteLedger(trader._db_conn)
    trader.registry_state = RegistryState(trader._db_conn)
    trader.registry_state.bootstrap_if_empty(reason="replay")
    trader.policy_snapshot = PolicySnapshot(trader._db_conn)
    trader.policy_snapshot.capture(trader._capture_policy_dict(), initiated_by="replay")

    rows_emitted = 0
    feature_names = trader.feature_names["900s_btc_v3_20260315"]
    for row in df.iter_rows(named=True):
        boundary_ms = int(row["ts_ms"])
        feat_vec = [row[c] for c in feature_names]
        proba = float(trader.models["900s_btc_v3_20260315"].predict(
            np.array(feat_vec).reshape(1, -1)
        )[0])
        direction = "up" if proba > 0.5 else "down"
        trader._emit_prediction_rows(
            model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
            boundary_ms=boundary_ms, ts_model_ran_ms=boundary_ms,
            pred_proba_raw=proba, pred_proba_calibrated=proba,
            pred_direction=direction, above_threshold=(proba >= 0.55),
            warmup=False, platform="paper",
            price_at_open=float(row["mid_price"]),
        )
        rows_emitted += 1
    print(f"emitted {rows_emitted} prediction sets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
