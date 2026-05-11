import json
import os
from collections import defaultdict
from datetime import datetime, timezone

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
                    # Store price trace
                    symbol = data["symbol"]
                    ts = data["ts_model_ran_ms"]
                    if symbol == "BTCUSDT":
                      ts_snap = (ts // 60000) * 60000
                      price_trace[ts_snap] = data["price_at_contract_open"]
            except: continue

    # Daily Stats for BTC 900s
    # Key: date_str -> {raw: {n, wins, pnl}, traded: {n, wins, pnl}}
    daily_stats = defaultdict(lambda: {
        "raw": {"n": 0, "wins": 0, "pnl": 0.0},
        "traded": {"n": 0, "wins": 0, "pnl": 0.0}
    })

    for p in preds.values():
        if p["symbol"] != "BTCUSDT": continue
        ts_entry = p["ts_model_ran_ms"]
        price_entry = p["price_at_contract_open"]
        direction = p["pred_direction"]
        p_market = p.get("p_market", 0.5)
        conf = p["pred_proba"] if direction == "up" else (1.0 - p["pred_proba"])
        
        # Date
        dt = datetime.fromtimestamp(ts_entry / 1000, tz=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")
        
        # Resolve 900s later
        ts_close = ((ts_entry // 60000) * 60000) + 900000
        price_close = None
        for offset in [0, 60000, -60000]:
            if (ts_close + offset) in price_trace:
                price_close = price_trace[ts_close + offset]; break
        
        if price_close is not None:
            correct = (price_close > price_entry) if direction == "up" else (price_close < price_entry)
            pnl = compute_realized_net(direction, correct, p_market, FEE)
            
            # Record Raw (Any signal)
            daily_stats[date_str]["raw"]["n"] += 1
            if correct: daily_stats[date_str]["raw"]["wins"] += 1
            daily_stats[date_str]["raw"]["pnl"] += pnl
            
            # Record Traded (>= 0.55)
            if conf >= 0.55:
                daily_stats[date_str]["traded"]["n"] += 1
                if correct: daily_stats[date_str]["traded"]["wins"] += 1
                daily_stats[date_str]["traded"]["pnl"] += pnl

    return daily_stats

if __name__ == "__main__":
    results = analyze()
    # Sort by date
    sorted_dates = sorted(results.keys())
    final = []
    for d in sorted_dates:
        final.append({"date": d, "stats": results[d]})
    print(json.dumps(final, indent=2))
