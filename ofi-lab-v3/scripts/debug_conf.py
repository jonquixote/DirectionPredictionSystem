import json
import time

CONFIDENCE_THRESHOLD = 0.56
CUTOFF_MS = int(time.time() * 1000) - (4 * 24 * 3600 * 1000)

directories = {
    "Baseline": "/data/logs/",
    "Model A": "/data/logs_model_a/",
    "Model B": "/data/logs_model_b/",
    "Model A Clone (V2)": "/data/logs_model_a_clone/"
}

for name, d in directories.items():
    pred_file = f"{d}predictions_h300.jsonl"
    max_conf = 0.0
    total = 0
    valid_time = 0
    with open(pred_file, 'r') as f:
        for line in f:
            if 'BTCUSDT' not in line: continue
            try:
                data = json.loads(line)
                if data.get('record_type') != 'prediction': continue
                total += 1
                if data.get('ts_model_ran_ms', 0) >= CUTOFF_MS:
                    valid_time += 1
                    proba = data.get('pred_proba', 0.5)
                    dir = data.get('pred_direction')
                    conf = proba if dir == 'up' else (1.0 - proba)
                    if conf > max_conf: max_conf = conf
            except: pass
    print(f"{name}: Total BTC= {total}, Recent BTC= {valid_time}, Max Conf= {max_conf:.4f}")
