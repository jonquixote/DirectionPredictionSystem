"""
Polymarket contract discovery and market price queries.

Discovers active 5-minute crypto up/down contracts and retrieves
the market mid-price (p_market) for the "Up" outcome.

Slug pattern: {sym}-updown-5m-{boundary_unix_ts}
  - btc-updown-5m-1774866600
  - eth-updown-5m-1774866600
  - sol-updown-5m-1774866600
"""

import logging
import time
from typing import Optional

import aiohttp

logger = logging.getLogger("polymarket_discovery")

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

# Map Bybit symbols to Polymarket slug prefixes
SYMBOL_TO_SLUG_PREFIX = {
    "BTCUSDT": "btc",
    "ETHUSDT": "eth",
    "SOLUSDT": "sol",
}

# Cache: (symbol, boundary_ts) -> {"up_token_id": str, "p_market_gamma": float}
_contract_cache: dict[tuple[str, int], dict] = {}


def _make_slug(symbol: str, boundary_ts: int) -> Optional[str]:
    """Build the Polymarket slug for a 5-min contract."""
    prefix = SYMBOL_TO_SLUG_PREFIX.get(symbol)
    if not prefix:
        return None
    return f"{prefix}-updown-5m-{boundary_ts}"


async def discover_contract(
    session: aiohttp.ClientSession,
    symbol: str,
    boundary_ts: int,
) -> Optional[dict]:
    """
    Discover the active Polymarket 5-min contract for a symbol at a boundary.

    Returns dict with:
        up_token_id: str
        down_token_id: str
        p_market_gamma: float  (last trade price from Gamma, fallback)
        slug: str
    Or None if discovery fails.
    """
    cache_key = (symbol, boundary_ts)
    if cache_key in _contract_cache:
        return _contract_cache[cache_key]

    slug = _make_slug(symbol, boundary_ts)
    if not slug:
        return None

    url = f"{GAMMA_BASE}/markets?slug={slug}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status != 200:
                logger.warning("Gamma API %d for %s", resp.status, slug)
                return None
            data = await resp.json()

        if not data:
            logger.debug("No market found for slug %s", slug)
            return None

        market = data[0]
        import json
        tokens = json.loads(market.get("clobTokenIds", "[]"))
        prices = json.loads(market.get("outcomePrices", "[]"))

        if len(tokens) < 2 or len(prices) < 2:
            logger.warning("Incomplete market data for %s", slug)
            return None

        result = {
            "up_token_id": tokens[0],
            "down_token_id": tokens[1],
            "p_market_gamma": float(prices[0]),  # Up price from Gamma
            "slug": slug,
        }
        _contract_cache[cache_key] = result
        return result

    except Exception as e:
        logger.warning("Contract discovery failed for %s: %s", slug, e)
        return None


async def get_clob_midpoint(
    session: aiohttp.ClientSession,
    up_token_id: str,
) -> Optional[float]:
    """
    Query the CLOB order book midpoint for the "Up" token.
    This is more accurate than the Gamma last-trade price.
    Returns p_market as a float, or None on failure.
    """
    url = f"{CLOB_BASE}/midpoint?token_id={up_token_id}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
            if resp.status != 200:
                logger.debug("CLOB midpoint %d for token", resp.status)
                return None
            data = await resp.json()
            mid = data.get("mid")
            if mid is not None:
                return float(mid)
            return None
    except Exception as e:
        logger.debug("CLOB midpoint query failed: %s", e)
        return None


async def get_p_market(
    session: aiohttp.ClientSession,
    symbol: str,
    boundary_ts: int,
) -> Optional[float]:
    """
    Get p_market (Up contract probability) for a symbol at a boundary.

    Strategy:
    1. Discover contract via Gamma API (cached)
    2. Query CLOB midpoint for live order book mid
    3. Fall back to Gamma last-trade price if CLOB fails
    4. Return None if both fail (do not block predictions)
    """
    contract = await discover_contract(session, symbol, boundary_ts)
    if not contract:
        return None

    # Try CLOB midpoint first (order book based, more accurate)
    clob_mid = await get_clob_midpoint(session, contract["up_token_id"])
    if clob_mid is not None:
        return clob_mid

    # Fall back to Gamma last-trade price
    logger.debug("Using Gamma fallback p_market for %s: %.3f",
                 symbol, contract["p_market_gamma"])
    return contract["p_market_gamma"]


def clear_cache():
    """Clear the contract cache (e.g., at startup)."""
    _contract_cache.clear()
