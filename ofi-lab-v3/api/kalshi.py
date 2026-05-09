"""
Kalshi exchange connector — REST + WebSocket.

Auth: RSA private-key signing (PKCS1v15 / SHA-256) per Kalshi v2 API.
Headers required on every request:
    KALSHI-ACCESS-KEY        — API key UUID from dashboard
    KALSHI-ACCESS-SIGNATURE  — base64(sign(timestamp + METHOD + PATH))
    KALSHI-ACCESS-TIMESTAMP  — unix milliseconds

This module does NOT touch model code, features, or stake math. It is a
thin connector returning typed dicts; gating and EV decisions live in
execution/kalshi_live_trader.py and trading/paper_trader.py.

Demo vs prod is selected by env var KALSHI_ENV (demo | prod).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional
from uuid import uuid4

if TYPE_CHECKING:
    import aiohttp
else:
    try:
        import aiohttp
    except ImportError:
        aiohttp = None  # type: ignore

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    _CRYPTO_AVAILABLE = True
except ImportError:
    serialization = None  # type: ignore
    hashes = None  # type: ignore
    padding = None  # type: ignore
    rsa = None  # type: ignore
    _CRYPTO_AVAILABLE = False

try:
    import websockets
    _WS_AVAILABLE = True
except ImportError:
    websockets = None  # type: ignore
    _WS_AVAILABLE = False

logger = logging.getLogger("kalshi")

# ─── Endpoints ───────────────────────────────────────────────────────
REST_DEMO = "https://demo-api.kalshi.co/trade-api/v2"
REST_PROD = "https://api.elections.kalshi.com/trade-api/v2"
WS_DEMO = "wss://demo-api.kalshi.co/trade-api/ws/v2"
WS_PROD = "wss://api.elections.kalshi.com/trade-api/ws/v2"
WS_PATH_FOR_SIG = "/trade-api/ws/v2"  # path component for signature

# Markets / portfolio path components
PATH_MARKETS = "/markets"
PATH_BALANCE = "/portfolio/balance"
PATH_POSITIONS = "/portfolio/positions"
PATH_FILLS = "/portfolio/fills"
PATH_ORDERS = "/portfolio/orders"


# ─── Auth ────────────────────────────────────────────────────────────
class KalshiAuth:
    """
    Loads an RSA private key from disk and produces signed headers
    for REST and WebSocket requests.
    """

    def __init__(
        self,
        api_key_id: Optional[str] = None,
        private_key_path: Optional[str] = None,
    ):
        if not _CRYPTO_AVAILABLE:
            raise RuntimeError(
                "cryptography package required for Kalshi auth — pip install cryptography"
            )
        self.api_key_id = api_key_id or os.environ.get("KALSHI_API_KEY_ID", "")
        if not self.api_key_id:
            raise RuntimeError("KALSHI_API_KEY_ID missing")

        path = private_key_path or os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
        if not path or not os.path.exists(path):
            raise RuntimeError(f"KALSHI_PRIVATE_KEY_PATH missing or file not found: {path!r}")
        with open(path, "rb") as f:
            self._key = serialization.load_pem_private_key(f.read(), password=None)  # type: ignore[union-attr]
        if not isinstance(self._key, rsa.RSAPrivateKey):  # type: ignore[union-attr]
            raise RuntimeError("Kalshi private key must be RSA")

    def sign_headers(self, method: str, path_for_sig: str) -> dict[str, str]:
        """
        Build signed headers for a request.

        path_for_sig must be the path including the API prefix that Kalshi
        signs (e.g., '/trade-api/v2/markets' for REST, '/trade-api/ws/v2' for WS).

        Algorithm (verified empirically against demo-api.kalshi.co 2026-05-02):
          RSA-PSS with MGF1-SHA256, salt_length = DIGEST_LENGTH (32 for SHA256),
          digest = SHA256.

        Earlier blueprint specified PKCS1v15 — that returns
        401 INCORRECT_API_KEY_SIGNATURE. Do not change.
        """
        ts = str(int(time.time() * 1000))
        payload = (ts + method.upper() + path_for_sig).encode()
        pss = padding.PSS(  # type: ignore[union-attr]
            mgf=padding.MGF1(hashes.SHA256()),  # type: ignore[union-attr]
            salt_length=padding.PSS.DIGEST_LENGTH,  # type: ignore[union-attr]
        )
        sig = self._key.sign(payload, pss, hashes.SHA256())  # type: ignore[union-attr,call-arg]
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }


# ─── REST client ─────────────────────────────────────────────────────
class KalshiRestClient:
    """Async REST client. Demo or prod selected at construction."""

    def __init__(
        self,
        auth: KalshiAuth,
        env: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None,
    ):
        self.env = (env or os.environ.get("KALSHI_ENV", "demo")).lower()
        if self.env not in ("demo", "prod"):
            raise RuntimeError(f"KALSHI_ENV must be 'demo' or 'prod', got {self.env!r}")
        self.base = REST_PROD if self.env == "prod" else REST_DEMO
        self._auth = auth
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self):
        if self._owns_session:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc):
        if self._owns_session and self._session is not None:
            await self._session.close()

    def _path_for_sig(self, path: str) -> str:
        # Kalshi signs the FULL path including /trade-api/v2 prefix.
        prefix = "/trade-api/v2"
        return f"{prefix}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> dict:
        url = f"{self.base}{path}"
        headers = self._auth.sign_headers(method, self._path_for_sig(path))
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        assert self._session is not None
        async with self._session.request(
            method, url, headers=headers, params=params,
            json=json_body, timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise KalshiAPIError(resp.status, text)
            try:
                return json.loads(text) if text else {}
            except json.JSONDecodeError as e:
                raise KalshiAPIError(resp.status, f"non-JSON response: {text[:200]}") from e

    # ─── Market data ────────────────────────────────────────────────
    async def get_active_tickers(
        self,
        series_ticker: str = "KXBTC15M",
    ) -> list[dict]:
        """
        Fetch open markets for the given series. Returns raw market dicts so
        callers can inspect close_time for timezone verification.
        """
        resp = await self._request(
            "GET", PATH_MARKETS,
            params={"series_ticker": series_ticker, "status": "open"},
        )
        return resp.get("markets", [])

    async def get_orderbook(self, ticker: str) -> dict:
        """Returns orderbook dict with 'yes' and 'no' levels.

        Handles both legacy format (key 'orderbook', cent integers) and
        current V2 format (key 'orderbook_fp', dollar strings).
        Normalises to {yes: [[price_float, size_float], ...], no: [...]}.
        """
        resp = await self._request("GET", f"{PATH_MARKETS}/{ticker}/orderbook")
        # Current V2 API returns orderbook_fp with yes_dollars / no_dollars
        ob_fp = resp.get("orderbook_fp")
        if ob_fp:
            return {
                "yes": [[float(p), float(s)] for p, s in (ob_fp.get("yes_dollars") or [])],
                "no":  [[float(p), float(s)] for p, s in (ob_fp.get("no_dollars") or [])],
            }
        # Legacy fallback (cent integers)
        ob = resp.get("orderbook", {})
        if ob:
            return {
                "yes": [[p / 100.0, float(s)] for p, s in (ob.get("yes") or [])],
                "no":  [[p / 100.0, float(s)] for p, s in (ob.get("no") or [])],
            }
        return {}

    # ─── Portfolio ──────────────────────────────────────────────────
    async def get_balance(self) -> dict:
        """Returns {balance: <int cents>, ...}. balance is in CENTS per Kalshi convention."""
        return await self._request("GET", PATH_BALANCE)

    async def get_positions(self) -> dict:
        return await self._request("GET", PATH_POSITIONS)

    async def get_fills(self, *, ticker: Optional[str] = None, limit: int = 100) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        return await self._request("GET", PATH_FILLS, params=params)

    # ─── Orders ─────────────────────────────────────────────────────
    async def place_order(
        self,
        ticker: str,
        side: str,                # "yes" | "no"
        yes_price: float,         # YES-side price in [0.01, 0.99]
        contracts: int,
        order_type: str = "limit",  # "limit" | "market"
        action: str = "buy",        # "buy" | "sell"
        client_order_id: Optional[str] = None,
    ) -> dict:
        """
        Place an order. Returns Kalshi's order response (includes order_id).

        yes_price is a float in [0.01, 0.99]. Sent as a 4-decimal-place
        dollar string via 'yes_price_dollars' for V2 API compatibility.
        """
        side = side.lower()
        if side not in ("yes", "no"):
            raise ValueError("side must be 'yes' or 'no'")
        if not (0.005 <= yes_price <= 0.995):
            raise ValueError(f"yes_price must be ~0.01-0.99, got {yes_price}")
        if contracts < 1:
            raise ValueError("contracts must be >= 1")

        payload = {
            "ticker": ticker,
            "client_order_id": client_order_id or str(uuid4()),
            "type": order_type,
            "action": action,
            "side": side,
            "count": int(contracts),
            "yes_price": int(round(yes_price * 100)),
        }
        return await self._request("POST", PATH_ORDERS, json_body=payload)

    async def cancel_order(self, order_id: str) -> dict:
        return await self._request("DELETE", f"{PATH_ORDERS}/{order_id}")

    async def get_order(self, order_id: str) -> dict:
        """Get order status including fill count."""
        return await self._request("GET", f"{PATH_ORDERS}/{order_id}")


class KalshiAPIError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Kalshi API {status}: {body}")
        self.status = status
        self.body = body


# ─── Timezone diagnostic ─────────────────────────────────────────────
async def tz_diagnostic(
    rest: KalshiRestClient,
    series_ticker: str = "KXBTC15M",
    n: int = 5,
) -> list[dict]:
    """
    Fetch a few open markets and log close_time in UTC, ET, and local TZ.

    Resolves the open question of whether 15m contract boundaries align
    with our model's UTC-aligned prediction boundaries (see
    LIVE_TRADING_PLAN.md §11). Run this once at startup before enabling
    live trades.
    """
    markets = await rest.get_active_tickers(series_ticker=series_ticker)
    out = []
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
    except ImportError:
        et = None  # type: ignore

    for m in markets[:n]:
        ticker = m.get("ticker")
        close_iso = m.get("close_time")
        row: dict[str, Any] = {"ticker": ticker, "close_time_raw": close_iso}
        if close_iso:
            try:
                # Kalshi returns ISO-8601 with timezone (typically UTC, suffix Z).
                dt_utc = datetime.fromisoformat(close_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
                row["close_utc"] = dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
                if et is not None:
                    row["close_et"] = dt_utc.astimezone(et).strftime("%Y-%m-%d %H:%M:%S ET")
                row["close_unix"] = int(dt_utc.timestamp())
                row["minute_utc"] = dt_utc.minute
                row["aligns_15m_utc"] = dt_utc.minute % 15 == 0
            except Exception as e:
                row["parse_error"] = str(e)
        out.append(row)
        logger.info(
            "TZ DIAG  ticker=%s  close_utc=%s  minute_utc=%s  aligns_15m_utc=%s",
            row.get("ticker"), row.get("close_utc"),
            row.get("minute_utc"), row.get("aligns_15m_utc"),
        )
    return out


# ─── WebSocket streamer ──────────────────────────────────────────────
class KalshiWebSocket:
    """
    Resilient WS streamer with exponential backoff and 15-minute rollover.

    Subscribes to a set of market tickers; the on_message callback receives
    decoded JSON messages (orderbook_snapshot, orderbook_delta, ticker, trade).

    Rollover: ~30s before each contract's close_time, fetches the next
    active ticker via REST and re-subscribes. The set of active tickers is
    maintained in self._active_tickers.
    """

    def __init__(
        self,
        auth: KalshiAuth,
        rest: KalshiRestClient,
        on_message: Callable[[dict], Awaitable[None]],
        env: Optional[str] = None,
        series_ticker: str = "KXBTC15M",
        channels: Optional[list[str]] = None,
    ):
        if not _WS_AVAILABLE:
            raise RuntimeError("websockets package required for Kalshi WS")
        self._auth = auth
        self._rest = rest
        self._on_message = on_message
        env = (env or os.environ.get("KALSHI_ENV", "demo")).lower()
        self._url = WS_PROD if env == "prod" else WS_DEMO
        self._series = series_ticker
        self._channels = channels or ["orderbook_delta", "ticker", "trade"]
        self._active_tickers: set[str] = set()
        self._next_msg_id = 1
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Run forever with exponential backoff on disconnect."""
        delay = 1.0
        while not self._stop.is_set():
            try:
                await self._run_once()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("WS disconnected (%s). Retry in %.1fs", e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def _run_once(self) -> None:
        headers = self._auth.sign_headers("GET", WS_PATH_FOR_SIG)
        async with websockets.connect(self._url, additional_headers=headers) as ws:
            await self._refresh_subscriptions(ws)
            rollover_task = asyncio.create_task(self._rollover_loop(ws))
            try:
                async for raw in ws:
                    if self._stop.is_set():
                        break
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    await self._on_message(msg)
            finally:
                rollover_task.cancel()

    async def _refresh_subscriptions(self, ws) -> None:
        markets = await self._rest.get_active_tickers(series_ticker=self._series)
        new_tickers = {m["ticker"] for m in markets if m.get("ticker")}
        to_add = new_tickers - self._active_tickers
        to_remove = self._active_tickers - new_tickers
        if to_add:
            await self._send(ws, "subscribe", {
                "channels": self._channels,
                "market_tickers": sorted(to_add),
            })
            logger.info("WS subscribed: %s", sorted(to_add))
        if to_remove:
            await self._send(ws, "unsubscribe", {
                "market_tickers": sorted(to_remove),
            })
            logger.info("WS unsubscribed: %s", sorted(to_remove))
        self._active_tickers = new_tickers

    async def _send(self, ws, cmd: str, params: dict) -> None:
        msg = {"id": self._next_msg_id, "cmd": cmd, "params": params}
        self._next_msg_id += 1
        await ws.send(json.dumps(msg))

    async def _rollover_loop(self, ws) -> None:
        """Every 60s, refresh subscriptions to catch newly-listed contracts
        and drop expired ones. Run forever until cancelled."""
        try:
            while not self._stop.is_set():
                await asyncio.sleep(60)
                try:
                    await self._refresh_subscriptions(ws)
                except Exception as e:
                    logger.warning("rollover refresh failed: %s", e)
        except asyncio.CancelledError:
            return


def price_to_float(price: float) -> float:
    """Clamp a fractional price to [0.01, 0.99]."""
    return max(0.01, min(0.99, round(price, 4)))


def cents_to_price(cents: int) -> float:
    """Kalshi quotes prices in integer cents (1-99). Convert to [0.01, 0.99]."""
    return cents / 100.0


def price_to_cents(price: float) -> int:
    """Round a fractional price to nearest cent, clamped to [1, 99]."""
    cents = int(round(price * 100))
    return max(1, min(99, cents))
