#!/usr/bin/env python3
import json

for model, path in [("V1", "/data/logs/predictions_h60.jsonl"), ("H300", "/data/logs/predictions_h300.jsonl"), ("V3", "/data/logs/predictions_h60_v3.jsonl")]:
    try:
        preds = [json.loads(l) for l in open(path)]
    except FileNotFoundError:
        print(f"{model}: FILE NOT FOUND at {path}")
        continue
    live = [p for p in preds if p.get("record_type") == "prediction" and not p.get("warmup", False)]
    recent = sorted(live, key=lambda x: x.get("ts_model_ran_ms", 0))[-200:]
    for sym in ["SOLUSDT", "BTCUSDT", "ETHUSDT", "ALL"]:
        sub = recent if sym == "ALL" else [p for p in recent if p["symbol"] == sym]
        if not sub:
            continue
        up = sum(1 for p in sub if p["pred_direction"] == "up")
        divs = [abs(p.get("p_model_minus_market") or 0) for p in sub if p.get("p_market")]
        mean_div = sum(divs) / len(divs) if divs else None
        div_str = f"{mean_div:.4f}" if mean_div else "n/a"
        print(f"{model} {sym}: n={len(sub)}, up={up/len(sub)*100:.1f}%, mean|div|={div_str}")
    print()
