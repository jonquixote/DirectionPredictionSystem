import json
import glob
import urllib.request
import time
from datetime import datetime

def fetch_klines():
    earliest_ts = int(time.time() * 1000)
    for line in open('/data/logs/paper_trades_h300.jsonl'):
        if 'BTCUSDT' in line:
            try:
                ts = json.loads(line).get('ts_contract_open_ms')
                if ts and ts < earliest_ts: earliest_ts = ts
            except: pass

    current_start = earliest_ts - 900000
    now = int(time.time() * 1000)
    prices_1m = {}
    
    print(f"Fetching klines from {current_start} to {now}...")
    while current_start < now:
        url = f'https://api.binance.us/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime={current_start}&limit=1000'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                if not data: break
                for k in data: prices_1m[k[0]] = float(k[4])
                current_start = data[-1][0] + 60000
        except: break
    print(f"Loaded {len(prices_1m)} klines.")
    return prices_1m

def analyze(prices):
    cohorts = {
        "W-W": {"count": 0, "probs": [], "alpha": [], "up": 0, "down": 0},
        "W-L": {"count": 0, "probs": [], "alpha": [], "up": 0, "down": 0},
        "L-W": {"count": 0, "probs": [], "alpha": [], "up": 0, "down": 0},
        "L-L": {"count": 0, "probs": [], "alpha": [], "up": 0, "down": 0}
    }
    
    seen = set()
    for line in open('/data/logs/paper_trades_h300.jsonl'):
        if 'BTCUSDT' not in line or '\"model\": \"h300\"' not in line: continue
        try:
            d = json.loads(line)
            if d.get('record_type') != 'prediction' and d.get('record_type') != 'trade_entry': continue
            
            ts = d.get('ts_contract_open_ms')
            if not ts or ts in seen: continue
            seen.add(ts)
            
            start_price = prices.get(ts - (ts % 60000)) or d.get('price_at_contract_open')
            if not start_price: continue
            
            pred_dir = d.get('pred_direction')
            prob = d.get('pred_proba', 0.0)
            conf = prob if pred_dir == 'up' else (1.0 - prob)
            alpha = d.get('p_model_minus_market', 0.0)
            
            p300 = prices.get((ts + 300000) - ((ts + 300000) % 60000))
            p900 = prices.get((ts + 900000) - ((ts + 900000) % 60000))
            
            if p300 and p900:
                d300 = 'up' if p300 > start_price else ('down' if p300 < start_price else 'flat')
                d900 = 'up' if p900 > start_price else ('down' if p900 < start_price else 'flat')
                
                win_300 = pred_dir == d300
                loss_300 = pred_dir != d300 and d300 != 'flat'
                win_900 = pred_dir == d900
                loss_900 = pred_dir != d900 and d900 != 'flat'
                
                cohort = None
                if win_300 and win_900: cohort = "W-W"
                elif win_300 and loss_900: cohort = "W-L"
                elif loss_300 and win_900: cohort = "L-W"
                elif loss_300 and loss_900: cohort = "L-L"
                
                if cohort:
                    cohorts[cohort]["count"] += 1
                    cohorts[cohort]["probs"].append(conf)
                    cohorts[cohort]["alpha"].append(alpha)
                    if pred_dir == 'up': cohorts[cohort]["up"] += 1
                    else: cohorts[cohort]["down"] += 1
        except: pass
    return cohorts

prices = fetch_klines()
cohorts = analyze(prices)

print("\n" + "="*80)
print("  BASELINE (3k+ TRADES) DEEP DIVE: 5-MIN vs 15-MIN ON BTCUSDT")
print("="*80)

total_evaluated = sum(c["count"] for c in cohorts.values())
print(f"Total fully evaluated exact-match predictions: {total_evaluated}\n")

print(f"{'COHORT (5m-15m)':<15} | {'COUNT':<6} | {'% TOTAL':<8} | {'AVG CONF':<8} | {'AVG ALPHA':<10} | {'UP/DOWN RATIO'}")
print("-" * 80)

for name in ["W-W", "W-L", "L-W", "L-L"]:
    data = cohorts[name]
    count = data["count"]
    if count == 0: continue
    pct = (count / total_evaluated) * 100
    avg_conf = sum(data["probs"]) / count
    avg_alpha = sum(data["alpha"]) / count
    up_down = f"{data['up']}U / {data['down']}D"
    
    print(f"{name:<15} | {count:<6} | {pct:>5.1f}%   | {avg_conf:>8.4f} | {avg_alpha:>10.4f} | {up_down}")

print("\n--- ANALYSIS ---")
w_w = cohorts["W-W"]["count"]
w_l = cohorts["W-L"]["count"]
l_w = cohorts["L-W"]["count"]
l_l = cohorts["L-L"]["count"]

print(f"Total 5-min Wins: {w_w + w_l} ({(w_w + w_l)/total_evaluated*100:.1f}%)")
print(f"Total 15-min Wins: {w_w + l_w} ({(w_w + l_w)/total_evaluated*100:.1f}%)")

print("\n1. WHEN IS IT OPTIMAL TO BET AT 5 MIN?")
print("Look at the W-L (Win at 5m, Loss at 15m) cohort. These are the trades that were optimal for 5m but died by 15m.")

print("\n2. WHAT HAPPENS WHEN THEY END UP WITH DIFFERENT RESULTS (W-L vs L-W)?")
print("W-L: Quick mean-reversion trades where the longer-term structural trend runs them over by minute 15.")
print("L-W: Slower structural trend trades where minute 5 was just short-term noise against them.")

print("\n3. WHAT HAPPENS WHEN THEY ARE THE SAME (W-W vs L-L)?")
print("W-W: Perfect alignment of short-term order flow and 15-minute momentum.")
print("L-L: Complete model misfires, or overwhelming macro trends ignoring the VWAP/OFI signals.")
