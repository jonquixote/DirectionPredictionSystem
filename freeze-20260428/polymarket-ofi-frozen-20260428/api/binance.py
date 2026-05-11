from __future__ import annotations
"""
Binance WebSocket order book manager.
Spec v2.6, Section 7.

Maintains local order book from diff depth stream.
Keys normalised to float at insert — not left as strings.
Sync protocol per official Binance documentation.
"""

import asyncio
import json
import logging

import aiohttp
import websockets

logger = logging.getLogger(__name__)


class BinanceOrderBookManager:
    """
    Maintains local order book from diff depth stream.
    Keys normalised to float at insert — not left as strings.
    Prevents subtle ordering bugs if string comparison is ever invoked.
    """

    def __init__(self, symbol: str, levels: int = 10):
        self.symbol = symbol
        self.levels = levels
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.last_update_id: int = 0
        self.synced: bool = False

    def apply_update(self, event: dict) -> None:
        """Normalise to float keys at insert, not at read time."""
        for price_str, qty_str in event["b"]:
            price, qty = float(price_str), float(qty_str)
            if qty == 0.0:
                self.bids.pop(price, None)
            else:
                self.bids[price] = qty
        for price_str, qty_str in event["a"]:
            price, qty = float(price_str), float(qty_str)
            if qty == 0.0:
                self.asks.pop(price, None)
            else:
                self.asks[price] = qty
        self.last_update_id = event["u"]

    def get_top_levels(self) -> tuple[list, list]:
        """Returns (sorted_bids_desc, sorted_asks_asc), each truncated to self.levels."""
        sorted_bids = sorted(self.bids.items(), reverse=True)[: self.levels]
        sorted_asks = sorted(self.asks.items())[: self.levels]
        return sorted_bids, sorted_asks

    def detect_gap(self, event: dict) -> bool:
        """Trigger resync if sequence gap detected."""
        return event["U"] > self.last_update_id + 1


# Sync protocol:
#   1. Open WebSocket, buffer diff depth events
#   2. GET /api/v3/depth?symbol={symbol}&limit=1000 for snapshot
#   3. Discard events where u < snapshot.lastUpdateId
#   4. Verify first remaining event: U <= lastUpdateId+1 <= u
#   5. Apply buffered events sequentially
#   6. On gap (event.U > local_update_id + 1): resync from step 2


async def sync_order_book(
    manager: BinanceOrderBookManager,
    ws_url: str | None = None,
    rest_base: str = "https://api.binance.com",
) -> None:
    """
    Full sync protocol implementation.
    Opens WebSocket, fetches snapshot, reconciles buffered events,
    then applies live updates. Resyncs on gap detection.
    """
    symbol_lower = manager.symbol.lower()
    ws_url = ws_url or f"wss://stream.binance.com:9443/ws/{symbol_lower}@depth@100ms"
    snapshot_url = f"{rest_base}/api/v3/depth?symbol={manager.symbol}&limit=1000"

    buffer: list[dict] = []

    async with websockets.connect(ws_url) as ws:
        # Step 1: Buffer incoming events while fetching snapshot
        async def buffer_events():
            while not manager.synced:
                msg = await ws.recv()
                buffer.append(json.loads(msg))

        buffer_task = asyncio.create_task(buffer_events())

        # Step 2: GET snapshot
        async with aiohttp.ClientSession() as session:
            async with session.get(snapshot_url) as resp:
                snapshot = await resp.json()

        snapshot_last_id = snapshot["lastUpdateId"]

        # Initialize book from snapshot
        manager.bids = {
            float(p): float(q) for p, q in snapshot["bids"]
        }
        manager.asks = {
            float(p): float(q) for p, q in snapshot["asks"]
        }
        manager.last_update_id = snapshot_last_id

        # Step 3-5: Apply buffered events
        for event in buffer:
            if event["u"] < snapshot_last_id:
                continue  # Step 3: discard stale events
            if event["U"] <= snapshot_last_id + 1 <= event["u"]:
                # Step 4: first valid event
                manager.apply_update(event)
            elif event["U"] > snapshot_last_id:
                manager.apply_update(event)

        manager.synced = True
        buffer_task.cancel()

        # Step 6: Live updates with gap detection
        async for msg in ws:
            event = json.loads(msg)
            if manager.detect_gap(event):
                logger.warning(
                    "Sequence gap detected for %s, triggering resync",
                    manager.symbol,
                )
                manager.synced = False
                # Recursive resync
                await sync_order_book(manager, ws_url, rest_base)
                return
            manager.apply_update(event)
