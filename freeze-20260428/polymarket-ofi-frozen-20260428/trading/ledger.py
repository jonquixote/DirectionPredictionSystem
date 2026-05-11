from __future__ import annotations
"""
Append-only JSONL ledger for paper trading.

Design: Two separate records per prediction, linked by prediction_id.
  - Record 1 ("prediction") — written at prediction time
  - Record 2 ("resolution") — written 5/15 minutes later
Never mutates existing records. weekly_report merges by prediction_id.

Files:
  predictions_{model}.jsonl  — all predictions (above + below threshold)
  paper_trades_{model}.jsonl — threshold-filtered entries only
"""

import json
import uuid
import logging
import fcntl
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _generate_id() -> str:
    return str(uuid.uuid4())


def _append_record(filepath: Path, record: dict) -> None:
    """Thread-safe append to JSONL file."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    with open(filepath, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(line)
        fcntl.flock(f, fcntl.LOCK_UN)


def read_records(filepath: Path) -> list[dict]:
    """Read all records from a JSONL file."""
    if not filepath.exists():
        return []
    records = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed line in %s", filepath)
    return records


def merge_predictions_with_resolutions(records: list[dict]) -> list[dict]:
    """
    Merge prediction + resolution records by prediction_id.
    Returns list of dicts with all fields merged.
    """
    by_id: dict[str, dict] = {}
    for r in records:
        pid = r.get("prediction_id")
        if not pid:
            continue
        if pid not in by_id:
            by_id[pid] = {}
        by_id[pid].update(r)
    return list(by_id.values())


def merge_trades_with_resolutions(records: list[dict]) -> list[dict]:
    """
    Merge trade_entry + trade_resolution records by trade_id.
    Unlike prediction merging, trades must merge on trade_id because
    multiple trades (300s + 900s) share the same prediction_id.
    """
    by_id: dict[str, dict] = {}
    for r in records:
        tid = r.get("trade_id")
        if not tid:
            continue
        if tid not in by_id:
            by_id[tid] = {}
        by_id[tid].update(r)
    return list(by_id.values())


class Ledger:
    """
    Manages prediction and trade JSONL files for one model.

    Files created:
      {log_dir}/predictions_{model_name}.jsonl
      {log_dir}/paper_trades_{model_name}.jsonl
    """

    def __init__(self, log_dir: str | Path, model_name: str):
        self.log_dir = Path(log_dir)
        self.model_name = model_name
        self.predictions_path = self.log_dir / f"predictions_{model_name}.jsonl"
        self.trades_path = self.log_dir / f"paper_trades_{model_name}.jsonl"

    def log_prediction(
        self,
        symbol: str,
        pred_proba: float,
        pred_direction: str,
        above_threshold: bool,
        price_at_open: float,
        ts_model_ran_ms: int,
        ts_contract_open_ms: int,
        features: dict,
        warmup: bool = False,
        trade_eligible: bool = True,
        utc_hour: int | None = None,
        day_of_week: int | None = None,
        is_weekend: bool | None = None,
        relative_spread: float | None = None,
        p_market: float | None = None,
        p_model_minus_market: float | None = None,
    ) -> str:
        """Log a prediction record. Returns prediction_id."""
        prediction_id = _generate_id()
        record = {
            "record_type": "prediction",
            "prediction_id": prediction_id,
            "model": self.model_name,
            "ts_model_ran_ms": ts_model_ran_ms,
            "ts_contract_open_ms": ts_contract_open_ms,
            "symbol": symbol,
            "pred_proba": round(pred_proba, 6),
            "pred_direction": pred_direction,
            "above_threshold": above_threshold,
            "warmup": warmup,
            "trade_eligible": trade_eligible,
            "p_market": round(p_market, 6) if p_market is not None else None,
            "p_model_minus_market": round(p_model_minus_market, 6) if p_model_minus_market is not None else None,
            "utc_hour": utc_hour,
            "day_of_week": day_of_week,
            "is_weekend": is_weekend,
            "relative_spread": round(relative_spread, 8) if relative_spread is not None else None,
            "price_at_contract_open": price_at_open,
            "features": {k: round(v, 8) if isinstance(v, float) else v for k, v in features.items()},
        }
        _append_record(self.predictions_path, record)
        return prediction_id

    def log_trade(
        self,
        prediction_id: str,
        symbol: str,
        pred_proba: float,
        pred_direction: str,
        confidence_threshold: float,
        contract_duration_seconds: int,
        price_at_open: float,
        ts_model_ran_ms: int,
        ts_contract_open_ms: int,
        simulated_stake_usdc: float = 10.0,
        p_market: float | None = None,
        p_model_minus_market: float | None = None,
        suppressed_reason: str | None = None,
    ) -> str:
        """Log a paper trade entry record. Returns trade_id."""
        trade_id = _generate_id()
        record = {
            "record_type": "trade_entry",
            "trade_id": trade_id,
            "prediction_id": prediction_id,
            "model": self.model_name,
            "ts_model_ran_ms": ts_model_ran_ms,
            "ts_contract_open_ms": ts_contract_open_ms,
            "symbol": symbol,
            "pred_proba": round(pred_proba, 6),
            "pred_direction": pred_direction,
            "confidence_threshold_used": confidence_threshold,
            "contract_duration_seconds": contract_duration_seconds,
            "price_at_contract_open": price_at_open,
            "simulated_stake_usdc": simulated_stake_usdc,
            "p_market": round(p_market, 6) if p_market is not None else None,
            "p_model_minus_market": round(p_model_minus_market, 6) if p_model_minus_market is not None else None,
        }
        if suppressed_reason:
            record["suppressed_reason"] = suppressed_reason
        _append_record(self.trades_path, record)
        return trade_id

    def log_prediction_resolution(
        self,
        prediction_id: str,
        ts_contract_close_ms: int,
        price_at_close: float,
        contract_result: str,
        prediction_correct: bool,
    ) -> None:
        """Log a resolution record for a prediction."""
        record = {
            "record_type": "resolution",
            "prediction_id": prediction_id,
            "ts_contract_close_ms": ts_contract_close_ms,
            "price_at_contract_close": price_at_close,
            "contract_result": contract_result,
            "prediction_correct": prediction_correct,
        }
        _append_record(self.predictions_path, record)

    def log_trade_resolution(
        self,
        trade_id: str,
        prediction_id: str,
        ts_contract_close_ms: int,
        price_at_close: float,
        contract_result: str,
        prediction_correct: bool,
        gross_pnl: float,
        fee_paid: float,
        net_pnl: float,
        trade_result: str,
    ) -> None:
        """Log a resolution record for a paper trade."""
        record = {
            "record_type": "trade_resolution",
            "trade_id": trade_id,
            "prediction_id": prediction_id,
            "ts_contract_close_ms": ts_contract_close_ms,
            "price_at_contract_close": price_at_close,
            "contract_result": contract_result,
            "prediction_correct": prediction_correct,
            "gross_pnl": round(gross_pnl, 6),
            "fee_paid": round(fee_paid, 6),
            "net_pnl": round(net_pnl, 6),
            "trade_result": trade_result,
        }
        _append_record(self.trades_path, record)

    def get_completed_predictions(self) -> list[dict]:
        """Get all predictions with their resolutions merged."""
        records = read_records(self.predictions_path)
        return [r for r in merge_predictions_with_resolutions(records)
                if r.get("record_type") == "resolution" or r.get("prediction_correct") is not None]

    def get_completed_trades(self) -> list[dict]:
        """Get all trades with their resolutions merged."""
        records = read_records(self.trades_path)
        merged = merge_predictions_with_resolutions(records)
        return [r for r in merged if r.get("prediction_correct") is not None]

    def get_trade_count(self) -> int:
        """Count completed trades."""
        return len(self.get_completed_trades())
