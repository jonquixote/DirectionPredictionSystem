#!/usr/bin/env python3
"""Debug H300 rolling-50 discrepancy between weekly report and checkpoint."""
import json
from datetime import datetime, timezone

# === Weekly report approach: uses paper_trades file, counts trade entries ===
trades_path = "/data/logs/paper_trades_h300.jsonl"
preds_path = "/data/logs/predictions_h300.jsonl"

trades = [json.loads(l) for l in open(trades_path)]
trade_entries = [t for t in trades if t.get("record_type") == "trade_entry"]
trade_resolutions = {t["trade_id"]: t for t in trades if t.get("record_type") == "trade_resolution"}

completed = []
for te in trade_entries:
    tid = te["trade_id"]
    if tid in trade_resolutions:
        tr = trade_resolutions[tid]
        merged = {**te, **tr}
        completed.append(merged)

completed.sort(key=lambda t: t.get("ts_model_ran_ms", 0))

# Show what the weekly report sees as last 50 trades
last_50_trades = completed[-50:]
correct = sum(1 for t in last_50_trades if t.get("prediction_correct"))
print(f"=== WEEKLY REPORT APPROACH (paper trades) ===")
print(f"Total completed paper trades: {len(completed)}")
print(f"Last 50 accuracy: {correct}/{len(last_50_trades)} = {correct/len(last_50_trades)*100:.1f}%")
print(f"Last trade time: {datetime.fromtimestamp(last_50_trades[-1]['ts_model_ran_ms']/1000, tz=timezone.utc)}")
print(f"First trade time: {datetime.fromtimestamp(last_50_trades[0]['ts_model_ran_ms']/1000, tz=timezone.utc)}")
print(f"Last 5 trade durations: {[t['contract_duration_seconds'] for t in last_50_trades[-5:]]}")
print()

# Break down by duration
for dur in [300, 900]:
    dur_trades = [t for t in last_50_trades if t.get("contract_duration_seconds") == dur]
    if dur_trades:
        dur_correct = sum(1 for t in dur_trades if t.get("prediction_correct"))
        print(f"  Duration {dur}s: {len(dur_trades)} trades, acc={dur_correct/len(dur_trades)*100:.1f}%")
print()

# === Checkpoint approach: uses predictions file, counts predictions ===
preds = [json.loads(l) for l in open(preds_path)]
by_id = {}
for r in preds:
    pid = r.get("prediction_id")
    if not pid: continue
    if pid not in by_id: by_id[pid] = {}
    by_id[pid].update(r)

resolved_preds = sorted(
    [v for v in by_id.values() if v.get("prediction_correct") is not None and not v.get("warmup", False)],
    key=lambda x: x.get("ts_model_ran_ms", 0)
)

last_50_preds = resolved_preds[-50:]
correct_p = sum(1 for p in last_50_preds if p["prediction_correct"])
print(f"=== CHECKPOINT APPROACH (predictions) ===")
print(f"Total resolved predictions: {len(resolved_preds)}")
print(f"Last 50 accuracy: {correct_p}/{len(last_50_preds)} = {correct_p/len(last_50_preds)*100:.1f}%")
print(f"Last pred time: {datetime.fromtimestamp(last_50_preds[-1]['ts_model_ran_ms']/1000, tz=timezone.utc)}")
print(f"First pred time: {datetime.fromtimestamp(last_50_preds[0]['ts_model_ran_ms']/1000, tz=timezone.utc)}")
print()
print(f"Ratio: {len(completed)} trades / {len(resolved_preds)} predictions = {len(completed)/len(resolved_preds):.1f}x")
print("(2x is expected since each prediction fires both 300s and 900s trades)")
