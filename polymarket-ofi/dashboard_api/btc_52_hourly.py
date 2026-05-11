import json
import os
import datetime
from collections import defaultdict

LOG_DIR = "/data/logs"
MODEL = "h300"
FEE = 0.009

def compute_realized_net(direction, correct, p_market, fee=0.009):
    if p_market is None: p_market = 0.5
    if direction == "up":
        return +(1 - p_market - fee) if correct else -(p_market + fee)
    else: # down
        return +(p_market - fee) if correct else -((1 - p_market) + fee)

def analyze():
    preds = {}
    price_trace = {}
    
    pred_path = os.path.join(LOG_DIR, f"predictions_{MODEL}.jsonl")
    with open(pred_path, "r") as f:
        for line in f:
            try:
                data = json.loads(line)
                if data.get("record_type") == "prediction" and not data.get("warmup"):
                    preds[data["prediction_id"]] = data
                    if data["symbol"] == "BTCUSDT":
                        ts_snap = (data["ts_model_ran_ms"] // 60000) * 60000
                        price_trace[ts_snap] = data["price_at_contract_open"]
            except: continue

    hourly_stats = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})

    for p in preds.values():
        if p["symbol"] != "BTCUSDT": continue
        
        conf = p["pred_proba"] if p["pred_direction"] == "up" else (1.0 - p["pred_proba"])
        if conf < 0.52: continue
        
        ts_entry = p["ts_model_ran_ms"]
        dt = datetime.datetime.fromtimestamp(ts_entry / 1000, tz=datetime.timezone.utc)
        hour = dt.hour
        
        ts_close = ((ts_entry // 60000) * 60000) + 900000
        price_close = None
        for offset in [0, 60000, -60000]:
            if (ts_close + offset) in price_trace:
                price_close = price_trace[ts_close + offset]; break
        
        if price_close is not None:
            correct = (price_close > p["price_at_contract_open"]) if p["pred_direction"] == "up" else (price_close < p["price_at_contract_open"])
            pnl = compute_realized_net(p["pred_direction"], correct, p.get("p_market", 0.5), FEE)
            
            s = hourly_stats[hour]
            s["n"] += 1
            if correct: s["wins"] += 1
            s["pnl"] += pnl

    return hourly_stats

if __name__ == "__main__":
    results = analyze()
    output = []
    for h in range(24):
        s = results.get(h, {"n":0, "wins":0, "pnl":0.0})
        output.append({
            "hour": h,
            "n": s["n"],
            "accuracy": s["wins"] / s["n"] if s["n"] > 0 else 0,
            "pnl": s["pnl"]
        })
    print(json.dumps(output, indent=2))
