#!/usr/bin/env python3
"""WebSocket feed service.

Connects to Bybit spot WS, maintains local order books, and streams
1-second canonical book state over a Unix domain socket.  A separate
control socket supports health checks and full-book snapshot requests.

This isolates the WS connection from the paper trader's synchronous
prediction loop, eliminating event-loop starvation that was causing
ping/pong timeouts and connection drops.

Usage:
    python -m api.ws_feed_service
    python -m api.ws_feed_service --testnet
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import time

from api.bybit import BybitOrderBookManager

logger = logging.getLogger(__name__)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
DOWNSAMPLE_MS = 1000

DEFAULT_DATA_SOCKET = "/tmp/v3-feed.sock"
DEFAULT_CONTROL_SOCKET = "/tmp/v3-feed-ctl.sock"


class FeedServer:
    def __init__(
        self,
        symbols: list[str],
        testnet: bool = False,
        data_socket_path: str = DEFAULT_DATA_SOCKET,
        control_socket_path: str = DEFAULT_CONTROL_SOCKET,
    ) -> None:
        self.symbols = [s.upper() for s in symbols]
        self.testnet = testnet
        self.data_socket_path = data_socket_path
        self.control_socket_path = control_socket_path

        self.book_manager = BybitOrderBookManager(
            symbols=self.symbols,
            levels=10,
            testnet=testnet,
        )
        self.book_manager.on_update(self._on_book_update)

        self._per_symbol_last_ts: dict[str, int] = {}
        self._data_clients: list[asyncio.StreamWriter] = []
        self._connected_at_ms: int = 0
        self._last_tick_ts: dict[str, int] = {}
        self._ws_connected: bool = False
        self._running = True
        self._data_server: asyncio.Server | None = None
        self._control_server: asyncio.Server | None = None

    # ── Book update callback (runs on the event loop) ──────────

    def _on_book_update(self, symbol: str, bids: list, asks: list) -> None:
        cts_ms = self.book_manager.books.get(symbol, {}).get("exchange_ts", 0)
        last = self._per_symbol_last_ts.get(symbol, 0)
        if cts_ms and (cts_ms - last) < DOWNSAMPLE_MS:
            return
        self._per_symbol_last_ts[symbol] = cts_ms
        self._last_tick_ts[symbol] = int(time.time() * 1000)

        msg = json.dumps(
            {
                "type": "tick",
                "symbol": symbol,
                "bids": bids,
                "asks": asks,
                "exchange_ts": cts_ms,
            },
            separators=(",", ":"),
        )

        self._broadcast(msg)

    def _broadcast(self, line: str) -> None:
        payload = (line + "\n").encode()
        dead: list[asyncio.StreamWriter] = []
        for writer in self._data_clients:
            try:
                writer.write(payload)
            except Exception:
                dead.append(writer)
        for w in dead:
            self._data_clients.discard(w)
            try:
                w.close()
            except Exception:
                pass

    # ── Data socket handler ────────────────────────────────────

    async def _handle_data_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername") or "unknown"
        logger.info("data_client_connected peer=%s", peer)
        self._data_clients.append(writer)

        try:
            for sym in self.symbols:
                snap = self.book_manager.get_snapshot(sym)
                book = self.book_manager.books.get(sym, {})
                cts_ms = book.get("exchange_ts", 0)
                if snap["bids"] or snap["asks"]:
                    msg = json.dumps(
                        {
                            "type": "snapshot",
                            "symbol": sym,
                            "bids": snap["bids"],
                            "asks": snap["asks"],
                            "exchange_ts": cts_ms,
                        },
                        separators=(",", ":"),
                    )
                    writer.write((msg + "\n").encode())
                    await writer.drain()
        except Exception:
            pass

        try:
            while self._running:
                line = await reader.readline()
                if not line:
                    break
        except Exception:
            pass
        finally:
            self._data_clients.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("data_client_disconnected peer=%s", peer)

    # ── Control socket handler ─────────────────────────────────

    async def _handle_control_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            buf = await asyncio.wait_for(reader.read(4096), timeout=30.0)
            if not buf:
                return
            req = json.loads(buf.decode().strip())
            cmd = req.get("cmd", "")

            if cmd == "health":
                resp = {
                    "status": "connected" if self._ws_connected else "disconnected",
                    "uptime_s": (int(time.time() * 1000) - self._connected_at_ms) // 1000,
                    "symbols": self.symbols,
                    "last_tick_ts": self._last_tick_ts,
                    "clients": len(self._data_clients),
                }
            elif cmd == "snapshot":
                sym = req.get("symbol", "").upper()
                snap = self.book_manager.get_snapshot(sym)
                book = self.book_manager.books.get(sym, {})
                resp = {
                    "symbol": sym,
                    "bids": snap["bids"],
                    "asks": snap["asks"],
                    "exchange_ts": book.get("exchange_ts", 0),
                    "synced": book.get("synced", False),
                }
            else:
                resp = {"error": f"unknown_command: {cmd}"}

            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
        except asyncio.TimeoutError:
            pass
        except Exception as exc:
            logger.debug("control_client_error: %s", exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # ── Heartbeat broadcaster ──────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        while self._running:
            await asyncio.sleep(5)
            hb = json.dumps(
                {"type": "heartbeat", "ts": int(time.time() * 1000)},
                separators=(",", ":"),
            )
            self._broadcast(hb)

    # ── Main entry ─────────────────────────────────────────────

    async def run(self) -> None:
        self._connected_at_ms = int(time.time() * 1000)

        for p in (self.data_socket_path, self.control_socket_path):
            if os.path.exists(p):
                os.unlink(p)

        self._data_server = await asyncio.start_unix_server(
            self._handle_data_client,
            path=self.data_socket_path,
        )
        os.chmod(self.data_socket_path, 0o666)
        logger.info("data_socket_listening path=%s", self.data_socket_path)

        self._control_server = await asyncio.start_unix_server(
            self._handle_control_client,
            path=self.control_socket_path,
        )
        os.chmod(self.control_socket_path, 0o666)
        logger.info("control_socket_listening path=%s", self.control_socket_path)

        logger.info("starting_bybit_ws symbols=%s testnet=%s", self.symbols, self.testnet)
        try:
            self._ws_connected = True
            await asyncio.gather(
                self.book_manager.connect_async(),
                self._heartbeat_loop(),
            )
        except Exception as exc:
            logger.error("ws_fatal_error: %s", exc, exc_info=True)
            raise
        finally:
            self._running = False
            self._ws_connected = False
            if self._data_server:
                self._data_server.close()
            if self._control_server:
                self._control_server.close()
            for p in (self.data_socket_path, self.control_socket_path):
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except Exception:
                        pass
            logger.info("feed_server_stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="v3 WS feed service")
    parser.add_argument("--testnet", action="store_true", default=False)
    parser.add_argument("--data-socket", default=DEFAULT_DATA_SOCKET)
    parser.add_argument("--control-socket", default=DEFAULT_CONTROL_SOCKET)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    server = FeedServer(
        symbols=SYMBOLS,
        testnet=args.testnet,
        data_socket_path=args.data_socket,
        control_socket_path=args.control_socket,
    )

    def handle_signal(sig, frame):
        logger.info("signal_received sig=%s shutting_down", sig)
        server._running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    asyncio.run(server.run())


if __name__ == "__main__":
    main()
