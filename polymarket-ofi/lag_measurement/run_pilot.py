#!/usr/bin/env python3
"""
Propagation Lag Measurement — Pilot Phase.
Spec v2.6, Section 3.2-3.3.

Uses Coinbase Exchange WebSocket for order book data (no geo-restriction).
Uses Polymarket Gamma API (tag_id=102467) for 15-minute crypto direction markets.

Collects 10 events per asset (40 total) to compute sigma_pilot and n_required.
Does NOT evaluate LAG_DECISION_RULES — that happens after the main collection.

Usage:
    python -m lag_measurement.run_pilot --bankroll USDC_AMOUNT
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import os
import sqlite3
import sys
import time

import aiohttp

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CONFIG
from feature_engineering.mlofi import MLOFICalculator
from lag_measurement.collector import LagMeasurementCollector
from lag_measurement.pilot_analysis import (
    classify_session,
    compute_pilot_statistics,
    check_stopping_rule,
    SESSIONS,
)
from api.polymarket import parse_book

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)-25s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pilot")

# ── Asset mapping ─────────────────────────────────────────────────────────

COINBASE_PRODUCTS = {
    "BTCUSDT": "BTC-USD",
    "ETHUSDT": "ETH-USD",
    "SOLUSDT": "SOL-USD",
    "XRPUSDT": "XRP-USD",
}
COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"

# Polymarket 15-minute crypto direction markets tag
POLYMARKET_15M_TAG_ID = "102467"

# Slug prefixes → config asset names
SLUG_TO_ASSET = {
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
    "xrp": "XRPUSDT",
}


# ── Polymarket token resolution ──────────────────────────────────────────

async def resolve_token_ids(
    session: aiohttp.ClientSession,
) -> dict[str, str]:
    """
    Resolve currently accepting 15-minute direction contract token IDs.
    Uses tag_id=102467 to find active markets.
    clobTokenIds is a JSON string in the Gamma API response.
    Markets rotate every 15 min — call this periodically.
    Returns {asset: token_id} for the "Up" (first) token.
    """
    token_map = {}
    url = "https://gamma-api.polymarket.com/events"
    try:
        params = {"tag_id": POLYMARKET_15M_TAG_ID, "active": "true", "closed": "false"}
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                logger.warning("Gamma API returned %d", resp.status)
                return token_map
            events = await resp.json()

        for ev in events:
            slug = (ev.get("slug") or "").lower()
            for prefix, asset in SLUG_TO_ASSET.items():
                if asset in token_map:
                    continue
                if slug.startswith(prefix) and "15m" in slug:
                    for market in ev.get("markets", []):
                        # acceptingOrders is camelCase in Gamma API
                        if not market.get("acceptingOrders"):
                            continue
                        tokens_raw = market.get("clobTokenIds", "[]")
                        # clobTokenIds is a JSON string, not a list
                        if isinstance(tokens_raw, str):
                            tokens = json.loads(tokens_raw)
                        else:
                            tokens = tokens_raw
                        if tokens:
                            token_map[asset] = tokens[0]
                            logger.info(
                                "Resolved %s → %s (%s)",
                                asset, tokens[0][:24] + "...",
                                market.get("question", "")[:50],
                            )
                            break

    except Exception as e:
        logger.error("Token resolution failed: %s", e)

    resolved = len(token_map)
    missing = [a for a in CONFIG["assets"] if a not in token_map]
    if missing:
        logger.info(
            "Tokens resolved: %d/%d (missing: %s — markets may not be open yet)",
            resolved, len(CONFIG["assets"]), ", ".join(missing),
        )
    else:
        logger.info("All %d tokens resolved", resolved)
    return token_map


# ── SQLite event storage ──────────────────────────────────────────────────

PILOT_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS pilot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset TEXT NOT NULL,
    t_ofi_signal_ms INTEGER NOT NULL,
    mlofi_normalised REAL NOT NULL,
    t_ask_depth_drop_ms INTEGER,
    t_ask_price_move_ms INTEGER,
    ask_depth_at_signal REAL,
    ask_depth_viable_threshold REAL,
    session_stratum TEXT NOT NULL,
    operational_lag_ms INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


class PilotDB:
    """Simple SQLite store for pilot events."""

    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(PILOT_DB_SCHEMA)

    def insert(self, event: dict) -> None:
        self.conn.execute(
            """INSERT INTO pilot_events
            (asset, t_ofi_signal_ms, mlofi_normalised,
             t_ask_depth_drop_ms, t_ask_price_move_ms,
             ask_depth_at_signal, ask_depth_viable_threshold,
             session_stratum, operational_lag_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event["asset"], event["t_ofi_signal_ms"], event["mlofi_normalised"],
                event.get("t_ask_depth_drop_ms"), event.get("t_ask_price_move_ms"),
                event.get("ask_depth_at_signal"), event.get("ask_depth_viable_threshold"),
                event["session_stratum"], event.get("operational_lag_ms"),
            ),
        )
        self.conn.commit()

    def count_by_asset(self) -> dict[str, int]:
        return dict(self.conn.execute(
            "SELECT asset, COUNT(*) FROM pilot_events GROUP BY asset"
        ).fetchall())

    def get_all_lags(self) -> list[float]:
        return [r[0] for r in self.conn.execute(
            "SELECT operational_lag_ms FROM pilot_events WHERE operational_lag_ms IS NOT NULL"
        ).fetchall()]

    def get_lags_by_session(self) -> dict[str, list[float]]:
        result: dict[str, list[float]] = {s: [] for s in SESSIONS}
        for stratum, lag in self.conn.execute(
            "SELECT session_stratum, operational_lag_ms FROM pilot_events "
            "WHERE operational_lag_ms IS NOT NULL"
        ).fetchall():
            if stratum in result:
                result[stratum].append(lag)
        return result

    def close(self):
        self.conn.close()


