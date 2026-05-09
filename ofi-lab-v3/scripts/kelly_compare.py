import json
from datetime import datetime, timezone

INITIAL_BANKROLL = 100.0
THRESHOLD = 0.52

pred_file = "/data/logs_model_a_clone/predictions_h300.jsonl"
predictions = {}
resolutions = {}

with open(pred_file) as f:
    for line in f:
        try:
            d = json.loads(line)
            pid = d.get("prediction_id")
            rt = d.get("record_type")
            if rt == "prediction" and d.get("symbol") == "BTCUSDT":
                predictions[pid] = d
            elif rt == "resolution":
                resolutions[pid] = d
        except:
            pass

sorted_preds = sorted(predictions.values(), key=lambda x: x.get("ts_model_ran_ms", 0))

for kelly_frac in [0.20, 0.50, 1.00]:
    bankroll = INITIAL_BANKROLL
    trades = 0
    wins = 0
    losses = 0
    stakes = []
    max_bank = INITIAL_BANKROLL
    min_bank = INITIAL_BANKROLL
    max_drawdown = 0
    
    for p in sorted_preds:
        pid = p.get("prediction_id")
        res = resolutions.get(pid)
        
        proba = p.get("pred_proba", 0.5)
        direction = p.get("pred_direction")
        conf = proba if direction == "up" else (1.0 - proba)
        
        if conf < THRESHOLD:
            continue
        if not res:
            continue
        is_correct = res.get("prediction_correct")
        if is_correct is None:
            continue
        
        p_market = p.get("p_market")
        if p_market is None or p_market <= 0 or p_market >= 1:
            p_market = 0.50
        p_market_adj = p_market if direction == "up" else (1.0 - p_market)
        
        edge = conf - p_market_adj
        if edge <= 0:
            stake = bankroll * 0.01
        else:
            odds = (1.0 - p_market_adj) / p_market_adj if p_market_adj < 1.0 else 99.0
            kelly_raw = edge / odds
            kelly_adj = min(max(kelly_raw * kelly_frac, 0), 1.0)
            stake = bankroll * kelly_adj
        
        stake = max(0.01, min(stake, bankroll))
        if stake < 0.10:
            continue
        
        pct_of_bank = (stake / bankroll) * 100
        stakes.append((stake, pct_of_bank))
        trades += 1
        
        if is_correct:
            wins += 1
            profit = (stake / p_market_adj) - stake
            bankroll += profit
        else:
            losses += 1
            bankroll -= stake
        
        if bankroll > max_bank:
            max_bank = bankroll
        if bankroll < min_bank:
            min_bank = bankroll
        dd = (max_bank - bankroll) / max_bank * 100
        if dd > max_drawdown:
            max_drawdown = dd
    
    avg_stake = sum(s[0] for s in stakes) / len(stakes) if stakes else 0
    avg_pct = sum(s[1] for s in stakes) / len(stakes) if stakes else 0
    min_stake = min(s[0] for s in stakes) if stakes else 0
    max_stake = max(s[0] for s in stakes) if stakes else 0
    min_pct = min(s[1] for s in stakes) if stakes else 0
    max_pct = max(s[1] for s in stakes) if stakes else 0
    
    wr = wins / trades * 100 if trades else 0
    pnl = bankroll - INITIAL_BANKROLL
    roi = (bankroll / INITIAL_BANKROLL - 1) * 100
    
    print(f"{'='*60}")
    print(f"  KELLY {kelly_frac*100:.0f}% — Gate 0.52 — Model A Clone")
    print(f"{'='*60}")
    print(f"  Trades:        {trades} ({wins}W / {losses}L)")
    print(f"  Win Rate:      {wr:.1f}%")
    print(f"  Final Bank:    ${bankroll:.2f}")
    print(f"  Net P&L:       ${pnl:+.2f} ({roi:+.1f}% ROI)")
    print(f"  Max Drawdown:  {max_drawdown:.1f}%")
    print(f"  ---")
    print(f"  Avg bet size:  ${avg_stake:.2f} ({avg_pct:.1f}% of bankroll)")
    print(f"  Min bet size:  ${min_stake:.2f} ({min_pct:.1f}% of bankroll)")
    print(f"  Max bet size:  ${max_stake:.2f} ({max_pct:.1f}% of bankroll)")
    print()
