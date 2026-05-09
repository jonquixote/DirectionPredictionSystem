import json
from pathlib import Path

def analyze_model_a():
    path = Path("/data/logs_model_a/paper_trades_h300.jsonl")
    
    trades = {}
    with open(path, "r") as f:
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
                
    # We want to compare two scenarios for SOLUSDT 900s with confidence >= 0.55:
    # 1. ALL bets (both up and down)
    # 2. ONLY 'down' bets

    scenario_all = {"wins": 0, "losses": 0, "pnl": 0.0}
    scenario_down = {"wins": 0, "losses": 0, "pnl": 0.0}

    for tid, t in trades.items():
        # Only look at SOLUSDT 900s
        if t.get("symbol") != "SOLUSDT": continue
        if t.get("contract_duration_seconds") != 900: continue
        
        # Only look at resolved trades
        result = t.get("trade_result")
        if not result: continue
        
        # Determine actual confidence (some might have been filtered, but we want to simulate threshold = 0.55)
        # Note: Model A ran without 0.55 threshold, so we apply it retroactively to the raw logs
        pred_proba = t.get("pred_proba", 0.0)
        direction = t.get("pred_direction")
        
        confidence = pred_proba if direction == "up" else (1.0 - pred_proba)
        
        if confidence < 0.55:
            continue
            
        pnl = t.get("net_pnl", 0.0)
        
        # Scenario 1: ALL bets
        if result == "win":
            scenario_all["wins"] += 1
            scenario_all["pnl"] += pnl
        elif result == "loss":
            scenario_all["losses"] += 1
            scenario_all["pnl"] += pnl
            
        # Scenario 2: ONLY DOWN bets
        if direction == "down":
            if result == "win":
                scenario_down["wins"] += 1
                scenario_down["pnl"] += pnl
            elif result == "loss":
                scenario_down["losses"] += 1
                scenario_down["pnl"] += pnl

    print("=== SCENARIO 1: All SOL 900s Bets (Confidence >= 0.55) ===")
    w_all = scenario_all["wins"]
    l_all = scenario_all["losses"]
    wr_all = (w_all / (w_all + l_all)) * 100 if (w_all + l_all) > 0 else 0
    print(f"Record: {w_all}W - {l_all}L")
    print(f"Win Rate: {wr_all:.1f}%")
    print(f"P&L: ${scenario_all['pnl']:.2f}\\n")
    
    print("=== SCENARIO 2: ONLY 'Down' SOL 900s Bets (Confidence >= 0.55) ===")
    w_down = scenario_down["wins"]
    l_down = scenario_down["losses"]
    wr_down = (w_down / (w_down + l_down)) * 100 if (w_down + l_down) > 0 else 0
    print(f"Record: {w_down}W - {l_down}L")
    print(f"Win Rate: {wr_down:.1f}%")
    print(f"P&L: ${scenario_down['pnl']:.2f}")

analyze_model_a()
