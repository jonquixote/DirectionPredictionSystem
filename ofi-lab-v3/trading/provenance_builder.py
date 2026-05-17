"""Provenance + audit-trail construction extracted from PaperTrader.

Owns the construction of ProvenanceEnvelope, compact decision records,
and verbose trace rows. These methods are pure data-shaping over the
underlying storage primitives in storage/ — relocating them keeps
PaperTrader focused on the prediction → trade pipeline.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from storage.policy_snapshot import PolicySnapshot
    from storage.sqlite_ledger import SQLiteLedger
    from storage.decision_trace import DecisionTraceWriter
    from storage.provenance import ProvenanceEnvelope
    from storage.registry_state import RegistryState
    from execution.calibration import CalibratorRegistry

from storage.provenance import calibration_map_hash, ProvenanceEnvelope
from storage.decision_trace import FilterEval

logger = logging.getLogger("provenance_builder")


class ProvenanceBuilder:
    """Pure data-shaping helpers for provenance envelopes and audit trails.

    Rather than copying the storage-object references at construction time,
    ProvenanceBuilder holds a back-reference to the owning PaperTrader instance
    and reads its attributes (``policy_snapshot``, ``decision_trace``, …) at
    call time.  This ensures that scripts which rebind ``trader.policy_snapshot``
    (e.g. ``replay_v2_features.py``) are automatically reflected without any
    extra plumbing.

    Parameters
    ----------
    trader:
        The PaperTrader instance that owns this builder.
    """

    def __init__(self, *, trader: object) -> None:
        self._trader = trader

    # ------------------------------------------------------------------
    # Public API (mirrors the 4 extracted PaperTrader methods)
    # ------------------------------------------------------------------

    def capture_policy_dict(self) -> dict:
        """Snapshot the runtime-mutable filter/threshold/Kelly config.

        Canonical input to PolicySnapshot.capture(). Any field that
        influences a trade decision and can change at runtime must
        appear here.
        """
        f = self._trader.filters
        return {
            "confidence_threshold": f.get("confidence_threshold"),
            "per_symbol_confidence": f.get("per_symbol_confidence", {}),
            "kelly_fraction": f.get("kelly_fraction"),
            "ev_threshold": f.get("ev_threshold", 0.0),
            "circuit_breaker_drawdown": f.get("circuit_breaker_drawdown"),
            "clob_divergence_min_edge": f.get("clob_divergence_min_edge"),
            "filter_mode": f.get("filter_mode"),
            "blackout_hours_utc": list(f.get("blackout_hours_utc", [])),
        }

    def build_envelope(self, model_name: str, *, platform: str) -> ProvenanceEnvelope:
        """Construct the provenance envelope for the next prediction.

        Re-captures the policy snapshot (cheap; only writes a new audit row
        if the canonical form changed) and re-hashes the active calibration
        map for the model so any out-of-band refit flows through.
        """
        t = self._trader
        meta = t._model_meta[model_name]
        art_hash = t._model_envelopes[model_name]["model_artifact_hash"]
        fname_hash = t._model_envelopes[model_name]["feature_names_hash"]
        policy_v, policy_h = t.policy_snapshot.capture(
            self.capture_policy_dict(), initiated_by="prediction"
        )
        cal = t.calibrators.get(model_name, meta["symbol"],
                                meta["training_horizon_seconds"])
        cal_map = {"method": "binmap", "bins": list(cal._bins)}
        cal_h = calibration_map_hash(cal_map)
        return ProvenanceEnvelope(
            model_name=model_name,
            model_artifact_hash=art_hash,
            feature_names_hash=fname_hash,
            feature_version=meta["feature_version"],
            training_horizon_seconds=meta["training_horizon_seconds"],
            train_window_start=meta["train_window_start"],
            train_window_end=meta["train_window_end"],
            train_cutoff=meta["train_cutoff"],
            registry_load_generation=t.registry_state.current_generation(),
            policy_config_hash=policy_h,
            decision_policy_version=policy_v,
            calibration_map_hash=cal_h,
            platform=platform,
        )

    def record_compact_decision(
        self,
        *,
        prediction_id: str,
        outcome: str,
        reason: Optional[str],
        ev_estimate: Optional[float],
        kelly_fraction_capped: Optional[float],
        final_size_usdc: Optional[float],
        order_type: Optional[str],
    ) -> None:
        """UPDATE the prediction row with the inline compact-decision fields."""
        self._trader.sqlite_ledger.log_compact_decision(
            prediction_id=prediction_id,
            decision_outcome=outcome,
            decision_reason=reason,
            ev_estimate=ev_estimate,
            kelly_fraction_capped=kelly_fraction_capped,
            final_size_usdc=final_size_usdc,
            order_type=order_type,
        )

    def write_verbose_trace_for_v2_filters(
        self,
        *,
        prediction_id: str,
        envelope: ProvenanceEnvelope,
        filter_inputs: dict,
        kelly_raw: Optional[float],
        kelly_capped: Optional[float],
        bankroll_used: Optional[float],
        per_trade_cap_usdc: Optional[float],
        fee_model: str,
        fee_amount: Optional[float],
        platform_gate: Optional[dict],
        warmup: bool,
        consensus_data: Optional[dict],
    ) -> None:
        """Adapter from v2 filter dict to FilterEval rows.

        ``filter_inputs`` shape: {name: (threshold, input_value, passed)}.
        """
        filters = [
            FilterEval(name=n, threshold=t, input_value=v, passed=bool(p))
            for n, (t, v, p) in filter_inputs.items()
        ]
        self._trader.decision_trace.write(
            prediction_id=prediction_id,
            filters=filters,
            kelly_raw=kelly_raw, kelly_capped=kelly_capped,
            bankroll_used=bankroll_used,
            per_trade_cap_usdc=per_trade_cap_usdc,
            fee_model=fee_model, fee_amount=fee_amount,
            platform_gate=platform_gate,
            warmup=warmup, consensus_data=consensus_data,
            policy_config_hash=envelope.policy_config_hash,
            calibration_map_hash=envelope.calibration_map_hash,
            registry_load_generation=envelope.registry_load_generation,
        )
