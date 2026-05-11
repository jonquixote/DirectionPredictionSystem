from __future__ import annotations
"""
Polymarket CLOB API integration.
Spec v2.6, Section 6.
"""

import aiohttp

POLYMARKET_CLOB_BASE = "https://clob.polymarket.com"


async def get_order_book(session: aiohttp.ClientSession, token_id: str) -> dict:
    """
    GET /book?token_id={token_id}
    Returns bids[], asks[], market metadata.
    Rate limit: 50 req / 10s
    """
    url = f"{POLYMARKET_CLOB_BASE}/book"
    params = {"token_id": token_id}
    async with session.get(url, params=params) as resp:
        return await resp.json()


async def get_fee_rate(session: aiohttp.ClientSession, token_id: str) -> float:
    """
    GET /fee-rate/{token_id}
    Returns fee rate for this specific contract.
    MUST be called per-contract before execution. Never hardcode.

    Polymarket January 2026 fee structure: taker fees are probability-NONLINEAR.
    Fee is highest at p ≈ 0.50 (~3%) and declines toward 0 and 1.
    The per-contract API call handles this correctly.
    """
    url = f"{POLYMARKET_CLOB_BASE}/fee-rate/{token_id}"
    async with session.get(url) as resp:
        data = await resp.json()
        return float(data["fee_rate"])


def parse_book(book_data: dict) -> dict | None:
    """
    Extracts execution-relevant fields from /book response.
    Returns: best_ask_price, best_ask_size, best_bid_price, best_bid_size,
             spread_bps, mid. Returns None on empty book.
    """
    asks = sorted(book_data.get("asks", []), key=lambda x: float(x["price"]))
    bids = sorted(
        book_data.get("bids", []), key=lambda x: float(x["price"]), reverse=True
    )
    if not asks or not bids:
        return None
    best_ask = float(asks[0]["price"])
    best_bid = float(bids[0]["price"])
    mid = (best_ask + best_bid) / 2
    spread_bps = ((best_ask - best_bid) / mid) * 10000 if mid > 0 else None
    return {
        "best_ask_price": best_ask,
        "best_ask_size": float(asks[0]["size"]),
        "best_bid_price": best_bid,
        "best_bid_size": float(bids[0]["size"]),
        "spread_bps": spread_bps,
        "mid": mid,
    }
