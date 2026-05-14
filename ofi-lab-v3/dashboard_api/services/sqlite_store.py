"""SQLite-backed DataStore — replaces jsonl_reader.DataStore.

Reads from the same v3.db the PaperTrader writes to (WAL mode).
Materializes predictions, trades, and model_metadata into memory
on refresh(), keeping the same public interface as jsonl_reader.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from services.db import get_db

logger = logging.getLogger("dashboard.sqlite_store")


class DataStore:
    """In-memory store of all prediction and trade data from SQLite."""

    def __init__(self):
        self.predictions: list[dict] = []
        self.trades: list[dict] = []
        self.model_metadata: dict[str, dict] = {}
        self._last_refresh: float = 0
        self._pred_count: int = -1
        self._trade_count: int = -1

    def refresh(self) -> None:
        """Re-read from SQLite. Skips if row counts unchanged."""
        try:
            conn = get_db()
            cur_pred = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
            cur_trade = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
        except Exception:
            logger.warning("SQLite unavailable during refresh check")
            return

        if cur_pred == self._pred_count and cur_trade == self._trade_count and self._last_refresh > 0:
            return

        t0 = time.monotonic()
        self.predictions = self._load_predictions(conn)
        self.trades = self._load_trades(conn)
        self.model_metadata = self._load_model_metadata(conn)

        self.predictions.sort(key=lambda r: r.get("ts_model_ran_ms", 0))
        self.trades.sort(key=lambda r: r.get("ts_model_ran_ms", 0))

        self._pred_count = cur_pred
        self._trade_count = cur_trade
        elapsed = (time.monotonic() - t0) * 1000
        self._last_refresh = time.time()
        logger.info(
            "Data refreshed: %d predictions, %d trades (%.1fms)",
            len(self.predictions), len(self.trades), elapsed,
        )

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {k: row[k] for k in row.keys()}

    def _load_predictions(self, conn) -> list[dict]:
        rows = conn.execute(
            "SELECT "
            "  prediction_id, model_name, symbol, market_window_seconds, resolution_type, "
            "  ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms, "
            "  pred_proba_raw, pred_proba_calibrated, pred_direction, "
            "  above_threshold, warmup, trade_eligible, platform, "
            "  p_market, p_model_minus_market, "
            "  utc_hour, day_of_week, "
            "  regime_volatility, regime_liquidity, regime_trend, relative_spread, "
            "  price_at_open, price_at_close, contract_result, "
            "  prediction_correct, resolved, ts_resolved_ms, "
            "  decision_outcome, decision_reason, ev_estimate "
            "FROM predictions "
            "ORDER BY ts_model_ran_ms"
        ).fetchall()

        result = []
        for row in rows:
            d = self._row_to_dict(row)

            d["model"] = d.pop("model_name")
            d["contract_duration_seconds"] = d.pop("market_window_seconds")
            d["pred_proba"] = d.pop("pred_proba_calibrated")

            if d.get("p_model_minus_market") is not None:
                d["signed_divergence"] = round(d["p_model_minus_market"], 6)
            pm = d.get("p_market")
            pp = d.get("pred_proba")
            if pm is not None and pp is not None:
                d["divergence"] = round(abs(pp - pm), 6)
                if "signed_divergence" not in d:
                    d["signed_divergence"] = round(pp - pm, 6)
            else:
                d["divergence"] = None
                d["signed_divergence"] = None

            correct_raw = d.get("prediction_correct")
            if correct_raw is None:
                d["prediction_correct"] = None
                d["outcome"] = "unresolved"
            elif correct_raw:
                d["prediction_correct"] = True
                d["outcome"] = "correct"
            else:
                d["prediction_correct"] = False
                d["outcome"] = "incorrect"

            d["warmup"] = bool(d.get("warmup", 0))
            d["resolved"] = bool(d.get("resolved", 0))
            d["above_threshold"] = bool(d.get("above_threshold", 0))
            d["trade_eligible"] = bool(d.get("trade_eligible", 1))

            d["suppressed_reason"] = None

            result.append(d)
        return result

    def _load_trades(self, conn) -> list[dict]:
        rows = conn.execute(
            "SELECT "
            "  t.trade_id, t.prediction_id, t.model_name, t.symbol, "
            "  t.market_window_seconds, t.resolution_type, "
            "  t.ts_model_ran_ms, t.ts_contract_open_ms, t.ts_resolve_at_ms, "
            "  t.pred_proba_raw, t.pred_proba_calibrated, t.pred_direction, "
            "  t.confidence_threshold_used, t.simulated_stake_usdc, "
            "  t.p_market, t.suppressed_reason, t.filter_mode, t.warmup, t.platform, "
            "  t.decision_outcome, t.decision_reason, t.ev_estimate, "
            "  t.kelly_fraction_capped, t.final_size_usdc, t.order_type, "
            "  t.price_at_open, t.price_at_close, t.contract_result, "
            "  t.prediction_correct, t.gross_pnl, t.fee_paid, t.net_pnl, "
            "  t.trade_result, t.resolved, t.ts_resolved_ms "
            "FROM paper_trades t "
            "ORDER BY t.ts_model_ran_ms"
        ).fetchall()

        result = []
        for row in rows:
            d = self._row_to_dict(row)

            d["model"] = d.pop("model_name")
            d["contract_duration_seconds"] = d.pop("market_window_seconds")
            d["pred_proba"] = d.pop("pred_proba_calibrated")
            d["direction"] = d.get("pred_direction")
            d["contract_duration"] = d.get("contract_duration_seconds")
            d["timestamp_ms"] = d.get("ts_model_ran_ms", 0)
            d["price_at_contract_open"] = d.pop("price_at_open")
            d["price_at_contract_close"] = d.pop("price_at_close")
            d["ts_contract_close_ms"] = d.pop("ts_resolved_ms")
            d["simulated_stake_usdc"] = d.get("simulated_stake_usdc", 10.0)

            correct_raw = d.get("prediction_correct")
            if correct_raw is None:
                d["prediction_correct"] = None
                d["outcome"] = "unresolved"
            elif correct_raw:
                d["prediction_correct"] = True
                d["outcome"] = "correct"
            else:
                d["prediction_correct"] = False
                d["outcome"] = "incorrect"

            d["warmup"] = bool(d.get("warmup", 0))
            d["resolved"] = bool(d.get("resolved", 0))

            result.append(d)
        return result

    def _load_model_metadata(self, conn) -> dict[str, dict]:
        rows = conn.execute(
            "SELECT name, symbol, training_horizon_seconds, is_baseline, "
            "  paper_active, live_eligible, lifecycle_state, "
            "  artifact_path, feature_names_path, "
            "  train_window_start, train_window_end, train_days, "
            "  feature_version, evaluation_windows, generation "
            "FROM model_registry"
        ).fetchall()

        metadata: dict[str, dict] = {}
        for row in rows:
            d = self._row_to_dict(row)
            name = d["name"]
            meta: dict[str, Any] = {"version": name}
            meta["symbol"] = d.get("symbol")
            meta["training_horizon_seconds"] = d.get("training_horizon_seconds")
            meta["is_baseline"] = bool(d.get("is_baseline", 0))
            meta["paper_active"] = bool(d.get("paper_active", 1))
            meta["live_eligible"] = bool(d.get("live_eligible", 0))
            meta["lifecycle_state"] = d.get("lifecycle_state", "prediction_only")
            meta["artifact_path"] = d.get("artifact_path")
            meta["feature_names_path"] = d.get("feature_names_path")
            meta["train_window_start"] = d.get("train_window_start")
            meta["train_window_end"] = d.get("train_window_end")
            meta["train_days"] = d.get("train_days")
            meta["feature_version"] = d.get("feature_version", "v3")
            raw_ew = d.get("evaluation_windows")
            if isinstance(raw_ew, str):
                try:
                    meta["evaluation_windows"] = json.loads(raw_ew)
                except (json.JSONDecodeError, TypeError):
                    meta["evaluation_windows"] = [300, 900, 1800]
            elif isinstance(raw_ew, list):
                meta["evaluation_windows"] = raw_ew
            else:
                meta["evaluation_windows"] = [300, 900, 1800]
            metadata[name] = meta
        return metadata

    @property
    def unique_symbols(self) -> list[str]:
        return sorted(set(p.get("symbol", "") for p in self.predictions if p.get("symbol")))

    @property
    def unique_models(self) -> list[str]:
        return sorted(set(p.get("model", "") for p in self.predictions if p.get("model")))

    def get_predictions(
        self,
        model: str | None = None,
        symbol: str | None = None,
        contract_duration: int | None = None,
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
        filtered = self._filter_records(
            self.predictions, model=model, symbol=symbol,
            from_ms=from_ms, to_ms=to_ms, direction=direction,
            suppressed=suppressed, warmup=warmup, outcome=outcome,
        )
        if contract_duration is not None:
            filtered = [
                r for r in filtered
                if r.get("contract_duration_seconds") == contract_duration
            ]
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
        reverse = order == "desc"
        filtered.sort(key=lambda r: r.get(sort, 0) or 0, reverse=reverse)
        total = len(filtered)
        start = (page - 1) * page_size
        end = start + page_size
        return filtered[start:end], total

    def get_open_trades(self) -> list[dict]:
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
        return list(result)


_store = DataStore()


def get_store() -> DataStore:
    return _store
