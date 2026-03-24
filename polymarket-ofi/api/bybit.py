from __future__ import annotations
"""
Bybit spot WebSocket order book manager.
Implements AbstractOrderBookFeed for the Bybit exchange.

Channel: orderbook.200.{SYMBOL} (spot)
Delta protocol:
  - type: "snapshot" → replace the full book entirely
  - type: "delta"    → size > 0 = insert/update level, size == 0 = delete level
  - data.u = update ID — validate sequentially; gap → request new snapshot
  - u == 1 = service restart → discard book, wait for next snapshot
  - Use cts as exchange_ts in Parquet schema — not local receipt time
"""

import asyncio
import json
import logging
from typing import Callable

import websockets

from .feed import AbstractOrderBookFeed

logger = logging.getLogger(__name__)

# Bybit WebSocket endpoints
BYBIT_WS_SPOT = "wss://stream.bybit.com/v5/public/spot"
BYBIT_WS_SPOT_TESTNET = "wss://stream-testnet.bybit.com/v5/public/spot"


class BybitOrderBookManager(AbstractOrderBookFeed):
    """
    Maintains local order book from Bybit spot WebSocket stream.
    Keys normalised to float at insert — not left as strings.
    Implements full delta protocol with gap detection and restart handling.
    """

    def __init__(
        self,
        symbols: list[str] | None = None,
        levels: int = 10,
        testnet: bool = False,
    ):
        self.symbols = [s.upper() for s in (symbols or ["BTCUSDT"])]
        self.levels = levels
        self.testnet = testnet
        self.ws_url = BYBIT_WS_SPOT_TESTNET if testnet else BYBIT_WS_SPOT

        # Per-symbol books
        self.books: dict[str, dict] = {}
        for sym in self.symbols:
            self.books[sym] = {
                "bids": {},      # {price_float: qty_float}
                "asks": {},
                "last_u": 0,     # last update ID
                "synced": False,
                "exchange_ts": 0,  # cts from Bybit
            }

        self._callbacks: list[Callable] = []
        self._ws = None

    # ── AbstractOrderBookFeed implementation ────────────────────────

    def connect(self) -> None:
        """Open WebSocket — call via asyncio.run() or event loop."""
        raise NotImplementedError(
            "Use connect_async() for async WebSocket connection"
        )

    def subscribe(self, symbols: list[str]) -> None:
        """Subscribe to order book updates for given symbols."""
        self.symbols = [s.upper() for s in symbols]
        for sym in self.symbols:
            if sym not in self.books:
                self.books[sym] = {
                    "bids": {}, "asks": {},
                    "last_u": 0, "synced": False, "exchange_ts": 0,
                }

    def get_snapshot(self, symbol: str) -> dict:
        """
        Return current full order book snapshot.
        Returns: {"bids": [(price, size), ...], "asks": [(price, size), ...]}
        """
        symbol = symbol.upper()
        book = self.books.get(symbol)
        if not book:
            return {"bids": [], "asks": []}

        sorted_bids = sorted(book["bids"].items(), reverse=True)[:self.levels]
        sorted_asks = sorted(book["asks"].items())[:self.levels]
        return {"bids": sorted_bids, "asks": sorted_asks}

    def on_update(self, callback: Callable) -> None:
        """Register callback fired on every incremental update."""
        self._callbacks.append(callback)

    def disconnect(self) -> None:
        """Close connection cleanly."""
        if self._ws:
            asyncio.get_event_loop().create_task(self._ws.close())

    # ── Bybit-specific delta protocol ───────────────────────────────

    def get_top_levels(self, symbol: str | None = None) -> tuple[list, list]:
        """
        Returns (sorted_bids_desc, sorted_asks_asc), truncated to self.levels.
        If no symbol given, uses first symbol.
        """
        sym = (symbol or self.symbols[0]).upper()
        book = self.books.get(sym)
        if not book:
            return [], []
        sorted_bids = sorted(book["bids"].items(), reverse=True)[:self.levels]
        sorted_asks = sorted(book["asks"].items())[:self.levels]
        return sorted_bids, sorted_asks

    def apply_snapshot(self, symbol: str, data: dict) -> None:
        """
        type: "snapshot" → replace the full book entirely.
        """
        symbol = symbol.upper()
        book = self.books[symbol]
        book["bids"] = {}
        book["asks"] = {}

        for price_str, size_str in data.get("b", []):
            price, size = float(price_str), float(size_str)
            if size > 0:
                book["bids"][price] = size

        for price_str, size_str in data.get("a", []):
            price, size = float(price_str), float(size_str)
            if size > 0:
                book["asks"][price] = size

        book["last_u"] = data.get("u", 0)
        book["synced"] = True
        book["exchange_ts"] = data.get("cts", 0)

    def apply_delta(self, symbol: str, data: dict) -> None:
        """
        type: "delta" → size > 0 = insert/update, size == 0 = delete.
        """
        symbol = symbol.upper()
        book = self.books[symbol]

        for price_str, size_str in data.get("b", []):
            price, size = float(price_str), float(size_str)
            if size == 0.0:
                book["bids"].pop(price, None)
            else:
                book["bids"][price] = size

        for price_str, size_str in data.get("a", []):
            price, size = float(price_str), float(size_str)
            if size == 0.0:
                book["asks"].pop(price, None)
            else:
                book["asks"][price] = size

        book["last_u"] = data.get("u", 0)
        book["exchange_ts"] = data.get("cts", 0)

    def detect_gap(self, symbol: str, update_id: int) -> bool:
        """Trigger resync if sequence gap detected."""
        book = self.books[symbol.upper()]
        if book["last_u"] == 0:
            return False
        return update_id > book["last_u"] + 1

    def handle_restart(self, symbol: str, update_id: int) -> bool:
        """
        u == 1 = Bybit service restart.
        Discard book, wait for next snapshot.
        Returns True if this was a restart event.
        """
        if update_id == 1:
            symbol = symbol.upper()
            book = self.books[symbol]
            book["bids"] = {}
            book["asks"] = {}
            book["last_u"] = 0
            book["synced"] = False
            logger.warning("Bybit service restart detected for %s, waiting for snapshot", symbol)
            return True
        return False

    def process_message(self, msg: dict) -> None:
        """
        Process a raw Bybit WebSocket message.
        Handles both snapshot and delta types per the delta protocol.

        Expected message format:
        {
            "topic": "orderbook.200.BTCUSDT",
            "type": "snapshot" | "delta",
            "ts": 1234567890,
            "data": {
                "s": "BTCUSDT",
                "b": [["price", "size"], ...],
                "a": [["price", "size"], ...],
                "u": 12345,
                "seq": 67890
            },
            "cts": 1234567890
        }
        """
        topic = msg.get("topic", "")
        msg_type = msg.get("type", "")
        data = msg.get("data", {})
        symbol = data.get("s", "")
        update_id = data.get("u", 0)

        if not symbol or symbol.upper() not in self.books:
            return

        symbol = symbol.upper()

        # Inject cts into data for timestamp tracking
        data["cts"] = msg.get("cts", 0)

        if msg_type == "snapshot":
            self.apply_snapshot(symbol, data)
        elif msg_type == "delta":
            book = self.books[symbol]

            # u == 1 on a delta: service restart — discard and wait for snapshot
            if self.handle_restart(symbol, update_id):
                return

            if not book["synced"]:
                # Skip deltas until we have a snapshot
                return

            # Gap detection
            if self.detect_gap(symbol, update_id):
                logger.warning(
                    "Sequence gap for %s (expected %d, got %d), requesting resync",
                    symbol, book["last_u"] + 1, update_id,
                )
                book["synced"] = False
                return

            self.apply_delta(symbol, data)

        # Fire callbacks
        for cb in self._callbacks:
            try:
                bids, asks = self.get_top_levels(symbol)
                cb(symbol, bids, asks)
            except Exception as e:
                logger.error("Callback error: %s", e)

    async def connect_async(self) -> None:
        """
        Async WebSocket connection with automatic resubscription.
        """
        topics = [f"orderbook.200.{sym}" for sym in self.symbols]
        sub_msg = {
            "op": "subscribe",
            "args": topics,
        }

        while True:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self._ws = ws
                    await ws.send(json.dumps(sub_msg))
                    logger.info("Subscribed to %s", topics)

                    async for raw_msg in ws:
                        msg = json.loads(raw_msg)

                        # Skip subscription confirmations
                        if msg.get("op") == "subscribe":
                            if msg.get("success"):
                                logger.info("Subscription confirmed")
                            else:
                                logger.error("Subscription failed: %s", msg)
                            continue

                        self.process_message(msg)

            except websockets.ConnectionClosedError as e:
                logger.warning("WebSocket closed: %s, reconnecting in 5s", e)
                await asyncio.sleep(5)
            except Exception as e:
                logger.error("WebSocket error: %s, reconnecting in 5s", e)
                await asyncio.sleep(5)
