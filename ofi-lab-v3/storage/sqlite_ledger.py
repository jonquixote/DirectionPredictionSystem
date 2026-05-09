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
from typing import Iterable, Optional

from storage.provenance import ProvenanceEnvelope
from storage.window_planner import ResolutionRow


def _new_id_prefix() -> str:
    return uuid.uuid4().hex


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
    ) -> str:
        """Insert one native + N evaluation rows for a single boundary.

        Returns the **native** row's ``prediction_id``. Evaluation rows
        share the same prefix with a window suffix.
        """
        prefix = _new_id_prefix()
        native_id: Optional[str] = None
        with self._lock:
            for row in rows:
                pid = f"{prefix}_{row.market_window_seconds}{row.resolution_type[0]}"
                if row.resolution_type == "native":
                    native_id = pid
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
                    " utc_hour, day_of_week, is_weekend, relative_spread"
                    ") VALUES ("
                    " ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?"
                    ")",
                    (
                        pid,
                        envelope.model_name, envelope.model_artifact_hash,
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
                    ),
                )
        assert native_id is not None, "rows must include exactly one native row"
        return native_id

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
