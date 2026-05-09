#!/usr/bin/env python3
"""Analyze last 24h vs previous performance and detect restart gaps."""
import json
from datetime import datetime, timezone, timedelta

records = [json.loads(l) for l in open("/data/logs/predictions_h60.jsonl")]

by_id = {}
for r in records:
    pid = r.get("prediction_id")
    if not pid:
        continue
    if pid not in by_id:
        by_id[pid] = {}
    by_id[pid].update(r)

merged = [v for v in by_id.values()
          if v.get("prediction_correct") is not None and not v.get("warmup", False)]

now = datetime.now(timezone.utc)
cutoff_24h = int((now - timedelta(hours=24)).timestamp() * 1000)
cutoff_48h = int((now - timedelta(hours=48)).timestamp() * 1000)

last_24h = [p for p in merged if p.get("ts_model_ran_ms", 0) >= cutoff_24h]
prev_24h = [p for p in merged if cutoff_48h <= p.get("ts_model_ran_ms", 0) < cutoff_24h]
before_that = [p for p in merged if p.get("ts_model_ran_ms", 0) < cutoff_48h]

for label, subset in [("Before 48h ago", before_that), ("24-48h ago", prev_24h), ("Last 24h", last_24h)]:
    if not subset:
        print(f"{label}: no data")
        continue
    n = len(subset)
    correct = sum(1 for p in subset if p["prediction_correct"])
    acc = correct / n * 100
    up = sum(1 for p in subset if p["pred_direction"] == "up")
    down = n - up

    ts_min = min(p["ts_model_ran_ms"] for p in subset)
    ts_max = max(p["ts_model_ran_ms"] for p in subset)
    dt_min = datetime.fromtimestamp(ts_min / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
    dt_max = datetime.fromtimestamp(ts_max / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")

    print(f"{label} ({dt_min} -> {dt_max}):")
    print(f"  n={n}  acc={acc:.1f}%  up={up} down={down} ({down/n*100:.0f}% down)")

    syms = sorted(set(p["symbol"] for p in subset))
    for sym in syms:
        s = [p for p in subset if p["symbol"] == sym]
        sc = sum(1 for p in s if p["prediction_correct"])
        su = sum(1 for p in s if p["pred_direction"] == "up")
        print(f"    {sym}: n={len(s)} acc={sc/len(s)*100:.1f}% up={su} down={len(s)-su}")
    print()

# Restart gaps
print("=== Restart gaps (>10min between predictions) ===")
all_preds = sorted(
    [v for v in by_id.values() if not v.get("warmup", False) and v.get("ts_model_ran_ms")],
    key=lambda x: x["ts_model_ran_ms"],
)
for i in range(1, len(all_preds)):
    gap_ms = all_preds[i]["ts_model_ran_ms"] - all_preds[i - 1]["ts_model_ran_ms"]
    if gap_ms > 600000:
        t1 = datetime.fromtimestamp(all_preds[i - 1]["ts_model_ran_ms"] / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
        t2 = datetime.fromtimestamp(all_preds[i]["ts_model_ran_ms"] / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
        print(f"  Gap: {t1} -> {t2}  ({gap_ms/60000:.0f} min)")
