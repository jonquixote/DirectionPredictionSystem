import json
import time

# Simulation Parameters
CONFIDENCE_THRESHOLD = 0.56
KELLY_FRACTION = 0.20
INITIAL_BANKROLL = 100.0
CUTOFF_MS = int(time.time() * 1000) - (4 * 24 * 3600 * 1000)

directories = {
    "Baseline": "/data/logs/",
    "Model A": "/data/logs_model_a/",
    "Model B": "/data/logs_model_b/",
    "Model A Clone (V2)": "/data/logs_model_a_clone/"
}

def compute_stake(bankroll, conf, p_market_adjusted):
    edge = conf - p_market_adjusted
    if edge <= 0: return 0.0
    odds = (1.0 - p_market_adjusted) / p_market_adjusted if p_market_adjusted < 1.0 else 99.0
    optimal_f = edge / odds
    fraction = optimal_f * KELLY_FRACTION
    stake = bankroll * fraction
    return max(0.01, min(stake, bankroll))

def simulate_container(name, log_dir):
    pred_file = f"{log_dir}predictions_h300.jsonl"
    
    predictions = {}
    resolutions = {}
    
    try:
        with open(pred_file, 'r') as f:
            for line in f:
                try:
                    d = json.loads(line)
                    pid = d.get('prediction_id')
                    rtype = d.get('record_type')
                    
                    if rtype == 'prediction':
                        if d.get('symbol') != 'BTCUSDT': continue
                        if d.get('ts_model_ran_ms', 0) < CUTOFF_MS: continue
                        predictions[pid] = d
                    elif rtype == 'resolution':
                        resolutions[pid] = d
                except: pass
    except Exception as e:
        print(f"[{name}] Could not read {pred_file}: {e}")
        return
        
    sorted_preds = sorted(predictions.values(), key=lambda x: x.get('ts_model_ran_ms', 0))
    
    bankroll = INITIAL_BANKROLL
    trades_taken = 0
    wins = 0
    losses = 0
    gross_pnl = 0.0
    
    for p in sorted_preds:
        pid = p.get('prediction_id')
        res = resolutions.get(pid)
        if not res: continue # unresolved
        
        proba = p.get('pred_proba', 0.5)
        dir = p.get('pred_direction')
        conf = proba if dir == 'up' else (1.0 - proba)
        
        if conf < CONFIDENCE_THRESHOLD:
            continue
            
        p_market = p.get('p_market')
        if p_market is None or p_market <= 0 or p_market >= 1:
            p_market = 0.50
            
        # Adjust p_market to match the direction of the bet
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
        
    print(f"--- {name.upper()} ---")
    print(f"Trades Taken: {trades_taken}")
    if trades_taken > 0:
        print(f"Win Rate:     {win_rate*100:.1f}% ({wins}W / {losses}L)")
        print(f"Net P&L:      ${gross_pnl:.2f}")
        print(f"Final Bank:   ${bankroll:.2f} ({((bankroll/INITIAL_BANKROLL)-1)*100:+.1f}%)")
    print("")

print("===================================================================")
print(" 4-DAY KALSHI SIMULATION (BTCUSDT 900s) ")
print(f" Confidence Gate: >={CONFIDENCE_THRESHOLD}")
print(f" Kelly Fraction:  {KELLY_FRACTION*100}%")
print(f" Initial Bank:    ${INITIAL_BANKROLL}")
print("===================================================================\n")

for name, d in directories.items():
    simulate_container(name, d)
