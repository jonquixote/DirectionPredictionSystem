import json
from pathlib import Path

def get_trades(path_str):
    path = Path("/data") / path_str
    trades_files = list(path.glob("paper_trades_*.jsonl"))
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
    
    # Extract only BTCUSDT resolved trades
    btc_trades = {}
    for tid, t in trades.items():
        if t.get("suppressed_reason"): continue
        if t.get("symbol") != "BTCUSDT": continue
        if not t.get("trade_result"): continue
        
        ts = t.get("ts_contract_open_ms")
        btc_trades[ts] = t
    return btc_trades

model_a = get_trades("logs_model_a")
v2_clone = get_trades("logs_model_a_clone")

all_ts = sorted(list(set(model_a.keys()) | set(v2_clone.keys())))

print(f"{'Time (ms)':<15} | {'Model A Result':<25} | {'V2 Clone Result':<25}")
print("-" * 70)
for ts in all_ts:
    a = model_a.get(ts, {})
    v = v2_clone.get(ts, {})
    
    a_str = f"{a.get('trade_result', 'MISSING')} (p={a.get('pred_proba', 0):.3f} {a.get('pred_direction', '')})" if a else "MISSING"
    v_str = f"{v.get('trade_result', 'MISSING')} (p={v.get('pred_proba', 0):.3f} {v.get('pred_direction', '')})" if v else "MISSING"
    
    if a_str != v_str:
        print(f"{ts:<15} | {a_str:<25} | {v_str:<25}")
