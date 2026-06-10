"""SQLite-backed ledger.

Drop-in peer of ``trading/ledger.py``. The paper trader is rewired to
call ``SQLiteLedger`` in T16. The legacy JSONL ledger is preserved
unchanged for migration use.

This module is intentionally narrow: it knows how to **persist** rows
that already carry a complete provenance envelope. It does not compute
hashes, schedule resolutions, or evaluate filters; those are the paper
trader's responsibility.
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

from storage.provenance import ProvenanceEnvelope

logger = logging.getLogger(__name__)
from storage.window_planner import ResolutionRow


def _new_id_prefix() -> str:
    return uuid.uuid4().hex


def _utc_iso_seconds(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


class SQLiteLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Predictions
    # ------------------------------------------------------------------
    def log_prediction_set(
        self,
        *,
        envelope: ProvenanceEnvelope,
        symbol: str,
        ts_model_ran_ms: int,
        ts_contract_open_ms: int,
        rows: Iterable[ResolutionRow],
        pred_proba_raw: float,
        pred_proba_calibrated: float,
        pred_direction: str,
        above_threshold: bool,
        warmup: bool,
        platform: str,
        p_market: Optional[float] = None,
        p_model_minus_market: Optional[float] = None,
        utc_hour: Optional[int] = None,
        day_of_week: Optional[int] = None,
        is_weekend: Optional[int] = None,
        relative_spread: Optional[float] = None,
        trade_eligible: bool = True,
        regime_volatility: Optional[str] = None,
        regime_liquidity: Optional[str] = None,
        regime_trend: Optional[str] = None,
        features_json: Optional[str] = None,
        served_contract_json: Optional[str] = None,
    ) -> str:
        """Insert evaluation rows for a single boundary.

        Returns the first row's ``prediction_id`` (the 300s eval row,
        which serves as the canonical prediction for downstream linking).
        All rows share the same prefix with a window+type suffix.

        ``features_json`` / ``served_contract_json`` are written on the
        canonical (first) row only — the vector is identical across a
        set's window rows, so duplicating it would triple storage.
        """
        prefix = _new_id_prefix()
        canonical_id: Optional[str] = None
        with self._lock:
            for row in rows:
                pid = f"{prefix}_{row.market_window_seconds}{row.resolution_type[0]}"
                is_canonical = canonical_id is None
                if is_canonical:
                    canonical_id = pid
                self._conn.execute(
                    "INSERT INTO predictions ("
                    " prediction_id, model_name, model_artifact_hash,"
                    " feature_names_hash, feature_version,"
                    " training_horizon_seconds, train_window_start,"
                    " train_window_end, train_cutoff,"
                    " registry_load_generation,"
                    " policy_config_hash, decision_policy_version,"
                    " calibration_map_hash,"
                    " symbol, market_window_seconds, resolution_type,"
                    " ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms,"
                    " pred_proba_raw, pred_proba_calibrated, pred_direction,"
                    " above_threshold, warmup, trade_eligible, platform,"
                    " p_market, p_model_minus_market,"
                    " utc_hour, day_of_week, is_weekend, relative_spread,"
                    " regime_volatility, regime_liquidity, regime_trend,"
                    " features_json, served_contract_json"
                    ") VALUES ("
        " ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?"
                    ")",
                    (
                        pid, envelope.model_name, envelope.model_artifact_hash,
                        envelope.feature_names_hash, envelope.feature_version,
                        envelope.training_horizon_seconds,
                        envelope.train_window_start, envelope.train_window_end,
                        envelope.train_cutoff, envelope.registry_load_generation,
                        envelope.policy_config_hash, envelope.decision_policy_version,
                        envelope.calibration_map_hash,
                        symbol, row.market_window_seconds, row.resolution_type,
                        ts_model_ran_ms, ts_contract_open_ms, row.ts_resolve_at_ms,
                        float(pred_proba_raw), float(pred_proba_calibrated),
                        pred_direction, int(above_threshold),
                        int(warmup), int(trade_eligible), platform,
                        p_market, p_model_minus_market,
                        utc_hour, day_of_week, is_weekend, relative_spread,
                        regime_volatility, regime_liquidity, regime_trend,
                        features_json if is_canonical else None,
                        served_contract_json if is_canonical else None,
                    ),
                )
            assert canonical_id is not None, "rows must include at least one row"
            return canonical_id

        # ------------------------------------------------------------------
        # Paper trades
        # ------------------------------------------------------------------
    def log_paper_trade(
        self,
        *,
        prediction_id: str,
        envelope: ProvenanceEnvelope,
        symbol: str,
        market_window_seconds: int,
        resolution_type: str,
        ts_model_ran_ms: int,
        ts_contract_open_ms: int,
        ts_resolve_at_ms: int,
        pred_proba_raw: float,
        pred_proba_calibrated: float,
        pred_direction: str,
        confidence_threshold_used: float,
        simulated_stake_usdc: float,
        decision_outcome: str,
        decision_reason: Optional[str],
        ev_estimate: Optional[float],
        kelly_fraction_capped: Optional[float],
        final_size_usdc: Optional[float],
        order_type: Optional[str],
        warmup: bool,
        platform: str,
        p_market: Optional[float] = None,
        suppressed_reason: Optional[str] = None,
        filter_mode: Optional[str] = None,
    ) -> str:
        trade_id = _new_id_prefix() + "_t"
        with self._lock:
            self._conn.execute(
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
                " p_market, suppressed_reason, filter_mode,"
                " warmup, platform,"
                " decision_outcome, decision_reason,"
                " ev_estimate, kelly_fraction_capped,"
" final_size_usdc, order_type"
        ") VALUES ("
        " ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?"
                ")",
                (
                    trade_id, prediction_id,
                    envelope.model_name, envelope.model_artifact_hash,
                    envelope.policy_config_hash, envelope.decision_policy_version,
                    envelope.calibration_map_hash,
                    envelope.registry_load_generation, envelope.feature_version,
                    envelope.training_horizon_seconds,
                    symbol, market_window_seconds, resolution_type,
                    ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms,
                    float(pred_proba_raw), float(pred_proba_calibrated),
                    pred_direction, float(confidence_threshold_used),
                    simulated_stake_usdc, p_market, suppressed_reason, filter_mode,
                    int(warmup), platform,
                    decision_outcome, decision_reason,
                    ev_estimate, kelly_fraction_capped,
                    final_size_usdc, order_type,
                ),
            )
        return trade_id

    # ------------------------------------------------------------------
    # Compact decision (inline fields on the prediction row)
    # ------------------------------------------------------------------
    def log_compact_decision(
        self,
        *,
        prediction_id: str,
        decision_outcome: str,
        decision_reason: Optional[str],
        ev_estimate: Optional[float],
        kelly_fraction_capped: Optional[float],
        final_size_usdc: Optional[float],
        order_type: Optional[str],
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE predictions SET"
                "  decision_outcome = ?,"
                "  decision_reason = ?,"
                "  ev_estimate = ?,"
                "  kelly_fraction_capped = ?,"
                "  final_size_usdc = ?,"
                "  order_type = ?"
                " WHERE prediction_id = ?",
                (decision_outcome, decision_reason, ev_estimate,
                 kelly_fraction_capped, final_size_usdc, order_type,
                 prediction_id),
            )

    # ------------------------------------------------------------------
    # Resolution writers
    # ------------------------------------------------------------------
    def record_resolution(
        self,
        *,
        prediction_id: str,
        ts_resolved_ms: int,
        price_at_open: float,
        price_at_close: float,
        contract_result: str,
        prediction_correct: bool,
        feed_calibrator: bool = False,
    ) -> None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT model_name, symbol, market_window_seconds,"
                " resolution_type, pred_proba_calibrated, warmup,"
                " regime_volatility, regime_liquidity"
                " FROM predictions WHERE prediction_id = ?",
                (prediction_id,),
            ).fetchone()
            if row is None:
                logger.warning("record_resolution: unknown prediction_id %r, skipping", prediction_id)
                return
            self._conn.execute(
                "UPDATE predictions SET"
                " resolved = 1, ts_resolved_ms = ?,"
                " price_at_open = ?, price_at_close = ?,"
                " contract_result = ?, prediction_correct = ?"
                " WHERE prediction_id = ?",
                (ts_resolved_ms, price_at_open, price_at_close,
                 contract_result, int(prediction_correct), prediction_id),
            )
            if feed_calibrator:
                self._conn.execute(
                    "INSERT INTO calibration_outcomes ("
                    " ts, prediction_id, model_name, symbol,"
                    " market_window_seconds, resolution_type,"
                    " side_conf, won, warmup,"
                    " regime_volatility, regime_liquidity"
                    ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (_utc_iso_seconds(ts_resolved_ms), prediction_id,
                     row["model_name"], row["symbol"],
                     row["market_window_seconds"], row["resolution_type"],
                     float(row["pred_proba_calibrated"]),
                     int(prediction_correct), row["warmup"],
                     row["regime_volatility"], row["regime_liquidity"]),
        )

    def record_trade_resolution(
        self,
        *,
        trade_id: str,
        ts_resolved_ms: int,
        price_at_close: float,
        contract_result: str,
        prediction_correct: bool,
        gross_pnl: float,
        fee_paid: float,
        net_pnl: float,
        trade_result: str,
        pnl_method: str,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE paper_trades SET"
                "  resolved = 1, ts_resolved_ms = ?,"
                "  price_at_close = ?, contract_result = ?,"
                "  prediction_correct = ?,"
                "  gross_pnl = ?, fee_paid = ?, net_pnl = ?,"
                "  trade_result = ?, pnl_method = ?"
                " WHERE trade_id = ?",
                (ts_resolved_ms, price_at_close, contract_result,
                 int(prediction_correct), gross_pnl, fee_paid, net_pnl,
                 trade_result, pnl_method, trade_id),
            )
