import json
import glob

directories = {
    "Baseline": "/data/logs/",
    "Model A": "/data/logs_model_a/",
    "Model B": "/data/logs_model_b/",
    "Model A Clone (V2)": "/data/logs_model_a_clone/"
}

print("ALL-TIME BTCUSDT CONFIDENCE DISTRIBUTION\n" + "="*40)

for name, d in directories.items():
    pred_file = f"{d}predictions_h300.jsonl"
    
    total = 0
    gt_54 = 0
    gt_55 = 0
    gt_56 = 0
    gt_57 = 0
    
    try:
        with open(pred_file, 'r') as f:
            for line in f:
                if 'BTCUSDT' not in line: continue
                try:
                    data = json.loads(line)
                    if data.get('record_type') != 'prediction': continue
                    total += 1
                    
                    proba = data.get('pred_proba', 0.5)
                    dir = data.get('pred_direction')
                    conf = proba if dir == 'up' else (1.0 - proba)
                    
                    if conf >= 0.54: gt_54 += 1
                    if conf >= 0.55: gt_55 += 1
                    if conf >= 0.56: gt_56 += 1
                    if conf >= 0.57: gt_57 += 1
                except: pass
    except: pass
    
    print(f"--- {name} ---")
    print(f"Total BTC Predictions: {total}")
    print(f">= 0.54: {gt_54}")
    print(f">= 0.55: {gt_55}")
    print(f">= 0.56: {gt_56}")
    print(f">= 0.57: {gt_57}\n")
