#!/usr/bin/env python3
"""
Paper Trader — dual model comparison on Bybit spot L2 data.

Runs two LightGBM models (H=60 and H=300) simultaneously against
live WebSocket order book data, logging predictions and simulated
trades to append-only JSONL ledgers.

Usage:
    python -m trading.paper_trader --testnet     # validate on testnet
    python -m trading.paper_trader               # production WebSocket
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import argparse
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import numpy as np
import lightgbm as lgb

from api.bybit import BybitOrderBookManager
from trading.live_features import LiveFeatureComputer, V3_FEATURE_COLS
from trading.ledger import Ledger
from trading.polymarket_discovery import get_p_market

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("paper_trader")

# ── Configuration ──────────────────────────────────────────────

PREDICTION_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]  # all symbols get predictions
TRADE_SYMBOLS = ["BTCUSDT", "SOLUSDT"]                   # ETH excluded from trades
CONFIDENCE_THRESHOLD = 0.55
CONTRACT_DURATIONS = [300, 900]  # seconds
SIMULATED_STAKE_USDC = 10.0
POLYMARKET_FEE_COEFFICIENT = 0.072  # crypto taker fee: fee = shares * price * 0.072 * p * (1-p)
MIN_FEATURE_WARMUP_SECONDS = 120   # feature buffer fill
MAD_WARMUP_SECONDS = 1800          # 30 minutes for MAD normalization convergence

# UTC blackout: H60 models suppress paper trades during these hours
# (predictions still logged for all models at all hours)
H60_BLACKOUT_HOURS = set(range(21, 24)) | set(range(0, 4))  # 21:00-03:59 UTC
H60_BLACKOUT_MODELS = {"h60", "h60_v2", "h60_v3"}  # all H60 variants

# H300: suppress 300s contracts (5-min too short for signal to materialize)
H300_SUPPRESS_DURATIONS = {"h300": {300}}  # model -> set of suppressed durations

# Mid-price training ranges (from training data distributions)
MID_PRICE_TRAINING_RANGE = {
    "BTCUSDT": [58_000.0, 110_000.0],
    "ETHUSDT": [1_400.0, 4_200.0],
    "SOLUSDT": [90.0, 220.0],
}


class PaperTrader:
    """
    Main paper trading loop.

    Connects to Bybit WebSocket, computes features at each L2 update,
    scores both models at 5-minute contract boundaries, and logs
    predictions and paper trades.
    """

    def __init__(
        self,
        model_paths: dict[str, str],
        log_dir: str = "/data/logs",
        testnet: bool = True,
        features_dir: str | None = None,
    ):
        self.testnet = testnet
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Load models
        self.models: dict[str, lgb.Booster] = {}
        self.feature_names: dict[str, list[str]] = {}
        for name, path in model_paths.items():
            model_dir = Path(path).parent
            self.models[name] = lgb.Booster(model_file=path)
            fn_path = model_dir / "feature_names.json"
            if fn_path.exists():
                with open(fn_path) as f:
                    self.feature_names[name] = json.load(f)
            else:
                self.feature_names[name] = V3_FEATURE_COLS
            logger.info("Loaded model %s from %s (%d features)",
                        name, path, len(self.feature_names[name]))

        # Ledgers (one per model)
        self.ledgers: dict[str, Ledger] = {
            name: Ledger(self.log_dir, name)
            for name in self.models
        }

        # Feature computer (all prediction symbols need L2 data)
        self.feature_computer = LiveFeatureComputer(
            symbols=PREDICTION_SYMBOLS,
            features_dir=features_dir or "/data/features_v3",
        )

        # Order book manager
        self.book_manager = BybitOrderBookManager(
            symbols=PREDICTION_SYMBOLS,
            levels=10,
            testnet=testnet,
        )

        # Pending resolutions: list of (resolve_at_ms, prediction_data)
        self._pending_resolutions: list[tuple[int, dict]] = []

        # aiohttp session for Polymarket API queries
        self._http_session: Optional[aiohttp.ClientSession] = None

        # Tracking
        self._last_contract_boundary_ms = 0
        self._prediction_count = 0
        self._trade_count = 0
        self._running = True
        self._start_time_ms = int(time.time() * 1000)
        self._first_data_time_ms: int | None = None

    def _is_in_warmup(self) -> bool:
        """Check if we're still in the 30-minute MAD warmup period."""
        if self._first_data_time_ms is None:
            return True
        elapsed_ms = int(time.time() * 1000) - self._first_data_time_ms
        return elapsed_ms < MAD_WARMUP_SECONDS * 1000

    def _on_book_update(self, symbol: str, bids: list, asks: list) -> None:
        """Callback fired on each L2 update from WebSocket."""
        if not bids or not asks:
            return

        # Track first data arrival
        if self._first_data_time_ms is None:
            self._first_data_time_ms = int(time.time() * 1000)
            logger.info("First L2 data received — MAD warmup starts (30 min)")

        # Get exchange timestamp from book manager
        book = self.book_manager.books.get(symbol, {})
        cts_ms = book.get("exchange_ts", int(time.time() * 1000))

        # Feed to feature computer
        self.feature_computer.on_book_update(symbol, bids, asks, cts_ms)

    async def _contract_boundary_loop(self) -> None:
        """Check for 5-minute contract boundaries and trigger predictions."""
        while self._running:
            now_ms = int(time.time() * 1000)
            contract_interval_ms = 300_000  # 5 minutes
            boundary_ms = (now_ms // contract_interval_ms) * contract_interval_ms

            # Are we within ±30s of a boundary we haven't processed?
            near_boundary = abs(now_ms - boundary_ms) <= 30_000 or \
                            abs(now_ms - (boundary_ms + contract_interval_ms)) <= 30_000

            if near_boundary and boundary_ms > self._last_contract_boundary_ms:
                self._last_contract_boundary_ms = boundary_ms
                await self._run_predictions(now_ms, boundary_ms)

            # Check pending resolutions
            await self._check_resolutions(now_ms)

            await asyncio.sleep(1.0)

    async def _run_predictions(self, now_ms: int, boundary_ms: int) -> None:
        """Score all symbols with all models at a contract boundary."""
        boundary_ts = boundary_ms // 1000

        for symbol in PREDICTION_SYMBOLS:
            if not self.feature_computer.is_warmed_up(symbol):
                logger.info("Skipping %s — not warmed up yet", symbol)
                continue

            trade_eligible = symbol in TRADE_SYMBOLS

            # Get feature bar
            bar = self.feature_computer.get_1min_bar(symbol)
            if bar is None:
                logger.warning("No 1-min bar for %s at boundary", symbol)
                continue

            # Mid-price range check
            mid_price = bar.get("mid_price", 0)
            self._check_mid_price_range(symbol, mid_price)

            # Query Polymarket p_market once per symbol per boundary
            p_market = None
            if self._http_session:
                try:
                    p_market = await get_p_market(self._http_session, symbol, boundary_ts)
                    if p_market is not None:
                        logger.debug("[%s] p_market=%.3f", symbol, p_market)
                except Exception as e:
                    logger.warning("p_market query failed for %s: %s", symbol, e)

            # Score with each model
            for model_name, model in self.models.items():
                features = {col: bar.get(col, 0.0) for col in self.feature_names[model_name]}
                feature_vec = np.array(
                    [features[col] for col in self.feature_names[model_name]],
                    dtype=np.float64,
                ).reshape(1, -1)

                pred_proba = float(model.predict(feature_vec)[0])
                pred_direction = "up" if pred_proba > 0.5 else "down"
                above_threshold = pred_proba > CONFIDENCE_THRESHOLD or \
                                  pred_proba < (1 - CONFIDENCE_THRESHOLD)

                ts_model_ran_ms = int(time.time() * 1000)
                in_warmup = self._is_in_warmup()

                # Compute metadata for prediction record
                pred_dt = datetime.fromtimestamp(ts_model_ran_ms / 1000, tz=timezone.utc)
                utc_hour = pred_dt.hour
                day_of_week = pred_dt.weekday()  # 0=Monday, 6=Sunday

                # Compute divergence
                p_model_minus_market = None
                if p_market is not None:
                    p_model_minus_market = round(pred_proba - p_market, 6)

                # Log prediction (always — even during warmup, even for non-trade symbols)
                ledger = self.ledgers[model_name]
                prediction_id = ledger.log_prediction(
                    symbol=symbol,
                    pred_proba=pred_proba,
                    pred_direction=pred_direction,
                    above_threshold=above_threshold,
                    price_at_open=mid_price,
                    ts_model_ran_ms=ts_model_ran_ms,
                    ts_contract_open_ms=boundary_ms,
                    features=features,
                    warmup=in_warmup,
                    trade_eligible=trade_eligible,
                    utc_hour=utc_hour,
                    day_of_week=day_of_week,
                    is_weekend=(day_of_week >= 5),
                    relative_spread=features.get("relative_spread", None),
                    p_market=p_market,
                    p_model_minus_market=p_model_minus_market,
                )
                self._prediction_count += 1

                # Suppress trades during warmup
                if in_warmup:
                    if above_threshold and trade_eligible:
                        warmup_elapsed = (ts_model_ran_ms - (self._first_data_time_ms or ts_model_ran_ms)) / 1000
                        warmup_remaining = MAD_WARMUP_SECONDS - warmup_elapsed
                        logger.info(
                            "[%s] %s: proba=%.4f WARMUP (%.0fs remaining) — trade suppressed",
                            model_name, symbol, pred_proba, warmup_remaining,
                        )
                    continue

                # Skip trades for non-eligible symbols
                if not trade_eligible:
                    if above_threshold:
                        logger.debug(
                            "[%s] %s: proba=%.4f (trade_eligible=false)",
                            model_name, symbol, pred_proba,
                        )
                    continue

                # UTC blackout: suppress H60 trades during overnight hours
                if model_name in H60_BLACKOUT_MODELS and utc_hour in H60_BLACKOUT_HOURS:
                    if above_threshold:
                        # Log the suppression to the ledger so outcome is still tracked
                        for duration in CONTRACT_DURATIONS:
                            ledger.log_trade(
                                prediction_id=prediction_id,
                                symbol=symbol,
                                pred_proba=pred_proba,
                                pred_direction=pred_direction,
                                confidence_threshold=CONFIDENCE_THRESHOLD,
                                contract_duration_seconds=duration,
                                price_at_open=mid_price,
                                ts_model_ran_ms=ts_model_ran_ms,
                                ts_contract_open_ms=boundary_ms,
                                simulated_stake_usdc=SIMULATED_STAKE_USDC,
                                p_market=p_market,
                                p_model_minus_market=p_model_minus_market,
                                suppressed_reason="utc_blackout",
                            )
                        logger.info(
                            "[%s] %s: proba=%.4f dir=%s SUPPRESSED (utc_blackout %02d:00)",
                            model_name, symbol, pred_proba, pred_direction, utc_hour,
                        )
                    continue

                # Log trades if above threshold (post-warmup, trade-eligible only)
                if above_threshold:
                    for duration in CONTRACT_DURATIONS:
                        # Check if this duration is suppressed for this model
                        suppressed_durs = H300_SUPPRESS_DURATIONS.get(model_name, set())
                        suppress_reason = "contract_mismatch" if duration in suppressed_durs else None

                        trade_id = ledger.log_trade(
                            prediction_id=prediction_id,
                            symbol=symbol,
                            pred_proba=pred_proba,
                            pred_direction=pred_direction,
                            confidence_threshold=CONFIDENCE_THRESHOLD,
                            contract_duration_seconds=duration,
                            price_at_open=mid_price,
                            ts_model_ran_ms=ts_model_ran_ms,
                            ts_contract_open_ms=boundary_ms,
                            simulated_stake_usdc=SIMULATED_STAKE_USDC,
                            p_market=p_market,
                            p_model_minus_market=p_model_minus_market,
                            suppressed_reason=suppress_reason,
                        )

                        if suppress_reason:
                            # Suppressed: logged but not scheduled for resolution
                            continue

                        self._trade_count += 1

                        # Schedule resolution
                        resolve_at_ms = boundary_ms + duration * 1000
                        self._pending_resolutions.append((resolve_at_ms, {
                            "model_name": model_name,
                            "prediction_id": prediction_id,
                            "trade_id": trade_id,
                            "symbol": symbol,
                            "pred_proba": pred_proba,
                            "pred_direction": pred_direction,
                            "price_at_open": mid_price,
                            "contract_duration_seconds": duration,
                        }))

                    logger.info(
                        "[%s] %s %s: proba=%.4f dir=%s TRADE (%d pending)",
                        model_name, symbol, "🔼" if pred_direction == "up" else "🔽",
                        pred_proba, pred_direction, len(self._pending_resolutions),
                    )
                else:
                    logger.debug(
                        "[%s] %s: proba=%.4f (below threshold)", model_name, symbol, pred_proba,
                    )

        logger.info(
            "Boundary %s: %d predictions total, %d trades total",
            datetime.fromtimestamp(boundary_ms / 1000, tz=timezone.utc).strftime("%H:%M"),
            self._prediction_count, self._trade_count,
        )

    async def _check_resolutions(self, now_ms: int) -> None:
        """Resolve pending trades whose contracts have expired."""
        still_pending = []
        for resolve_at_ms, data in self._pending_resolutions:
            if now_ms >= resolve_at_ms:
                await self._resolve_trade(data, resolve_at_ms)
            else:
                still_pending.append((resolve_at_ms, data))
        self._pending_resolutions = still_pending

    async def _resolve_trade(self, data: dict, resolve_at_ms: int) -> None:
        """Resolve a single pending trade with current price."""
        symbol = data["symbol"]
        model_name = data["model_name"]

        # Get current mid-price from feature computer
        state = self.feature_computer.states.get(symbol)
        if not state or not state.mid_price_history:
            logger.warning("Cannot resolve %s trade — no price data", symbol)
            return

        price_at_close = state.mid_price_history[-1]
        ts_close_ms = int(time.time() * 1000)

        # Determine result — flat (close == open) is a loss for any prediction
        if price_at_close > data["price_at_open"]:
            contract_result = "up"
        elif price_at_close < data["price_at_open"]:
            contract_result = "down"
        else:
            contract_result = "flat"
        prediction_correct = (contract_result == data["pred_direction"])  # flat never matches

        # P&L calculation — Polymarket crypto fee: fee = stake * 0.072 * p * (1-p)
        p = data.get("pred_proba", 0.5)
        p_side = p if p > 0.5 else (1 - p)  # probability of the side we're buying
        fee = SIMULATED_STAKE_USDC * POLYMARKET_FEE_COEFFICIENT * p_side * (1 - p_side)
        if prediction_correct:
            gross_pnl = SIMULATED_STAKE_USDC  # simplified: win = 1x stake
            trade_result = "win"
        else:
            gross_pnl = -SIMULATED_STAKE_USDC  # lose stake
            trade_result = "loss"
        net_pnl = gross_pnl - fee

        ledger = self.ledgers[model_name]

        # Log prediction resolution
        ledger.log_prediction_resolution(
            prediction_id=data["prediction_id"],
            ts_contract_close_ms=ts_close_ms,
            price_at_close=price_at_close,
            contract_result=contract_result,
            prediction_correct=prediction_correct,
        )

        # Log trade resolution
        ledger.log_trade_resolution(
            trade_id=data["trade_id"],
            prediction_id=data["prediction_id"],
            ts_contract_close_ms=ts_close_ms,
            price_at_close=price_at_close,
            contract_result=contract_result,
            prediction_correct=prediction_correct,
            gross_pnl=gross_pnl,
            fee_paid=fee,
            net_pnl=net_pnl,
            trade_result=trade_result,
        )

        duration = data["contract_duration_seconds"]
        emoji = "✅" if prediction_correct else "❌"
        logger.info(
            "[%s] %s %s %ds: open=%.2f close=%.2f %s %s net=%.2f",
            model_name, symbol, data["pred_direction"], duration,
            data["price_at_open"], price_at_close, contract_result, emoji, net_pnl,
        )

    def _check_mid_price_range(self, symbol: str, mid_price: float) -> None:
        """Warn if mid_price is outside training range."""
        range_ = MID_PRICE_TRAINING_RANGE.get(symbol)
        if range_ and (mid_price < range_[0] or mid_price > range_[1]):
            logger.warning(
                "⚠️  %s mid_price %.2f OUTSIDE training range [%.0f, %.0f]",
                symbol, mid_price, range_[0], range_[1],
            )

    async def run(self) -> None:
        """Start the paper trading loop."""
        logger.info("=" * 60)
        logger.info("Paper Trader starting")
        logger.info("  Testnet: %s", self.testnet)
        logger.info("  Prediction symbols: %s", PREDICTION_SYMBOLS)
        logger.info("  Trade symbols: %s", TRADE_SYMBOLS)
        logger.info("  Models: %s", list(self.models.keys()))
        logger.info("  Threshold: %.2f", CONFIDENCE_THRESHOLD)
        logger.info("  MAD warmup: %ds", MAD_WARMUP_SECONDS)
        logger.info("  Log dir: %s", self.log_dir)
        logger.info("=" * 60)

        # Preload EWM state from historical parquets (for V3 mid_price_dev_30d)
        self.feature_computer.preload_ewm()

        # Register book update callback
        self.book_manager.on_update(self._on_book_update)

        # Create aiohttp session for Polymarket API
        self._http_session = aiohttp.ClientSession()
        logger.info("  Polymarket API session created")

        # Run WebSocket + contract boundary loop concurrently
        try:
            await asyncio.gather(
                self.book_manager.connect_async(),
                self._contract_boundary_loop(),
            )
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        except Exception as e:
            logger.error("Fatal error: %s", e, exc_info=True)
            raise
        finally:
            if self._http_session:
                await self._http_session.close()
            self.book_manager.disconnect()
            logger.info("Paper trader stopped. Predictions: %d, Trades: %d",
                        self._prediction_count, self._trade_count)


def main():
    parser = argparse.ArgumentParser(description="Paper trader — dual model comparison")
    parser.add_argument("--testnet", action="store_true", default=False,
                        help="Use Bybit testnet WebSocket")
    parser.add_argument("--log-dir", type=str, default="/data/logs",
                        help="Log/ledger directory")
    parser.add_argument("--h60-model", type=str,
                        default="/data/models/latest_h60/model.lgb",
                        help="Path to H=60 model")
    parser.add_argument("--h60-v3-model", type=str,
                        default="",
                        help="Path to H=60 V3 (debiased with mid_price_dev_30d) model.")
    parser.add_argument("--h300-model", type=str,
                        default="/data/models/latest_h300/model.lgb",
                        help="Path to H=300 model")
    parser.add_argument("--features-dir", type=str, default="/data/features_v3",
                        help="Path to features_v3 parquets for EWM preload")
    args = parser.parse_args()

    model_paths = {
        "h60": args.h60_model,
        "h300": args.h300_model,
    }
    if args.h60_v3_model:
        model_paths["h60_v3"] = args.h60_v3_model

    # Verify model files exist
    for name, path in model_paths.items():
        if not Path(path).exists():
            logger.error("Model file not found: %s (%s)", path, name)
            sys.exit(1)

    trader = PaperTrader(
        model_paths=model_paths,
        log_dir=args.log_dir,
        testnet=args.testnet,
        features_dir=args.features_dir,
    )

    # Handle signals gracefully
    def handle_signal(sig, frame):
        logger.info("Signal %s received, shutting down...", sig)
        trader._running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    asyncio.run(trader.run())


if __name__ == "__main__":
    main()
