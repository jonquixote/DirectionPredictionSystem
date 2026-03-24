from __future__ import annotations
"""
Propagation lag measurement collector.
Spec v2.6, Section 3.2.

Runs during the propagation lag measurement phase.
No trained model exists yet — uses normalised MLOFI as proxy trigger.
"""

import asyncio
import logging
import time

import aiohttp

from config import CONFIG

logger = logging.getLogger(__name__)


class LagMeasurementCollector:
    """
    Runs during propagation lag measurement phase.
    No trained model exists yet. Uses lag_measurement_trigger_threshold
    as a proxy — normalised MLOFI exceeding this absolute value triggers
    a lag measurement event. Value fixed in CONFIG before first log entry.
    """

    def __init__(
        self,
        assets: list[str],
        kelly_fraction: float,
        bankroll_usdc: float,
        trigger_threshold: float | None = None,
    ):
        self.assets = assets
        self.kelly_fraction = kelly_fraction
        self.bankroll_usdc = bankroll_usdc
        self.trigger_threshold = (
            trigger_threshold or CONFIG["lag_measurement_trigger_threshold"]
        )
        self.execution_viable_size: dict[str, float] = {}
        self.events: list[dict] = []

    def compute_viable_size(self, asset: str, p_market: float = 0.5) -> None:
        """
        Must be computed from CONFIG["kelly_fraction"] before measurement begins.
        Fixed for the entire measurement run — do not recompute per event.
        Call once per asset at startup.

        p_market defaults to 0.5 — the natural anchor for binary direction
        contracts which open near 0.50 at each hourly reset.
        """
        position_usdc = self.kelly_fraction * self.bankroll_usdc
        self.execution_viable_size[asset] = position_usdc / p_market

    def should_trigger(self, mlofi_normalised: float) -> bool:
        """Proxy threshold — no model required."""
        return abs(mlofi_normalised) > self.trigger_threshold

    async def on_ofi_threshold_cross(
        self, asset: str, t_ofi_ms: int, mlofi_normalised: float
    ) -> dict:
        """Called when normalised MLOFI exceeds trigger_threshold."""
        event = {
            "asset": asset,
            "t_ofi_signal_ms": t_ofi_ms,
            "mlofi_normalised": mlofi_normalised,
            "t_ask_depth_drop_ms": None,
            "t_ask_price_move_ms": None,
            "ask_depth_at_signal": None,
            "ask_depth_viable_threshold": self.execution_viable_size.get(asset),
        }
        await self._monitor_polymarket_response(event)
        self.events.append(event)
        return event

    async def _monitor_polymarket_response(
        self, event: dict, monitor_window_s: int = 120
    ) -> None:
        """
        Poll Polymarket at 1s resolution for up to 2 minutes after OFI signal.
        GET https://clob.polymarket.com/book?token_id={token_id}
        Parse: asks[] → best ask price and size
        Log t_ask_depth_drop_ms when asks[0].size < viable_threshold
        Log t_ask_price_move_ms when asks[0].price > baseline * 1.005
        operational_lag = t_ask_depth_drop_ms - t_ofi_signal_ms  (PRIMARY metric)
        """
        from api.polymarket import get_order_book, parse_book

        viable_threshold = event.get("ask_depth_viable_threshold", 0)
        baseline_price = None

        async with aiohttp.ClientSession() as session:
            for _ in range(monitor_window_s):
                try:
                    # TODO: token_id resolution — requires market-to-token mapping
                    book_data = await get_order_book(session, event["asset"])
                    parsed = parse_book(book_data)

                    if parsed is None:
                        await asyncio.sleep(1)
                        continue

                    now_ms = int(time.time() * 1000)

                    if baseline_price is None:
                        baseline_price = parsed["best_ask_price"]
                        event["ask_depth_at_signal"] = parsed["best_ask_size"]

                    # Check depth drop
                    if (
                        event["t_ask_depth_drop_ms"] is None
                        and viable_threshold > 0
                        and parsed["best_ask_size"] < viable_threshold
                    ):
                        event["t_ask_depth_drop_ms"] = now_ms
                        logger.info(
                            "Depth drop detected for %s at %d ms",
                            event["asset"],
                            now_ms,
                        )

                    # Check price move
                    if (
                        event["t_ask_price_move_ms"] is None
                        and baseline_price > 0
                        and parsed["best_ask_price"] > baseline_price * 1.005
                    ):
                        event["t_ask_price_move_ms"] = now_ms
                        logger.info(
                            "Price move detected for %s at %d ms",
                            event["asset"],
                            now_ms,
                        )

                    # Stop early if both recorded
                    if (
                        event["t_ask_depth_drop_ms"] is not None
                        and event["t_ask_price_move_ms"] is not None
                    ):
                        break

                except Exception as e:
                    logger.warning("Monitoring error for %s: %s", event["asset"], e)

                await asyncio.sleep(1)

    @staticmethod
    def compute_operational_lag(event: dict) -> int | None:
        """Primary metric: t_ask_depth_drop - t_ofi_signal"""
        if event["t_ask_depth_drop_ms"] and event["t_ofi_signal_ms"]:
            return event["t_ask_depth_drop_ms"] - event["t_ofi_signal_ms"]
        return None
