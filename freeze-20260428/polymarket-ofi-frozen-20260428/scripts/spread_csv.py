import json, csv
from datetime import datetime, timezone

preds = [json.loads(l) for l in open("/data/logs/predictions_h60.jsonl")]
pred_preds = [p for p in preds if p.get("record_type") == "prediction"]
has_rs = sum(1 for p in pred_preds if "relative_spread" in p.get("features", {}))
print(f"Predictions with relative_spread in features: {has_rs}/{len(pred_preds)}")
if has_rs > 0:
    sample = next(p for p in pred_preds if "relative_spread" in p.get("features", {}))
    print(f"Sample: {sample['features']['relative_spread']}")

records = [json.loads(l) for l in open("/data/logs/paper_trades_h60.jsonl")]
by_tid = {}
for r in records:
    tid = r.get("trade_id")
    if tid:
        by_tid.setdefault(tid, {}).update(r)
completed = [t for t in by_tid.values() if t.get("prediction_correct") is not None and not t.get("warmup", False)]
completed.sort(key=lambda t: t.get("ts_model_ran_ms", 0))

pred_by_id = {}
for p in pred_preds:
    pid = p.get("prediction_id")
    if pid:
        pred_by_id[pid] = p

rows = []
for t in completed:
    pred = pred_by_id.get(t.get("prediction_id"), {})
    features = pred.get("features", {})
    ts_ms = t.get("ts_model_ran_ms", 0)
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    op = t.get("price_at_contract_open", 0)
    cl = t.get("price_at_contract_close", 0)
    actual = "up" if cl > op else ("down" if cl < op else "flat")
    correct = t.get("pred_direction") == actual
    rows.append([t.get("trade_id"), t.get("symbol"), features.get("relative_spread"),
                 correct, dt.hour, dt.weekday(), ts_ms])

with_rs = sum(1 for r in rows if r[2] is not None)
print(f"Trades with relative_spread: {with_rs}/{len(rows)}")

with open("/data/logs/spread_analysis.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["trade_id","symbol","relative_spread","prediction_correct","utc_hour","day_of_week","ts_model_ran_ms"])
    w.writerows(rows)
print(f"Wrote {len(rows)} rows to /data/logs/spread_analysis.csv")
for r in rows[:3]:
    print(f"  {str(r[0])[:12]} sym={r[1]} rs={r[2]} correct={r[3]} hr={r[4]}")
