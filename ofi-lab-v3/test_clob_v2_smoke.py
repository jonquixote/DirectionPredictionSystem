import asyncio
import aiohttp
from trading.polymarket_discovery import get_p_market, discover_contract
from api.polymarket import get_order_book, get_fee_rate
import time

async def test_clob_v2_smoke():
    boundary_ts = (int(time.time()) // 300) * 300 + 300
    print(f"Testing for boundary: {boundary_ts}")
    
    async with aiohttp.ClientSession() as session:
        # Test discovery (Gamma API)
        contract = await discover_contract(session, "BTCUSDT", boundary_ts)
        print(f"Discovery Result: {contract}")
        
        if contract:
            up_token = contract["up_token_id"]
            
            # Test orderbook
            book = await get_order_book(session, up_token)
            print(f"Orderbook Bids count: {len(book.get('bids', []))}")
            
            # Test fee rate
            try:
                fee = await get_fee_rate(session, up_token)
                print(f"Fee Rate: {fee}")
            except Exception as e:
                print(f"Fee Rate Error: {e}")
                
            # Test midpoint
            p_market = await get_p_market(session, "BTCUSDT", boundary_ts)
            print(f"P Market (midpoint): {p_market}")

if __name__ == "__main__":
    asyncio.run(test_clob_v2_smoke())
