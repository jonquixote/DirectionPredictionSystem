import json
from pathlib import Path

def analyze_directory(name, path_str):
    path = Path("/data") / path_str
    trades_files = list(path.glob("paper_trades_*.jsonl"))
    
    # Trade deduplication (resolution merges)
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
                    
    # Aggregation: stats[symbol] = {"wins", "losses", "pnl", "active"}
    stats = {}
    
    for tid, t in trades.items():
        if t.get("suppressed_reason"): continue
        
        sym = t.get("symbol", "UNKNOWN")
        if sym not in stats:
            stats[sym] = {"wins": 0, "losses": 0, "pnl": 0.0, "active": 0}
            
        result = t.get("trade_result")
        if result == "win":
            stats[sym]["wins"] += 1
            stats[sym]["pnl"] += t.get("net_pnl", 0.0)
        elif result == "loss":
            stats[sym]["losses"] += 1
            stats[sym]["pnl"] += t.get("net_pnl", 0.0)
        else:
            stats[sym]["active"] += 1

    return stats

directories = {
    "Baseline": "logs",
    "Model A (Shifted)": "logs_model_a",
    "Model B (Extended)": "logs_model_b",
    "Model A V2 Clone (Filtered)": "logs_model_a_clone"
}

results = {}
for name, dir_name in directories.items():
    results[name] = analyze_directory(name, dir_name)

print("="*60)
print("              UPDATED TRADING SUMMARY")
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
        a = s['active']
        wr = (w / (w + l)) * 100 if (w + l) > 0 else 0
        
        print(f"  > {sym:10} | {w:4}W - {l:4}L ({wr:5.1f}%) | Active: {a:2} | P&L: ${pnl:7.2f}")
