import json
import glob

def get_all_prices():
    prices = {}
    files = glob.glob("/data/*/paper_trades_*.jsonl")
    for f in files:
        with open(f, 'r') as file:
            for line in file:
                if not line.strip(): continue
                try:
                    data = json.loads(line)
                    if data.get("symbol") != "BTCUSDT": continue
                    
                    if "ts_contract_open_ms" in data and "price_at_contract_open" in data:
                        prices[data["ts_contract_open_ms"]] = data["price_at_contract_open"]
                        
                    if "ts_contract_close_ms" in data and "price_at_contract_close" in data:
                        prices[data["ts_contract_close_ms"]] = data["price_at_contract_close"]
                except Exception:
                    pass
    return prices

def analyze(directory, global_prices):
    files = glob.glob(f"{directory}/paper_trades_*.jsonl")
    predictions = []
    
    for f in files:
        with open(f, 'r') as file:
            for line in file:
                if not line.strip(): continue
                try:
                    data = json.loads(line)
                    if data.get("symbol") != "BTCUSDT": continue
                    if data.get("model") == "h300" and data.get("contract_duration_seconds") == 900:
                        if data.get("record_type") == "trade_entry" or data.get("record_type") == "prediction":
                            predictions.append(data)
                except Exception:
                    pass
                    
    stats = {
        "ALL": {"total": 0, "win_300": 0, "loss_300": 0, "win_900": 0, "loss_900": 0},
        "GATED_055": {"total": 0, "win_300": 0, "loss_300": 0, "win_900": 0, "loss_900": 0},
        "GATED_056": {"total": 0, "win_300": 0, "loss_300": 0, "win_900": 0, "loss_900": 0}
    }
    
    seen_ts = set()
    
    for p in predictions:
        ts = p.get("ts_contract_open_ms")
        if not ts or ts in seen_ts: continue
        seen_ts.add(ts)
        
        start_price = p.get("price_at_contract_open")
        if not start_price:
            start_price = global_prices.get(ts)
        if not start_price: continue
        
        pred_dir = p.get("pred_direction")
        prob = p.get("pred_proba", 0.0)
        conf = prob if pred_dir == "up" else (1.0 - prob)
        
        ts_300 = ts + 300000
        ts_900 = ts + 900000
        
        price_300 = global_prices.get(ts_300)
        price_900 = global_prices.get(ts_900)
        
        if price_300 and price_900:
            dir_300 = "up" if price_300 > start_price else ("down" if price_300 < start_price else "flat")
            dir_900 = "up" if price_900 > start_price else ("down" if price_900 < start_price else "flat")
            
            win_300 = 1 if pred_dir == dir_300 else 0
            loss_300 = 1 if pred_dir != dir_300 and dir_300 != "flat" else 0
            
            win_900 = 1 if pred_dir == dir_900 else 0
            loss_900 = 1 if pred_dir != dir_900 and dir_900 != "flat" else 0
            
            def add_stats(cat):
                stats[cat]["total"] += 1
                stats[cat]["win_300"] += win_300
                stats[cat]["loss_300"] += loss_300
                stats[cat]["win_900"] += win_900
                stats[cat]["loss_900"] += loss_900
                
            add_stats("ALL")
            if conf >= 0.55:
                add_stats("GATED_055")
            if conf >= 0.56:
                add_stats("GATED_056")
                
    return stats

dirs = {
    "Model A (Shifted)": "/data/logs_model_a",
    "Model A V2 Clone": "/data/logs_model_a_clone"
}

global_prices = get_all_prices()

print("="*70)
print("     DEEP DIVE: 300s (5m) vs 900s (15m) HORIZON on BTCUSDT")
print("="*70)

for name, d in dirs.items():
    stats = analyze(d, global_prices)
    print(f"\\n[ {name} ]")
    
    for cat in ["ALL", "GATED_055", "GATED_056"]:
        s = stats[cat]
        tot = s['total']
        if tot == 0: continue
        w3 = s['win_300']
        l3 = s['loss_300']
        w9 = s['win_900']
        l9 = s['loss_900']
        
        wr3 = w3/(w3+l3)*100 if (w3+l3) > 0 else 0
        wr9 = w9/(w9+l9)*100 if (w9+l9) > 0 else 0
        
        cat_str = f"Category: {cat}".ljust(22)
        print(f"  {cat_str} | Total Samples: {tot}")
        print(f"    -> At +300s (5m):  {w3:3}W - {l3:3}L ({wr3:5.1f}%)")
        print(f"    -> At +900s (15m): {w9:3}W - {l9:3}L ({wr9:5.1f}%)")
        print(f"    -> Edge Difference (15m vs 5m): {wr9 - wr3:+.1f}%")
        print("-" * 50)