# ── Polymarket monitoring ─────────────────────────────────────────────────

async def monitor_polymarket_response(
    event: dict, session: aiohttp.ClientSession,
    token_id: str | None, monitor_window_s: int = 120,
) -> None:
    """
    Poll Polymarket /book at 1s resolution for up to 2 minutes.
    Detects depth drop and price move per Kavajecz ordering.
    """
    if not token_id:
        return

    viable_threshold = event.get("ask_depth_viable_threshold", 0)
    baseline_price = None

    for _ in range(monitor_window_s):
        try:
            async with session.get(
                "https://clob.polymarket.com/book", params={"token_id": token_id}
            ) as resp:
                if resp.status != 200:
                    await asyncio.sleep(1)
                    continue
                book_data = await resp.json()

            parsed = parse_book(book_data)
            if parsed is None:
                await asyncio.sleep(1)
                continue

            now_ms = int(time.time() * 1000)
            if baseline_price is None:
                baseline_price = parsed["best_ask_price"]
                event["ask_depth_at_signal"] = parsed["best_ask_size"]

            # Depth drop detection (PRIMARY — Kavajecz ordering)
            if (event["t_ask_depth_drop_ms"] is None and viable_threshold > 0
                    and parsed["best_ask_size"] < viable_threshold):
                event["t_ask_depth_drop_ms"] = now_ms
                lag_ms = now_ms - event["t_ofi_signal_ms"]
                logger.info(
                    "  ↳ DEPTH DROP %s: size=%.1f < threshold=%.1f at +%dms",
                    event["asset"], parsed["best_ask_size"], viable_threshold, lag_ms,
                )

            # Price move detection (SECONDARY)
            if (event["t_ask_price_move_ms"] is None and baseline_price > 0
                    and parsed["best_ask_price"] > baseline_price * 1.005):
                event["t_ask_price_move_ms"] = now_ms
                lag_ms = now_ms - event["t_ofi_signal_ms"]
                logger.info(
                    "  ↳ PRICE MOVE %s: ask=%.4f > baseline*1.005 at +%dms",
                    event["asset"], parsed["best_ask_price"], lag_ms,
                )

            if event["t_ask_depth_drop_ms"] is not None and event["t_ask_price_move_ms"] is not None:
                break
        except Exception as e:
            logger.warning("Polymarket poll error: %s", e)

        await asyncio.sleep(1)


