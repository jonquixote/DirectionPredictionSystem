import asyncio
import os
import json
import sys
import aiohttp

sys.path.append('.')
from api.kalshi import KalshiAuth, KalshiRestClient

async def main():
    auth = KalshiAuth(
        api_key_id=os.environ.get('KALSHI_API_KEY_ID', ''),
        private_key_path=os.environ.get('KALSHI_PRIVATE_KEY_PATH', '/app/secrets/kalshi_live.key')
    )
    async with aiohttp.ClientSession() as session:
        client = KalshiRestClient(auth, env='prod', session=session)
        
        fills = await client.get_fills(limit=100)
        fills = fills.get('fills', [])
        
        wins = 0
        losses = 0
        pnl = 0.0
        
        for f in fills:
            ticker = f.get('ticker')
            side = f.get('side')
            count = float(f.get('count_fp', 0))
            fee = float(f.get('fee_cost', 0))
            if side == 'yes':
                cost_per = float(f.get('yes_price_dollars', 0))
            else:
                cost_per = float(f.get('no_price_dollars', 0))
                
            cost = (count * cost_per) + fee
            
            try:
                resp = await client._request('GET', f'/markets/{ticker}')
                market = resp.get('market', {})
                status = market.get('status')
                result = market.get('result')
                
                if status == 'settled' or result:
                    won = (result == side)
                    if won:
                        wins += 1
                        pnl += (count - cost)
                    else:
                        losses += 1
                        pnl -= cost
                    print(f'{ticker} | Side={side} | Cost=${cost:.2f} | Result={result} | WON={won}')
                else:
                    print(f'{ticker} | Side={side} | Status={status} | NOT RESOLVED YET')
                    
            except Exception as e:
                print(f'Error fetching {ticker}: {e}')

        print(f'\nKalshi Real Wins: {wins}, Real Losses: {losses}')
        print(f'Net PnL from these {wins+losses} trades: ${pnl:.2f}')

if __name__ == '__main__':
    asyncio.run(main())
