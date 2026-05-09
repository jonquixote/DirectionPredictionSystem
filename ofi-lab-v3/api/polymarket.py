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
    GET /fee-rate?token_id={token_id}   (CLOB V2 — query param, not path param)
    Returns the contract-level base fee parameter (NOT the effective taker rate).

    V2 response: {"base_fee": 1000}  → 1000 bps = 0.1
    This is the smart contract parameter (≈ 2× peak effective fee at p=0.5).

    Actual taker fee rates differ by market category:
      Crypto = 0.072, Finance = 0.04, Sports = 0.03, Geopolitics = 0.00
    Formula: fee = C × feeRate × p × (1-p)

    NOTE: Paper traders correctly use POLYMARKET_FEE_COEFFICIENT = 0.072 for
    crypto markets. This endpoint returns the raw contract param, not that rate.
    """
    url = f"{POLYMARKET_CLOB_BASE}/fee-rate"
    params = {"token_id": token_id}
    async with session.get(url, params=params) as resp:
        data = await resp.json()
        return float(data.get("base_fee", 0)) / 10000.0


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
