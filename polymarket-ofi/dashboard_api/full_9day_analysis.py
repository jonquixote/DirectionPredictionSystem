import json
import os
import sys
from collections import defaultdict

LOG_DIR = "/data/logs"
MODEL = "h300"

def analyze():
    preds = {} # pid -> {symbol, direction, proba, ts}
    resolutions = defaultdict(list) # pid -> [res_data]
    
    # 1. Parse Predictions
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

    # 2. Aggregator
    # Key: (symbol, duration)
    stats = defaultdict(lambda: {"n": 0, "wins": 0})

    # Join and Aggregate
    for pid, p in preds.items():
        res_list = resolutions.get(pid, [])
        if not res_list: continue
        
        symbol = p.get("symbol")
        ts_entry = p.get("ts_model_ran_ms")
        
        for r in res_list:
            dur_ms = r.get("ts_contract_close_ms") - ts_entry
            dur = round(dur_ms / 300000) * 300
            if dur not in [300, 900]: continue
            
            correct = r.get("prediction_correct")
            key = (symbol, dur)
            stats[key]["n"] += 1
            if correct: stats[key]["wins"] += 1

    # Format Results
    output = []
    for (sym, dur), v in sorted(stats.items()):
        output.append({
            "asset": sym,
            "duration": dur,
            "total_samples": v["n"],
            "accuracy": v["wins"] / v["n"] if v["n"] > 0 else 0
        })
    return output

print(json.dumps(analyze(), indent=2))
