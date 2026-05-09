import json
import datetime
from pathlib import Path
from collections import defaultdict

def get_session(dt):
    # UTC times
    hour = dt.hour
    if 14 <= hour < 21:
        return "US_Session"
    elif 1 <= hour < 9:
        return "Asian_Session"
    else:
        return "Euro_Off_Hours"

def get_time_of_day(dt):
    hour = dt.hour
    if 0 <= hour < 6: return "Late_Night (00-06)"
    elif 6 <= hour < 12: return "Morning (06-12)"
    elif 12 <= hour < 18: return "Afternoon (12-18)"
    else: return "Evening (18-24)"

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
                    
    # Aggregation
    # stats[symbol][session][tod][trend] = {"wins", "losses", "pnl", "active"}
    stats = defaultdict(lambda: defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0, "active": 0}))
    
    for tid, t in trades.items():
        if t.get("suppressed_reason"): continue
        
        sym = t.get("symbol", "UNKNOWN")
        ts_ms = t.get("ts_contract_open_ms")
        if not ts_ms: continue
        
        dt = datetime.datetime.fromtimestamp(ts_ms / 1000.0, tz=datetime.timezone.utc)
        session = get_session(dt)
        tod = get_time_of_day(dt)
        
        # Determine trend
        outcome = t.get("contract_result")
        if outcome:
            trend = f"Market_Went_{outcome.upper()}"
        else:
            trend = f"Active_Bet_{t.get('pred_direction', 'unknown').upper()}"
            
        key = (session, tod, trend)
        
        if t.get("trade_result") == "win":
            stats[sym][key]["wins"] += 1
            stats[sym][key]["pnl"] += t.get("net_pnl", 0.0)
        elif t.get("trade_result") == "loss":
            stats[sym][key]["losses"] += 1
            stats[sym][key]["pnl"] += t.get("net_pnl", 0.0)
        else:
            stats[sym][key]["active"] += 1

    return stats

directories = {
    "Baseline": "logs",
    "Model A": "logs_model_a",
    "Model B": "logs_model_b",
    "Model A V2 Clone": "logs_model_a_clone"
}

results = {}
for name, dir_name in directories.items():
    results[name] = analyze_directory(name, dir_name)

# Format the output clearly
for model_name, model_stats in results.items():
    print(f"\\n{'='*70}\\n[ {model_name} ]\\n{'='*70}")
    if not model_stats:
        print("No trades found.")
        continue
        
    for sym, sym_stats in model_stats.items():
        print(f"\\n--- Symbol: {sym} ---")
        total_wins = 0
        total_losses = 0
        total_pnl = 0.0
        
        # Flatten and sort keys
        for (session, tod, trend), counts in sorted(sym_stats.items()):
            w = counts['wins']
            l = counts['losses']
            pnl = counts['pnl']
            a = counts['active']
            
            total_wins += w
            total_losses += l
            total_pnl += pnl
            
            if w+l+a > 0:
                print(f"  {session:15} | {tod:17} | {trend:17} -> {w}W {l}L {a}A (P&L: ${pnl:.2f})")
                
        wr = (total_wins / (total_wins + total_losses)) * 100 if (total_wins + total_losses) > 0 else 0
        print(f"  TOTAL {sym}: {total_wins}W {total_losses}L | WinRate: {wr:.1f}% | P&L: ${total_pnl:.2f}")
