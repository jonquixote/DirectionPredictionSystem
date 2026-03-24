"""
Tests for execution gates, FeeRegimeChecker, Sanderink, AdverseSelectionModel.
Spec v2.6, Section 14.
"""

import sys
import os
import datetime

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from execution.gates import (
    ExecutionGates,
    AdverseSelectionModel,
    SanderinkGate,
    PositionSizer,
    compute_net_edge,
    check_net_edge,
)
from execution.fee_regime import FeeRegimeChecker
from config import TRAINING_BOUNDARIES


# ----------------------------------------------------------
# STRUCTURAL GATE TESTS
# ----------------------------------------------------------

class TestStructuralGate:

    def test_blocks_t_remaining_below_90s(self):
        """Structural gate: blocks t_remaining < 90s."""
        passed, reason = ExecutionGates.check_structural(
            seconds_to_resolution=60, p_market=0.5, fee_regime_active=True
        )
        assert not passed
        assert reason == "t_remaining < 90s"

    def test_blocks_near_expiry_noise_zone(self):
        """Structural gate: blocks near-expiry noise zone."""
        passed, reason = ExecutionGates.check_structural(
            seconds_to_resolution=120,  # < 180
            p_market=0.48,              # |0.48 - 0.5| = 0.02 < 0.10
            fee_regime_active=True,
        )
        assert not passed
        assert reason == "near_expiry_noise_zone"

    def test_blocks_when_fee_regime_inactive(self):
        """Structural gate: blocks when FeeRegimeChecker.is_active() returns False."""
        passed, reason = ExecutionGates.check_structural(
            seconds_to_resolution=300, p_market=0.5, fee_regime_active=False
        )
        assert not passed
        assert reason == "outside_trained_fee_regime"

    def test_passes_when_all_conditions_met(self):
        """Structural gate: passes when all conditions met."""
        passed, reason = ExecutionGates.check_structural(
            seconds_to_resolution=300, p_market=0.5, fee_regime_active=True
        )
        assert passed
        assert reason is None


# ----------------------------------------------------------
# FEE REGIME CHECKER TESTS
# ----------------------------------------------------------

class TestFeeRegimeChecker:

    def test_returns_false_when_boundary_crossed(self):
        """FeeRegimeChecker: returns False when current date crosses a post-training boundary."""
        checker = FeeRegimeChecker(
            boundaries=TRAINING_BOUNDARIES,
            trained_start="2023-04-01",
            trained_end="2023-08-01",
        )
        # Current date after the 2023-09-07 boundary
        current = datetime.datetime(2023, 10, 1, tzinfo=datetime.timezone.utc)
        assert not checker.is_active(current)

    def test_returns_true_within_trained_regime(self):
        """FeeRegimeChecker: returns True for dates within trained regime."""
        checker = FeeRegimeChecker(
            boundaries=TRAINING_BOUNDARIES,
            trained_start="2023-04-01",
            trained_end="2023-08-01",
        )
        # Current date within the same regime (post 2023-03-22, before 2023-09-07)
        current = datetime.datetime(2023, 7, 1, tzinfo=datetime.timezone.utc)
        assert checker.is_active(current)


# ----------------------------------------------------------
# NET EDGE TESTS
# ----------------------------------------------------------

class TestNetEdge:

    def test_ne_zero_when_equal_probs_no_costs(self):
        """NE_t = 0 when p_model == p_market and spread=fee=0."""
        p = 0.6
        payout = (1 - p) / p  # b
        ne = compute_net_edge(p_model=p, p_market=p, payout=payout, spread_t=0, fee_t=0)
        # NE_t = p*b - (1-p) = p*(1-p)/p - (1-p) = (1-p) - (1-p) = 0
        assert ne == pytest.approx(0.0, abs=1e-10)

    def test_ne_negative_with_realistic_costs(self):
        """NE_t < 0 with realistic spread/fee and zero edge."""
        p = 0.5
        payout = (1 - p) / p  # = 1.0
        ne = compute_net_edge(
            p_model=p, p_market=p, payout=payout,
            spread_t=0.01, fee_t=0.02,
        )
        # NE_t = 0.5*1 - 0.5 - 0.01 - 0.02 = -0.03
        assert ne < 0
        assert ne == pytest.approx(-0.03, abs=1e-10)


# ----------------------------------------------------------
# SANDERINK GATE TESTS
# ----------------------------------------------------------

class TestSanderinkGate:

    def test_suspends_when_posterior_high(self):
        """Sanderink: suspends when posterior > 0.80."""
        gate = SanderinkGate(prior_wins=52, prior_losses=48, suspend_threshold=0.80)
        # Add many losses to shift posterior
        for _ in range(100):
            gate.update(won=False)
        # With very high beta, P(p_true <= breakeven) should be high
        passed, reason, prob, p_star = gate.check(fee_t=0.02, payout_t=1.0)
        assert not passed
        assert "sanderink" in reason

    def test_prior_mean_correct(self):
        """Sanderink: Beta(52,48) prior mean = 0.52."""
        gate = SanderinkGate(prior_wins=52, prior_losses=48)
        mean = gate.alpha / (gate.alpha + gate.beta)
        assert mean == pytest.approx(0.52, abs=1e-10)

    def test_breakeven_at_zero_ne(self):
        """Sanderink: p*_t formula — verify break-even at zero NE_t."""
        gate = SanderinkGate()
        # At fee=0, payout=1 (p_market=0.5): p* = (1+0)/(1+1) = 0.5
        p_star = gate.compute_breakeven_win_rate(fee_t=0.0, payout_t=1.0)
        assert p_star == pytest.approx(0.5, abs=1e-10)

        # At fee=0.02, payout=1: p* = 1.02/2 = 0.51
        p_star = gate.compute_breakeven_win_rate(fee_t=0.02, payout_t=1.0)
        assert p_star == pytest.approx(0.51, abs=1e-10)


# ----------------------------------------------------------
# ADVERSE SELECTION MODEL TESTS
# ----------------------------------------------------------

class TestAdverseSelectionModel:

    def test_fit_requires_30_adverse_events(self):
        """AdverseSelectionModel: raises AssertionError if fit called with < 30 adverse events."""
        model = AdverseSelectionModel()
        rng = np.random.default_rng(42)
        n = 100
        labels = np.zeros(n)
        labels[:10] = 1  # Only 10 adverse events < 30

        with pytest.raises(AssertionError, match="Minimum 30"):
            model.fit(
                rng.normal(size=n),
                rng.normal(size=n),
                rng.normal(size=n),
                labels,
            )

    def test_fpr_threshold_stored_after_fit(self):
        """AdverseSelectionModel: fpr_threshold stored as instance state after fit."""
        model = AdverseSelectionModel()
        rng = np.random.default_rng(42)
        n = 200
        labels = np.zeros(n)
        labels[:50] = 1  # 50 adverse events

        model.fit(
            rng.normal(size=n),
            rng.normal(size=n),
            rng.normal(size=n),
            labels,
        )
        assert model.is_fitted
        assert model.fpr_threshold is not None
        assert isinstance(model.fpr_threshold, float)

    def test_score_raises_before_fit(self):
        """AdverseSelectionModel: score() raises AssertionError before fit."""
        model = AdverseSelectionModel()
        with pytest.raises(AssertionError, match="Call fit"):
            model.score(0.1, 0.2, 0.3)
