#!/usr/bin/env python3
"""
BTC_900s Paper Trader — Aggressive Compounding Strategy.

Dedicated high-conviction paper trader for BTCUSDT 900s contracts only.
Based on extensive backtest analysis showing:
  - h300 BTC 900s: 70.0% win rate, +$3.73/trade, +$1.49/hr
  - h60  BTC 900s: 59.8% win rate, +$1.77/trade, +$1.87/hr
  - Combined when both fire: ~63% win rate, $3.46/hr

Strategy: Trade every prediction (no threshold), Kelly-aggressive sizing,
compound bankroll, both models always fire independently.

Position sizing (aggressive Kelly):
  - h300: ~70% historical accuracy → full Kelly ≈ 40% → capped at 12%
  - h60:  ~60% historical accuracy → full Kelly ≈ 20% → capped at 3%
  - When both models agree on direction: each fires independently (2 bets)
  - When they disagree: each still fires (independent edge)

Bankroll compounds after every resolution.

Usage:
    python -m trading.btc_900s_paper_trader --testnet
    python -m trading.btc_900s_paper_trader
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import argparse
import signal
import sys
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

import aiohttp
import numpy as np
import lightgbm as lgb

from api.bybit import BybitOrderBookManager
from trading.live_features import LiveFeatureComputer
from feature_engineering.feature_contract import resolve_model_feature_contract
from trading.polymarket_discovery import get_p_market

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("btc_900s_trader")

# ── Configuration ──────────────────────────────────────────────

SYMBOL = "BTCUSDT"
CONTRACT_DURATION = 900  # 15-minute contracts only
INITIAL_BANKROLL_USDC = 10.0
POLYMARKET_FEE_COEFFICIENT = 0.072
MIN_FEATURE_WARMUP_SECONDS = 120
MAD_WARMUP_SECONDS = 1800  # 30 minutes

# Aggressive Kelly sizing based on historical accuracy
# h300: 70% win → Kelly* = 2p-1 = 40% → we use 12%
# h60:  60% win → Kelly* = 2p-1 = 20% → we use 3%
MODEL_CONFIG = {
    "h300": {
        "accuracy_estimate": 0.70,
        "kelly_fraction": 0.06,   # 6% — sweet spot from live analysis (+16.5% vs 4.6% at 12%)
        "default_path": "/data/models/latest_h300/model.lgb",
    },
    "h60": {
        "accuracy_estimate": 0.60,
        "kelly_fraction": 0.03,   # 3% — collecting data, edge questionable
        "default_path": "/data/models/latest_h60/model.lgb",
    },
}

# Mid-price training range
MID_PRICE_TRAINING_RANGE = [58_000.0, 110_000.0]


@dataclass
class PendingTrade:
    """Tracks a single pending resolution."""
    trade_id: str
    model_name: str
    prediction_id: str
    pred_direction: str
    pred_proba: float
    price_at_open: float
    stake_usdc: float
    ts_contract_open_ms: int
    resolve_at_ms: int


class BTC900sLedger:
    """
    Append-only JSONL ledger for the BTC 900s strategy.

    Single file: BTC_900s_paper_trader.jsonl
    Record types: prediction, trade_entry, trade_resolution
    """

    def __init__(self, log_dir: str | Path = "/data/logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.log_dir / "BTC_900s_paper_trader.jsonl"

    def _append(self, record: dict) -> None:
        """Thread-safe append."""
        line = json.dumps(record, default=str) + "\n"
        with open(self.path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(line)
            fcntl.flock(f, fcntl.LOCK_UN)

    def log_prediction(
        self,
        prediction_id: str,
        model_name: str,
        symbol: str,
        pred_proba: float,
        pred_direction: str,
        price_at_open: float,
        ts_model_ran_ms: int,
        ts_contract_open_ms: int,
        features: dict,
        warmup: bool,
        p_market: float | None = None,
    ) -> None:
        self._append({
            "record_type": "prediction",
            "prediction_id": prediction_id,
            "model": model_name,
            "ts_model_ran_ms": ts_model_ran_ms,
            "ts_contract_open_ms": ts_contract_open_ms,
            "symbol": symbol,
            "pred_proba": round(pred_proba, 6),
            "pred_direction": pred_direction,
            "price_at_contract_open": price_at_open,
            "features": {k: round(v, 8) if isinstance(v, float) else v for k, v in features.items()},
            "warmup": warmup,
            "p_market": round(p_market, 6) if p_market is not None else None,
        })

    def log_trade(
        self,
        trade_id: str,
        prediction_id: str,
        model_name: str,
        symbol: str,
        pred_proba: float,
        pred_direction: str,
        stake_usdc: float,
        price_at_open: float,
        ts_model_ran_ms: int,
        ts_contract_open_ms: int,
        contract_duration_seconds: int,
        kelly_fraction: float,
        bankroll_at_time: float,
        p_market: float | None = None,
    ) -> None:
        self._append({
            "record_type": "trade_entry",
            "trade_id": trade_id,
            "prediction_id": prediction_id,
            "model": model_name,
            "ts_model_ran_ms": ts_model_ran_ms,
            "ts_contract_open_ms": ts_contract_open_ms,
            "symbol": symbol,
            "pred_proba": round(pred_proba, 6),
            "pred_direction": pred_direction,
            "simulated_stake_usdc": round(stake_usdc, 2),
            "contract_duration_seconds": contract_duration_seconds,
            "price_at_contract_open": price_at_open,
            "kelly_fraction_used": kelly_fraction,
            "bankroll_at_trade": round(bankroll_at_time, 2),
            "p_market": round(p_market, 6) if p_market is not None else None,
        })

    def log_resolution(
        self,
        trade_id: str,
        prediction_id: str,
        model_name: str,
        ts_contract_close_ms: int,
        price_at_close: float,
        contract_result: str,
        prediction_correct: bool,
        gross_pnl: float,
        fee_paid: float,
        net_pnl: float,
        trade_result: str,
        bankroll_after: float,
    ) -> None:
        self._append({
            "record_type": "trade_resolution",
            "trade_id": trade_id,
            "prediction_id": prediction_id,
            "model": model_name,
            "ts_contract_close_ms": ts_contract_close_ms,
            "price_at_contract_close": price_at_close,
            "contract_result": contract_result,
            "prediction_correct": prediction_correct,
            "gross_pnl": round(gross_pnl, 6),
            "fee_paid": round(fee_paid, 6),
            "net_pnl": round(net_pnl, 6),
            "trade_result": trade_result,
            "bankroll_after": round(bankroll_after, 2),
        })


class BTC900sPaperTrader:
    """
    Aggressive compounding paper trader for BTC 900s contracts.

    Always trades (no confidence threshold). Uses both h60 and h300 models.
    Each model fires independently with its own Kelly-based stake size.
    Bankroll compounds after every resolution.
    """

    def __init__(
        self,
        model_paths: dict[str, str],
        log_dir: str = "/data/logs",
        testnet: bool = True,
        features_dir: str | None = None,
        initial_bankroll: float = INITIAL_BANKROLL_USDC,
    ):
        self.testnet = testnet
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Bankroll
        self.bankroll = initial_bankroll
        self.initial_bankroll = initial_bankroll

        # Ledger
        self.ledger = BTC900sLedger(self.log_dir)

        # Load models
        self.models: dict[str, lgb.Booster] = {}
        self.feature_names: dict[str, list[str]] = {}
        for name, path in list(model_paths.items()):
            try:
                booster = lgb.Booster(model_file=path)
                model_dir = Path(path).parent
                fn_path = model_dir / "feature_names.json"
                feat_names = resolve_model_feature_contract(booster, fn_path)
                self.models[name] = booster
                self.feature_names[name] = feat_names
                cfg = MODEL_CONFIG[name]
                logger.info(
                    "Loaded %s: %d features, accuracy_est=%.2f, kelly=%.1f%%",
                    name, len(self.feature_names[name]),
                    cfg["accuracy_estimate"], cfg["kelly_fraction"] * 100,
                )
            except Exception as e:
                logger.error("Failed to load model or resolve feature contract for %s from %s: %s",
                             name, path, e)
                raise RuntimeError(f"Could not load model {name}: {e}") from e

        # Feature computer (BTC only)
        self.feature_computer = LiveFeatureComputer(
            symbols=[SYMBOL],
            features_dir=features_dir or "/data/features_v3",
        )

        # Order book manager
        self.book_manager = BybitOrderBookManager(
            symbols=[SYMBOL],
            levels=10,
            testnet=testnet,
        )

        # Pending resolutions
        self._pending_resolutions: list[PendingTrade] = []

        # aiohttp session
        self._http_session: Optional[aiohttp.ClientSession] = None

        # Tracking
        self._last_contract_boundary_ms = 0
        self._prediction_count = 0
        self._trade_count = 0
        self._running = True
        self._start_time_ms = int(time.time() * 1000)
        self._first_data_time_ms: int | None = None

        # State file for crash recovery
        self.state_file = self.log_dir / "BTC_900s_state.json"
        self._load_state()

        # Preload EWM
        self.feature_computer.preload_ewm()

    def _load_state(self):
        """Restore state from disk (bankroll, pending resolutions)."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    state = json.load(f)
                self.bankroll = state.get("bankroll", self.initial_bankroll)
                self.initial_bankroll = state.get("initial_bankroll", self.initial_bankroll)
                logger.info(
                    "Restored state: bankroll=$%.2f, %d pending resolutions",
                    self.bankroll, len(state.get("pending", [])),
                )
            except Exception as e:
                logger.error("Failed to restore state: %s", e)

    def _save_state(self):
        """Persist state to disk."""
        pending = [{
            "trade_id": p.trade_id,
            "model_name": p.model_name,
            "prediction_id": p.prediction_id,
            "pred_direction": p.pred_direction,
            "pred_proba": p.pred_proba,
            "price_at_open": p.price_at_open,
            "stake_usdc": p.stake_usdc,
            "ts_contract_open_ms": p.ts_contract_open_ms,
            "resolve_at_ms": p.resolve_at_ms,
        } for p in self._pending_resolutions]

        state = {
            "bankroll": round(self.bankroll, 2),
            "initial_bankroll": self.initial_bankroll,
            "pending": pending,
            "last_save_ms": int(time.time() * 1000),
        }
        try:
            with open(self.state_file, "w") as f:
                json.dump(state, f)
        except Exception as e:
            logger.error("Failed to save state: %s", e)

    def _is_in_warmup(self) -> bool:
        """Check if we're still in the 30-minute MAD warmup."""
        if self._first_data_time_ms is None:
            return True
        elapsed_ms = int(time.time() * 1000) - self._first_data_time_ms
        return elapsed_ms < MAD_WARMUP_SECONDS * 1000

    def _calculate_stake(self, model_name: str) -> float:
        """
        Calculate stake size based on Kelly fraction and current bankroll.

        Uses the analysis-derived win rate to determine Kelly edge:
          Kelly* = 2p - 1 (for binary bet with 1:1 payout)
          Actual stake = Kelly* * kelly_multiplier * bankroll

        The kelly_fraction in MODEL_CONFIG already incorporates the
        aggressive multiplier (2x the conservative recommendation).
        """
        cfg = MODEL_CONFIG[model_name]
        kelly_frac = cfg["kelly_fraction"]
        stake = kelly_frac * self.bankroll
        # Minimum stake of $0.10 to avoid micro-bets but allow early compounding
        return max(stake, 0.10)

    def _on_book_update(self, symbol: str, bids: list, asks: list) -> None:
        """Callback on each L2 update."""
        if not bids or not asks:
            return

        if self._first_data_time_ms is None:
            self._first_data_time_ms = int(time.time() * 1000)
            logger.info("First L2 data received — MAD warmup starts (30 min)")

        book = self.book_manager.books.get(symbol, {})
        cts_ms = book.get("exchange_ts", int(time.time() * 1000))

        self.feature_computer.on_book_update(symbol, bids, asks, cts_ms)

    async def _contract_boundary_loop(self) -> None:
        """Check for 15-minute contract boundaries (00:00, 00:15, 00:30, 00:45)
        and trigger predictions only at those exact times.
        """
        while self._running:
            now_ms = int(time.time() * 1000)
            contract_interval_ms = 900_000  # 15 minutes — Polymarket contract boundaries
            boundary_ms = (now_ms // contract_interval_ms) * contract_interval_ms

            near_boundary = abs(now_ms - boundary_ms) <= 30_000 or \
                            abs(now_ms - (boundary_ms + contract_interval_ms)) <= 30_000

            if near_boundary and boundary_ms > self._last_contract_boundary_ms:
                self._last_contract_boundary_ms = boundary_ms
                await self._run_predictions(now_ms, boundary_ms)

            await self._check_resolutions(now_ms)
            await asyncio.sleep(1.0)

    async def _run_predictions(self, now_ms: int, boundary_ms: int) -> None:
        """Score BTC with all models at contract boundary, trade always."""
        boundary_ts = boundary_ms // 1000

        if not self.feature_computer.is_warmed_up(SYMBOL):
            logger.info("Skipping %s — not warmed up yet", SYMBOL)
            return

        # Get feature bar
        bar = self.feature_computer.get_1min_bar(SYMBOL)
        if bar is None:
            logger.warning("No 1-min bar for %s at boundary", SYMBOL)
            return

        # Mid-price range check
        mid_price = bar.get("mid_price", 0)
        if mid_price < MID_PRICE_TRAINING_RANGE[0] or mid_price > MID_PRICE_TRAINING_RANGE[1]:
            logger.warning(
                "⚠️  %s mid_price %.2f OUTSIDE training range [%.0f, %.0f]",
                SYMBOL, mid_price, MID_PRICE_TRAINING_RANGE[0], MID_PRICE_TRAINING_RANGE[1],
            )

        # Query Polymarket p_market
        p_market = None
        if self._http_session:
            try:
                p_market = await get_p_market(self._http_session, SYMBOL, boundary_ts)
                if p_market is not None:
                    logger.debug("[%s] p_market=%.3f", SYMBOL, p_market)
            except Exception as e:
                logger.warning("p_market query failed: %s", e)

        in_warmup = self._is_in_warmup()

        for model_name, model in self.models.items():
            features = {col: bar.get(col, 0.0) for col in self.feature_names[model_name]}
            feature_vec = np.array(
                [features[col] for col in self.feature_names[model_name]],
                dtype=np.float64,
            ).reshape(1, -1)

            pred_proba = float(model.predict(feature_vec)[0])
            pred_direction = "up" if pred_proba > 0.5 else "down"

            ts_model_ran_ms = int(time.time() * 1000)

            # Generate unique prediction_id
            import uuid
            prediction_id = str(uuid.uuid4())

            # Log prediction (always)
            self.ledger.log_prediction(
                prediction_id=prediction_id,
                model_name=model_name,
                symbol=SYMBOL,
                pred_proba=pred_proba,
                pred_direction=pred_direction,
                price_at_open=mid_price,
                ts_model_ran_ms=ts_model_ran_ms,
                ts_contract_open_ms=boundary_ms,
                features=features,
                warmup=in_warmup,
                p_market=p_market,
            )
            self._prediction_count += 1

            # Suppress during warmup
            if in_warmup:
                warmup_elapsed = (ts_model_ran_ms - (self._first_data_time_ms or ts_model_ran_ms)) / 1000
                warmup_remaining = MAD_WARMUP_SECONDS - warmup_elapsed
                logger.info(
                    "[%s] %s: proba=%.4f WARMUP (%.0fs remaining) — trade suppressed",
                    model_name, SYMBOL, pred_proba, warmup_remaining,
                )
                continue

            # Calculate stake and log trade
            stake = self._calculate_stake(model_name)
            kelly_frac = MODEL_CONFIG[model_name]["kelly_fraction"]

            trade_id = str(uuid.uuid4())

            self.ledger.log_trade(
                trade_id=trade_id,
                prediction_id=prediction_id,
                model_name=model_name,
                symbol=SYMBOL,
                pred_proba=pred_proba,
                pred_direction=pred_direction,
                stake_usdc=stake,
                price_at_open=mid_price,
                ts_model_ran_ms=ts_model_ran_ms,
                ts_contract_open_ms=boundary_ms,
                contract_duration_seconds=CONTRACT_DURATION,
                kelly_fraction=kelly_frac,
                bankroll_at_time=self.bankroll,
                p_market=p_market,
            )
            self._trade_count += 1

            # Schedule resolution
            resolve_at_ms = boundary_ms + CONTRACT_DURATION * 1000
            self._pending_resolutions.append(PendingTrade(
                trade_id=trade_id,
                model_name=model_name,
                prediction_id=prediction_id,
                pred_direction=pred_direction,
                pred_proba=pred_proba,
                price_at_open=mid_price,
                stake_usdc=stake,
                ts_contract_open_ms=boundary_ms,
                resolve_at_ms=resolve_at_ms,
            ))

            self._save_state()

            logger.info(
                "[%s] %s %s: proba=%.4f dir=%s stake=$%.2f bankroll=$%.2f (%d pending)",
                model_name, SYMBOL, "🔼" if pred_direction == "up" else "🔽",
                pred_proba, pred_direction, stake, self.bankroll,
                len(self._pending_resolutions),
            )

        logger.info(
            "Boundary %s: predictions=%d, trades=%d, bankroll=$%.2f, pending=%d",
            datetime.fromtimestamp(boundary_ms / 1000, tz=timezone.utc).strftime("%H:%M"),
            self._prediction_count, self._trade_count,
            self.bankroll, len(self._pending_resolutions),
        )

    async def _check_resolutions(self, now_ms: int) -> None:
        """Resolve pending trades whose contracts have expired."""
        still_pending = []
        for pending in self._pending_resolutions:
            if now_ms >= pending.resolve_at_ms:
                try:
                    success = await self._resolve_trade(pending)
                    if not success:
                        if now_ms - pending.resolve_at_ms > 600_000:
                            logger.error("Giving up on trade %s — missing data", pending.trade_id)
                        else:
                            still_pending.append(pending)
                except Exception as e:
                    logger.error("Error resolving trade %s: %s", pending.trade_id, e)
                    if now_ms - pending.resolve_at_ms > 600_000:
                        logger.error("Giving up on trade %s after repeated errors", pending.trade_id)
                    else:
                        still_pending.append(pending)
            else:
                still_pending.append(pending)

        if len(self._pending_resolutions) != len(still_pending):
            self._pending_resolutions = still_pending
            self._save_state()

    async def _resolve_trade(self, pending: PendingTrade) -> bool:
        """Resolve a single trade with current price."""
        symbol = SYMBOL

        # Get current mid-price
        state = self.feature_computer.states.get(symbol)
        if not state or not state.mid_price_history:
            logger.warning("Cannot resolve %s trade — no price data", symbol)
            return False

        price_at_close = state.mid_price_history[-1]
        ts_close_ms = int(time.time() * 1000)

        # Determine result
        if price_at_close > pending.price_at_open:
            contract_result = "up"
        elif price_at_close < pending.price_at_open:
            contract_result = "down"
        else:
            contract_result = "flat"

        prediction_correct = (contract_result == pending.pred_direction)

        # P&L with Polymarket fee
        p = pending.pred_proba
        p_side = p if p > 0.5 else (1 - p)
        fee = pending.stake_usdc * POLYMARKET_FEE_COEFFICIENT * p_side * (1 - p_side)

        if prediction_correct:
            gross_pnl = pending.stake_usdc
            trade_result = "win"
        else:
            gross_pnl = -pending.stake_usdc
            trade_result = "loss"

        net_pnl = gross_pnl - fee
        self.bankroll += net_pnl

        # Log resolution
        self.ledger.log_resolution(
            trade_id=pending.trade_id,
            prediction_id=pending.prediction_id,
            model_name=pending.model_name,
            ts_contract_close_ms=ts_close_ms,
            price_at_close=price_at_close,
            contract_result=contract_result,
            prediction_correct=prediction_correct,
            gross_pnl=gross_pnl,
            fee_paid=fee,
            net_pnl=net_pnl,
            trade_result=trade_result,
            bankroll_after=self.bankroll,
        )

        emoji = "✅" if prediction_correct else "❌"
        total_return = (self.bankroll / self.initial_bankroll - 1) * 100
        logger.info(
            "[%s] %s %s 900s: open=%.2f close=%.2f result=%s %s "
            "stake=$%.2f net=$%.2f fee=$%.2f bankroll=$%.2f (%+.1f%%) %s",
            pending.model_name, symbol, pending.pred_direction,
            pending.price_at_open, price_at_close, contract_result, emoji,
            pending.stake_usdc, net_pnl, fee, self.bankroll, total_return,
            "🚀" if total_return > 50 else "💰" if total_return > 20 else "📈" if total_return > 0 else "📉",
        )
        return True

    async def run(self) -> None:
        """Start the paper trading loop."""
        total_return = (self.bankroll / self.initial_bankroll - 1) * 100
        logger.info("=" * 70)
        logger.info("BTC 900s AGGRESSIVE COMPOUND PAPER TRADER")
        logger.info("=" * 70)
        logger.info("  Symbol:         %s", SYMBOL)
        logger.info("  Duration:       %ds (15 min)", CONTRACT_DURATION)
        logger.info("  Testnet:        %s", self.testnet)
        logger.info("  Models:         %s", list(self.models.keys()))
        logger.info("  Initial BR:     $%.2f", self.initial_bankroll)
        logger.info("  Restored BR:    $%.2f (%+.1f%%)", self.bankroll, total_return)
        logger.info("  Kelly fractions: h300=%.0f%%, h60=%.0f%%",
                     MODEL_CONFIG["h300"]["kelly_fraction"] * 100,
                     MODEL_CONFIG["h60"]["kelly_fraction"] * 100)
        logger.info("  Threshold:      NONE (trade every prediction)")
        logger.info("  MAD warmup:     %ds", MAD_WARMUP_SECONDS)
        logger.info("  Log file:       %s", self.ledger.path)
        logger.info("=" * 70)

        self.book_manager.on_update(self._on_book_update)

        self._http_session = aiohttp.ClientSession()
        logger.info("  Polymarket API session created")

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
            total_return = (self.bankroll / self.initial_bankroll - 1) * 100
            logger.info(
                "Paper trader stopped. Predictions: %d, Trades: %d, "
                "Bankroll: $%.2f (%+.1f%%)",
                self._prediction_count, self._trade_count,
                self.bankroll, total_return,
            )


def main():
    parser = argparse.ArgumentParser(
        description="BTC 900s Aggressive Compounding Paper Trader"
    )
    parser.add_argument("--testnet", action="store_true", default=False,
                        help="Use Bybit testnet WebSocket")
    parser.add_argument("--log-dir", type=str, default="/data/logs",
                        help="Log directory")
    parser.add_argument("--h60-model", type=str,
                        default=MODEL_CONFIG["h60"]["default_path"],
                        help="Path to H=60 model")
    parser.add_argument("--h300-model", type=str,
                        default=MODEL_CONFIG["h300"]["default_path"],
                        help="Path to H=300 model")
    parser.add_argument("--features-dir", type=str, default="/data/features_v3",
                        help="Path to features_v3 parquets for EWM preload")
    parser.add_argument("--initial-bankroll", type=float, default=INITIAL_BANKROLL_USDC,
                        help=f"Initial bankroll in USDC (default: {INITIAL_BANKROLL_USDC})")
    args = parser.parse_args()

    model_paths = {
        "h60": args.h60_model,
        "h300": args.h300_model,
    }

    for name, path in model_paths.items():
        if not Path(path).exists():
            logger.error("Model file not found: %s (%s)", path, name)
            sys.exit(1)

    trader = BTC900sPaperTrader(
        model_paths=model_paths,
        log_dir=args.log_dir,
        testnet=args.testnet,
        features_dir=args.features_dir,
        initial_bankroll=args.initial_bankroll,
    )

    def handle_signal(sig, frame):
        logger.info("Signal %s received, shutting down...", sig)
        trader._running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    asyncio.run(trader.run())


if __name__ == "__main__":
    main()