# ── Coinbase WebSocket feed ───────────────────────────────────────────────

async def run_coinbase_feed(
    assets: list[str],
    collectors: dict[str, LagMeasurementCollector],
    mlofi_calcs: dict[str, MLOFICalculator],
    db: PilotDB,
    events_per_asset: int,
    event_counts: dict[str, int],
    http_session: aiohttp.ClientSession,
    cooldown_s: float = 120.0,
) -> None:
    """
    Single Coinbase WS connection for all assets.
    L2 snapshot on subscribe + incremental l2update messages.
    Refreshes Polymarket tokens every 15 minutes.
    """
    product_ids = [COINBASE_PRODUCTS[a] for a in assets]
    product_to_asset = {v: k for k, v in COINBASE_PRODUCTS.items()}

    books: dict[str, dict] = {}
    last_trigger_ms: dict[str, int] = {a: 0 for a in assets}

    # Resolve tokens (will be refreshed periodically)
    token_map = await resolve_token_ids(http_session)
    last_token_refresh = time.time()
    TOKEN_REFRESH_INTERVAL = 900  # 15 minutes

    while True:
        total = sum(event_counts.get(a, 0) for a in assets)
        if total >= events_per_asset * len(assets):
            return

        try:
            logger.info("Connecting to Coinbase WebSocket...")

            async with http_session.ws_connect(
                COINBASE_WS_URL, heartbeat=30, receive_timeout=60,
            ) as ws:
                await ws.send_json({
                    "type": "subscribe",
                    "product_ids": product_ids,
                    "channels": ["level2_batch"],
                })
                logger.info("Subscribed to level2_batch for %s", product_ids)

                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                        continue

                    data = json.loads(msg.data)
                    msg_type = data.get("type")
                    product_id = data.get("product_id")

                    if msg_type == "snapshot":
                        bids = {float(p): float(s) for p, s in data.get("bids", []) if float(s) > 0}
                        asks = {float(p): float(s) for p, s in data.get("asks", []) if float(s) > 0}
                        books[product_id] = {"bids": bids, "asks": asks}
                        asset = product_to_asset.get(product_id, product_id)
                        logger.info("[%s] L2 snapshot: %d bids, %d asks", asset, len(bids), len(asks))

                    elif msg_type == "l2update":
                        if product_id not in books:
                            continue
                        book = books[product_id]
                        for side, price_str, size_str in data.get("changes", []):
                            price, size = float(price_str), float(size_str)
                            target = book["bids"] if side == "buy" else book["asks"]
                            if size == 0.0:
                                target.pop(price, None)
                            else:
                                target[price] = size

                        asset = product_to_asset.get(product_id)
                        if not asset or event_counts.get(asset, 0) >= events_per_asset:
                            continue

                        levels = CONFIG["binance_depth_levels"]
                        sorted_bids = sorted(book["bids"].items(), reverse=True)[:levels]
                        sorted_asks = sorted(book["asks"].items())[:levels]
                        if len(sorted_bids) < levels or len(sorted_asks) < levels:
                            continue

                        calc = mlofi_calcs[asset]
                        raw_mlofi = calc.compute_mlofi(sorted_bids, sorted_asks)
                        normalised = calc.normalise_mad(raw_mlofi)

                        collector = collectors[asset]
                        if collector.should_trigger(normalised):
                            now_ms = int(time.time() * 1000)
                            if now_ms - last_trigger_ms[asset] < cooldown_s * 1000:
                                continue
                            last_trigger_ms[asset] = now_ms

                            # Refresh tokens if stale
                            if time.time() - last_token_refresh > TOKEN_REFRESH_INTERVAL:
                                logger.info("Refreshing Polymarket token IDs...")
                                token_map = await resolve_token_ids(http_session)
                                last_token_refresh = time.time()

                            ts_utc = datetime.datetime.fromtimestamp(
                                now_ms / 1000.0, tz=datetime.timezone.utc
                            )
                            session_stratum = classify_session(ts_utc)

                            lag_event = {
                                "asset": asset,
                                "t_ofi_signal_ms": now_ms,
                                "mlofi_normalised": normalised,
                                "t_ask_depth_drop_ms": None,
                                "t_ask_price_move_ms": None,
                                "ask_depth_at_signal": None,
                                "ask_depth_viable_threshold": collector.execution_viable_size.get(asset),
                                "session_stratum": session_stratum,
                                "operational_lag_ms": None,
                            }

                            n_so_far = event_counts.get(asset, 0) + 1
                            has_token = asset in token_map
                            logger.info(
                                "★ TRIGGER %s [%d/%d] mlofi=%.3f session=%s polymarket=%s",
                                asset, n_so_far, events_per_asset, normalised,
                                session_stratum, "polling" if has_token else "no-token",
                            )

                            token_id = token_map.get(asset)
                            await monitor_polymarket_response(
                                lag_event, http_session, token_id
                            )

                            lag_event["operational_lag_ms"] = (
                                LagMeasurementCollector.compute_operational_lag(lag_event)
                            )

                            db.insert(lag_event)
                            event_counts[asset] = event_counts.get(asset, 0) + 1
                            collector.events.append(lag_event)

                            logger.info(
                                "  → lag=%s ms | depth_drop=%s | price_move=%s",
                                lag_event["operational_lag_ms"],
                                lag_event["t_ask_depth_drop_ms"] is not None,
                                lag_event["t_ask_price_move_ms"] is not None,
                            )

                            total = sum(event_counts.get(a, 0) for a in assets)
                            if total >= events_per_asset * len(assets):
                                return

                    elif msg_type == "subscriptions":
                        logger.info("Confirmed: %s", data.get("channels", []))
                    elif msg_type == "error":
                        logger.error("Coinbase WS error: %s", data.get("message", data))

        except aiohttp.ClientError as e:
            logger.warning("Connection error: %s — retry in 5s", e)
            await asyncio.sleep(5)
        except asyncio.TimeoutError:
            logger.warning("Timeout — retry in 5s")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error("Error: %s — retry in 5s", e)
            await asyncio.sleep(5)


