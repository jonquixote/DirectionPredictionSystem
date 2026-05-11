import json
import os
from collections import defaultdict

LOG_DIR = "/data/logs"
MODEL = "h300"
FEE = 0.009

def compute_realized_net(direction, correct, p_market, fee=0.009):
    if p_market is None: return 0
    if direction == "up":
        return +(1 - p_market - fee) if correct else -(p_market + fee)
    else: # down
        return +(p_market - fee) if correct else -((1 - p_market) + fee)

def analyze():
    preds = {}
    price_trace = {}
    
    pred_path = os.path.join(LOG_DIR, f"predictions_{MODEL}.jsonl")
    if not os.path.exists(pred_path): 
        return {"error": "Prediction file not found"}

    # Pass 1: Build Price Trace
    with open(pred_path, "r") as f:
        for line in f:
            try:
                data = json.loads(line)
                if data.get("record_type") == "prediction" and not data.get("warmup"):
                    pid = data.get("prediction_id")
                    preds[pid] = data
                    symbol = data["symbol"]
                    ts = data["ts_model_ran_ms"]
                    price = data.get("price_at_contract_open")
                    if price:
                        ts_snap = (ts // 60000) * 60000
                        price_trace[(symbol, ts_snap)] = price
            except: continue

    thresholds = [0.50, 0.505, 0.51, 0.515, 0.52, 0.525, 0.53, 0.535, 0.54, 0.545, 0.55]
    
    results = defaultdict(lambda: defaultdict(list)) # asset -> threshold -> stats

    for threshold in thresholds:
        for p in preds.values():
            symbol = p["symbol"]
            ts_entry = p["ts_model_ran_ms"]
            price_entry = p.get("price_at_contract_open")
            direction = p["pred_direction"]
            proba = p["pred_proba"]
            p_market = p.get("p_market", 0.5)
            
            # Check confidence (normalized as highest proba)
            # if proba > 0.5, conf = proba. if proba < 0.5, conf = 1-proba.
            conf = proba if direction == "up" else (1.0 - proba)
            
            if conf < threshold: continue
            
            # Resolve at 900s
            ts_close = ((ts_entry // 60000) * 60000) + 900000
            price_close = None
            for offset in [0, 60000, -60000]:
                if (symbol, ts_close + offset) in price_trace:
                    price_close = price_trace[(symbol, ts_close + offset)]
                    break
            
            if price_close is not None:
                correct = (price_close > price_entry) if direction == "up" else (price_close < price_entry)
                pnl = compute_realized_net(direction, correct, p_market, FEE)
                
                results[symbol][threshold].append(pnl)

    # Summarize
    final_output = defaultdict(list)
    for sym, thres_dict in results.items():
        for thres in thresholds:
            pnl_list = thres_dict.get(thres, [])
            n = len(pnl_list)
            if n > 0:
                wins = len([p for p in pnl_list if p > 0]) # rough win approximation from pnl
                # wait, let's just count based on direction logic inside the loop to be precise
                # actually I'll just re-run with correct/incorrect logic
                pass
    
    # RE-LOOPING for precision on win/loss
    final_table = defaultdict(list)
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        for threshold in thresholds:
            n = 0
            wins = 0
            total_pnl = 0.0
            for p in preds.values():
                if p["symbol"] != sym: continue
                conf = p["pred_proba"] if p["pred_direction"] == "up" else (1.0 - p["pred_proba"])
                if conf < threshold: continue
                
                ts_close = ((p["ts_model_ran_ms"] // 60000) * 60000) + 900000
                price_close = None
                for offset in [0, 60000, -60000]:
                    if (sym, ts_close + offset) in price_trace:
                        price_close = price_trace[(sym, ts_close + offset)]; break
                
                if price_close is not None:
                    correct = (price_close > p["price_at_contract_open"]) if p["pred_direction"] == "up" else (price_close < p["price_at_contract_open"])
                    n += 1
                    if correct: wins += 1
                    total_pnl += compute_realized_net(p["pred_direction"], correct, p.get("p_market", 0.5), FEE)
            
            final_table[sym].append({
                "threshold": threshold,
                "n": n,
                "accuracy": wins/n if n > 0 else 0,
                "pnl": total_pnl
            })
            
    return final_table

if __name__ == "__main__":
    table = analyze()
    print(json.dumps(table, indent=2))
