"""
Deep audit Part 2: Query Kalshi API for actual settlements, 
get floor_strike for each market we traded, and compare to Binance open prices.
"""
import asyncio
import os
import sys
import json
sys.path.append('.')
from api.kalshi import KalshiAuth, KalshiRestClient
import aiohttp

KALSHI_PATH = "/tmp/vps_kalshi_orders_fresh.jsonl"

async def main():
    auth = KalshiAuth(
        api_key_id=os.environ.get('KALSHI_API_KEY_ID', ''),
        private_key_path=os.environ.get('KALSHI_PRIVATE_KEY_PATH', '/app/secrets/kalshi_live.key')
    )
    async with aiohttp.ClientSession() as session:
        client = KalshiRestClient(auth, env='prod', session=session)
        
        # Get balance
        bal = await client.get_balance()
        print(f"Current Kalshi Balance: ${bal.get('balance', 0)/100:.2f}")
        
        # Get all fills with their actual prices
        fills = await client.get_fills(limit=100)
        fills_list = fills.get('fills', [])
        
        print(f"Total fills from Kalshi API: {len(fills_list)}")
        
        # Group fills by order_id
        from collections import defaultdict
        fills_by_order = defaultdict(list)
        for f in fills_list:
            fills_by_order[f.get('order_id')].append(f)
        
        # Load our kalshi orders
        kalshi_placed = []
        with open(KALSHI_PATH) as fh:
            for line in fh:
                if not line.strip(): continue
                o = json.loads(line)
                if o.get('gate_result') == 'PLACED' and o.get('symbol') == 'BTCUSDT':
                    kalshi_placed.append(o)
        
        print(f"\nKalshi placed orders in our ledger: {len(kalshi_placed)}")
        
        # For each placed order, get the market details (floor_strike) and settlement
        print(f"\n{'='*100}")
        print(f"TRADE-BY-TRADE AUDIT: KALSHI MARKET DETAILS + SETTLEMENT")
        print(f"{'='*100}")
        
        total_cost = 0
        total_payout = 0
        wins = 0
        losses = 0
        pending = 0
        
        for k in kalshi_placed:
            ticker = k.get('ticker')
            side = k.get('side')
            order_id = k.get('order_id')
            model_p = k.get('model_p', 0)
            ts = k.get('ts', '')
            
            try:
                resp = await client._request('GET', f'/markets/{ticker}')
                market = resp.get('market', {})
                
                floor_strike = market.get('floor_strike')
                cap_strike = market.get('cap_strike')
                status = market.get('status')
                result = market.get('result')
                close_time = market.get('close_time')
                subtitle = market.get('subtitle', '')
                
                # Get actual fill details
                order_fills = fills_by_order.get(order_id, [])
                total_count = sum(float(f.get('count_fp', 0)) for f in order_fills)
                total_fee = sum(float(f.get('fee_cost', 0)) for f in order_fills)
                
                if side == 'yes':
                    fill_prices = [float(f.get('yes_price_dollars', 0)) for f in order_fills]
                    avg_price = sum(p * float(f.get('count_fp', 0)) for p, f in zip(fill_prices, order_fills)) / max(total_count, 0.01) if order_fills else 0
                    cost = sum(float(f.get('count_fp', 0)) * float(f.get('yes_price_dollars', 0)) for f in order_fills) + total_fee
                else:
                    fill_prices = [float(f.get('no_price_dollars', 0)) for f in order_fills]
                    avg_price = sum(p * float(f.get('count_fp', 0)) for p, f in zip(fill_prices, order_fills)) / max(total_count, 0.01) if order_fills else 0
                    cost = sum(float(f.get('count_fp', 0)) * float(f.get('no_price_dollars', 0)) for f in order_fills) + total_fee
                
                total_cost += cost
                
                if status == 'settled' or result:
                    won = (result == side)
                    if won:
                        wins += 1
                        payout = total_count
                        total_payout += payout
                    else:
                        losses += 1
                        payout = 0
                    
                    result_str = f"{'WIN' if won else 'LOSS':>4}"
                    pnl = payout - cost
                else:
                    pending += 1
                    result_str = "PEND"
                    pnl = 0
                
                dir_conf = max(model_p, 1 - model_p)
                
                print(f"\n{ts} | {ticker}")
                print(f"  Side: {side:>3} | Strike: {floor_strike} | Subtitle: {subtitle}")
                print(f"  Conf: {dir_conf:.4f} | Model_p: {model_p:.4f} | Fills: {len(order_fills)} | Contracts: {total_count:.1f}")
                print(f"  AvgFillPx: ${avg_price:.4f} | Cost: ${cost:.2f} | Fee: ${total_fee:.3f}")
                print(f"  Kalshi Result: {result} | Status: {status} | Outcome: {result_str} | PnL: ${pnl:+.2f}")
                
            except Exception as e:
                print(f"\n{ts} | {ticker} | ERROR: {e}")
        
        print(f"\n{'='*100}")
        print(f"SUMMARY")
        print(f"{'='*100}")
        print(f"Wins: {wins} | Losses: {losses} | Pending: {pending}")
        if wins + losses > 0:
            print(f"Win Rate: {wins/(wins+losses)*100:.1f}%")
        print(f"Total Cost: ${total_cost:.2f}")
        print(f"Total Payouts: ${total_payout:.2f}")
        print(f"Net PnL: ${total_payout - total_cost:+.2f}")
        print(f"Current Balance: ${bal.get('balance', 0)/100:.2f}")

if __name__ == '__main__':
    asyncio.run(main())