# ── Summary output ────────────────────────────────────────────────────────

def print_summary(db: PilotDB) -> None:
    """Print pilot analysis summary."""
    print("\n" + "=" * 70)
    print("  PILOT PHASE — PROPAGATION LAG MEASUREMENT SUMMARY")
    print("=" * 70)

    counts = db.count_by_asset()
    print("\n▸ Events collected per asset:")
    for asset in CONFIG["assets"]:
        n = counts.get(asset, 0)
        print(f"    {asset:12s}: {n}")
    print(f"    {'TOTAL':12s}: {sum(counts.values())}")

    all_lags = db.get_all_lags()
    if not all_lags:
        print("\n▸ No operational lag measurements recorded.")
        print("  If Polymarket markets were not accepting orders, lag = NULL.")
        print("  Re-run during active market hours for full lag measurement.")
        return

    stats = compute_pilot_statistics(all_lags)
    print(f"\n▸ sigma_pilot = {stats['sigma_pilot']:.3f} s")
    print(f"▸ n_required per stratum = {stats['n_required_per_stratum']}")
    print(f"▸ n_total required (3 strata) = {stats['n_total_required']}")
    print(f"▸ Mean lag = {stats['mean_lag_s']:.3f} s")
    print(f"▸ Median lag = {stats['median_lag_s']:.3f} s")

    import numpy as np
    lags_by_session = db.get_lags_by_session()
    print("\n▸ By session stratum:")
    fmt = "    {:<10s} {:>4s} {:>10s} {:>10s} {:>20s} {:>8s}"
    print(fmt.format("Stratum", "n", "Mean (s)", "Median (s)", "95% CI", "Stops?"))
    print("    " + "-" * 65)
    for sname in ["asian", "eu", "us"]:
        lags_ms = lags_by_session.get(sname, [])
        n = len(lags_ms)
        if n == 0:
            print(fmt.format(sname, str(n), "—", "—", "—", "—"))
            continue
        lags_s = np.array(lags_ms) / 1000.0
        mean, median = float(np.mean(lags_s)), float(np.median(lags_s))
        if n >= 2:
            std = float(np.std(lags_s, ddof=1))
            ci_h = 1.96 * std / np.sqrt(n)
            ci_lo, ci_hi = mean - ci_h, mean + ci_h
            stops = (ci_lo > 10 or ci_hi < 10) and (ci_lo > 30 or ci_hi < 30)
            ci_str = f"[{ci_lo:.1f}, {ci_hi:.1f}]"
            stops_str = "YES" if stops else "no"
        else:
            ci_str, stops_str = "n<2", "n<2"
        print(f"    {sname:10s} {n:4d} {mean:10.3f} {median:10.3f} {ci_str:>20s} {stops_str:>8s}")

    if any(len(v) >= 2 for v in lags_by_session.values()):
        stop = check_stopping_rule(lags_by_session, stats["n_required_per_stratum"])
        print(f"\n▸ Stopping rule met: {'YES' if stop['can_stop'] else 'NO'}")
        if not stop["can_stop"]:
            print(f"    Reason: {stop.get('reason', 'N/A')}")

    print(f"\n▸ NOTE: Do NOT evaluate LAG_DECISION_RULES yet.")
    print(f"    Full collection requires {stats['n_total_required']} events across 3 strata.")
    print("=" * 70 + "\n")


