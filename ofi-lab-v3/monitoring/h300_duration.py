#!/usr/bin/env python3
"""Analyze H300 300s vs 900s contract outcomes."""
import json
from collections import defaultdict
from datetime import datetime, timezone

trades_path = "/data/logs/paper_trades_h300.jsonl"
trades = [json.loads(l) for l in open(trades_path)]

entries = [t for t in trades if t.get("record_type") == "trade_entry" and not t.get("suppressed_reason")]
resolutions = {t["trade_id"]: t for t in trades if t.get("record_type") == "trade_resolution"}

# Group completed trades by prediction_id and duration
by_pred = defaultdict(dict)
for te in entries:
    tid = te["trade_id"]
    if tid not in resolutions:
        continue
    tr = resolutions[tid]
    merged = {**te, **tr}
    dur = te["contract_duration_seconds"]
    by_pred[te["prediction_id"]][dur] = merged

# === Task 2: exact n for 300s ===
all_300 = []
all_900 = []
for pid, durations in by_pred.items():
    if 300 in durations:
        all_300.append(durations[300])
    if 900 in durations:
        all_900.append(durations[900])

print("=== H300 by contract duration (ALL completed trades) ===")
for label, subset in [("300s", all_300), ("900s", all_900)]:
    if not subset:
        print(f"  {label}: no trades")
        continue
    n = len(subset)
    correct = sum(1 for t in subset if t.get("prediction_correct"))
    print(f"  {label}: n={n}, acc={correct/n*100:.1f}% ({correct}/{n})")

# Last 50 trades per duration (from weekly report's perspective)
for label, subset in [("300s (last 25)", sorted(all_300, key=lambda t: t["ts_model_ran_ms"])[-25:]),
                       ("900s (last 25)", sorted(all_900, key=lambda t: t["ts_model_ran_ms"])[-25:])]:
    if not subset:
        continue
    n = len(subset)
    correct = sum(1 for t in subset if t.get("prediction_correct"))
    first_ts = datetime.fromtimestamp(subset[0]["ts_model_ran_ms"] / 1000, tz=timezone.utc)
    last_ts = datetime.fromtimestamp(subset[-1]["ts_model_ran_ms"] / 1000, tz=timezone.utc)
    print(f"  {label}: acc={correct/n*100:.1f}% ({correct}/{n}), range={first_ts.strftime('%m-%d %H:%M')} to {last_ts.strftime('%m-%d %H:%M')}")

# === Task 3: 300s vs 900s outcome agreement ===
print("\n=== 300s vs 900s OUTCOME AGREEMENT ===")

both = []
for pid, durations in by_pred.items():
    if 300 in durations and 900 in durations:
        both.append((durations[300], durations[900]))

if not both:
    print("  No prediction events with both 300s and 900s resolved")
else:
    n = len(both)
    same = 0
    diff_300_wrong = 0  # 300s wrong, 900s right
    diff_900_wrong = 0  # 300s right, 900s wrong
    both_wrong = 0
    both_right = 0
    for t300, t900 in both:
        c300 = t300.get("prediction_correct", False)
        c900 = t900.get("prediction_correct", False)
        if c300 == c900:
            same += 1
            if c300:
                both_right += 1
            else:
                both_wrong += 1
        else:
            if c300 and not c900:
                diff_900_wrong += 1
            else:
                diff_300_wrong += 1

    print(f"  Total prediction events with both durations: {n}")
    print(f"  Same outcome: {same}/{n} ({same/n*100:.1f}%)")
    print(f"    Both right:  {both_right}")
    print(f"    Both wrong:  {both_wrong}")
    print(f"  Different outcome: {n-same}/{n} ({(n-same)/n*100:.1f}%)")
    print(f"    300s wrong, 900s right: {diff_300_wrong}")
    print(f"    300s right, 900s wrong: {diff_900_wrong}")
    print(f"  300s accuracy: {(both_right+diff_900_wrong)/n*100:.1f}%")
    print(f"  900s accuracy: {(both_right+diff_300_wrong)/n*100:.1f}%")
    print(f"  If 300s suppressed, 900s-only accuracy: {(both_right+diff_300_wrong)/n*100:.1f}%")
