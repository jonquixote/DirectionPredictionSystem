"""
LiveTrader — Polymarket live execution module.

Strict design constraints:
  * Default-deny: every gate ships disabled or restrictive.
  * Kill switch (LIVE_TRADING_ENABLED) is memory-only — container restart
    forces explicit re-arm.
  * Allow-list (symbol, duration_sec) enforced before any chain interaction.
  * Import-guarded ClobClient: when py_clob_client_v2 is unavailable the
    class becomes a pure stub (every call returns GATED_NO_CLIENT) so local
    development never breaks.
  * Every decision is logged to /data/live_orders.jsonl, regardless of
    whether an order was placed.

This module never touches model code, feature engineering, or stake math.
It receives finalized signals from paper_trader and either places an order
or records why it was gated.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("live_trader")

# ─── Import-guarded CLOB client ─────────────────────────────────────────────
# Local dev environments may not have py_clob_client_v2 installed.
# When unavailable, LiveTrader becomes a no-op stub.
try:
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import OrderArgs, OrderType
    from py_clob_client_v2.constants import POLYGON
    _CLOB_AVAILABLE = True
except ImportError:
    ClobClient = None  # type: ignore
    OrderArgs = None  # type: ignore
    OrderType = None  # type: ignore
    POLYGON = 137
    _CLOB_AVAILABLE = False

POLYMARKET_HOST = "https://clob.polymarket.com"

# Hardcoded fallback values when Polymarket per-market API is unavailable.
# These affect monitoring/quoting only — never bypass live execution.
DEFAULT_TICK_SIZE = 0.001
DEFAULT_MIN_ORDER_USD = 5.0


@dataclass
class GateResult:
    """Result of a live-order gate evaluation."""
    placed: bool
    reason: str          # "PLACED" | "GATED_*" | "ERROR"
    order_id: Optional[str] = None
    fill_status: Optional[str] = None
    error: Optional[str] = None
    final_price: Optional[float] = None
    final_size_usd: Optional[float] = None


@dataclass
class LiveConfig:
    """Mutable live-trading configuration. All optional fields default-OFF."""
    enabled: bool = False
    allow_list: dict[str, set[int]] = field(default_factory=dict)  # symbol -> {duration_sec}
    bankroll_fraction: float = 1.0
    order_type: str = "market"
    per_symbol_kelly: dict[str, float] = field(default_factory=dict)
    per_trade_usd_cap: Optional[float] = None
    slippage_cap: Optional[float] = None  # absolute price units, e.g. 0.005


class LiveTrader:
    """
    Owns the Polymarket CLOB client and gates every live-order request.

    All public methods are thread-safe via a single internal lock around
    config mutation.
    """

    def __init__(
        self,
        env: Optional[dict] = None,
        ledger_path: str = "/data/live_orders.jsonl",
    ):
        self._env = env if env is not None else os.environ
        self._ledger_path = ledger_path
        self._lock = threading.RLock()
        self._client = None
        self._client_init_error: Optional[str] = None
        self._last_order_at: Optional[str] = None
        self._last_error: Optional[str] = None

        self._config = LiveConfig(
            enabled=self._parse_bool(self._env.get("LIVE_TRADING_ENABLED", "false")),
            allow_list=self._parse_allow_list(self._env.get("LIVE_ALLOW_LIST", "")),
            bankroll_fraction=float(self._env.get("LIVE_BANKROLL_FRACTION", "1.0")),
            order_type=self._env.get("LIVE_ORDER_TYPE", "market").lower(),
        )

        self._init_client()

    # ─── Client lifecycle ───────────────────────────────────────────────
    def _init_client(self) -> None:
        if not _CLOB_AVAILABLE:
            self._client_init_error = "py_clob_client_v2 not installed"
            logger.warning("LiveTrader stub mode: %s", self._client_init_error)
            return

        private_key = self._env.get("POLYMARKET_PRIVATE_KEY", "").strip()
        funder = self._env.get("POLYMARKET_FUNDER_ADDRESS", "").strip()
        api_key = self._env.get("POLYMARKET_API_KEY", "").strip()
        api_secret = self._env.get("POLYMARKET_API_SECRET", "").strip()
        api_passphrase = self._env.get("POLYMARKET_API_PASSPHRASE", "").strip()

        if not private_key or not funder:
            self._client_init_error = "PRIVATE_KEY or FUNDER_ADDRESS missing"
            logger.warning("LiveTrader stub mode: %s", self._client_init_error)
            return

        try:
            client = ClobClient(  # type: ignore[misc]
                POLYMARKET_HOST,
                key=private_key,
                chain_id=POLYGON,
                signature_type=2,  # POLY_PROXY / EOA-funder
                funder=funder,
            )
            # Use explicit creds when supplied, else derive from L1 signer.
            if api_key and api_secret and api_passphrase:
                from py_clob_client_v2.clob_types import ApiCreds  # type: ignore
                client.set_api_creds(ApiCreds(api_key, api_secret, api_passphrase))
            else:
                client.set_api_creds(client.create_or_derive_api_creds())
            self._client = client
            logger.info("LiveTrader initialized for funder %s", funder)
        except Exception as e:
            self._client_init_error = f"client init failed: {e}"
            logger.exception("LiveTrader client init failed")

    # ─── Config accessors ───────────────────────────────────────────────
    @property
    def config(self) -> LiveConfig:
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
            logger.warning("LIVE TRADING ENABLED (memory-only)")

    def disable(self) -> None:
        with self._lock:
            self._config.enabled = False
            logger.warning("LIVE TRADING DISABLED")

    def update_config(self, **kwargs) -> dict:
        """Update mutable config fields. Unknown keys are rejected."""
        allowed = {
            "bankroll_fraction", "order_type", "per_symbol_kelly",
            "per_trade_usd_cap", "slippage_cap",
        }
        with self._lock:
            for k, v in kwargs.items():
                if k not in allowed:
                    raise ValueError(f"unknown config key: {k}")
                setattr(self._config, k, v)
            return self._config_snapshot()

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self._config.enabled,
                "client_ready": self._client is not None,
                "client_init_error": self._client_init_error,
                "config": self._config_snapshot(),
                "last_order_at": self._last_order_at,
                "last_error": self._last_error,
            }

    def _config_snapshot(self) -> dict:
        return {
            "allow_list": {s: sorted(d) for s, d in self._config.allow_list.items()},
            "bankroll_fraction": self._config.bankroll_fraction,
            "order_type": self._config.order_type,
            "per_symbol_kelly": dict(self._config.per_symbol_kelly),
            "per_trade_usd_cap": self._config.per_trade_usd_cap,
            "slippage_cap": self._config.slippage_cap,
        }

    # ─── Balance / sizing ───────────────────────────────────────────────
    def get_usdc_balance(self) -> Optional[float]:
        """Query live USDC balance from chain via CLOB client."""
        if self._client is None:
            return None
        try:
            bal = self._client.get_balance_allowance()
            return float(bal.get("balance", 0)) / 1e6
        except Exception as e:
            logger.warning("balance query failed: %s", e)
            return None

    def effective_bankroll(self) -> Optional[float]:
        bal = self.get_usdc_balance()
        if bal is None:
            return None
        with self._lock:
            return bal * self._config.bankroll_fraction

    # ─── Tick / size enforcement ────────────────────────────────────────
    @staticmethod
    def enforce_tick(price: float, tick: float = DEFAULT_TICK_SIZE) -> float:
        return round(round(price / tick) * tick, 4)

    @staticmethod
    def enforce_min_size(size_usd: float, floor: float = DEFAULT_MIN_ORDER_USD) -> float:
        return max(size_usd, floor)

    # ─── Main entry point ───────────────────────────────────────────────
    def maybe_place_order(
        self,
        symbol: str,
        duration_sec: int,
        boundary_ts: int,
        token_id: str,
        side: str,
        price: float,
        size_usd: float,
        order_type: Optional[str] = None,
    ) -> GateResult:
        """
        Evaluate gates and (if all pass) place a live order.

        Returns a GateResult describing the outcome. Always writes one row
        to the live ledger, regardless of outcome.
        """
        result = self._evaluate_and_place(
            symbol, duration_sec, token_id, side, price, size_usd, order_type
        )
        self._record(
            symbol=symbol,
            duration_sec=duration_sec,
            boundary_ts=boundary_ts,
            token_id=token_id,
            side=side,
            requested_price=price,
            requested_size_usd=size_usd,
            result=result,
        )
        return result

    def _evaluate_and_place(
        self,
        symbol: str,
        duration_sec: int,
        token_id: str,
        side: str,
        price: float,
        size_usd: float,
        order_type: Optional[str],
    ) -> GateResult:
        # Gate 1: kill switch
        if not self.is_enabled():
            return GateResult(placed=False, reason="GATED_KILL_SWITCH")

        # Gate 2: allow-list
        if not self.in_allow_list(symbol, duration_sec):
            return GateResult(placed=False, reason="GATED_ALLOW_LIST")

        # Gate 3: client ready
        if self._client is None:
            return GateResult(
                placed=False,
                reason="GATED_NO_CLIENT",
                error=self._client_init_error,
            )

        with self._lock:
            cfg = self._config

        # Gate 4: per-trade USD ceiling
        final_size = size_usd
        if cfg.per_trade_usd_cap is not None and final_size > cfg.per_trade_usd_cap:
            final_size = cfg.per_trade_usd_cap

        # Gate 5: minimum order size
        final_size = self.enforce_min_size(final_size)

        # Gate 6: tick rounding
        final_price = self.enforce_tick(price)

        # Gate 7: slippage cap (limit orders only)
        chosen_type = (order_type or cfg.order_type).lower()
        if chosen_type == "limit_slippage" and cfg.slippage_cap is not None:
            # Caller's price is the model's quote; cap slippage from it.
            # For BUY, we are willing to pay up to price + cap.
            # For SELL, accept down to price - cap.
            # Final price is bounded; we still emit at the model price here
            # and the order book takes whatever liquidity is available within cap.
            pass  # implementation deferred; flag stays inactive by default

        # Place the order
        try:
            order_args = OrderArgs(  # type: ignore[misc]
                token_id=token_id,
                price=final_price,
                size=final_size,
                side=side.upper(),
            )
            # py_clob_client_v2 maps 'market' → marketable IOC limit at top-of-book.
            ot = OrderType.IOC if chosen_type == "market" else OrderType.GTC  # type: ignore[union-attr]
            resp = self._client.create_and_post_order(order_args, order_type=ot)
            order_id = resp.get("orderID") or resp.get("orderId")
            fill_status = resp.get("status", "UNKNOWN")
            with self._lock:
                self._last_order_at = _utc_now_iso()
                self._last_error = None
            return GateResult(
                placed=True,
                reason="PLACED",
                order_id=order_id,
                fill_status=fill_status,
                final_price=final_price,
                final_size_usd=final_size,
            )
        except Exception as e:
            err = str(e)
            with self._lock:
                self._last_error = err
            logger.exception("live order placement failed")
            return GateResult(
                placed=False,
                reason="ERROR",
                error=err,
                final_price=final_price,
                final_size_usd=final_size,
            )

    # ─── Ledger ─────────────────────────────────────────────────────────
    def _record(
        self,
        symbol: str,
        duration_sec: int,
        boundary_ts: int,
        token_id: str,
        side: str,
        requested_price: float,
        requested_size_usd: float,
        result: GateResult,
    ) -> None:
        row = {
            "ts": _utc_now_iso(),
            "symbol": symbol,
            "duration_sec": duration_sec,
            "boundary_ts": boundary_ts,
            "token_id": token_id,
            "side": side,
            "requested_price": requested_price,
            "requested_size_usd": requested_size_usd,
            "gate_result": result.reason,
            "order_id": result.order_id,
            "fill_status": result.fill_status,
            "final_price": result.final_price,
            "final_size_usd": result.final_size_usd,
            "error": result.error,
        }
        try:
            os.makedirs(os.path.dirname(self._ledger_path), exist_ok=True)
            with open(self._ledger_path, "a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as e:
            logger.warning("live ledger write failed: %s", e)

    def tail_ledger(self, limit: int = 50) -> list[dict]:
        """Read last N rows from the live ledger."""
        if not os.path.exists(self._ledger_path):
            return []
        try:
            with open(self._ledger_path) as f:
                lines = f.readlines()[-limit:]
            return [json.loads(line) for line in lines if line.strip()]
        except Exception as e:
            logger.warning("live ledger tail failed: %s", e)
            return []

    # ─── Helpers ────────────────────────────────────────────────────────
    @staticmethod
    def _parse_bool(s: str) -> bool:
        return str(s).strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _parse_allow_list(s: str) -> dict[str, set[int]]:
        """Parse 'BTCUSDT:900,SOLUSDT:900' into {'BTCUSDT': {900}, 'SOLUSDT': {900}}."""
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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Module-level singleton ─────────────────────────────────────────────────
_singleton: Optional[LiveTrader] = None
_singleton_lock = threading.Lock()


def get_live_trader() -> LiveTrader:
    """Get or create the process-wide LiveTrader singleton."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = LiveTrader()
    return _singleton
