"""Kalshi live order dispatch path extracted from PaperTrader.

Owns the small set of Kalshi-specific methods: eligibility check, ticker
resolution against the boundary, mid-price math, and the async dispatch
to KalshiLiveTrader. These have a real-money blast radius — pure relocate,
no behavior change, careful preservation of retry timings and side effects.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

logger = logging.getLogger("kalshi_dispatcher")

# Hoist magic numbers to named constants (light touch — values match originals)
KALSHI_ROLLOVER_ATTEMPTS = 16      # 16 × 4 sec = 64 sec total window for rollover
KALSHI_ROLLOVER_POLL_SEC = 4       # poll interval for new-contract appearance
KALSHI_BOOK_RETRY_ATTEMPTS = 5     # retries for empty-orderbook recovery
KALSHI_BOOK_RETRY_POLL_SEC = 2     # poll interval for empty-orderbook retry


class KalshiDispatcher:
    """Kalshi-specific dispatch helpers extracted from PaperTrader.

    Rather than copying the live-trader references at construction time,
    KalshiDispatcher holds a back-reference to the owning PaperTrader instance
    and reads its attributes (_kalshi_trader, _db_conn, _model_meta) at call
    time. This ensures that scripts which rebind trader._kalshi_trader
    (e.g. test harnesses, reconnect logic) are automatically reflected without
    any extra plumbing.

    Parameters
    ----------
    trader:
        The PaperTrader instance that owns this dispatcher.
    """

    def __init__(self, *, trader: object) -> None:
        self._trader = trader

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def dispatch_live(
        self,
        *,
        symbol: str,
        duration_sec: int,
        boundary_ms: int,
        pred_proba: float,
        pred_direction: str,
        paper_stake_usd: float,
        features,
        model_name: str,
    ) -> None:
        """Dispatch a finalized signal to Kalshi for live execution.

        Called only on 15-minute boundaries (enforced at the call site) so
        the model's 900s prediction window aligns exactly with the Kalshi
        15M contract window.

        All gating (kill switch, allow-list, confidence, sizing) lives in
        KalshiLiveTrader. This method only:
          1. Fetches the currently open KXBTC15M market.
          2. Fetches the orderbook midpoint for the YES outcome.
          3. Maps pred_direction → Kalshi side (up→yes, down→no).
          4. Awaits maybe_place_order, which records to the live ledger
             regardless of whether an order is placed.
          5. On ZERO_CONTRACTS, retries up to 5× with 2s delays to let
             market makers post quotes after the dead zone clears.

        Errors are caught and logged so paper trading is never disrupted.
        """
        t = self._trader
        if t._kalshi_trader is None or t._kalshi_trader._rest is None:
            return
        try:
            # Refresh calibration map if it changed on disk
            t._kalshi_trader._calibrator.reload()

            ticker = await self.resolve_ticker_for_boundary(
                symbol=symbol,
                duration_sec=duration_sec,
                boundary_ms=boundary_ms,
            )
            if ticker is None:
                logger.info(
                    "kalshi dispatch: no market matches boundary close_unix=%d",
                    boundary_ms // 1000 + duration_sec,
                )
                return

            side = "yes" if pred_direction == "up" else "no"
            max_attempts = KALSHI_BOOK_RETRY_ATTEMPTS
            for attempt in range(max_attempts):
                ob = await t._kalshi_trader._rest.get_orderbook(ticker)
                yes_levels = ob.get("yes") or []
                no_levels = ob.get("no") or []
                yes_mid = self.midpoint_from_levels(yes_levels, no_levels)
                if yes_mid is None:
                    if attempt < max_attempts - 1:
                        logger.info("kalshi dispatch: empty book for %s, retry %d/%d",
                                    ticker, attempt + 1, max_attempts)
                        await asyncio.sleep(KALSHI_BOOK_RETRY_POLL_SEC)
                        continue
                    logger.info("kalshi dispatch: empty book for %s after %d attempts",
                                ticker, max_attempts)
                    return
                # Defense: reject extreme-priced markets
                if yes_mid < 0.05 or yes_mid > 0.95:
                    logger.info(
                        "kalshi dispatch: skipping extreme price yes_mid=%.4f on %s",
                        yes_mid, ticker,
                    )
                    return

                logger.info(
                    "kalshi dispatch: %s %s on %s (conf=%.4f, yes_mid=%.2f, attempt=%d)",
                    side.upper(), pred_direction, ticker, pred_proba, yes_mid, attempt + 1,
                )
                result = await t._kalshi_trader.maybe_place_order(
                    symbol=symbol,
                    duration_sec=duration_sec,
                    boundary_ts=boundary_ms // 1000,
                    model_p=pred_proba,
                    side=side,
                    ticker=ticker,
                    market_yes_price=yes_mid,
                    paper_stake_usd=paper_stake_usd,
                    features=features,
                    model_name=model_name,
                    pred_direction=pred_direction,
                )

                # If ZERO_CONTRACTS, the market price may shift — retry
                if result.reason == "GATED_ZERO_CONTRACTS" and attempt < max_attempts - 1:
                    logger.info(
                        "kalshi dispatch: ZERO_CONTRACTS on %s, retrying in 2s (%d/%d)",
                        ticker, attempt + 1, max_attempts,
                    )
                    await asyncio.sleep(KALSHI_BOOK_RETRY_POLL_SEC)
                    continue

                # Any other result (PLACED, GATED_*, ERROR) — stop retrying
                break

        except Exception as e:
            logger.warning("kalshi dispatch error (paper continues): %s", e)

    async def resolve_ticker_for_boundary(
        self,
        *,
        symbol: str,
        duration_sec: int,
        boundary_ms: int,
    ) -> str | None:
        """Return the Kalshi ticker whose close_time matches the contract that
        spans (boundary_ms, boundary_ms + duration_sec].

        Kalshi rollover quirk (empirically measured 2026-05-03 23:14-23:15 UTC):
          - At boundary T, the OLD contract (close=T) stays in the active list
            for ~39 seconds AFTER T.
          - The NEW contract (close=T+market_window_seconds) is NOT in the active list during
            that window — neither active nor reachable via no-status-filter
            queries.
          - At ~T+39s, Kalshi flips: OLD disappears, NEW appears as active.

        So we retry every 4 seconds for up to 60 seconds, looking for an
        exact close_time match against (boundary_ts + market_window_seconds). Once found we
        return the ticker and trading still has ~14 min remaining.
        """
        t = self._trader
        if t._kalshi_trader is None or t._kalshi_trader._rest is None:
            return None
        target_close_unix = boundary_ms // 1000 + duration_sec

        from datetime import datetime
        max_attempts = KALSHI_ROLLOVER_ATTEMPTS  # 16 × 4 sec = 64 sec total window
        for attempt in range(max_attempts):
            try:
                markets = await t._kalshi_trader._rest.get_active_tickers(
                    series_ticker=t._kalshi_trader.config.series_ticker,
                )
            except Exception as e:
                logger.warning("kalshi ticker discovery failed (attempt %d): %s", attempt, e)
                await asyncio.sleep(KALSHI_ROLLOVER_POLL_SEC)
                continue

            for m in markets or []:
                close_iso = m.get("close_time")
                if not close_iso:
                    continue
                try:
                    close_unix = int(datetime.fromisoformat(
                        close_iso.replace("Z", "+00:00")
                    ).timestamp())
                except (ValueError, TypeError):
                    continue
                if close_unix == target_close_unix:
                    if attempt > 0:
                        logger.info(
                            "kalshi resolver: matched %s after %d retries (~%ds)",
                            m.get("ticker"), attempt, attempt * KALSHI_ROLLOVER_POLL_SEC,
                        )
                    return m.get("ticker")

            # No match yet — log the gap once and keep retrying
            if attempt == 0:
                observed = [m.get("ticker") for m in (markets or [])[:3]]
                logger.info(
                    "kalshi resolver: waiting for rollover, target_close=%s, observed=%s",
                    datetime.fromtimestamp(target_close_unix).isoformat(),
                    observed,
                )
            await asyncio.sleep(KALSHI_ROLLOVER_POLL_SEC)

        logger.warning(
            "kalshi resolver: gave up after %ds, no market with close_unix=%d",
            max_attempts * KALSHI_ROLLOVER_POLL_SEC, target_close_unix,
        )
        return None

    @staticmethod
    def midpoint_from_levels(yes_levels: list, no_levels: list) -> float | None:
        """Compute YES-side midpoint from Kalshi orderbook levels.

        Levels are normalised by kalshi.py to [[price_float, size_float], ...].
        Prices are in [0, 1] (dollar fraction).

        YES best bid  = max price across yes_levels with size > 0
        YES best ask  = 1.0 - max(no_levels price) (Kalshi reciprocal)
        Midpoint      = (yes_bid + yes_ask) / 2
        """
        yes_bid = max((lvl[0] for lvl in yes_levels if lvl[1] > 0), default=None)
        no_bid = max((lvl[0] for lvl in no_levels if lvl[1] > 0), default=None)
        if yes_bid is None and no_bid is None:
            return None
        if yes_bid is None:
            assert no_bid is not None
            return 1.0 - no_bid
        if no_bid is None:
            return yes_bid
        yes_ask = 1.0 - no_bid
        return (yes_bid + yes_ask) / 2.0

    def is_eligible(
        self,
        *,
        model_name: str,
        symbol: str,
        market_window_seconds: int,
    ) -> bool:
        """Registry-driven Kalshi gate.

        Checks self._trader._model_meta (kalshi_dispatch_enabled, built from
        model_registry + config fallback) and model_registry.platform_active_json.

        Phase 57: tier gate uses per-window tier_by_window dict. Falls back to
        legacy model-level scalar when tier_by_window is not populated.
        """
        t = self._trader
        meta = t._model_meta.get(model_name)
        if meta is None:
            return False
        if not meta.get("kalshi_dispatch_enabled", False):
            return False
        if symbol != meta["symbol"]:
            return False
        if market_window_seconds != meta["training_horizon_seconds"]:
            return False
        # Phase 57: use per-window tier when available; fallback to legacy model-level tier
        tier_by_window = meta.get("tier_by_window")
        if tier_by_window is not None:
            window_tier = tier_by_window.get(market_window_seconds)
            if window_tier is None:
                # Missing row for this window → gate off by default
                import logging as _logging
                _logging.getLogger("dashboard.kalshi").info(
                    "live_dispatch_skipped no_window_tier model=%s window=%s",
                    model_name, market_window_seconds
                )
                return False
            model_tier = window_tier
        else:
            # Phase 5 legacy fallback
            model_tier = meta.get("tier", "watch")

        if model_tier != "gold":
            import logging as _logging
            _logging.getLogger("dashboard.kalshi").info(
                "live_dispatch_skipped tier=%s model=%s window=%s",
                model_tier, model_name, market_window_seconds
            )
            return False
        row = t._db_conn.execute(
            "SELECT platform_active_json FROM model_registry WHERE name=?",
            (model_name,),
        ).fetchone()
        if row and row["platform_active_json"]:
            import json as _json
            pa = _json.loads(row["platform_active_json"])
            if not pa.get("kalshi", False):
                return False
        return True
