from __future__ import annotations
"""
Execution gates, adverse selection model, Sanderink gate, position sizer.
Spec v2.6, Section 5.

Four-stage gate structure replacing original seven correlated binary checks.
"""

import numpy as np
from scipy.stats import beta as beta_dist
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_curve


# ----------------------------------------------------------
# STAGE 1: STRUCTURAL GATES
# ----------------------------------------------------------


class ExecutionGates:
    """
    Four-stage gate structure. Correlated gates fire together during
    high-volatility periods — exactly when edge may be strongest —
    creating asymmetric suppression.
    """

    @staticmethod
    def check_structural(
        seconds_to_resolution: float,
        p_market: float,
        fee_regime_active: bool,
    ) -> tuple[bool, str | None]:
        """
        fee_regime_active: computed by FeeRegimeChecker.is_active().
        Returns (pass, reason | None)
        """
        if seconds_to_resolution < 90:
            return False, "t_remaining < 90s"
        if seconds_to_resolution < 180 and abs(p_market - 0.5) < 0.10:
            return False, "near_expiry_noise_zone"
        if not fee_regime_active:
            return False, "outside_trained_fee_regime"
        return True, None


# ----------------------------------------------------------
# STAGE 2: ADVERSE SELECTION COMPOSITE
# ----------------------------------------------------------


class AdverseSelectionModel:
    """
    Logistic regression on three inputs: D (depth change), S (spread change),
    B (Binance spread percentile). Fit once on calibration sample.

    Refit trigger: distribution PSI > 0.25 on MLOFI distribution.
    Minimum 30 adversely-selected events before first fit.
    """

    def __init__(self):
        self.beta_0: float | None = None
        self.beta_D: float | None = None
        self.beta_S: float | None = None
        self.beta_B: float | None = None
        self.fpr_threshold: float | None = None
        self.is_fitted: bool = False

    def fit(
        self,
        depth_changes: np.ndarray,
        spread_changes: np.ndarray,
        binance_pcts: np.ndarray,
        labels: np.ndarray,
        fpr_target: float = 0.20,
    ) -> None:
        """
        labels: 1 = adversely selected (resolved against position), 0 = clean
        Minimum 30 positive labels required before calling.
        Sets fpr_threshold at the score achieving fpr_target false positive rate.
        """
        assert sum(labels) >= 30, (
            "Minimum 30 adversely-selected events required before fitting"
        )
        X = np.column_stack([depth_changes, spread_changes, binance_pcts])
        model = LogisticRegression()
        model.fit(X, labels)
        self.beta_0 = model.intercept_[0]
        self.beta_D, self.beta_S, self.beta_B = model.coef_[0]

        # Calibrate threshold at fpr_target on calibration sample
        scores = model.predict_proba(X)[:, 1]
        fpr, tpr, thresholds = roc_curve(labels, scores)
        valid = fpr <= fpr_target
        self.fpr_threshold = (
            float(thresholds[valid][-1]) if valid.any() else 0.5
        )
        self.is_fitted = True

    def score(
        self, depth_change: float, spread_change: float, binance_pct: float
    ) -> float:
        """Returns P(adverse) using stored coefficients."""
        assert self.is_fitted, "Call fit() before score()"
        logit = (
            self.beta_0
            + self.beta_D * depth_change
            + self.beta_S * spread_change
            + self.beta_B * binance_pct
        )
        return 1.0 / (1.0 + np.exp(-logit))

    def check(
        self, depth_change: float, spread_change: float, binance_pct: float
    ) -> tuple[bool, str | None, float]:
        """Returns (pass, reason, composite_score)."""
        composite = self.score(depth_change, spread_change, binance_pct)
        if composite >= self.fpr_threshold:
            return False, f"adverse_composite={composite:.3f}", composite
        return True, None, composite


# ----------------------------------------------------------
# STAGE 3: NET EDGE CONDITION
# ----------------------------------------------------------


def compute_net_edge(
    p_model: float,
    p_market: float,
    payout: float,
    spread_t: float,
    fee_t: float,
) -> float:
    """
    NE_t = p_model * payout - (1 - p_model) - spread_t - fee_t
    where payout = (1 - p_market) / p_market  (b in Kelly notation)
    fee_t MUST be queried per-contract from Polymarket API.
    """
    return p_model * payout - (1 - p_model) - spread_t - fee_t


def check_net_edge(
    p_model: float,
    p_market: float,
    payout: float,
    spread_t: float,
    fee_t: float,
) -> tuple[bool, str | None, float]:
    """Returns (pass, reason, ne_t)."""
    ne_t = compute_net_edge(p_model, p_market, payout, spread_t, fee_t)
    if ne_t <= 0:
        return False, f"NE_t={ne_t:.5f}", ne_t
    return True, None, ne_t


# ----------------------------------------------------------
# STAGE 4: BAYESIAN SANDERINK MODEL RELIABILITY GATE
# ----------------------------------------------------------


class SanderinkGate:
    """
    Beta posterior on true win rate. Operational from day one
    (no minimum n required — unlike frequentist test which needs n≈152).
    Updated after each resolved contract, not per trade.
    """

    def __init__(
        self,
        prior_wins: int = 52,
        prior_losses: int = 48,
        suspend_threshold: float = 0.80,
    ):
        self.alpha = prior_wins
        self.beta = prior_losses
        self.suspend_threshold = suspend_threshold
        self.n_observations = 0

    def update(self, won: bool) -> None:
        """Call after each contract resolution."""
        if won:
            self.alpha += 1
        else:
            self.beta += 1
        self.n_observations += 1

    def compute_breakeven_win_rate(self, fee_t: float, payout_t: float) -> float:
        """
        Dynamic break-even win rate per contract.
        payout_t = (1 - p_market) / p_market (net fractional odds)
        p*_t = (1 + fee_t) / (payout_t + 1)

        fee_t must be expressed as a fraction of STAKE (e.g., 0.02 for 2%).
        """
        return (1 + fee_t) / (payout_t + 1)

    def check(
        self, fee_t: float, payout_t: float
    ) -> tuple[bool, str | None, float, float]:
        """
        Suspend if P(p_true <= p*_t | data) > suspend_threshold.
        Returns (pass, reason, prob_at_or_below, p_star)
        """
        p_star = self.compute_breakeven_win_rate(fee_t, payout_t)
        posterior = beta_dist(self.alpha, self.beta)
        prob_at_or_below = posterior.cdf(p_star)
        if prob_at_or_below > self.suspend_threshold:
            return (
                False,
                f"sanderink={prob_at_or_below:.3f}",
                prob_at_or_below,
                p_star,
            )
        return True, None, prob_at_or_below, p_star


# ----------------------------------------------------------
# POSITION SIZER
# ----------------------------------------------------------


class PositionSizer:
    """
    Fractional Kelly: f* = (b*p - q) / b
    b = (1 - p_market) / p_market
    Use 0.25x-0.50x throughout paper trading.
    """

    def __init__(self, kelly_fraction: float = 0.25):
        self.kelly_fraction = kelly_fraction

    def compute(
        self, p_model: float, p_market: float, bankroll_usdc: float
    ) -> dict:
        b = (1 - p_market) / p_market
        q = 1 - p_model
        f_full = (b * p_model - q) / b
        f_fractional = max(0.0, f_full * self.kelly_fraction)
        return {
            "kelly_full": f_full,
            "kelly_fractional": f_fractional,
            "position_usdc": f_fractional * bankroll_usdc,
        }
