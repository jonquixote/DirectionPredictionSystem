import json
import glob
import urllib.request
import time

def fetch_klines():
    # Find earliest timestamp
    earliest_ts = int(time.time() * 1000)
    for f in glob.glob("/data/*/paper_trades_*.jsonl"):
        with open(f, 'r') as file:
            for line in file:
                if "BTCUSDT" not in line: continue
                try:
                    d = json.loads(line)
                    ts = d.get('ts_contract_open_ms')
                    if ts and ts < earliest_ts:
                        earliest_ts = ts
                except: pass

    # Back up by 15 mins just in case
    current_start = earliest_ts - 900000
    now = int(time.time() * 1000)
    prices_1m = {}
    
    print(f"Fetching from {current_start} to {now}...")
    while current_start < now:
        url = f"https://api.binance.us/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime={current_start}&limit=1000"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                if not data: break
                for k in data: prices_1m[k[0]] = float(k[4])
                current_start = data[-1][0] + 60000
        except Exception as e:
            print(f"Error fetching: {e}")
            break
    print(f"Loaded {len(prices_1m)} klines.")
    return prices_1m

def get_exact(ts, prices):
    return prices.get(ts - (ts % 60000))

def analyze(directory, prices):
    stats = {
        "ALL": {"tot": 0, "w3": 0, "l3": 0, "w9": 0, "l9": 0},
        "GATED_055": {"tot": 0, "w3": 0, "l3": 0, "w9": 0, "l9": 0},
        "GATED_056": {"tot": 0, "w3": 0, "l3": 0, "w9": 0, "l9": 0}
    }
    seen = set()
    for f in glob.glob(f"{directory}/paper_trades_*.jsonl"):
        with open(f, 'r') as file:
            for line in file:
                if "BTCUSDT" not in line or '"model": "h300"' not in line: continue
                try:
                    d = json.loads(line)
                    if d.get("record_type") not in ("trade_entry", "prediction"): continue
                    if d.get("contract_duration_seconds") != 900: continue
                    
                    ts = d.get("ts_contract_open_ms")
                    if not ts or ts in seen: continue
                    seen.add(ts)
                    
                    start_price = get_exact(ts, prices)
                    if not start_price: start_price = d.get("price_at_contract_open")
                    if not start_price: continue
                    
                    pred_dir = d.get("pred_direction")
                    prob = d.get("pred_proba", 0.0)
                    conf = prob if pred_dir == "up" else (1.0 - prob)
                    
                    p300 = get_exact(ts + 300000, prices)
                    p900 = get_exact(ts + 900000, prices)
                    
                    if p300 and p900:
                        d300 = "up" if p300 > start_price else ("down" if p300 < start_price else "flat")
                        d900 = "up" if p900 > start_price else ("down" if p900 < start_price else "flat")
                        
                        w3 = 1 if pred_dir == d300 else 0
                        l3 = 1 if pred_dir != d300 and d300 != "flat" else 0
                        w9 = 1 if pred_dir == d900 else 0
                        l9 = 1 if pred_dir != d900 and d900 != "flat" else 0
                        
                        for cat in ["ALL", "GATED_055", "GATED_056"]:
                            if cat == "GATED_055" and conf < 0.55: continue
                            if cat == "GATED_056" and conf < 0.56: continue
                            
                            stats[cat]["tot"] += 1
                            stats[cat]["w3"] += w3
                            stats[cat]["l3"] += l3
                            stats[cat]["w9"] += w9
                            stats[cat]["l9"] += l9
                except: pass
    return stats

prices = fetch_klines()

for name, d in {"Model A (Shifted)": "/data/logs_model_a", "Model A V2 Clone": "/data/logs_model_a_clone"}.items():
    print(f"\\n[ {name} ]")
    s = analyze(d, prices)
    for cat in ["ALL", "GATED_055", "GATED_056"]:
        tot = s[cat]["tot"]
        if tot == 0: continue
        w3, l3, w9, l9 = s[cat]["w3"], s[cat]["l3"], s[cat]["w9"], s[cat]["l9"]
        wr3 = w3/(w3+l3)*100 if (w3+l3)>0 else 0
        wr9 = w9/(w9+l9)*100 if (w9+l9)>0 else 0
        print(f"  {cat} | Tot: {tot} | +300s: {w3}W-{l3}L ({wr3:.1f}%) | +900s: {w9}W-{l9}L ({wr9:.1f}%) | Diff: {wr9-wr3:+.1f}%")

