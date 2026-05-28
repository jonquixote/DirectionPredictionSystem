"""SQLite-backed DataStore — SQL-driven, no full in-memory scan.

Reads from the same v3.db the PaperTrader writes to (WAL mode).

get_predictions() / get_trades() / count_predictions() / count_trades() /
get_predictions_by_id() / get_open_trades() / get_resolved_trades() all
issue parameterised SQL directly against the DB — no Python-side scan of
the last-hour cache.

refresh() stays cheap: only COUNT(*) + a small recent-window scan that
populates ``.predictions`` / ``.trades`` for LiveState's last-hour
aggregate metrics. Do not use those properties for paginated history.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

try:
    from services.db import get_db
except ModuleNotFoundError:
    from dashboard_api.services.db import get_db  # type: ignore

logger = logging.getLogger("dashboard.sqlite_store")

# Columns to SELECT for predictions — avoids SELECT *
_PRED_COLS = (
    "prediction_id, model_name, symbol, market_window_seconds, resolution_type, "
    "ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms, "
    "pred_proba_raw, pred_proba_calibrated, pred_direction, "
    "above_threshold, warmup, trade_eligible, platform, "
    "p_market, p_model_minus_market, "
    "utc_hour, day_of_week, "
    "regime_volatility, regime_liquidity, regime_trend, relative_spread, "
    "price_at_open, price_at_close, contract_result, "
    "prediction_correct, resolved, ts_resolved_ms, "
    "decision_outcome, decision_reason, ev_estimate"
)

_TRADE_COLS = (
    "t.trade_id, t.prediction_id, t.model_name, t.symbol, "
    "t.market_window_seconds, t.resolution_type, "
    "t.ts_model_ran_ms, t.ts_contract_open_ms, t.ts_resolve_at_ms, "
    "t.pred_proba_raw, t.pred_proba_calibrated, t.pred_direction, "
    "t.confidence_threshold_used, t.simulated_stake_usdc, "
    "t.p_market, t.suppressed_reason, t.filter_mode, t.warmup, t.platform, "
    "t.decision_outcome, t.decision_reason, t.ev_estimate, "
    "t.kelly_fraction_capped, t.final_size_usdc, t.order_type, "
    "t.price_at_open, t.price_at_close, t.contract_result, "
    "t.prediction_correct, t.gross_pnl, t.fee_paid, t.net_pnl, "
    "t.trade_result, t.resolved, t.ts_resolved_ms"
)

# Whitelist sort columns. Map user-facing name -> SQL column expression.
_VALID_PRED_SORT = {
    "ts_model_ran_ms": "ts_model_ran_ms",
    "pred_proba_calibrated": "pred_proba_calibrated",
    "pred_proba": "pred_proba_calibrated",
    "p_market": "p_market",
    "market_window_seconds": "market_window_seconds",
    "contract_duration_seconds": "market_window_seconds",
}
_VALID_TRADE_SORT = {
    "ts_model_ran_ms": "t.ts_model_ran_ms",
    "pred_proba_calibrated": "t.pred_proba_calibrated",
    "pred_proba": "t.pred_proba_calibrated",
    "p_market": "t.p_market",
    "market_window_seconds": "t.market_window_seconds",
    "contract_duration_seconds": "t.market_window_seconds",
}

_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 1000


def _clamp_page_size(n: int) -> int:
    if n is None or n <= 0:
        return _DEFAULT_PAGE_SIZE
    return min(int(n), _MAX_PAGE_SIZE)


def _sort_clause(sort: str, order: str, whitelist: dict[str, str], default_expr: str) -> str:
    """Return a safe ORDER BY fragment built only from whitelisted names."""
    expr = whitelist.get(sort or "", default_expr)
    direction = "DESC" if (order or "desc").lower() != "asc" else "ASC"
    return f"ORDER BY {expr} {direction}"


class DataStore:
    """SQL-driven store.

    All paginated/queryable accessors run parameterised SQL against the DB.
    ``.predictions`` / ``.trades`` properties remain as last-hour caches used
    only by LiveState aggregate metrics.
    """

    def __init__(self):
        # Small in-memory caches for LiveState metrics only
        self._recent_predictions: list[dict] = []  # last-hour only
        self._recent_trades: list[dict] = []        # last-hour only
        self.model_metadata: dict[str, dict] = {}
        self._last_refresh: float = 0
        self._pred_count: int = -1
        self._trade_count: int = -1

    # ------------------------------------------------------------------ #
    #  refresh() — cheap: COUNT check + 1-hour window for LiveState        #
    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        """Re-read aggregate data from SQLite. Skips if row counts unchanged."""
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
        one_hour_ago_ms = int((time.time() - 3600) * 1000)

        # Load only the last hour of predictions for rate/status metrics
        rows = conn.execute(
            f"SELECT {_PRED_COLS} FROM predictions "
            "WHERE ts_model_ran_ms >= ? ORDER BY ts_model_ran_ms",
            (one_hour_ago_ms,),
        ).fetchall()
        self._recent_predictions = [self._decorate_prediction(self._row_to_dict(r)) for r in rows]

        # Load only the last hour of trades for rate/status metrics
        trows = conn.execute(
            f"SELECT {_TRADE_COLS} FROM paper_trades t "
            "WHERE t.ts_model_ran_ms >= ? ORDER BY t.ts_model_ran_ms",
            (one_hour_ago_ms,),
        ).fetchall()
        self._recent_trades = [self._decorate_trade(self._row_to_dict(r)) for r in trows]

        self.model_metadata = self._load_model_metadata(conn)
        self._pred_count = cur_pred
        self._trade_count = cur_trade
        elapsed = (time.monotonic() - t0) * 1000
        self._last_refresh = time.time()
        logger.info(
            "Data refreshed: %d total predictions, %d total trades, "
            "%d recent predictions, %d recent trades (%.1fms)",
            cur_pred, cur_trade,
            len(self._recent_predictions), len(self._recent_trades),
            elapsed,
        )

    # ------------------------------------------------------------------ #
    #  Backwards-compat properties for LiveState callers                  #
    # ------------------------------------------------------------------ #

    @property
    def predictions(self) -> list[dict]:
        """Expose recent (1h) predictions for LiveState aggregate metrics.

        Do NOT use for paginated history — use get_predictions() instead.
        """
        return self._recent_predictions

    @property
    def trades(self) -> list[dict]:
        """Expose recent (1h) trades for LiveState aggregate metrics.

        Do NOT use for paginated history — use get_trades() instead.
        """
        return self._recent_trades

    # ------------------------------------------------------------------ #
    #  Row helpers                                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {k: row[k] for k in row.keys()}

    @staticmethod
    def _decorate_prediction(d: dict) -> dict:
        """Apply field renames + derived fields to a raw predictions row dict."""
        d["model"] = d.pop("model_name", d.get("model"))
        d["contract_duration_seconds"] = d.pop("market_window_seconds", d.get("contract_duration_seconds"))
        d["pred_proba"] = d.pop("pred_proba_calibrated", d.get("pred_proba"))

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
        return d

    @staticmethod
    def _decorate_trade(d: dict) -> dict:
        """Apply field renames + derived fields to a raw paper_trades row dict."""
        d["model"] = d.pop("model_name", d.get("model"))
        d["contract_duration_seconds"] = d.pop("market_window_seconds", d.get("contract_duration_seconds"))
        d["pred_proba"] = d.pop("pred_proba_calibrated", d.get("pred_proba"))
        d["direction"] = d.get("pred_direction")
        d["contract_duration"] = d.get("contract_duration_seconds")
        d["timestamp_ms"] = d.get("ts_model_ran_ms", 0)
        d["price_at_contract_open"] = d.pop("price_at_open", d.get("price_at_contract_open"))
        d["price_at_contract_close"] = d.pop("price_at_close", d.get("price_at_contract_close"))
        d["ts_contract_close_ms"] = d.pop("ts_resolved_ms", d.get("ts_contract_close_ms"))
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
        return d

    # ------------------------------------------------------------------ #
    #  SQL WHERE builder helpers                                           #
    # ------------------------------------------------------------------ #

    def _pred_where(
        self,
        model=None, symbol=None, from_ms=None, to_ms=None,
        direction=None, suppressed=None, warmup=None, outcome=None,
        contract_duration=None, resolved=None, prediction_id=None,
    ):
        clauses = ["1=1"]
        params: list = []
        if prediction_id is not None:
            clauses.append("prediction_id = ?")
            params.append(prediction_id)
        if model is not None:
            clauses.append("model_name = ?")
            params.append(model)
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if from_ms is not None:
            clauses.append("ts_model_ran_ms >= ?")
            params.append(from_ms)
        if to_ms is not None:
            clauses.append("ts_model_ran_ms <= ?")
            params.append(to_ms)
        if direction is not None:
            clauses.append("pred_direction = ?")
            params.append(direction)
        if warmup is not None:
            clauses.append("warmup = ?")
            params.append(1 if warmup else 0)
        if outcome == "correct":
            clauses.append("prediction_correct = 1")
        elif outcome == "incorrect":
            clauses.append("prediction_correct = 0")
        elif outcome == "unresolved":
            clauses.append("prediction_correct IS NULL")
        if contract_duration is not None:
            clauses.append("market_window_seconds = ?")
            params.append(contract_duration)
        if resolved is True:
            clauses.append("resolved = 1")
        elif resolved is False:
            clauses.append("resolved = 0")
        # suppressed: predictions table has no suppressed_reason col;
        # it's a trades-only concept. suppressed=True → return nothing.
        if suppressed is True:
            clauses.append("0=1")
        return " AND ".join(clauses), params

    def _trade_where(
        self,
        model=None, symbol=None, from_ms=None, to_ms=None,
        direction=None, suppressed=None, outcome=None,
        contract_duration=None, settled=None, suppressed_reason=None,
        prediction_id=None,
    ):
        clauses = ["1=1"]
        params: list = []
        if prediction_id is not None:
            clauses.append("t.prediction_id = ?")
            params.append(prediction_id)
        if model is not None:
            clauses.append("t.model_name = ?")
            params.append(model)
        if symbol is not None:
            clauses.append("t.symbol = ?")
            params.append(symbol)
        if from_ms is not None:
            clauses.append("t.ts_model_ran_ms >= ?")
            params.append(from_ms)
        if to_ms is not None:
            clauses.append("t.ts_model_ran_ms <= ?")
            params.append(to_ms)
        if direction is not None:
            clauses.append("t.pred_direction = ?")
            params.append(direction)
        if suppressed is True:
            clauses.append("t.suppressed_reason IS NOT NULL")
        elif suppressed is False:
            clauses.append("t.suppressed_reason IS NULL")
        if suppressed_reason is not None:
            clauses.append("t.suppressed_reason = ?")
            params.append(suppressed_reason)
        if outcome == "correct":
            clauses.append("t.prediction_correct = 1")
        elif outcome == "incorrect":
            clauses.append("t.prediction_correct = 0")
        elif outcome == "unresolved":
            clauses.append("t.prediction_correct IS NULL")
        if contract_duration is not None:
            clauses.append("t.market_window_seconds = ?")
            params.append(contract_duration)
        if settled is True:
            clauses.append("t.resolved = 1")
        elif settled is False:
            clauses.append("t.resolved = 0")
        return " AND ".join(clauses), params

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

    # ------------------------------------------------------------------ #
    #  Unique-symbol / unique-model: SELECT DISTINCT (not last-hour)       #
    # ------------------------------------------------------------------ #

    @property
    def unique_symbols(self) -> list[str]:
        try:
            conn = get_db()
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM predictions WHERE symbol IS NOT NULL"
            ).fetchall()
        except Exception:
            return []
        return sorted({r[0] for r in rows if r[0]})

    @property
    def unique_models(self) -> list[str]:
        try:
            conn = get_db()
            rows = conn.execute(
                "SELECT DISTINCT model_name FROM predictions WHERE model_name IS NOT NULL"
            ).fetchall()
        except Exception:
            return []
        return sorted({r[0] for r in rows if r[0]})

    # ------------------------------------------------------------------ #
    #  Paginated SQL accessors                                             #
    # ------------------------------------------------------------------ #

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
        resolved: bool | None = None,
        prediction_id: str | None = None,
        page: int = 1,
        page_size: int = _DEFAULT_PAGE_SIZE,
        sort: str = "ts_model_ran_ms",
        order: str = "desc",
        # legacy/explicit alternates accepted for caller flexibility
        sort_by: str | None = None,
        sort_dir: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> tuple[list[dict], int]:
        """Return (rows, total_count) for predictions matching the filters.

        Rows are decorated like the legacy in-memory output (renamed keys,
        derived ``divergence``/``outcome``/``signed_divergence`` fields).
        """
        where_sql, params = self._pred_where(
            model=model, symbol=symbol, from_ms=from_ms, to_ms=to_ms,
            direction=direction, suppressed=suppressed, warmup=warmup,
            outcome=outcome, contract_duration=contract_duration,
            resolved=resolved, prediction_id=prediction_id,
        )

        order_sql = _sort_clause(
            sort_by or sort, sort_dir or order,
            _VALID_PRED_SORT, "ts_model_ran_ms",
        )

        # Compute total via COUNT(*) using same WHERE/params
        try:
            conn = get_db()
            total = conn.execute(
                f"SELECT COUNT(*) FROM predictions WHERE {where_sql}",
                tuple(params),
            ).fetchone()[0]
        except Exception:
            logger.warning("SQLite unavailable during get_predictions count")
            return [], 0

        # Resolve pagination — explicit limit/offset wins over page/page_size
        if limit is not None or offset is not None:
            lim = _clamp_page_size(limit if limit is not None else _DEFAULT_PAGE_SIZE)
            off = max(int(offset or 0), 0)
        else:
            lim = _clamp_page_size(page_size)
            off = max((int(page or 1) - 1) * lim, 0)

        sql = (
            f"SELECT {_PRED_COLS} FROM predictions "
            f"WHERE {where_sql} {order_sql} LIMIT ? OFFSET ?"
        )
        rows = conn.execute(sql, (*params, lim, off)).fetchall()
        decorated = [self._decorate_prediction(self._row_to_dict(r)) for r in rows]
        return decorated, int(total)

    def count_predictions(
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
        resolved: bool | None = None,
        prediction_id: str | None = None,
    ) -> int:
        where_sql, params = self._pred_where(
            model=model, symbol=symbol, from_ms=from_ms, to_ms=to_ms,
            direction=direction, suppressed=suppressed, warmup=warmup,
            outcome=outcome, contract_duration=contract_duration,
            resolved=resolved, prediction_id=prediction_id,
        )
        try:
            conn = get_db()
            row = conn.execute(
                f"SELECT COUNT(*) FROM predictions WHERE {where_sql}",
                tuple(params),
            ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            logger.warning("SQLite unavailable during count_predictions")
            return 0

    def get_predictions_by_id(self, prediction_id: str) -> list[dict]:
        """Return a list (0 or 1) with the prediction matching prediction_id."""
        if not prediction_id:
            return []
        try:
            conn = get_db()
            rows = conn.execute(
                f"SELECT {_PRED_COLS} FROM predictions WHERE prediction_id = ? LIMIT 1",
                (prediction_id,),
            ).fetchall()
        except Exception:
            logger.warning("SQLite unavailable during get_predictions_by_id")
            return []
        return [self._decorate_prediction(self._row_to_dict(r)) for r in rows]

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
        suppressed_reason: str | None = None,
        prediction_id: str | None = None,
        page: int = 1,
        page_size: int = _DEFAULT_PAGE_SIZE,
        sort: str = "ts_model_ran_ms",
        order: str = "desc",
        sort_by: str | None = None,
        sort_dir: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> tuple[list[dict], int]:
        """Return (rows, total_count) for paper_trades matching the filters."""
        where_sql, params = self._trade_where(
            model=model, symbol=symbol, from_ms=from_ms, to_ms=to_ms,
            direction=direction, suppressed=suppressed, outcome=outcome,
            contract_duration=contract_duration, settled=settled,
            suppressed_reason=suppressed_reason, prediction_id=prediction_id,
        )

        order_sql = _sort_clause(
            sort_by or sort, sort_dir or order,
            _VALID_TRADE_SORT, "t.ts_model_ran_ms",
        )

        try:
            conn = get_db()
            total = conn.execute(
                f"SELECT COUNT(*) FROM paper_trades t WHERE {where_sql}",
                tuple(params),
            ).fetchone()[0]
        except Exception:
            logger.warning("SQLite unavailable during get_trades count")
            return [], 0

        if limit is not None or offset is not None:
            lim = _clamp_page_size(limit if limit is not None else _DEFAULT_PAGE_SIZE)
            off = max(int(offset or 0), 0)
        else:
            lim = _clamp_page_size(page_size)
            off = max((int(page or 1) - 1) * lim, 0)

        sql = (
            f"SELECT {_TRADE_COLS} FROM paper_trades t "
            f"WHERE {where_sql} {order_sql} LIMIT ? OFFSET ?"
        )
        rows = conn.execute(sql, (*params, lim, off)).fetchall()
        decorated = [self._decorate_trade(self._row_to_dict(r)) for r in rows]
        return decorated, int(total)

    def count_trades(
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
        suppressed_reason: str | None = None,
        prediction_id: str | None = None,
    ) -> int:
        where_sql, params = self._trade_where(
            model=model, symbol=symbol, from_ms=from_ms, to_ms=to_ms,
            direction=direction, suppressed=suppressed, outcome=outcome,
            contract_duration=contract_duration, settled=settled,
            suppressed_reason=suppressed_reason, prediction_id=prediction_id,
        )
        try:
            conn = get_db()
            row = conn.execute(
                f"SELECT COUNT(*) FROM paper_trades t WHERE {where_sql}",
                tuple(params),
            ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            logger.warning("SQLite unavailable during count_trades")
            return 0

    # ------------------------------------------------------------------ #
    #  Open / resolved helpers — now SQL-backed (not 1h-scoped)            #
    # ------------------------------------------------------------------ #

    def get_open_trades(self) -> list[dict]:
        """Return all unresolved, non-suppressed paper trades.

        Returns the lightweight row shape the dashboard expects (subset of
        columns) — built from a direct SQL query, not a Python filter over
        the last-hour cache.
        """
        try:
            conn = get_db()
            rows = conn.execute(
                f"SELECT {_TRADE_COLS} FROM paper_trades t "
                "WHERE t.resolved = 0 AND t.suppressed_reason IS NULL "
                "ORDER BY t.ts_model_ran_ms DESC"
            ).fetchall()
        except Exception:
            logger.warning("SQLite unavailable during get_open_trades")
            return []

        now_ms = int(time.time() * 1000)
        out: list[dict] = []
        for r in rows:
            t = self._decorate_trade(self._row_to_dict(r))
            elapsed = now_ms - (t.get("ts_model_ran_ms") or now_ms)
            out.append({
                "trade_id": t.get("trade_id"),
                "symbol": t.get("symbol"),
                "model": t.get("model"),
                "contract_duration": t.get("contract_duration_seconds"),
                "direction": t.get("pred_direction"),
                "timestamp_ms": t.get("ts_model_ran_ms"),
                "elapsed_ms": elapsed,
                "p_market": t.get("p_market"),
            })
        return out

    def get_resolved_trades(
        self, model: str | None = None, symbol: str | None = None
    ) -> list[dict]:
        """Return all resolved, non-suppressed paper trades with a known outcome.

        SQL-backed (not 1h-scoped). Returns the full decorated row dicts so
        callers in performance.py can compute aggregates.
        """
        clauses = [
            "t.resolved = 1",
            "t.suppressed_reason IS NULL",
            "t.prediction_correct IS NOT NULL",
        ]
        params: list = []
        if model is not None:
            clauses.append("t.model_name = ?")
            params.append(model)
        if symbol is not None:
            clauses.append("t.symbol = ?")
            params.append(symbol)
        where_sql = " AND ".join(clauses)
        try:
            conn = get_db()
            rows = conn.execute(
                f"SELECT {_TRADE_COLS} FROM paper_trades t "
                f"WHERE {where_sql} ORDER BY t.ts_model_ran_ms",
                tuple(params),
            ).fetchall()
        except Exception:
            logger.warning("SQLite unavailable during get_resolved_trades")
            return []
        return [self._decorate_trade(self._row_to_dict(r)) for r in rows]


_store = DataStore()


def get_store() -> DataStore:
    return _store
