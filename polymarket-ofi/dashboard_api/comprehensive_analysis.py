import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

LOG_DIR = "/data/logs"
MODELS = ["h60", "h300", "h60_v3"]

def analyze_model(model_name):
    # Mapping prediction_id -> metadata and resolution
    preds = {} # {id: {symbol, proba, direction, duration, correct}}
    trades = [] # List of finalized trades
    
    # 1. Parse Predictions
    pred_path = os.path.join(LOG_DIR, f"predictions_{model_name}.jsonl")
    if os.path.exists(pred_path):
        with open(pred_path, "r") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    rtype = data.get("record_type")
                    pid = data.get("prediction_id")
                    
                    if rtype == "prediction":
                        if data.get("warmup"): continue
                        preds[pid] = {
                            "symbol": data.get("symbol"),
                            "proba": data.get("pred_proba"),
                            "direction": data.get("pred_direction"),
                            "duration": 300, # Initial default for h60
                            "correct": None,
                            "ts": data.get("ts_model_ran_ms")
                        }
                    elif rtype == "resolution":
                        if pid in preds:
                            preds[pid]["correct"] = data.get("prediction_correct")
                except: continue

    # 2. Parse Trades
    trade_path = os.path.join(LOG_DIR, f"paper_trades_{model_name}.jsonl")
    if os.path.exists(trade_path):
        with open(trade_path, "r") as f:
            entry_cache = {} # trade_id -> entry_data
            for line in f:
                try:
                    data = json.loads(line)
                    rtype = data.get("record_type")
                    tid = data.get("trade_id")
                    pid = data.get("prediction_id")
                    
                    if rtype == "trade_entry":
                        # Even suppressed trades are recorded as entry to track potential savings
                        entry_cache[tid] = data
                        # If a prediction resulted in a trade, update duration
                        if pid in preds:
                            preds[pid]["duration"] = data.get("contract_duration_seconds", 300)
                    elif rtype == "trade_resolution":
                        if tid in entry_cache:
                            entry = entry_cache[tid]
                            trades.append({
                                "symbol": entry.get("symbol"),
                                "duration": entry.get("contract_duration_seconds", 300),
                                "correct": data.get("prediction_correct"),
                                "net_pnl": data.get("net_pnl", 0),
                                "proba": entry.get("pred_proba"),
                                "suppressed": entry.get("suppressed_reason") is not None,
                                "ts": entry.get("ts_model_ran_ms")
                            })
                except: continue

    # 3. Aggregate Predictions
    pred_stats = defaultdict(lambda: {"n": 0, "wins": 0, "avg_proba": 0, "sum_proba": 0})
    for p in preds.values():
        if p["correct"] is not None:
            key = (p["symbol"], p["duration"])
            s = pred_stats[key]
            s["n"] += 1
            if p["correct"]: s["wins"] += 1
            s["sum_proba"] += p["proba"]

    # 4. Aggregate Trades
    trade_stats = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0, "avg_proba": 0, "sum_proba": 0})
    for t in trades:
        if t["suppressed"]: continue
        key = (t["symbol"], t["duration"])
        s = trade_stats[key]
        s["n"] += 1
        if t["correct"]: s["wins"] += 1
        s["pnl"] += t["net_pnl"]
        s["sum_proba"] += t["proba"]

    # Finalize dictionaries for output
    return {
        "model": model_name,
        "prediction_accuracy": [
            {
                "symbol": k[0], "duration": k[1], 
                "n": v["n"], "accuracy": v["wins"]/v["n"] if v["n"] > 0 else 0,
                "avg_confidence": v["sum_proba"]/v["n"] if v["n"] > 0 else 0
            } for k, v in pred_stats.items()
        ],
        "trade_performance": [
            {
                "symbol": k[0], "duration": k[1], 
                "n": v["n"], "accuracy": v["wins"]/v["n"] if v["n"] > 0 else 0,
                "net_pnl_total": v["pnl"], "avg_confidence": v["sum_proba"]/v["n"] if v["n"] > 0 else 0
            } for k, v in trade_stats.items()
        ]
    }

print("RUNNING COMPREHENSIVE ANALYSIS...")
results = [analyze_model(m) for m in MODELS]
print(json.dumps(results, indent=2))
