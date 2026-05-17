"""Async client for the v3 WS feed service.

Connects to the feed's Unix domain data socket, parses canonical 1s
tick messages, and fires registered callbacks.  On disconnect, performs
snapshot+resume reconnection via the control socket.

Usage (inside an async context):
    client = FeedClient(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"])
    client.on_update(my_callback)
    await client.connect()
    # then: await client.listen()  (blocks until disconnect)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_DATA_SOCKET = "/tmp/v3-feed.sock"
DEFAULT_CONTROL_SOCKET = "/tmp/v3-feed-ctl.sock"

RECONNECT_BASE_DELAY = 2.0
RECONNECT_MAX_DELAY = 30.0


class FeedClient:
    def __init__(
        self,
        symbols: list[str],
        data_socket_path: str = DEFAULT_DATA_SOCKET,
        control_socket_path: str = DEFAULT_CONTROL_SOCKET,
    ) -> None:
        self.symbols = [s.upper() for s in symbols]
        self.data_socket_path = data_socket_path
        self.control_socket_path = control_socket_path

        self._tick_callbacks: list[Callable] = []
        self._snapshot_callbacks: list[Callable] = []

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._running = True
        self._connected = False
        self._first_data_time_ms: Optional[int] = None

    # ── Public API ─────────────────────────────────────────────

    def on_update(self, callback: Callable) -> None:
        """Register callback for canonical 1s ticks.

        Signature: callback(symbol, bids, asks, exchange_ts)
        """
        self._tick_callbacks.append(callback)

    def on_snapshot(self, callback: Callable) -> None:
        """Register callback for full-book snapshots (on connect / reconnect).

        Signature: callback(symbol, bids, asks, exchange_ts)
        """
        self._snapshot_callbacks.append(callback)

    @property
    def first_data_time_ms(self) -> Optional[int]:
        return self._first_data_time_ms

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self, max_retries: int = 30, retry_delay: float = 2.0) -> None:
        """Open the data socket connection with startup retry.

        Retries up to *max_retries* times (default 30 × 2s = 60s) so
        the paper trader can start before the feed service is fully up.
        """
        for attempt in range(max_retries):
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(
                    self.data_socket_path,
                )
                self._connected = True
                logger.info("feed_client_connected path=%s", self.data_socket_path)
                return
            except (ConnectionError, OSError) as exc:
                if attempt < max_retries - 1:
                    logger.info(
                        "feed_client_connect_retry attempt=%d/%d delay=%.1fs err=%s",
                        attempt + 1, max_retries, retry_delay, exc,
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    raise

    async def listen(self) -> None:
        """Read tick messages and fire callbacks. Reconnects on failure."""
        while self._running:
            try:
                if not self._connected:
                    await self._reconnect()

                assert self._reader is not None
                async for raw_line in self._reader:
                    line = raw_line.decode().strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("feed_client_invalid_json line=%s", line[:120])
                        continue
                    self._dispatch(msg)

            except (ConnectionError, OSError) as exc:
                logger.warning("feed_client_disconnected: %s", exc)
                self._close_writer()
                if not self._running:
                    break

            logger.info("feed_client_reconnecting")

    def close(self) -> None:
        self._running = False
        self._close_writer()

    # ── Control socket helpers ─────────────────────────────────

    async def health(self) -> dict:
        """Query the feed service health via the control socket."""
        return await self._control_request({"cmd": "health"})

    async def request_snapshot(self, symbol: str) -> dict:
        """Request a full book snapshot for *symbol* via the control socket."""
        return await self._control_request({"cmd": "snapshot", "symbol": symbol.upper()})

    async def _control_request(self, req: dict) -> dict:
        try:
            reader, writer = await asyncio.open_unix_connection(
                self.control_socket_path,
            )
            writer.write((json.dumps(req) + "\n").encode())
            await writer.drain()
            buf = await asyncio.wait_for(reader.read(65536), timeout=5.0)
            writer.close()
            await writer.wait_closed()
            return json.loads(buf.decode().strip())
        except Exception as exc:
            logger.warning("feed_client_control_error: %s", exc)
            return {"error": str(exc)}

    # ── Internal ───────────────────────────────────────────────

    def _dispatch(self, msg: dict) -> None:
        msg_type = msg.get("type", "")

        if msg_type == "tick":
            if self._first_data_time_ms is None:
                self._first_data_time_ms = int(time.time() * 1000)
            symbol = msg["symbol"]
            bids = [tuple(x) for x in msg.get("bids", [])]
            asks = [tuple(x) for x in msg.get("asks", [])]
            exchange_ts = msg.get("exchange_ts", 0)
            for cb in self._tick_callbacks:
                try:
                    cb(symbol, bids, asks, exchange_ts)
                except Exception as exc:
                    logger.exception("feed_client_tick_callback_error: %s", exc)

        elif msg_type == "snapshot":
            if self._first_data_time_ms is None:
                self._first_data_time_ms = int(time.time() * 1000)
            symbol = msg["symbol"]
            bids = [tuple(x) for x in msg.get("bids", [])]
            asks = [tuple(x) for x in msg.get("asks", [])]
            exchange_ts = msg.get("exchange_ts", 0)
            for cb in self._snapshot_callbacks:
                try:
                    cb(symbol, bids, asks, exchange_ts)
                except Exception as exc:
                    logger.exception("feed_client_snapshot_callback_error: %s", exc)

        elif msg_type == "heartbeat":
            pass

        else:
            logger.debug("feed_client_unknown_msg_type: %s", msg_type)

    async def _reconnect(self) -> None:
        """Reconnect with exponential backoff, then resume via snapshot."""
        delay = RECONNECT_BASE_DELAY
        while self._running:
            logger.info("feed_client_reconnect_attempt delay=%.1fs", delay)
            await asyncio.sleep(delay)
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(
                    self.data_socket_path,
                )
                self._connected = True
                logger.info("feed_client_reconnected")
                break
            except (ConnectionError, OSError) as exc:
                logger.warning("feed_client_reconnect_failed: %s", exc)
                delay = min(delay * 2, RECONNECT_MAX_DELAY)

        if self._connected:
            for sym in self.symbols:
                try:
                    snap = await self.request_snapshot(sym)
                    if "error" not in snap:
                        bids = [tuple(x) for x in snap.get("bids", [])]
                        asks = [tuple(x) for x in snap.get("asks", [])]
                        exchange_ts = snap.get("exchange_ts", 0)
                        for cb in self._snapshot_callbacks:
                            try:
                                cb(sym, bids, asks, exchange_ts)
                            except Exception as exc:
                                logger.exception("feed_client_resume_snapshot_error: %s", exc)
                except Exception as exc:
                    logger.warning("feed_client_resume_snapshot_failed sym=%s: %s", sym, exc)

    def _close_writer(self) -> None:
        self._connected = False
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
        self._reader = None
