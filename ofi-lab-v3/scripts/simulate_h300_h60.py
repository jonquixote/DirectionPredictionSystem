import json
import time

CONFIDENCE_THRESHOLD = 0.56
KELLY_FRACTION = 0.20
INITIAL_BANKROLL = 100.0
CUTOFF_MS = int(time.time() * 1000) - (4 * 24 * 3600 * 1000)

directories = {
    "Baseline": "/data/logs/",
    "Model A": "/data/logs_model_a/",
    "Model B": "/data/logs_model_b/",
    "Model A Clone": "/data/logs_model_a_clone/"
}

models = ["h300", "h60"]

def compute_stake(bankroll, conf, p_market_adjusted):
    edge = conf - p_market_adjusted
    if edge <= 0: return 0.0
    odds = (1.0 - p_market_adjusted) / p_market_adjusted if p_market_adjusted < 1.0 else 99.0
    optimal_f = edge / odds
    fraction = optimal_f * KELLY_FRACTION
    stake = bankroll * fraction
    return max(0.01, min(stake, bankroll))

print("===================================================================")
print(" 4-DAY KALSHI SIMULATION (BTCUSDT 900s) ")
print(" H300 vs H60 ")
print(f" Confidence Gate: >={CONFIDENCE_THRESHOLD}")
print(f" Kelly Fraction:  {KELLY_FRACTION*100}%")
print("===================================================================\n")

for name, d in directories.items():
    print(f"--- {name.upper()} ---")
    
    for mod in models:
        pred_file = f"{d}predictions_{mod}.jsonl"
        
        predictions = {}
        resolutions = {}
        
        try:
            with open(pred_file, 'r') as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        pid = data.get('prediction_id')
                        rtype = data.get('record_type')
                        
                        if rtype == 'prediction':
                            if data.get('symbol') != 'BTCUSDT': continue
                            if data.get('ts_model_ran_ms', 0) < CUTOFF_MS: continue
                            # Ensure we are evaluating for 900s horizon
                            predictions[pid] = data
                        elif rtype == 'resolution':
                            # In paper_trader, predictions are resolved at 300s and 900s.
                            # We want the 900s resolution. Since 900s comes after 300s in the file,
                            # overwriting resolutions[pid] will naturally capture the 900s resolution,
                            # EXCEPT we should be robust and check contract_duration_seconds if it exists.
                            # Oh wait, resolutions don't have duration in the JSON.
                            # They just overwrite. 
                            resolutions[pid] = data
                    except: pass
        except Exception as e:
            continue
            
        sorted_preds = sorted(predictions.values(), key=lambda x: x.get('ts_model_ran_ms', 0))
        
        bankroll = INITIAL_BANKROLL
        trades_taken = 0
        wins = 0
        losses = 0
        gross_pnl = 0.0
        
        for p in sorted_preds:
            pid = p.get('prediction_id')
            res = resolutions.get(pid)
            if not res: continue
            
            proba = p.get('pred_proba', 0.5)
            dir = p.get('pred_direction')
            conf = proba if dir == 'up' else (1.0 - proba)
            
            if conf < CONFIDENCE_THRESHOLD: continue
                
            p_market = p.get('p_market')
            if p_market is None or p_market <= 0 or p_market >= 1:
                p_market = 0.50
                
            p_market_adjusted = p_market if dir == 'up' else (1.0 - p_market)
            
            stake = compute_stake(bankroll, conf, p_market_adjusted)
            if stake < 0.10: continue 
            
            is_correct = res.get('prediction_correct')
            if is_correct is None: continue
            
            trades_taken += 1
            
            if is_correct:
                wins += 1
                gross_profit = (stake / p_market_adjusted) - stake
                bankroll += gross_profit
                gross_pnl += gross_profit
            else:
                losses += 1
                bankroll -= stake
                gross_pnl -= stake
                
        win_rate = wins / trades_taken if trades_taken > 0 else 0.0
        
        print(f"  {mod.ljust(5)} | Trades: {trades_taken:3} | {wins:2}W / {losses:2}L ({win_rate*100:5.1f}%) | P&L: ${gross_pnl:+7.2f} | Final: ${bankroll:6.2f}")
    
    print("")
