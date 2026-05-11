import json
import os
from collections import defaultdict
from datetime import datetime, timezone

LOG_DIR = "/data/logs"
MODEL = "h300"

def analyze():
    preds = []
    # 1. Parse Predictions
    pred_path = os.path.join(LOG_DIR, f"predictions_{MODEL}.jsonl")
    if not os.path.exists(pred_path): return {"error": "No pred file"}
    
    # Store price trace: (symbol, ts_ms_60s_snapped) -> price
    price_trace = {}
    
    with open(pred_path, "r") as f:
        for line in f:
            try:
                data = json.loads(line)
                if data.get("record_type") == "prediction" and not data.get("warmup"):
                    preds.append(data)
                    symbol = data["symbol"]
                    ts = data["ts_model_ran_ms"]
                    price = data.get("price_at_contract_open")
                    if price:
                        # Snap to closest minute to facilitate lookups
                        ts_snap = (ts // 60000) * 60000
                        price_trace[(symbol, ts_snap)] = price
            except: continue

    print(f"Loaded {len(preds)} predictions. Price trace has {len(price_trace)} points.")

    # 2. Resolve using internal trace
    # Key: (asset, duration)
    stats = defaultdict(lambda: {"n": 0, "wins": 0})
    
    for p in preds:
        symbol = p["symbol"]
        ts_entry = p["ts_model_ran_ms"]
        price_entry = p.get("price_at_contract_open")
        direction = p["pred_direction"]
        
        # Snap entry ts to minute
        ts_min = (ts_entry // 60000) * 60000
        
        for dur in [300, 900]:
            ts_close = ts_min + (dur * 1000)
            
            # Look for price at close (+/- 60s tolerance)
            price_close = None
            for offset in [0, 60000, -60000]:
                if (symbol, ts_close + offset) in price_trace:
                    price_close = price_trace[(symbol, ts_close + offset)]
                    break
            
            if price_close is not None:
                correct = False
                if direction == "up":
                    correct = price_close > price_entry
                else: 
                    correct = price_close < price_entry
                
                key = (symbol, dur)
                stats[key]["n"] += 1
                if correct: stats[key]["wins"] += 1

    # 3. Final Aggregates
    results = []
    for k, v in sorted(stats.items()):
        results.append({
            "asset": k[0],
            "duration": k[1],
            "n": v["n"],
            "accuracy": v["wins"] / v["n"] if v["n"] > 0 else 0
        })
    return results

if __name__ == "__main__":
    res = analyze()
    print(json.dumps(res, indent=2))
