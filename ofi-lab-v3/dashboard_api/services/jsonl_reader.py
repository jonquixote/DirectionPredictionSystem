"""
JSONL Reader — data access layer.

Replaces the SQLite db.py from the spec. Reads JSONL files produced by
the trading system's Ledger and merges predictions with resolutions.

Data is loaded into memory and refreshed periodically. The entire dataset
is small (~1000 records) so full in-memory is efficient.
"""
from __future__ import annotations
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("dashboard.jsonl")

LOG_DIR = os.environ.get("LOG_DIR", "/data/logs")
MODEL_DIR = os.environ.get("MODEL_DIR", "/data/models")

# Model name → log file stems
MODELS = {
    "h60": {"predictions": "predictions_h60.jsonl", "trades": "paper_trades_h60.jsonl"},
    "h300": {"predictions": "predictions_h300.jsonl", "trades": "paper_trades_h300.jsonl"},
    "h60_v3": {"predictions": "predictions_h60_v3.jsonl", "trades": "paper_trades_h60_v3.jsonl"},
}


class DataStore:
    """In-memory store of all prediction and trade data from JSONL files."""

    def __init__(self):
        self.predictions: list[dict] = []
        self.trades: list[dict] = []
        self.model_metadata: dict[str, dict] = {}
        self._last_refresh: float = 0
        self._file_sizes: dict[str, int] = {}

    def refresh(self) -> None:
        """Re-read all JSONL files. Only re-reads if file sizes changed."""
        changed = False
        for model, files in MODELS.items():
            for ftype, fname in files.items():
                path = os.path.join(LOG_DIR, fname)
                try:
                    size = os.path.getsize(path)
                except FileNotFoundError:
                    size = 0
                key = f"{model}:{ftype}"
                if size != self._file_sizes.get(key, -1):
                    changed = True
                    self._file_sizes[key] = size

        if not changed and self._last_refresh > 0:
            return

        t0 = time.monotonic()
        self.predictions = []
        self.trades = []

        for model, files in MODELS.items():
            # Load predictions
            pred_path = os.path.join(LOG_DIR, files["predictions"])
            preds_by_id = self._load_predictions(pred_path, model)
            self.predictions.extend(preds_by_id.values())

            # Load trades
            trade_path = os.path.join(LOG_DIR, files["trades"])
            trades = self._load_trades(trade_path, model, preds_by_id)
            self.trades.extend(trades)

        # Sort by timestamp
        self.predictions.sort(key=lambda r: r.get("ts_model_ran_ms", 0))
        self.trades.sort(key=lambda r: r.get("ts_model_ran_ms", 0))

        # Load model metadata
        self.model_metadata = self._load_model_metadata()

        elapsed = (time.monotonic() - t0) * 1000
        self._last_refresh = time.time()
        logger.info(
            "Data refreshed: %d predictions, %d trades (%.1fms)",
            len(self.predictions), len(self.trades), elapsed,
        )

    def _load_predictions(self, path: str, model: str) -> dict[str, dict]:
        """Load and merge prediction + resolution records by prediction_id."""
        by_id: dict[str, dict] = {}
        try:
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    pid = record.get("prediction_id")
                    if not pid:
                        continue

                    if pid not in by_id:
                        by_id[pid] = {"_model": model}

                    by_id[pid].update(record)
        except FileNotFoundError:
            logger.warning("Predictions file not found: %s", path)

        # Normalize fields
        for pid, rec in by_id.items():
            rec.setdefault("model", model)
            rec.setdefault("warmup", False)
            rec.setdefault("suppressed_reason", None)
            rec.setdefault("prediction_correct", None)

            # Compute divergence
            pm = rec.get("p_market")
            pp = rec.get("pred_proba")
            if pm is not None and pp is not None:
                rec["divergence"] = round(abs(pp - pm), 6)
                rec["signed_divergence"] = round(pp - pm, 6)
            else:
                rec["divergence"] = None
                rec["signed_divergence"] = None

            # Determine outcome
            correct = rec.get("prediction_correct")
            if correct is None:
                rec["outcome"] = "unresolved"
            elif correct:
                rec["outcome"] = "correct"
            else:
                rec["outcome"] = "incorrect"

        return by_id

    def _load_trades(
        self, path: str, model: str, preds_by_id: dict[str, dict]
    ) -> list[dict]:
        """Load trade entries + resolutions, merge by trade_id."""
        entries: dict[str, dict] = {}
        resolutions: dict[str, dict] = {}

        try:
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    rt = record.get("record_type")
                    if rt == "trade_entry":
                        tid = record.get("trade_id")
                        if tid:
                            entries[tid] = record
                    elif rt == "trade_resolution":
                        tid = record.get("trade_id")
                        if tid:
                            resolutions[tid] = record
        except FileNotFoundError:
            logger.warning("Trades file not found: %s", path)

        # Merge entries with resolutions
        result = []
        for tid, entry in entries.items():
            merged = {**entry}
            merged["model"] = model
            merged.setdefault("suppressed_reason", None)

            if tid in resolutions:
                merged.update(resolutions[tid])
                merged["resolved"] = True
            else:
                merged["resolved"] = False
                merged.setdefault("prediction_correct", None)

            # Determine outcome
            correct = merged.get("prediction_correct")
            if correct is None:
                merged["outcome"] = "unresolved"
            elif correct:
                merged["outcome"] = "correct"
            else:
                merged["outcome"] = "incorrect"

            # Carry over pred_direction from prediction if not on trade
            if "pred_direction" not in merged:
                pid = merged.get("prediction_id")
                if pid and pid in preds_by_id:
                    merged["pred_direction"] = preds_by_id[pid].get("pred_direction")

            # Rename for API consistency
            merged["direction"] = merged.get("pred_direction")
            merged["contract_duration"] = merged.get("contract_duration_seconds")
            merged["timestamp_ms"] = merged.get("ts_model_ran_ms", 0)

            result.append(merged)

        return result

    def _load_model_metadata(self) -> dict[str, dict]:
        """Load model metadata from /data/models/."""
        metadata = {}
        for model_name, _ in MODELS.items():
            # Map model names to directory names
            if model_name == "h60":
                dirs = ["latest_h60"]
            elif model_name == "h300":
                dirs = ["latest_h300"]
            elif model_name == "h60_v3":
                dirs = ["models_v3/latest_h60_v3"]
            else:
                continue

            for d in dirs:
                model_dir = os.path.join(MODEL_DIR, d) if "/" not in d else os.path.join(
                    os.path.dirname(MODEL_DIR), d
                )
                if not os.path.isdir(model_dir):
                    # Try under MODEL_DIR directly
                    model_dir = os.path.join(MODEL_DIR, d.split("/")[-1])
                    if not os.path.isdir(model_dir):
                        continue

                meta: dict[str, Any] = {"version": model_name}

                # Load metrics.json
                metrics_path = os.path.join(model_dir, "metrics.json")
                if os.path.exists(metrics_path):
                    with open(metrics_path) as f:
                        meta["metrics"] = json.load(f)
                    meta["auc_train"] = meta["metrics"].get("auc_full")
                    meta["auc_val"] = meta["metrics"].get("auc_at_contract_times")
                    meta["auc_test"] = None
                    meta["trained_date"] = meta["metrics"].get("timestamp", "")[:10]

                # Load feature_names.json
                features_path = os.path.join(model_dir, "feature_names.json")
                if os.path.exists(features_path):
                    with open(features_path) as f:
                        meta["feature_list"] = json.load(f)
                    meta["feature_count"] = len(meta["feature_list"])

                # Load config_snapshot.json
                config_path = os.path.join(model_dir, "config_snapshot.json")
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        meta["config"] = json.load(f)
                    meta["lgbm_params"] = meta["config"].get("LGBM_PARAMS", {})

                meta["file_path"] = model_dir
                metadata[model_name] = meta

        return metadata

    # ── Query Methods ──────────────────────────────────────────

    def get_predictions(
        self,
        model: str | None = None,
        symbol: str | None = None,
        from_ms: int | None = None,
        to_ms: int | None = None,
        direction: str | None = None,
        suppressed: bool | None = None,
        warmup: bool | None = None,
        outcome: str | None = None,
        page: int = 1,
        page_size: int = 50,
        sort: str = "ts_model_ran_ms",
        order: str = "desc",
    ) -> tuple[list[dict], int]:
        """Filter and paginate predictions. Returns (items, total_count)."""
        filtered = self._filter_records(
            self.predictions, model=model, symbol=symbol,
            from_ms=from_ms, to_ms=to_ms, direction=direction,
            suppressed=suppressed, warmup=warmup, outcome=outcome,
        )

        # Sort
        reverse = order == "desc"
        filtered.sort(key=lambda r: r.get(sort, 0) or 0, reverse=reverse)

        total = len(filtered)
        start = (page - 1) * page_size
        end = start + page_size

        return filtered[start:end], total

    def get_trades(
        self,
        model: str | None = None,
        symbol: str | None = None,
        from_ms: int | None = None,
        to_ms: int | None = None,
        direction: str | None = None,
        suppressed: bool | None = None,
        outcome: str | None = None,
        contract_duration: int | None = None,
        settled: bool | None = None,
        page: int = 1,
        page_size: int = 50,
        sort: str = "ts_model_ran_ms",
        order: str = "desc",
    ) -> tuple[list[dict], int]:
        """Filter and paginate trades. Returns (items, total_count)."""
        filtered = self._filter_records(
            self.trades, model=model, symbol=symbol,
            from_ms=from_ms, to_ms=to_ms, direction=direction,
            suppressed=suppressed, outcome=outcome,
        )

        if contract_duration is not None:
            filtered = [
                r for r in filtered
                if r.get("contract_duration_seconds") == contract_duration
                or r.get("contract_duration") == contract_duration
            ]

        if settled is not None:
            filtered = [r for r in filtered if r.get("resolved", False) == settled]

        # Sort
        reverse = order == "desc"
        filtered.sort(key=lambda r: r.get(sort, 0) or 0, reverse=reverse)

        total = len(filtered)
        start = (page - 1) * page_size
        end = start + page_size

        return filtered[start:end], total

    def get_open_trades(self) -> list[dict]:
        """Get unresolved trades."""
        now_ms = int(time.time() * 1000)
        result = []
        for t in self.trades:
            if not t.get("resolved", False) and not t.get("suppressed_reason"):
                elapsed = now_ms - t.get("ts_model_ran_ms", now_ms)
                result.append({
                    "trade_id": t.get("trade_id"),
                    "symbol": t.get("symbol"),
                    "model": t.get("model"),
                    "contract_duration": t.get("contract_duration_seconds"),
                    "direction": t.get("pred_direction"),
                    "timestamp_ms": t.get("ts_model_ran_ms"),
                    "elapsed_ms": elapsed,
                    "p_market": t.get("p_market"),
                })
        return result

    def get_resolved_trades(
        self, model: str | None = None, symbol: str | None = None
    ) -> list[dict]:
        """Get all resolved, non-suppressed trades for a model+symbol."""
        return [
            t for t in self.trades
            if t.get("resolved")
            and not t.get("suppressed_reason")
            and t.get("prediction_correct") is not None
            and (model is None or t.get("model") == model)
            and (symbol is None or t.get("symbol") == symbol)
        ]

    def _filter_records(
        self,
        records: list[dict],
        model: str | None = None,
        symbol: str | None = None,
        from_ms: int | None = None,
        to_ms: int | None = None,
        direction: str | None = None,
        suppressed: bool | None = None,
        warmup: bool | None = None,
        outcome: str | None = None,
    ) -> list[dict]:
        """Apply common filters to a record list."""
        result = records

        if model is not None:
            result = [r for r in result if r.get("model") == model]
        if symbol is not None:
            result = [r for r in result if r.get("symbol") == symbol]
        if from_ms is not None:
            result = [r for r in result if r.get("ts_model_ran_ms", 0) >= from_ms]
        if to_ms is not None:
            result = [r for r in result if r.get("ts_model_ran_ms", 0) <= to_ms]
        if direction is not None:
            result = [
                r for r in result
                if r.get("pred_direction") == direction or r.get("direction") == direction
            ]
        if suppressed is not None:
            if suppressed:
                result = [r for r in result if r.get("suppressed_reason") is not None]
            else:
                result = [r for r in result if r.get("suppressed_reason") is None]
        if warmup is not None:
            result = [r for r in result if r.get("warmup", False) == warmup]
        if outcome is not None:
            result = [r for r in result if r.get("outcome") == outcome]

        return list(result)  # return a copy


# Global singleton
_store = DataStore()


def get_store() -> DataStore:
    """Get the global DataStore singleton."""
    return _store
