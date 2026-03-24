from __future__ import annotations
"""
Main execution loop and trainer initialization.
Spec v2.6, Section 8 (init_trainer) and Section 5 (orchestration).

init_trainer() calls validate_gmadl_inputs at startup — this is the
enforcement point that cannot be skipped.
"""

import logging

import torch

from execution.gates import (
    ExecutionGates,
    AdverseSelectionModel,
    SanderinkGate,
    PositionSizer,
    compute_net_edge,
    check_net_edge,
)
from execution.fee_regime import FeeRegimeChecker
from models.loss import validate_gmadl_inputs

logger = logging.getLogger(__name__)


def init_trainer(
    sample_returns_true: torch.Tensor,
    sample_returns_pred: torch.Tensor,
    prior_wins: int = 52,
    prior_losses: int = 48,
    suspend_threshold: float = 0.80,
    kelly_fraction: float = 0.25,
) -> tuple[SanderinkGate, PositionSizer]:
    """
    Called once before the training loop with a pre-computed representative
    sample. Caller is responsible for model inference and the 2*p-1 conversion,
    which is model-track-specific.

    Example call site:
        with torch.no_grad():
            probs_a = track_a(X_val_tabular[:64])
            probs_b = track_b(X_val_sequence[:64])
            probs   = meta_learner.predict(probs_a.numpy(), probs_b.numpy())
        returns_pred = torch.tensor(2 * probs - 1, dtype=torch.float32)
        returns_true = torch.tensor(
            [1.0 if y == 1 else -1.0 for y in y_val[:64]], dtype=torch.float32
        )
        sanderink, sizer = init_trainer(returns_true, returns_pred)
    """
    # Raises AssertionError if raw probabilities passed instead of 2*p-1.
    validate_gmadl_inputs(sample_returns_true, sample_returns_pred)

    sanderink = SanderinkGate(
        prior_wins=prior_wins,
        prior_losses=prior_losses,
        suspend_threshold=suspend_threshold,
    )
    sizer = PositionSizer(kelly_fraction=kelly_fraction)
    return sanderink, sizer


class ExecutionLoop:
    """
    Main execution loop orchestrating all four gate stages.
    Evaluates each candidate trade through the full gate pipeline
    and logs the result (executed or suppressed with reason).
    """

    def __init__(
        self,
        fee_regime_checker: FeeRegimeChecker,
        adverse_model: AdverseSelectionModel | None,
        sanderink: SanderinkGate,
        sizer: PositionSizer,
        log_writer=None,
    ):
        self.gates = ExecutionGates()
        self.fee_regime_checker = fee_regime_checker
        self.adverse_model = adverse_model
        self.sanderink = sanderink
        self.sizer = sizer
        self.log_writer = log_writer

    def evaluate_candidate(
        self,
        p_model: float,
        p_market: float,
        seconds_to_resolution: float,
        current_ts_utc,
        fee_t: float,
        spread_t: float,
        depth_change: float | None = None,
        spread_change: float | None = None,
        binance_pct: float | None = None,
        bankroll_usdc: float = 0.0,
        **extra_fields,
    ) -> dict:
        """
        Run candidate through all four gate stages.
        Returns full trade record (for logging) including all gate results.
        """
        import datetime

        payout = (1 - p_market) / p_market if p_market > 0 else 0.0

        record = {
            "timestamp_ms": int(current_ts_utc.timestamp() * 1000)
            if isinstance(current_ts_utc, datetime.datetime)
            else int(current_ts_utc),
            "p_model": p_model,
            "p_market": p_market,
            "payout": payout,
            "fee_t": fee_t,
            "spread_t": spread_t,
            "seconds_to_resolution": seconds_to_resolution,
            "executed": 0,
            "suppression_reason": None,
        }
        record.update(extra_fields)

        # Stage 1: Structural gates
        fee_active = self.fee_regime_checker.is_active(current_ts_utc)
        s1_pass, s1_reason = self.gates.check_structural(
            seconds_to_resolution, p_market, fee_active
        )
        record["gate_structural_pass"] = int(s1_pass)
        record["gate_structural_reason"] = s1_reason
        if not s1_pass:
            record["suppression_reason"] = s1_reason
            self._log(record)
            return record

        # Stage 2: Adverse selection composite
        if self.adverse_model and self.adverse_model.is_fitted:
            s2_pass, s2_reason, s2_composite = self.adverse_model.check(
                depth_change or 0.0,
                spread_change or 0.0,
                binance_pct or 0.0,
            )
            record["gate_adverse_pass"] = int(s2_pass)
            record["gate_adverse_composite_value"] = s2_composite
            record["gate_adverse_fpr_threshold"] = self.adverse_model.fpr_threshold
            if not s2_pass:
                record["suppression_reason"] = s2_reason
                self._log(record)
                return record
        else:
            record["gate_adverse_pass"] = 1  # Not yet fitted — pass through

        # Stage 3: Net edge
        ne_t = compute_net_edge(p_model, p_market, payout, spread_t, fee_t)
        record["ne_t_computed"] = ne_t
        s3_pass, s3_reason, _ = check_net_edge(
            p_model, p_market, payout, spread_t, fee_t
        )
        record["gate_ne_positive"] = int(s3_pass)
        if not s3_pass:
            record["suppression_reason"] = s3_reason
            self._log(record)
            return record

        # Stage 4: Sanderink gate
        payout_t = payout
        s4_pass, s4_reason, s4_posterior, s4_pstar = self.sanderink.check(
            fee_t, payout_t
        )
        record["gate_sanderink_pass"] = int(s4_pass)
        record["gate_sanderink_posterior"] = s4_posterior
        record["gate_sanderink_pstar"] = s4_pstar
        record["gate_sanderink_n_obs"] = self.sanderink.n_observations
        if not s4_pass:
            record["suppression_reason"] = s4_reason
            self._log(record)
            return record

        # All gates passed — execute
        sizing = self.sizer.compute(p_model, p_market, bankroll_usdc)
        record["executed"] = 1
        record["kelly_full"] = sizing["kelly_full"]
        record["kelly_fractional"] = sizing["kelly_fractional"]
        record["position_size_fraction"] = sizing["kelly_fractional"]
        self._log(record)
        return record

    def _log(self, record: dict) -> None:
        """Log the trade record if a writer is available."""
        if self.log_writer:
            self.log_writer.enqueue(record)
