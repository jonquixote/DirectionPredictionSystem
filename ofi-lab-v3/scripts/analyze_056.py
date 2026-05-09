import json
from pathlib import Path

def analyze_directory(name, path_str):
    path = Path("/data") / path_str
    trades_files = list(path.glob("paper_trades_*.jsonl"))
    
    trades = {}
    for tf in trades_files:
        with open(tf, "r") as f:
            for line in f:
                if not line.strip(): continue
                try:
                    record = json.loads(line)
                    tid = record.get("trade_id")
                    if not tid: continue
                    if tid not in trades:
                        trades[tid] = {}
                    trades[tid].update(record)
                except:
                    pass
                    
    stats_056 = {}
    
    for tid, t in trades.items():
        # Only evaluate actual resolved trades that were originally taken
        if t.get("suppressed_reason"): continue
        result = t.get("trade_result")
        if not result: continue
        
        sym = t.get("symbol", "UNKNOWN")
        if sym not in stats_056:
            stats_056[sym] = {"wins": 0, "losses": 0, "pnl": 0.0, "total_original": 0}
            
        stats_056[sym]["total_original"] += 1
            
        # Calculate true confidence
        pred_proba = t.get("pred_proba", 0.0)
        direction = t.get("pred_direction")
        confidence = pred_proba if direction == "up" else (1.0 - pred_proba)
        
        # Retroactive 0.56 gate
        if confidence < 0.56:
            continue
            
        if result == "win":
            stats_056[sym]["wins"] += 1
            stats_056[sym]["pnl"] += t.get("net_pnl", 0.0)
        elif result == "loss":
            stats_056[sym]["losses"] += 1
            stats_056[sym]["pnl"] += t.get("net_pnl", 0.0)

    return stats_056

directories = {
    "Model A (Shifted)": "logs_model_a",
    "Model B (Extended)": "logs_model_b",
    "Model A V2 Clone": "logs_model_a_clone"
}

results = {}
for name, dir_name in directories.items():
    results[name] = analyze_directory(name, dir_name)

print("="*60)
print("     RETROACTIVE ANALYSIS: 0.56 CONFIDENCE GATE")
print("="*60)

for model_name, model_stats in results.items():
    print(f"\\n[ {model_name} ]")
    if not model_stats:
        print("  No trades found.")
        continue
        
    for sym, s in sorted(model_stats.items()):
        w = s['wins']
        l = s['losses']
        pnl = s['pnl']
        orig = s['total_original']
        wr = (w / (w + l)) * 100 if (w + l) > 0 else 0
        dropped = orig - (w + l)
        
        print(f"  > {sym:10} | {w:4}W - {l:4}L ({wr:5.1f}%) | Dropped: {dropped:4} trades | P&L: ${pnl:7.2f}")
