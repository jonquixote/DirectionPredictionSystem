#!/usr/bin/env python3
"""
h60_v2 Paper Trader — dedicated single-model BTC 900s compounding trader.

h60_v2 historical performance: 60.0% win rate, 80 trades, +$145.82, +$1.82/trade
Sample: 80 trades over 33 hours (small but strong signal).

Kelly at 60% win rate: theoretical Kelly = 20% → using 5% for safety.
Bankroll starts at $10, compounds after every resolution.

Separate from BTC_900s_paper_trader — own JSONL, own state.

Usage:
    python -m trading.h60_v2_paper_trader --testnet
    python -m trading.h60_v2_paper_trader
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
import uuid
import fcntl
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import lightgbm as lgb
import numpy as np

from api.bybit import BybitOrderBookManager
from trading.live_features import LiveFeatureComputer
from feature_engineering.feature_contract import resolve_model_feature_contract
from trading.polymarket_discovery import get_p_market

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("h60_v2_trader")

# ── Configuration ──────────────────────────────────────────────

SYMBOL = "BTCUSDT"
CONTRACT_DURATION = 900  # 15-minute contracts
INITIAL_BANKROLL_USDC = 10.0
KELLY_FRACTION = 0.05  # 5% — conservative for 60% historical win rate
POLYMARKET_FEE_COEFFICIENT = 0.072
MIN_FEATURE_WARMUP_SECONDS = 120
MAD_WARMUP_SECONDS = 1800  # 30 minutes
MID_PRICE_TRAINING_RANGE = [58_000.0, 110_000.0]
LEDGER_DIR = "/data/logs"
LEDGER_FILE = "h60_v2_paper_trader.jsonl"
STATE_FILE = "/data/logs/h60_v2_state.json"


@dataclass
class PendingTrade:
    trade_id: str
    prediction_id: str
    pred_direction: str
    pred_proba: float
    price_at_open: float
    stake_usdc: float
    ts_contract_open_ms: int
    resolve_at_ms: int


class V2Ledger:
    """Append-only JSONL ledger for h60_v2 trader."""

    def __init__(self, log_dir: str | Path = LEDGER_DIR):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.log_dir / LEDGER_FILE

    def _append(self, record: dict) -> None:
        line = json.dumps(record, default=str) + "\n"
        with open(self.path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(line)
            fcntl.flock(f, fcntl.LOCK_UN)

    def log_prediction(
        self, prediction_id: str, symbol: str, pred_proba: float,
        pred_direction: str, price_at_open: float, ts_model_ran_ms: int,
        ts_contract_open_ms: int, features: dict, warmup: bool,
        p_market: float | None = None,
    ) -> None:
        self._append({
            "record_type": "prediction",
            "prediction_id": prediction_id,
            "model": "h60_v2",
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
        self, trade_id: str, prediction_id: str, symbol: str,
        pred_proba: float, pred_direction: str, stake_usdc: float,
        price_at_open: float, ts_model_ran_ms: int, ts_contract_open_ms: int,
        contract_duration_seconds: int, kelly_fraction: float,
        bankroll_at_time: float, p_market: float | None = None,
    ) -> None:
        self._append({
            "record_type": "trade_entry",
            "trade_id": trade_id,
            "prediction_id": prediction_id,
            "model": "h60_v2",
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
        self, trade_id: str, prediction_id: str, ts_contract_close_ms: int,
        price_at_close: float, contract_result: str, prediction_correct: bool,
        gross_pnl: float, fee_paid: float, net_pnl: float, trade_result: str,
        bankroll_after: float,
    ) -> None:
        self._append({
            "record_type": "trade_resolution",
            "trade_id": trade_id,
            "prediction_id": prediction_id,
            "model": "h60_v2",
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


class H60V2PaperTrader:
    """
    Dedicated h60_v2 paper trader for BTC 900s contracts.

    Single model, compounding bankroll, 5% Kelly sizing.
    Independent from the BTC_900s_paper_trader (separate JSONL + state).
    """

    def __init__(
        self,
        model_path: str,
        log_dir: str = LEDGER_DIR,
        testnet: bool = True,
        features_dir: str = "/data/features_v3",
        initial_bankroll: float = INITIAL_BANKROLL_USDC,
    ):
        self.testnet = testnet
        self.bankroll = initial_bankroll
        self.initial_bankroll = initial_bankroll

        # Ledger
        self.ledger = V2Ledger(log_dir)

        # Load model
        model_dir = Path(model_path).parent
        self.model = lgb.Booster(model_file=model_path)
        fn_path = model_dir / "feature_names.json"
        self.feature_names = resolve_model_feature_contract(self.model, fn_path)
        logger.info("Loaded h60_v2 from %s (%d features)", model_path, len(self.feature_names))

        # Feature computer (BTC only)
        self.feature_computer = LiveFeatureComputer(
            symbols=[SYMBOL],
            features_dir=features_dir,
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

        # State persistence
        self._load_state()

        # Preload EWM
        self.feature_computer.preload_ewm()

    def _load_state(self):
        if Path(STATE_FILE).exists():
            try:
                with open(STATE_FILE) as f:
                    state = json.load(f)
                self.bankroll = state.get("bankroll", self.initial_bankroll)
                self.initial_bankroll = state.get("initial_bankroll", self.initial_bankroll)
                pending_raw = state.get("pending", [])
                self._pending_resolutions = [
                    PendingTrade(**p) for p in pending_raw
                ]
                logger.info(
                    "Restored state: bankroll=$%.2f, %d pending",
                    self.bankroll, len(self._pending_resolutions),
                )
            except Exception as e:
                logger.error("Failed to restore state: %s", e)

    def _save_state(self):
        pending = [
            {
                "trade_id": p.trade_id,
                "prediction_id": p.prediction_id,
                "pred_direction": p.pred_direction,
                "pred_proba": p.pred_proba,
                "price_at_open": p.price_at_open,
                "stake_usdc": p.stake_usdc,
                "ts_contract_open_ms": p.ts_contract_open_ms,
                "resolve_at_ms": p.resolve_at_ms,
            }
            for p in self._pending_resolutions
        ]
        state = {
            "bankroll": round(self.bankroll, 2),
            "initial_bankroll": self.initial_bankroll,
            "pending": pending,
        }
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(state, f)
        except Exception as e:
            logger.error("Failed to save state: %s", e)

    def _is_in_warmup(self) -> bool:
        if self._first_data_time_ms is None:
            return True
        elapsed_ms = int(time.time() * 1000) - self._first_data_time_ms
        return elapsed_ms < MAD_WARMUP_SECONDS * 1000

    def _calculate_stake(self) -> float:
        return max(KELLY_FRACTION * self.bankroll, 0.10)

    def _on_book_update(self, symbol: str, bids: list, asks: list) -> None:
        if not bids or not asks:
            return
        if self._first_data_time_ms is None:
            self._first_data_time_ms = int(time.time() * 1000)
            logger.info("First L2 data — MAD warmup starts (30 min)")
        book = self.book_manager.books.get(symbol, {})
        cts_ms = book.get("exchange_ts", int(time.time() * 1000))
        self.feature_computer.on_book_update(symbol, bids, asks, cts_ms)

    async def _contract_boundary_loop(self) -> None:
        while self._running:
            now_ms = int(time.time() * 1000)
            contract_interval_ms = 900_000  # 15-min boundaries: :00, :15, :30, :45
            boundary_ms = (now_ms // contract_interval_ms) * contract_interval_ms

            near_boundary = abs(now_ms - boundary_ms) <= 30_000 or \
                            abs(now_ms - (boundary_ms + contract_interval_ms)) <= 30_000

            if near_boundary and boundary_ms > self._last_contract_boundary_ms:
                self._last_contract_boundary_ms = boundary_ms
                await self._run_predictions(now_ms, boundary_ms)

            await self._check_resolutions(now_ms)
            await asyncio.sleep(1.0)

    async def _run_predictions(self, now_ms: int, boundary_ms: int) -> None:
        boundary_ts = boundary_ms // 1000

        if not self.feature_computer.is_warmed_up(SYMBOL):
            logger.info("Skipping %s — not warmed up yet", SYMBOL)
            return

        bar = self.feature_computer.get_1min_bar(SYMBOL)
        if bar is None:
            logger.warning("No 1-min bar for %s at boundary", SYMBOL)
            return

        mid_price = bar.get("mid_price", 0)
        if mid_price < MID_PRICE_TRAINING_RANGE[0] or mid_price > MID_PRICE_TRAINING_RANGE[1]:
            logger.warning(
                "⚠️  %s mid_price %.2f OUTSIDE training range [%.0f, %.0f]",
                SYMBOL, mid_price, MID_PRICE_TRAINING_RANGE[0], MID_PRICE_TRAINING_RANGE[1],
            )

        p_market = None
        if self._http_session:
            try:
                p_market = await get_p_market(self._http_session, SYMBOL, boundary_ts)
            except Exception as e:
                logger.warning("p_market query failed: %s", e)

        in_warmup = self._is_in_warmup()

        features = {col: bar.get(col, 0.0) for col in self.feature_names}
        feature_vec = np.array(
            [features[col] for col in self.feature_names],
            dtype=np.float64,
        ).reshape(1, -1)

        pred_proba = float(self.model.predict(feature_vec)[0])
        pred_direction = "up" if pred_proba > 0.5 else "down"
        ts_model_ran_ms = int(time.time() * 1000)
        prediction_id = str(uuid.uuid4())

        self.ledger.log_prediction(
            prediction_id=prediction_id, symbol=SYMBOL,
            pred_proba=pred_proba, pred_direction=pred_direction,
            price_at_open=mid_price, ts_model_ran_ms=ts_model_ran_ms,
            ts_contract_open_ms=boundary_ms, features=features,
            warmup=in_warmup, p_market=p_market,
        )
        self._prediction_count += 1

        if in_warmup:
            warmup_elapsed = (ts_model_ran_ms - (self._first_data_time_ms or ts_model_ran_ms)) / 1000
            warmup_remaining = MAD_WARMUP_SECONDS - warmup_elapsed
            logger.info(
                "h60_v2 %s: proba=%.4f WARMUP (%.0fs remaining)",
                SYMBOL, pred_proba, warmup_remaining,
            )
            return

        stake = self._calculate_stake()
        trade_id = str(uuid.uuid4())

        self.ledger.log_trade(
            trade_id=trade_id, prediction_id=prediction_id, symbol=SYMBOL,
            pred_proba=pred_proba, pred_direction=pred_direction,
            stake_usdc=stake, price_at_open=mid_price,
            ts_model_ran_ms=ts_model_ran_ms, ts_contract_open_ms=boundary_ms,
            contract_duration_seconds=CONTRACT_DURATION,
            kelly_fraction=KELLY_FRACTION, bankroll_at_time=self.bankroll,
            p_market=p_market,
        )
        self._trade_count += 1

        resolve_at_ms = boundary_ms + CONTRACT_DURATION * 1000
        self._pending_resolutions.append(PendingTrade(
            trade_id=trade_id, prediction_id=prediction_id,
            pred_direction=pred_direction, pred_proba=pred_proba,
            price_at_open=mid_price, stake_usdc=stake,
            ts_contract_open_ms=boundary_ms, resolve_at_ms=resolve_at_ms,
        ))

        self._save_state()

        total_ret = (self.bankroll / self.initial_bankroll - 1) * 100
        logger.info(
            "h60_v2 %s %s: proba=%.4f dir=%s stake=$%.2f BR=$%.2f (%+.1f%%) (%d pending)",
            SYMBOL, "🔼" if pred_direction == "up" else "🔽",
            pred_proba, pred_direction, stake, self.bankroll,
            total_ret, len(self._pending_resolutions),
        )

        logger.info(
            "Boundary %s: predictions=%d, trades=%d, BR=$%.2f, pending=%d",
            datetime.fromtimestamp(boundary_ms / 1000, tz=timezone.utc).strftime("%H:%M"),
            self._prediction_count, self._trade_count,
            self.bankroll, len(self._pending_resolutions),
        )

    async def _check_resolutions(self, now_ms: int) -> None:
        still_pending = []
        for pending in self._pending_resolutions:
            if now_ms >= pending.resolve_at_ms:
                try:
                    success = await self._resolve_trade(pending)
                    if not success:
                        if now_ms - pending.resolve_at_ms > 600_000:
                            logger.error("Giving up on trade %s", pending.trade_id)
                        else:
                            still_pending.append(pending)
                except Exception as e:
                    logger.error("Error resolving trade %s: %s", pending.trade_id, e)
                    if now_ms - pending.resolve_at_ms > 600_000:
                        logger.error("Giving up on trade %s after errors", pending.trade_id)
                    else:
                        still_pending.append(pending)
            else:
                still_pending.append(pending)

        if len(self._pending_resolutions) != len(still_pending):
            self._pending_resolutions = still_pending
            self._save_state()

    async def _resolve_trade(self, pending: PendingTrade) -> bool:
        state = self.feature_computer.states.get(SYMBOL)
        if not state or not state.mid_price_history:
            logger.warning("Cannot resolve — no price data")
            return False

        price_at_close = state.mid_price_history[-1]
        ts_close_ms = int(time.time() * 1000)

        if price_at_close > pending.price_at_open:
            contract_result = "up"
        elif price_at_close < pending.price_at_open:
            contract_result = "down"
        else:
            contract_result = "flat"

        prediction_correct = (contract_result == pending.pred_direction)

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

        self.ledger.log_resolution(
            trade_id=pending.trade_id, prediction_id=pending.prediction_id,
            ts_contract_close_ms=ts_close_ms, price_at_close=price_at_close,
            contract_result=contract_result, prediction_correct=prediction_correct,
            gross_pnl=gross_pnl, fee_paid=fee, net_pnl=net_pnl,
            trade_result=trade_result, bankroll_after=self.bankroll,
        )

        total_ret = (self.bankroll / self.initial_bankroll - 1) * 100
        emoji = "✅" if prediction_correct else "❌"
        br_icon = "🚀" if total_ret > 50 else "💰" if total_ret > 20 else "📈" if total_ret > 0 else "📉"
        logger.info(
            "h60_v2 %s 900s: open=%.2f close=%.2f result=%s %s "
            "stake=$%.2f net=$%.2f fee=$%.2f BR=$%.2f (%+.1f%%) %s",
            pending.pred_direction, pending.price_at_open, price_at_close,
            contract_result, emoji, pending.stake_usdc, net_pnl, fee,
            self.bankroll, total_ret, br_icon,
        )
        return True

    async def run(self) -> None:
        total_ret = (self.bankroll / self.initial_bankroll - 1) * 100
        logger.info("=" * 70)
        logger.info("h60_v2 BTC 900s PAPER TRADER")
        logger.info("=" * 70)
        logger.info("  Symbol:         %s", SYMBOL)
        logger.info("  Duration:       %ds (15 min)", CONTRACT_DURATION)
        logger.info("  Testnet:        %s", self.testnet)
        logger.info("  Model:          h60_v2")
        logger.info("  Kelly:          %.0f%%", KELLY_FRACTION * 100)
        logger.info("  Initial BR:     $%.2f", self.initial_bankroll)
        logger.info("  Restored BR:    $%.2f (%+.1f%%)", self.bankroll, total_ret)
        logger.info("  Threshold:      NONE (trade every prediction)")
        logger.info("  MAD warmup:     %ds", MAD_WARMUP_SECONDS)
        logger.info("  Ledger:         %s", self.ledger.path)
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
            total_ret = (self.bankroll / self.initial_bankroll - 1) * 100
            logger.info(
                "h60_v2 stopped. Predictions: %d, Trades: %d, "
                "Bankroll: $%.2f (%+.1f%%)",
                self._prediction_count, self._trade_count,
                self.bankroll, total_ret,
            )


def main():
    import argparse
    parser = argparse.ArgumentParser(description="h60_v2 BTC 900s Paper Trader")
    parser.add_argument("--testnet", action="store_true", default=False)
    parser.add_argument("--log-dir", type=str, default=LEDGER_DIR)
    parser.add_argument("--model", type=str,
                        default="/data/models_v2/latest_h60/model.lgb")
    parser.add_argument("--features-dir", type=str, default="/data/features_v3")
    parser.add_argument("--initial-bankroll", type=float, default=INITIAL_BANKROLL_USDC)
    args = parser.parse_args()

    if not Path(args.model).exists():
        logger.error("Model file not found: %s", args.model)
        sys.exit(1)

    trader = H60V2PaperTrader(
        model_path=args.model,
        log_dir=args.log_dir,
        testnet=args.testnet,
        features_dir=args.features_dir,
        initial_bankroll=args.initial_bankroll,
    )

    def handle_signal(sig, frame):
        logger.info("Signal %s — shutting down...", sig)
        trader._running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    asyncio.run(trader.run())


if __name__ == "__main__":
    main()
