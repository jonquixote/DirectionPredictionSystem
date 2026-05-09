import json
import os
import sys
from datetime import datetime, timezone, timedelta

# Ensure we can import from execution
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from execution.apfs.feature_confirmation import FeatureConfirmationFilter

def simulate():
    # Load predictions
    preds = {}
    resolutions = {}
    
    with open('predictions_h300.jsonl') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                d = json.loads(line)
            except: continue
            rt = d.get('record_type')
            if rt == 'prediction':
                pid = d.get('prediction_id')
                if pid: preds[pid] = d
            elif rt == 'resolution':
                pid = d.get('prediction_id')
                if pid: resolutions[pid] = d

    # Join and filter
    joined = []
    for pid, p in preds.items():
        if pid in resolutions:
            p['_won'] = resolutions[pid].get('prediction_correct', False)
            joined.append(p)
            
    # BTC only, non-warmup, 15-min boundaries
    valid_preds = []
    for p in joined:
        if p.get('symbol') != 'BTCUSDT': continue
        if p.get('warmup', False): continue
        ts = p.get('ts_contract_open_ms', 0)
        if (ts // 1000) % 900 != 0: continue
        valid_preds.append(p)
        
    # Sort by time
    valid_preds.sort(key=lambda x: x.get('ts_contract_open_ms', 0))
    
    if not valid_preds:
        print("No valid predictions found.")
        return
        
    max_ts = valid_preds[-1].get('ts_contract_open_ms', 0)
    
    windows = {
        '24h': max_ts - (24 * 3600 * 1000),
        '48h': max_ts - (48 * 3600 * 1000),
        '72h': max_ts - (72 * 3600 * 1000),
        'All-Time': 0
    }
    
    # Initialize APFS in a temporary directory
    import tempfile
    tmp_dir = tempfile.mkdtemp()
    apfs = FeatureConfirmationFilter(state_dir=tmp_dir, trade_threshold=0.52)
    
    results = {
        'Baseline (conf >= 0.52)': {w: {'trades': 0, 'wins': 0} for w in windows},
        'Baseline (conf >= 0.55)': {w: {'trades': 0, 'wins': 0} for w in windows},
        'APFS (Adaptive)': {w: {'trades': 0, 'wins': 0} for w in windows}
    }
    
    for p in valid_preds:
        ts = p.get('ts_contract_open_ms', 0)
        pred_proba = p.get('pred_proba', 0.5)
        pred_direction = p.get('pred_direction', 'up')
        side_conf = pred_proba if pred_direction == 'up' else (1.0 - pred_proba)
        won = p['_won']
        
        # Determine which windows this falls into
        active_windows = [w for w, start_ts in windows.items() if ts >= start_ts]
        
        # Baseline 0.52
        if side_conf >= 0.52:
            for w in active_windows:
                results['Baseline (conf >= 0.52)'][w]['trades'] += 1
                if won: results['Baseline (conf >= 0.52)'][w]['wins'] += 1
                
        # Baseline 0.55 (Old static gate)
        if side_conf >= 0.55:
            for w in active_windows:
                results['Baseline (conf >= 0.55)'][w]['trades'] += 1
                if won: results['Baseline (conf >= 0.55)'][w]['wins'] += 1
                
        # APFS
        decision = apfs.evaluate(
            model_name='h300',
            symbol='BTCUSDT',
            pred_direction=pred_direction,
            pred_proba=pred_proba,
            features=p.get('features', {})
        )
        
        if decision.should_trade:
            for w in active_windows:
                results['APFS (Adaptive)'][w]['trades'] += 1
                if won: results['APFS (Adaptive)'][w]['wins'] += 1
                
        # Always feed outcome to APFS to let it learn
        apfs.record_outcome(
            model_name='h300',
            symbol='BTCUSDT',
            pred_direction=pred_direction,
            pred_proba=pred_proba,
            features=p.get('features', {}),
            won=won
        )
        
    # Print results
    print(f"Total BTC 15-min predictions: {len(valid_preds)}")
    print("=" * 60)
    for w in ['24h', '48h', '72h', 'All-Time']:
        print(f"WINDOW: {w}")
        for strategy in results.keys():
            t = results[strategy][w]['trades']
            w_c = results[strategy][w]['wins']
            wr = (w_c / t * 100) if t > 0 else 0.0
            print(f"  {strategy:25s}: Trades: {t:4d} | Wins: {w_c:4d} | WR: {wr:5.1f}%")
        print("-" * 60)
        
if __name__ == '__main__':
    simulate()
