"""
FeatureConfirmationFilter — Phase 1 of APFS.

Checks whether real-time features *confirm* or *oppose* the model's
directional prediction.  Each sub-filter tracks its own empirical win-rate
lift and auto-adapts after N resolutions.

Design principles
─────────────────
* Cold-start safe — all multipliers default to 1.0 (identity).
* Additive, not multiplicative — a filter can only improve or maintain
  the baseline.  If it consistently hurts, it auto-disables (multiplier → 1.0).
* Per (model, symbol, duration) — each combination maintains its own state.
* Observable — every filter decision is returned so the caller can log it.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("apfs.feature_confirmation")

# ── Defaults ──────────────────────────────────────────────────────
DEFAULT_STATE_DIR = "/data/apfs"
MIN_REFIT_OUTCOMES = 30          # Minimum outcomes before recalculating weights
DECAY_ALPHA = 0.05               # EWM smoothing for incremental updates


# ── Sub-filter definitions ────────────────────────────────────────
@dataclass
class SubFilterResult:
    """Result from a single sub-filter evaluation."""
    name: str
    passed: bool
    value: Optional[float] = None   # The raw feature value used
    weight: float = 1.0             # Current learned weight (multiplier)


@dataclass
class FilterDecision:
    """Full decision output from the FeatureConfirmationFilter."""
    trade_score: float              # Final composite score
    base_confidence: float          # Model's side confidence
    sub_filters: List[SubFilterResult] = field(default_factory=list)
    should_trade: bool = False
    threshold: float = 0.52


class FeatureConfirmationFilter:
    """
    Evaluates a model prediction against real-time feature confirmations.

    Sub-filters (Phase 1):
      1. eth_mlofi_confirms  — ETH order flow agrees with BTC prediction
      2. mlofi_sign_agrees   — Instantaneous MLOFI sign agrees with prediction
      3. confidence_gate     — side_conf ≥ threshold (replaces old static gate)

    Each sub-filter contributes a multiplier (0.8–1.2) to the trade score.
    The trade score is compared against a threshold to decide trade/skip.
    """

    def __init__(
        self,
        state_dir: str = DEFAULT_STATE_DIR,
        trade_threshold: float = 0.52,
    ):
        self._state_dir = state_dir
        self._trade_threshold = trade_threshold
        self._lock = threading.Lock()

        # Per (model, symbol) learned weights: {filter_name: {"lift": float, "n": int}}
        self._weights: Dict[str, Dict[str, Any]] = {}
        # Outcome buffer for auto-adaptation
        self._outcomes: List[Dict] = []

        # File paths
        self._weights_path = os.path.join(state_dir, "feature_confirmation_weights.json")
        self._outcomes_path = os.path.join(state_dir, "feature_confirmation_outcomes.jsonl")

        self._load_state()

    # ── Public API ────────────────────────────────────────────────

    def evaluate(
        self,
        *,
        model_name: str,
        symbol: str,
        pred_direction: str,
        pred_proba: float,
        features: Dict[str, float],
    ) -> FilterDecision:
        """
        Evaluate a prediction against all sub-filters.

        Returns a FilterDecision with the composite trade score and
        individual sub-filter results.
        """
        # Side confidence: model's confidence on the side it chose
        side_conf = pred_proba if pred_direction == "up" else (1.0 - pred_proba)
        model_says_up = pred_direction == "up"

        key = f"{model_name}:{symbol}"
        weights = self._weights.get(key, {})

        sub_results = []

        # ── Sub-filter 1: eth_mlofi_confirms ──────────────────────
        eth_mlofi = features.get("eth_mlofi_30s_mean")
        if eth_mlofi is not None and symbol == "BTCUSDT":
            eth_says_up = eth_mlofi > 0
            agrees = eth_says_up == model_says_up
            w = weights.get("eth_mlofi_confirms", {}).get("weight", 1.0)
            sub_results.append(SubFilterResult(
                name="eth_mlofi_confirms",
                passed=agrees,
                value=eth_mlofi,
                weight=w,
            ))

        # ── Sub-filter 2: mlofi_sign_agrees ───────────────────────
        mlofi = features.get("mlofi")
        if mlofi is not None:
            mlofi_says_up = mlofi > 0
            agrees = mlofi_says_up == model_says_up
            w = weights.get("mlofi_sign_agrees", {}).get("weight", 1.0)
            sub_results.append(SubFilterResult(
                name="mlofi_sign_agrees",
                passed=agrees,
                value=mlofi,
                weight=w,
            ))

        # ── Sub-filter 3: vwap_deviation_agrees ───────────────────
        vwap_dev = features.get("vwap_deviation")
        if vwap_dev is not None:
            vwap_says_up = vwap_dev > 0
            agrees = vwap_says_up == model_says_up
            w = weights.get("vwap_deviation_agrees", {}).get("weight", 1.0)
            sub_results.append(SubFilterResult(
                name="vwap_deviation_agrees",
                passed=agrees,
                value=vwap_dev,
                weight=w,
            ))

        # ── Sub-filter 4: mlofi_30s_mean_agrees ──────────────────
        mlofi_30s = features.get("mlofi_30s_mean")
        if mlofi_30s is not None:
            mlofi30_says_up = mlofi_30s > 0
            agrees = mlofi30_says_up == model_says_up
            w = weights.get("mlofi_30s_mean_agrees", {}).get("weight", 1.0)
            sub_results.append(SubFilterResult(
                name="mlofi_30s_mean_agrees",
                passed=agrees,
                value=mlofi_30s,
                weight=w,
            ))

        # ── Compute composite trade score ─────────────────────────
        # Start with base confidence, then apply filter multipliers.
        # Each filter that PASSES gets its learned weight as a boost.
        # Each filter that FAILS gets a penalty (inverse weight).
        # Cold-start: all weights = 1.0, so score = side_conf.
        score = side_conf
        for sf in sub_results:
            if sf.passed:
                score *= sf.weight
            else:
                # Penalty: if weight > 1.0 (filter is valuable), penalize for missing it
                penalty = 1.0 / sf.weight if sf.weight > 1.0 else 1.0
                score *= penalty

        # Clamp to [0, 1]
        score = max(0.0, min(1.0, score))

        should_trade = score >= self._trade_threshold

        return FilterDecision(
            trade_score=score,
            base_confidence=side_conf,
            sub_filters=sub_results,
            should_trade=should_trade,
            threshold=self._trade_threshold,
        )

    def record_outcome(
        self,
        *,
        model_name: str,
        symbol: str,
        pred_direction: str,
        pred_proba: float,
        features: Dict[str, float],
        won: bool,
    ) -> bool:
        """
        Record a resolved prediction outcome for auto-adaptation.

        Returns True if a refit was triggered.
        """
        # Re-evaluate filters to know which passed/failed
        decision = self.evaluate(
            model_name=model_name,
            symbol=symbol,
            pred_direction=pred_direction,
            pred_proba=pred_proba,
            features=features,
        )

        outcome = {
            "model": model_name,
            "symbol": symbol,
            "side_conf": decision.base_confidence,
            "won": won,
            "trade_score": decision.trade_score,
            "sub_filters": {
                sf.name: {"passed": sf.passed, "value": sf.value}
                for sf in decision.sub_filters
            },
        }

        with self._lock:
            self._outcomes.append(outcome)
            # Persist to disk
            self._append_outcome(outcome)

        # Check if refit is needed
        if len(self._outcomes) >= MIN_REFIT_OUTCOMES and len(self._outcomes) % MIN_REFIT_OUTCOMES == 0:
            return self._refit(model_name, symbol)

        return False

    @property
    def trade_threshold(self) -> float:
        return self._trade_threshold

    @trade_threshold.setter
    def trade_threshold(self, value: float):
        self._trade_threshold = value

    def get_weights(self) -> Dict:
        """Return current weights for diagnostics/API."""
        return dict(self._weights)

    def get_outcome_count(self) -> int:
        return len(self._outcomes)

    # ── Persistence ───────────────────────────────────────────────

    def _load_state(self):
        """Load weights and replay outcomes from disk."""
        # Load weights
        if os.path.exists(self._weights_path):
            try:
                with open(self._weights_path) as f:
                    self._weights = json.load(f)
                logger.info(
                    "APFS feature_confirmation: loaded weights from %s (%d keys)",
                    self._weights_path, len(self._weights),
                )
            except Exception as e:
                logger.warning("APFS: failed to load weights: %s", e)

        # Replay outcomes
        if os.path.exists(self._outcomes_path):
            try:
                with open(self._outcomes_path) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            self._outcomes.append(json.loads(line))
                logger.info(
                    "APFS feature_confirmation: loaded %d outcomes from %s",
                    len(self._outcomes), self._outcomes_path,
                )
            except Exception as e:
                logger.warning("APFS: failed to load outcomes: %s", e)

    def _append_outcome(self, outcome: Dict):
        """Append a single outcome to the JSONL file."""
        try:
            os.makedirs(self._state_dir, exist_ok=True)
            with open(self._outcomes_path, "a") as f:
                f.write(json.dumps(outcome) + "\n")
        except Exception as e:
            logger.warning("APFS: failed to persist outcome: %s", e)

    def _save_weights(self):
        """Atomically save weights to JSON."""
        try:
            os.makedirs(self._state_dir, exist_ok=True)
            tmp_path = self._weights_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(self._weights, f, indent=2)
            os.replace(tmp_path, self._weights_path)
            logger.info("APFS: weights saved to %s", self._weights_path)
        except Exception as e:
            logger.warning("APFS: failed to save weights: %s", e)

    # ── Auto-adaptation ───────────────────────────────────────────

    def _refit(self, model_name: str, symbol: str) -> bool:
        """
        Recalculate filter weights from accumulated outcomes.

        For each sub-filter, compute:
          - WR when filter passed
          - WR when filter failed
          - weight = (WR_pass / WR_fail) clamped to [0.85, 1.20]

        If a filter's WR_pass <= WR_fail, its weight → 1.0 (auto-disable).
        """
        key = f"{model_name}:{symbol}"

        # Collect outcomes for this (model, symbol)
        relevant = [o for o in self._outcomes if o["model"] == model_name and o["symbol"] == symbol]
        if len(relevant) < MIN_REFIT_OUTCOMES:
            return False

        # Discover all sub-filter names
        filter_names = set()
        for o in relevant:
            filter_names.update(o.get("sub_filters", {}).keys())

        new_weights = {}
        for fname in filter_names:
            pass_wins = 0
            pass_total = 0
            fail_wins = 0
            fail_total = 0
            for o in relevant:
                sf_data = o.get("sub_filters", {}).get(fname)
                if sf_data is None:
                    continue
                if sf_data["passed"]:
                    pass_wins += int(o["won"])
                    pass_total += 1
                else:
                    fail_wins += int(o["won"])
                    fail_total += 1

            # Need minimum samples in each bucket
            if pass_total < 5 or fail_total < 5:
                new_weights[fname] = {"weight": 1.0, "pass_wr": None, "fail_wr": None,
                                       "pass_n": pass_total, "fail_n": fail_total}
                continue

            wr_pass = pass_wins / pass_total
            wr_fail = fail_wins / fail_total

            if wr_fail > 0 and wr_pass > wr_fail:
                raw_weight = wr_pass / wr_fail
                weight = max(0.85, min(1.20, raw_weight))
            else:
                weight = 1.0  # No lift → auto-disable

            new_weights[fname] = {
                "weight": round(weight, 4),
                "pass_wr": round(wr_pass, 4),
                "fail_wr": round(wr_fail, 4),
                "pass_n": pass_total,
                "fail_n": fail_total,
            }
            logger.info(
                "APFS refit %s/%s: %s weight=%.4f (pass_wr=%.3f n=%d, fail_wr=%.3f n=%d)",
                model_name, symbol, fname, weight, wr_pass, pass_total, wr_fail, fail_total,
            )

        with self._lock:
            self._weights[key] = new_weights
            self._save_weights()

        return True
