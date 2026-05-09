"""
KalshiLiveTrader — gates and dispatches live orders on Kalshi.

Default-deny posture identical to the polymarket LiveTrader:
  * Kill switch (KALSHI_LIVE_ENABLED) is memory-only — restart re-arms OFF.
  * Allow-list (symbol, duration_sec) enforced before any order.
  * Every gate decision logged to /data/kalshi_orders.jsonl.

v2 scope:
  * BTCUSDT 900s only. SOL parked.
  * Fee-aware Kelly sizing with auto-recalibration.
  * Maker-first order strategy: place limit order, wait for fill,
    fall back to taker (market) after 5 unsuccessful maker retries.
  * Demo first; flip to prod via KALSHI_ENV=prod after end-to-end validation.

This module never touches model code, feature engineering, or training.
It receives finalized signals from paper_trader and either places an
order via api.kalshi or records why it was gated.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    from api.kalshi import KalshiAuth, KalshiRestClient

logger = logging.getLogger("kalshi_live_trader")

DEFAULT_LEDGER_PATH = "/data/kalshi_orders.jsonl"

# Kalshi taker fee at typical 50¢ region: 1.75¢ / contract = 3.5%.
# v2 uses maker by default; taker only after 5 unsuccessful maker retries.

MAKER_RETRIES = 5         # Maker attempts before falling back to taker
MAKER_WAIT_SECS = 3.0     # Seconds to wait for maker fill before checking


@dataclass
class GateResult:
    placed: bool
    reason: str       # "PLACED" | "GATED_*" | "ERROR"
    order_id: Optional[str] = None
    fill_status: Optional[str] = None
    error: Optional[str] = None
    final_yes_price_cents: Optional[int] = None
    final_contracts: Optional[int] = None
    fee_estimate_usd: Optional[float] = None
    is_maker: Optional[bool] = None


@dataclass
class KalshiLiveConfig:
    enabled: bool = False
    allow_list: dict[str, set[int]] = field(default_factory=dict)
    kelly_fraction: float = 0.20
    confidence_gate: float = 0.52
    bankroll_fraction: float = 1.0
    default_order_type: str = "maker"   # "maker" | "taker"
    per_trade_usd_cap: Optional[float] = None
    series_ticker: str = "KXBTC15M"
    # Hours (UTC, 0-23) during which live orders are suppressed.
    # Empty = no hour-based suppression.
    suppress_hours_utc: set[int] = field(default_factory=set)


class KalshiLiveTrader:
    """
    Owns the Kalshi REST client and gates every live-order request.

    Call sequence at startup:
        kt = KalshiLiveTrader()
        await kt.connect(session)              # binds REST client
        await kt.run_tz_diagnostic()           # verify boundaries align
        # later, on each finalized signal:
        await kt.maybe_place_order(...)
    """

    def __init__(
        self,
        env: Optional[dict] = None,
        ledger_path: str = DEFAULT_LEDGER_PATH,
    ):
        self._env = env if env is not None else os.environ
        self._ledger_path = ledger_path
        self._lock = threading.RLock()
        self._rest: Optional["KalshiRestClient"] = None
        self._auth: Optional["KalshiAuth"] = None
        self._client_init_error: Optional[str] = None
        self._last_order_at: Optional[str] = None
        self._last_error: Optional[str] = None

        self._config = KalshiLiveConfig(
            enabled=self._parse_bool(self._env.get("KALSHI_LIVE_ENABLED", "false")),
            allow_list=self._parse_allow_list(self._env.get("KALSHI_LIVE_ALLOW_LIST", "BTCUSDT:900")),
            kelly_fraction=float(self._env.get("KALSHI_KELLY_FRACTION", "0.20")),
            confidence_gate=float(self._env.get("KALSHI_CONFIDENCE_GATE", "0.52")),
            bankroll_fraction=float(self._env.get("KALSHI_BANKROLL_FRACTION", "1.0")),
            default_order_type=self._env.get("KALSHI_DEFAULT_ORDER_TYPE", "taker").lower(),
            series_ticker=self._env.get("KALSHI_SERIES_TICKER", "KXBTC15M"),
            suppress_hours_utc=self._parse_hour_list(self._env.get("KALSHI_SUPPRESS_HOURS_UTC", "")),
        )

        # Probability calibrator. Maps raw model_p → empirically calibrated p.
        # Default identity if no map at /data/calibration.json. Used for SIZING
        # ONLY (Kelly), not the confidence gate (gate stays on raw model_p so
        # operator-set thresholds map directly to model output).
        from execution.calibration import ProbabilityCalibrator
        self._calibrator = ProbabilityCalibrator()

        # APFS — Adaptive Prediction Filter System (Phase 1)
        from execution.apfs.feature_confirmation import FeatureConfirmationFilter
        apfs_dir = self._env.get("APFS_STATE_DIR", "/data/apfs")
        apfs_threshold = float(self._env.get("APFS_TRADE_THRESHOLD", "0.52"))
        self._apfs = FeatureConfirmationFilter(
            state_dir=apfs_dir,
            trade_threshold=apfs_threshold,
        )
        self._apfs_enabled = self._parse_bool(self._env.get("APFS_ENABLED", "true"))

    # ─── Connection ─────────────────────────────────────────────────────
    async def connect(self, session) -> None:
        """Initialize REST client. Logs (does not raise) on failure so paper
        trading continues even if live auth is misconfigured."""
        try:
            from api.kalshi import KalshiAuth, KalshiRestClient
            self._auth = KalshiAuth()
            self._rest = KalshiRestClient(self._auth, session=session)
            logger.info("KalshiLiveTrader connected (env=%s, series=%s)",
                        self._env.get("KALSHI_ENV", "demo"), self._config.series_ticker)
        except Exception as e:
            self._client_init_error = str(e)
            logger.warning("KalshiLiveTrader init failed (paper continues): %s", e)

    async def run_tz_diagnostic(self) -> list[dict]:
        """Run timezone diagnostic at startup — logs boundary alignment."""
        if self._rest is None:
            return []
        try:
            from api.kalshi import tz_diagnostic
            return await tz_diagnostic(self._rest, self._config.series_ticker, n=5)
        except Exception as e:
            logger.warning("tz_diagnostic failed: %s", e)
            return []

    # ─── Config / status ────────────────────────────────────────────────
    @property
    def config(self) -> KalshiLiveConfig:
        return self._config

    def is_enabled(self) -> bool:
        with self._lock:
            return self._config.enabled

    def in_allow_list(self, symbol: str, duration_sec: int) -> bool:
        with self._lock:
            return duration_sec in self._config.allow_list.get(symbol, set())

    def enable(self) -> None:
        with self._lock:
            self._config.enabled = True
            logger.warning("KALSHI LIVE TRADING ENABLED (memory-only)")

    def disable(self) -> None:
        with self._lock:
            self._config.enabled = False
            logger.warning("KALSHI LIVE TRADING DISABLED")

    def update_config(self, **kwargs) -> dict:
        allowed = {
            "kelly_fraction", "confidence_gate", "bankroll_fraction",
            "default_order_type", "per_trade_usd_cap",
            "allow_list", "suppress_hours_utc",
            "apfs_enabled", "apfs_threshold",
        }
        with self._lock:
            for k, v in kwargs.items():
                if k not in allowed:
                    raise ValueError(f"unknown config key: {k}")
                if k == "allow_list":
                    if not isinstance(v, dict):
                        raise ValueError("allow_list must be {symbol: [duration, ...]}")
                    coerced: dict[str, set[int]] = {}
                    for sym, durs in v.items():
                        if not isinstance(durs, (list, tuple, set)):
                            raise ValueError("allow_list values must be list of int durations")
                        coerced[sym.upper()] = {int(d) for d in durs}
                    self._config.allow_list = coerced
                elif k == "suppress_hours_utc":
                    if not isinstance(v, (list, tuple, set)):
                        raise ValueError("suppress_hours_utc must be list[int]")
                    coerced_hours = set()
                    for h in v:
                        h_int = int(h)
                        if 0 <= h_int <= 23:
                            coerced_hours.add(h_int)
                    self._config.suppress_hours_utc = coerced_hours
                elif k == "apfs_enabled":
                    self._apfs_enabled = bool(v)
                elif k == "apfs_threshold":
                    self._apfs.trade_threshold = float(v)
                else:
                    setattr(self._config, k, v)
            return self._snapshot()

    def status(self) -> dict:
        with self._lock:
            return {
                "exchange": "kalshi",
                "env": self._env.get("KALSHI_ENV", "demo"),
                "enabled": self._config.enabled,
                "client_ready": self._rest is not None,
                "client_init_error": self._client_init_error,
                "config": self._snapshot(),
                "last_order_at": self._last_order_at,
                "last_error": self._last_error,
            }

    def _snapshot(self) -> dict:
        return {
            "allow_list": {s: sorted(d) for s, d in self._config.allow_list.items()},
            "kelly_fraction": self._config.kelly_fraction,
            "confidence_gate": self._config.confidence_gate,
            "bankroll_fraction": self._config.bankroll_fraction,
            "default_order_type": self._config.default_order_type,
            "per_trade_usd_cap": self._config.per_trade_usd_cap,
            "series_ticker": self._config.series_ticker,
            "suppress_hours_utc": sorted(self._config.suppress_hours_utc),
            "apfs_enabled": self._apfs_enabled,
            "apfs_threshold": self._apfs.trade_threshold,
        }

    # ─── Balance / sizing ───────────────────────────────────────────────
    async def get_balance_usd(self) -> Optional[float]:
        """Return demo/live USDC balance in dollars (Kalshi returns cents)."""
        if self._rest is None:
            return None
        try:
            resp = await self._rest.get_balance()
            cents = float(resp.get("balance", 0))
            return cents / 100.0
        except Exception as e:
            with self._lock:
                self._last_error = f"balance: {e}"
            logger.warning("balance query failed: %s", e)
            return None

    async def effective_bankroll_usd(self) -> Optional[float]:
        bal = await self.get_balance_usd()
        if bal is None:
            return None
        with self._lock:
            return bal * self._config.bankroll_fraction

    # ─── Sizing math ────────────────────────────────────────────────────
    def compute_contracts(
        self,
        bankroll_usd: float,
        model_p: float,
        market_yes_price: float,
        side: str,
    ) -> tuple[int, float]:
        """
        Convert a (bankroll, model_p, market_price) tuple into a contract count
        using fee-aware Kelly with the configured fraction.

        Returns (n_contracts, kelly_fraction_used).
        """
        from execution.kalshi_fees import kelly_fraction as kelly_full

        is_maker = self._config.default_order_type == "maker"

        # For YES side: model_p IS the win probability against market_yes_price.
        # For NO side: invert — win probability against (1 - market_yes_price).
        if side.lower() == "no":
            adjusted_p = 1.0 - model_p
            adjusted_market = 1.0 - market_yes_price
        else:
            adjusted_p = model_p
            adjusted_market = market_yes_price

        full_k = kelly_full(adjusted_p, adjusted_market, is_maker=is_maker)
        scaled_k = full_k * self._config.kelly_fraction
        if scaled_k <= 0:
            return 0, 0.0

        stake_usd = bankroll_usd * scaled_k
        if self._config.per_trade_usd_cap is not None:
            stake_usd = min(stake_usd, self._config.per_trade_usd_cap)

        # Each contract costs (price + fee) USD. Round DOWN to integer count.
        from execution.kalshi_fees import fee as kalshi_fee
        per_contract_cost = adjusted_market + kalshi_fee(adjusted_market, 1, is_maker=is_maker)
        if per_contract_cost <= 0:
            return 0, scaled_k
        n = int(math.floor(stake_usd / per_contract_cost))
        return max(0, n), scaled_k

    # ─── Main entry point ───────────────────────────────────────────────
    async def maybe_place_order(
        self,
        *,
        symbol: str,
        duration_sec: int,
        boundary_ts: int,
        model_p: float,
        side: str,                # "yes" | "no"
        ticker: str,
        market_yes_price: float,  # 0.0–1.0
        order_type: Optional[str] = None,
        paper_stake_usd: Optional[float] = None,
        features: Optional[Dict[str, float]] = None,
        model_name: str = "h300",
        pred_direction: Optional[str] = None,
    ) -> GateResult:
        """
        Evaluate gate stack and (if all pass) place a live order.

        Always writes one row to the live ledger.
        """
        result = await self._evaluate_and_place(
            symbol=symbol, duration_sec=duration_sec, model_p=model_p,
            side=side, ticker=ticker, market_yes_price=market_yes_price,
            order_type=order_type, paper_stake_usd=paper_stake_usd,
            features=features, model_name=model_name,
            pred_direction=pred_direction,
        )
        self._record(
            symbol=symbol, duration_sec=duration_sec, boundary_ts=boundary_ts,
            ticker=ticker, side=side, model_p=model_p,
            market_yes_price=market_yes_price, result=result,
            paper_stake_usd=paper_stake_usd,
        )
        return result

    async def _evaluate_and_place(
        self,
        *,
        symbol: str,
        duration_sec: int,
        model_p: float,
        side: str,
        ticker: str,
        market_yes_price: float,
        order_type: Optional[str],
        paper_stake_usd: Optional[float] = None,
        features: Optional[Dict[str, float]] = None,
        model_name: str = "h300",
        pred_direction: Optional[str] = None,
    ) -> GateResult:
        apfs_decision = None
        # Gate 1: kill switch
        if not self.is_enabled():
            return GateResult(placed=False, reason="GATED_KILL_SWITCH")

        # Gate 2: allow-list
        if not self.in_allow_list(symbol, duration_sec):
            return GateResult(placed=False, reason="GATED_ALLOW_LIST")

        # Gate 3: hour-of-day suppress (UTC)
        with self._lock:
            suppress_hours = set(self._config.suppress_hours_utc)
        if suppress_hours:
            current_hour = datetime.now(timezone.utc).hour
            if current_hour in suppress_hours:
                return GateResult(placed=False, reason="GATED_HOUR")

        # Gate 4: client ready
        if self._rest is None:
            return GateResult(
                placed=False, reason="GATED_NO_CLIENT",
                error=self._client_init_error,
            )

        # Gate 4b: APFS feature confirmation filter
        if self._apfs_enabled and features is not None and pred_direction is not None:
            apfs_decision = self._apfs.evaluate(
                model_name=model_name,
                symbol=symbol,
                pred_direction=pred_direction,
                pred_proba=model_p,
                features=features,
            )
            if not apfs_decision.should_trade:
                sf_summary = ", ".join(
                    f"{sf.name}={'✓' if sf.passed else '✗'}" for sf in apfs_decision.sub_filters
                )
                logger.info(
                    "APFS GATED %s: score=%.4f threshold=%.4f [%s]",
                    symbol, apfs_decision.trade_score, apfs_decision.threshold, sf_summary,
                )
                return GateResult(
                    placed=False,
                    reason=f"GATED_APFS(score={apfs_decision.trade_score:.4f})",
                )
            else:
                sf_summary = ", ".join(
                    f"{sf.name}={'✓' if sf.passed else '✗'}" for sf in apfs_decision.sub_filters
                )
                logger.info(
                    "APFS PASSED %s: score=%.4f threshold=%.4f [%s]",
                    symbol, apfs_decision.trade_score, apfs_decision.threshold, sf_summary,
                )

        # Gate 4: confidence (skipped when APFS mode is active — APFS already decided)
        with self._lock:
            cfg_snapshot = (
                self._config.confidence_gate,
                self._config.kelly_fraction,
                self._config.default_order_type,
            )
        confidence_gate, _, default_type = cfg_snapshot
        # Confidence is the model's probability for the chosen side.
        side_p = model_p if side.lower() == "yes" else (1.0 - model_p)
        if not self._apfs_enabled:
            # Only enforce static confidence gate when APFS is off
            if side_p < confidence_gate:
                return GateResult(placed=False, reason="GATED_CONFIDENCE")

        # Gate 5: bankroll fetch + sizing
        bankroll = await self.effective_bankroll_usd()
        if bankroll is None or bankroll <= 0:
            return GateResult(
                placed=False, reason="GATED_NO_BANKROLL",
                error=self._last_error,
            )

        # Calibrate raw model_p → empirically-observed win rate before sizing.
        #
        # In APFS mode, we use the APFS trade_score directly for sizing. This
        # score already incorporates raw model confidence + real-time feature
        # boosts. If we used the legacy calibrator here, it would penalize
        # APFS-passed trades based on historical model-only performance,
        # often resulting in zero contracts.
        if self._apfs_enabled and apfs_decision is not None:
            # apfs_decision.trade_score is the score for the CHOSEN side.
            # compute_contracts expects calibrated_p to be the probability of YES.
            calibrated_p = apfs_decision.trade_score if side.lower() == "yes" else (1.0 - apfs_decision.trade_score)
        else:
            # Identity if no calibration map is loaded. Gate above uses raw model_p
            # so operator thresholds remain applied to model output, not the
            # post-calibration value.
            calibrated_p = self._calibrator.calibrate(model_p)

        contracts, _kelly_used = self.compute_contracts(
            bankroll, calibrated_p, market_yes_price, side,
        )
        if contracts < 1:
            return GateResult(placed=False, reason="GATED_ZERO_CONTRACTS")

        # ── Midpoint sizing ───────────────────────────────────────
        # When paper_stake_usd is provided, average the Kalshi Kelly stake
        # with the paper trader's stake for a conservative blend.
        from execution.kalshi_fees import fee as kalshi_fee
        chosen_type = (order_type or default_type).lower()

        # For sizing, assume maker fees (cheapest). If we fall back to taker
        # the actual fee will be higher but contracts are already committed.
        if side.lower() == "no":
            adjusted_market = 1.0 - market_yes_price
        else:
            adjusted_market = market_yes_price
        per_contract_cost = adjusted_market + kalshi_fee(adjusted_market, 1, is_maker=True)

        if paper_stake_usd is not None and per_contract_cost > 0:
            kalshi_stake_usd = contracts * per_contract_cost
            midpoint_usd = (paper_stake_usd + kalshi_stake_usd) / 2.0
            contracts = max(1, int(math.floor(midpoint_usd / per_contract_cost)))
            logger.info(
                "midpoint sizing: paper=$%.2f kalshi=$%.2f mid=$%.2f → %d contracts",
                paper_stake_usd, kalshi_stake_usd, midpoint_usd, contracts,
            )

        # Gate 6: tick rounding — prices are now floats [0.01, 0.99]
        yes_price = max(0.01, min(0.99, round(market_yes_price, 4)))

        # Gate 7: minimum 1 contract enforced above; per-trade cap applied in compute_contracts.

        # ── Maker-first order strategy ─────────────────────────────
        # 1. Try limit (maker) order up to MAKER_RETRIES times
        # 2. Wait MAKER_WAIT_SECS for each fill check
        # 3. If not filled after all retries, cancel and place taker (market)
        if chosen_type == "maker":
            result = await self._try_maker_then_taker(
                ticker=ticker, side=side, yes_price=yes_price,
                contracts=contracts, market_yes_price=market_yes_price,
            )
            return result
        else:
            # Direct taker path (explicit override or fallback)
            return await self._place_taker_order(
                ticker=ticker, side=side, yes_price=yes_price,
                contracts=contracts, market_yes_price=market_yes_price,
            )

    # ─── Maker-first helpers ──────────────────────────────────────────
    async def _try_maker_then_taker(
        self,
        *,
        ticker: str,
        side: str,
        yes_price: float,
        contracts: int,
        market_yes_price: float,
    ) -> GateResult:
        """Try limit (maker) order, wait for fill, fall back to taker."""
        import asyncio
        from execution.kalshi_fees import fee as kalshi_fee

        for attempt in range(MAKER_RETRIES):
            try:
                resp = await self._rest.place_order(
                    ticker=ticker,
                    side=side.lower(),
                    yes_price=yes_price,
                    contracts=contracts,
                    order_type="limit",
                    action="buy",
                )
                order = resp.get("order", resp)
                order_id = order.get("order_id") or order.get("id")
                fill_status = order.get("status") or "open"

                # If immediately filled (resting liquidity hit our price)
                if fill_status in ("executed", "filled"):
                    fee_est = kalshi_fee(market_yes_price, contracts, is_maker=True)
                    with self._lock:
                        self._last_order_at = _utc_now_iso()
                        self._last_error = None
                    logger.info("maker fill: immediate fill on attempt %d", attempt + 1)
                    return GateResult(
                        placed=True, reason="PLACED",
                        order_id=order_id, fill_status=fill_status,
                        final_yes_price_cents=int(round(yes_price * 100)),
                        final_contracts=contracts,
                        fee_estimate_usd=fee_est,
                        is_maker=True,
                    )

                # Wait for fill
                await asyncio.sleep(MAKER_WAIT_SECS)

                # Check fill status
                try:
                    order_resp = await self._rest.get_order(order_id)
                    order_data = order_resp.get("order", order_resp)
                    fill_status = order_data.get("status") or "open"
                except Exception as check_err:
                    logger.warning("maker fill check failed: %s", check_err)
                    fill_status = "unknown"

                if fill_status in ("executed", "filled"):
                    fee_est = kalshi_fee(market_yes_price, contracts, is_maker=True)
                    with self._lock:
                        self._last_order_at = _utc_now_iso()
                        self._last_error = None
                    logger.info("maker fill: filled after %ds on attempt %d",
                                int(MAKER_WAIT_SECS), attempt + 1)
                    return GateResult(
                        placed=True, reason="PLACED",
                        order_id=order_id, fill_status=fill_status,
                        final_yes_price_cents=int(round(yes_price * 100)),
                        final_contracts=contracts,
                        fee_estimate_usd=fee_est,
                        is_maker=True,
                    )

                # Not filled — cancel and retry or fall through to taker
                try:
                    await self._rest.cancel_order(order_id)
                    logger.info("maker retry: cancelled unfilled order %s (attempt %d/%d)",
                                order_id, attempt + 1, MAKER_RETRIES)
                except Exception as cancel_err:
                    logger.warning("maker cancel failed: %s", cancel_err)

            except Exception as e:
                logger.warning("maker attempt %d failed: %s", attempt + 1, e)

        # All maker attempts exhausted — fall back to taker
        logger.info("maker exhausted %d retries, falling back to taker", MAKER_RETRIES)
        return await self._place_taker_order(
            ticker=ticker, side=side, yes_price=yes_price,
            contracts=contracts, market_yes_price=market_yes_price,
        )

    async def _place_taker_order(
        self,
        *,
        ticker: str,
        side: str,
        yes_price: float,
        contracts: int,
        market_yes_price: float,
    ) -> GateResult:
        """Place a market (taker) order."""
        from execution.kalshi_fees import fee as kalshi_fee
        fee_est = kalshi_fee(market_yes_price, contracts, is_maker=False)
        try:
            resp = await self._rest.place_order(
                ticker=ticker,
                side=side.lower(),
                yes_price=yes_price,
                contracts=contracts,
                order_type="market",
                action="buy",
            )
            order = resp.get("order", resp)
            order_id = order.get("order_id") or order.get("id")
            fill_status = order.get("status") or "OPEN"
            with self._lock:
                self._last_order_at = _utc_now_iso()
                self._last_error = None
            return GateResult(
                placed=True, reason="PLACED",
                order_id=order_id, fill_status=fill_status,
                final_yes_price_cents=int(round(yes_price * 100)),
                final_contracts=contracts,
                fee_estimate_usd=fee_est,
                is_maker=False,
            )
        except Exception as e:
            err = str(e)
            with self._lock:
                self._last_error = err
            logger.exception("kalshi taker order placement failed")
            return GateResult(
                placed=False, reason="ERROR", error=err,
                final_yes_price_cents=int(round(yes_price * 100)),
                final_contracts=contracts,
                fee_estimate_usd=fee_est,
                is_maker=False,
            )

    # ─── Ledger ─────────────────────────────────────────────────────────
    def _record(
        self,
        *,
        symbol: str,
        duration_sec: int,
        boundary_ts: int,
        ticker: str,
        side: str,
        model_p: float,
        market_yes_price: float,
        result: GateResult,
        paper_stake_usd: Optional[float] = None,
    ) -> None:
        # Surface calibration in the ledger so we can audit pre/post values.
        cal_p = self._calibrator.calibrate(model_p)
        row = {
            "ts": _utc_now_iso(),
            "exchange": "kalshi",
            "symbol": symbol,
            "duration_sec": duration_sec,
            "boundary_ts": boundary_ts,
            "ticker": ticker,
            "side": side,
            "model_p": model_p,
            "market_yes_price": market_yes_price,
            "gate_result": result.reason,
            "order_id": result.order_id,
            "fill_status": result.fill_status,
            "final_yes_price_cents": result.final_yes_price_cents,
            "final_contracts": result.final_contracts,
            "fee_estimate_usd": result.fee_estimate_usd,
            "is_maker": result.is_maker,
            "error": result.error,
        }
        if self._calibrator.is_enabled():
            row["model_p_calibrated"] = cal_p
        if paper_stake_usd is not None:
            row["paper_stake_usd"] = paper_stake_usd
        try:
            os.makedirs(os.path.dirname(self._ledger_path), exist_ok=True)
            with open(self._ledger_path, "a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as e:
            logger.warning("kalshi ledger write failed: %s", e)

    def tail_ledger(self, limit: int = 50) -> list[dict]:
        if not os.path.exists(self._ledger_path):
            return []
        try:
            with open(self._ledger_path) as f:
                lines = f.readlines()[-limit:]
            return [json.loads(line) for line in lines if line.strip()]
        except Exception as e:
            logger.warning("kalshi ledger tail failed: %s", e)
            return []

    # ─── Helpers ────────────────────────────────────────────────────────
    @staticmethod
    def _parse_bool(s: str) -> bool:
        return str(s).strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _parse_allow_list(s: str) -> dict[str, set[int]]:
        out: dict[str, set[int]] = {}
        for pair in (s or "").split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            sym, dur = pair.split(":", 1)
            try:
                out.setdefault(sym.strip().upper(), set()).add(int(dur.strip()))
            except ValueError:
                continue
        return out

    @staticmethod
    def _parse_hour_list(s: str) -> set[int]:
        """'0,1,23' -> {0, 1, 23}. Silently drops invalid entries."""
        out: set[int] = set()
        for tok in (s or "").split(","):
            tok = tok.strip()
            if not tok:
                continue
            try:
                h = int(tok)
                if 0 <= h <= 23:
                    out.add(h)
            except ValueError:
                continue
        return out


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Module-level singleton ─────────────────────────────────────────────────
_singleton: Optional[KalshiLiveTrader] = None
_singleton_lock = threading.Lock()


def get_kalshi_live_trader() -> KalshiLiveTrader:
    """Get or create the process-wide KalshiLiveTrader singleton."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = KalshiLiveTrader()
    return _singleton
