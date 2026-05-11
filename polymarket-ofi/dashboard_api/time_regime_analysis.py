import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

LOG_DIR = "/data/logs"
MODEL = "h300"
SYSTEM_FEE = 0.009

def compute_realized_net(direction, correct, p_market, fee=0.009):
    if p_market is None: return 0
    if direction == "up":
        return +(1 - p_market - fee) if correct else -(p_market + fee)
    else: # down
        return +(p_market - fee) if correct else -((1 - p_market) + fee)

def analyze():
    preds = {} # pid -> {symbol, direction, proba, ts, p_market}
    resolutions = defaultdict(list) # pid -> [res_data]
    
    # 1. Parse Predictions & Resolutions
    pred_path = os.path.join(LOG_DIR, f"predictions_{MODEL}.jsonl")
    if not os.path.exists(pred_path): return {"error": "No pred file"}
    
    with open(pred_path, "r") as f:
        for line in f:
            try:
                data = json.loads(line)
                rtype = data.get("record_type")
                pid = data.get("prediction_id")
                if rtype == "prediction":
                    if data.get("warmup"): continue
                    preds[pid] = data
                elif rtype == "resolution":
                    resolutions[pid].append(data)
            except: continue

    # 2. Aggregators
    # Key: (date_str, duration)
    daily = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
    # Key: (hour, duration)
    hourly = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
    
    # Simulation (BTC @ 50% gate, all durations)
    sim_btc = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})

    # Join and Aggregate
    for pid, p in preds.items():
        res_list = resolutions.get(pid, [])
        if not res_list: continue
        
        symbol = p.get("symbol")
        p_market = p.get("p_market", 0.5)
        direction = p.get("pred_direction")
        ts_entry = p.get("ts_model_ran_ms")
        
        # Determine if this was an actual trade to include in daily/hourly (we only report traded stats there)
        # For simplicity, we assume if it's in the resolution list, we should report it. 
        # But wait, the user wants the "h300 at 900s and 300s" comparison.
        
        dt = datetime.fromtimestamp(ts_entry / 1000, tz=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")
        hour = dt.hour
        
        for r in res_list:
            # Duration calculation
            dur_ms = r.get("ts_contract_close_ms") - ts_entry
            # Snap to closest multiple of 300s
            dur = round(dur_ms / 300000) * 300
            if dur not in [300, 900]: continue
            
            correct = r.get("prediction_correct")
            pnl = compute_realized_net(direction, correct, p_market)
            
            # 1. Daily (All Symbols)
            d_key = (date_str, dur)
            daily[d_key]["n"] += 1
            if correct: daily[d_key]["wins"] += 1
            daily[d_key]["pnl"] += pnl
            
            # 2. Hourly (All Symbols)
            h_key = (hour, dur)
            hourly[h_key]["n"] += 1
            if correct: hourly[h_key]["wins"] += 1
            hourly[h_key]["pnl"] += pnl
            
            # 3. BTC 50% Gate Simulation
            if symbol == "BTCUSDT":
                # Included if we assume 50% gate (trading everything not 0.5)
                # (Proba logic check)
                s_key = dur
                sim_btc[s_key]["n"] += 1
                if correct: sim_btc[s_key]["wins"] += 1
                sim_btc[s_key]["pnl"] += pnl

    # Formating Output
    output = {
        "daily": [
            {"date": k[0], "duration": k[1], "n": v["n"], "accuracy": v["wins"]/v["n"] if v["n"] > 0 else 0, "pnl": v["pnl"]}
            for k, v in sorted(daily.items())
        ],
        "hourly": [
            {"hour": k[0], "duration": k[1], "n": v["n"], "accuracy": v["wins"]/v["n"] if v["n"] > 0 else 0, "pnl_per_trade": v["pnl"]/v["n"] if v["n"] > 0 else 0}
            for k, v in sorted(hourly.items())
        ],
        "sim_btc_50_pct": [
            {"duration": k, "n": v["n"], "accuracy": v["wins"]/v["n"] if v["n"] > 0 else 0, "net_pnl": v["pnl"]}
            for k, v in sorted(sim_btc.items())
        ]
    }
    return output

print(json.dumps(analyze(), indent=2))
