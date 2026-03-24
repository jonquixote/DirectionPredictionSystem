from __future__ import annotations
"""
Feature builder — all tiers.
Spec v2.6, Section 4.

Tiers: 1A (MLOFI), 1B (spread), 1C (VWAP-mid), 1D (time-to-res),
       2A (Roll), 2B (cross-asset OFI), 2C (VPIN), 3 (momentum)
"""

import numpy as np


class FeatureBuilder:
    """
    Computes all features at each 1-minute bar.
    """

    def compute_relative_spread(self, bid: float, ask: float) -> float:
        """Tier 1B. Execution gate: suppress above 95th pct rolling threshold."""
        mid = (bid + ask) / 2
        return (ask - bid) / mid if mid > 0 else 0.0

    def compute_vwap_deviation(
        self, vwap_buy: float, vwap_sell: float, mid: float
    ) -> tuple[float, float]:
        """
        Tier 1C. Preprint source — include, validate with ablation.
        Asymmetric reversion mechanism.
        """
        if mid == 0:
            return 0.0, 0.0
        return (vwap_buy - mid) / mid, (vwap_sell - mid) / mid

    def compute_roll_measure(self, prices: list[float], window: int = 15) -> float:
        """
        Tier 2A. Roll (1984) implied spread estimator.
        Formula: 2 * sqrt(|cov(delta_p_t, delta_p_{t-1})|)
        """
        if len(prices) < window + 1:
            return 0.0
        returns = np.diff(prices[-window - 1 :])
        cov = np.cov(returns[:-1], returns[1:])[0, 1]
        return 2 * np.sqrt(abs(cov))

    def compute_vpin(
        self, volume_buys: list[float], volume_sells: list[float], window: int = 15
    ) -> float:
        """
        Tier 2C. EMPIRICALLY CONTESTED — low prior weight.
        Crypto VPIN ~0.45-0.47 vs ~0.22 for equities.
        Formula: (1/W) * sum(|V_sell - V_buy| / V_total)
        """
        if len(volume_buys) < window:
            return 0.5
        total_imbalance = sum(
            abs(volume_sells[i] - volume_buys[i]) / (volume_buys[i] + volume_sells[i])
            for i in range(-window, 0)
            if (volume_buys[i] + volume_sells[i]) > 0
        )
        return total_imbalance / window

    def compute_time_to_resolution(
        self, market_open_time: float, current_time: float, horizon_minutes: int
    ) -> float:
        """Tier 1D. Structural gate — not a predictor."""
        resolution_time = market_open_time + horizon_minutes * 60
        return max(0, resolution_time - current_time)

    def compute_cross_asset_btc_features(
        self, btc_mlofi: float, btc_roll: float
    ) -> dict:
        """
        Tier 2B. Cross-asset features for ETH, SOL, XRP prediction.
        """
        return {"btc_mlofi": btc_mlofi, "btc_roll": btc_roll}

    def compute_momentum(
        self, prices: list[float], windows: list[int] | None = None
    ) -> dict:
        """
        Tier 3. Momentum features at multiple windows.
        """
        if windows is None:
            windows = [5, 15, 30]
        result = {}
        for w in windows:
            if len(prices) >= w + 1:
                ret = (prices[-1] - prices[-w - 1]) / prices[-w - 1] if prices[-w - 1] != 0 else 0.0
                result[f"momentum_{w}"] = ret
            else:
                result[f"momentum_{w}"] = 0.0
        return result
