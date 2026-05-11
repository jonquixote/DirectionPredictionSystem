import json
import os
from collections import defaultdict

LOG_DIR = "/data/logs"
MODEL = "h300"

def analyze():
    preds = {}
    price_trace = {}
    
    pred_path = os.path.join(LOG_DIR, f"predictions_{MODEL}.jsonl")
    if not os.path.exists(pred_path): 
        return {"error": "Prediction file not found"}

    # Pass 1: Load all predictions and build price trace
    with open(pred_path, "r") as f:
        for line in f:
            try:
                data = json.loads(line)
                if data.get("record_type") == "prediction" and not data.get("warmup"):
                    pid = data.get("prediction_id")
                    preds[pid] = data
                    
                    # Store price trace for resolution
                    symbol = data["symbol"]
                    ts = data["ts_model_ran_ms"]
                    price = data.get("price_at_contract_open")
                    if price:
                        ts_snap = (ts // 60000) * 60000
                        price_trace[(symbol, ts_snap)] = price
            except: continue

    # Pass 2: Resolve at 900s for any signal > 0.5 confidence
    stats = defaultdict(lambda: {"n": 0, "wins": 0})
    for p in preds.values():
        symbol = p["symbol"]
        ts_entry = p["ts_model_ran_ms"]
        price_entry = p.get("price_at_contract_open")
        direction = p["pred_direction"]
        proba = p["pred_proba"]
        
        # 0.5 gate: any non-neutral signal
        if proba == 0.5: continue
        
        # Look for price 900s later
        ts_close = ((ts_entry // 60000) * 60000) + 900000
        price_close = None
        for offset in [0, 60000, -60000]:
            if (symbol, ts_close + offset) in price_trace:
                price_close = price_trace[(symbol, ts_close + offset)]
                break
        
        if price_close is not None:
            correct = (price_close > price_entry) if direction == "up" else (price_close < price_entry)
            stats[symbol]["n"] += 1
            if correct: stats[symbol]["wins"] += 1

    return {k: {"n": v["n"], "accuracy": v["wins"]/v["n"]} for k,v in stats.items()}

if __name__ == "__main__":
    results = analyze()
    print(json.dumps(results, indent=2))