# ── Main ──────────────────────────────────────────────────────────────────

async def main(bankroll_usdc: float) -> None:
    assets = CONFIG["assets"]
    events_per_asset = 30  # 10 asian, 10 eu, 10 us
    kelly_fraction = CONFIG["kelly_fraction"]

    collectors = {}
    for asset in assets:
        c = LagMeasurementCollector(
            assets=[asset], kelly_fraction=kelly_fraction, bankroll_usdc=bankroll_usdc,
        )
        c.compute_viable_size(asset, p_market=0.5)
        collectors[asset] = c
        logger.info("Viable size for %s: %.2f USDC", asset, c.execution_viable_size[asset])

    mlofi_calcs = {
        asset: MLOFICalculator(
            levels=CONFIG["binance_depth_levels"],
            mad_window=CONFIG["mlofi_mad_window"],
        )
        for asset in assets
    }

    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pilot_events.db")
    db = PilotDB(db_path)
    logger.info("Pilot events DB: %s", db_path)

    event_counts = db.count_by_asset()
    total_existing = sum(event_counts.values())
    if total_existing > 0:
        logger.info("Resuming — %d events already collected: %s", total_existing, event_counts)
        if total_existing >= events_per_asset * len(assets):
            logger.info("Pilot already complete.")
            print_summary(db)
            db.close()
            return

    async with aiohttp.ClientSession() as http_session:
        logger.info(
            "Starting pilot: %d events/asset, %d total | bankroll: $%.0f | source: Coinbase WS",
            events_per_asset, events_per_asset * len(assets), bankroll_usdc,
        )

        try:
            await run_coinbase_feed(
                assets=assets,
                collectors=collectors,
                mlofi_calcs=mlofi_calcs,
                db=db,
                events_per_asset=events_per_asset,
                event_counts=event_counts,
                http_session=http_session,
            )
        except KeyboardInterrupt:
            logger.info("Interrupted — printing partial summary")

    print_summary(db)
    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lag Measurement Pilot Phase")
    parser.add_argument("--bankroll", type=float, required=True, help="Bankroll in USDC")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.bankroll))
    except KeyboardInterrupt:
        logger.info("Pilot interrupted")
