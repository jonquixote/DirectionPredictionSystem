import json
import os
from collections import defaultdict

def analyze_model(name, log_dir):
    pred_file = os.path.join(log_dir, "predictions_h300.jsonl")
    trade_file = os.path.join(log_dir, "paper_trades_h300.jsonl")
    
    preds_by_sym = defaultdict(int)
    
    if os.path.exists(pred_file):
        with open(pred_file) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    sym = r.get("symbol", "UNKNOWN")
                    preds_by_sym[sym] += 1
                except: pass
                
    trades = []
    if os.path.exists(trade_file):
        with open(trade_file) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("record_type") == "trade_resolution":
                        trades.append(r)
                except: pass
                
    wins = sum(1 for t in trades if t.get("trade_result") == "win")
    losses = sum(1 for t in trades if t.get("trade_result") == "loss")
    total_resolutions = wins + losses
    net_pnl = sum(t.get("net_pnl", 0) for t in trades)
    
    trades_by_sym = defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0.0})
    for t in trades:
        sym = t.get("symbol", "UNKNOWN")
        res = t.get("trade_result")
        if res == "win": trades_by_sym[sym]["w"] += 1
        elif res == "loss": trades_by_sym[sym]["l"] += 1
        trades_by_sym[sym]["pnl"] += t.get("net_pnl", 0)
    
    print("=" * 60)
    print(f"  {name}")
    print("=" * 60)
    
    total_preds = sum(preds_by_sym.values())
    print(f"  Total Predictions: {total_preds}")
    for sym, count in preds_by_sym.items():
        if sym != "UNKNOWN":
            print(f"    - {sym}: {count}")
        
    print(f"  Total Trades:      {total_resolutions}")
    
    if total_preds > 0:
        print(f"  Action Rate:       {(total_resolutions / total_preds * 100):.2f}%")
        
    if total_resolutions > 0:
        print(f"  Overall Win Rate:  {(wins / total_resolutions * 100):.1f}% ({wins}W / {losses}L)")
        print(f"  Overall Net P&L:   ${net_pnl:.2f}")
        print("\n  Breakdown by Symbol:")
        for sym, stats in trades_by_sym.items():
            t_total = stats['w'] + stats['l']
            wr = stats['w'] / t_total * 100 if t_total > 0 else 0
            print(f"    - {sym}: {t_total} trades ({stats['w']}W/{stats['l']}L) | Win Rate: {wr:.1f}% | P&L: ${stats['pnl']:.2f}")
    else:
        print("  No resolved trades yet.")
        
    print()

analyze_model("Model A (Shifted Window)", "/data/logs_model_a")
analyze_model("Model B (Extended Window)", "/data/logs_model_b")
analyze_model("Control (Original)", "/data/logs")
