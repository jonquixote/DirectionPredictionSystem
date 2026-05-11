#!/usr/bin/env python3
"""H300 900s deployment evaluation — full analysis."""
import json
from collections import defaultdict
from datetime import datetime, timezone

FEE = 0.009
STAKE = 10.0

trades_path = "/data/logs/paper_trades_h300.jsonl"
preds_path = "/data/logs/predictions_h300.jsonl"

# Load and merge trades
trades = [json.loads(l) for l in open(trades_path)]
entries = [t for t in trades if t.get("record_type") == "trade_entry"
           and t.get("contract_duration_seconds") == 900
           and not t.get("suppressed_reason")]
resolutions = {t["trade_id"]: t for t in trades if t.get("record_type") == "trade_resolution"}

completed = []
for te in entries:
    tid = te["trade_id"]
    if tid in resolutions:
        completed.append({**te, **resolutions[tid]})

completed.sort(key=lambda t: t.get("ts_model_ran_ms", 0))

print("=" * 65)
print("H300 900s DEPLOYMENT EVALUATION")
print("=" * 65)

# ═══════════════════════════════════════════════════════════
# 1. Overall realized NE_t
# ═══════════════════════════════════════════════════════════
print("\n── 1. OVERALL REALIZED NE_t ──")

n = len(completed)
correct = sum(1 for t in completed if t.get("prediction_correct"))
accuracy = correct / n if n else 0

ne_t_vals = []
with_pm = 0
for t in completed:
    pm = t.get("p_market")
    if pm is None:
        continue
    with_pm += 1
    won = t.get("prediction_correct", False)
    if t["pred_direction"] == "up":
        pnl = (1 - pm - FEE) * STAKE if won else -(pm + FEE) * STAKE
    else:
        pnl = (pm - FEE) * STAKE if won else -((1 - pm) + FEE) * STAKE
    ne_t_vals.append(pnl)

ne_total = sum(ne_t_vals)
ne_per = ne_total / len(ne_t_vals) if ne_t_vals else 0

print(f"  Total 900s trades:     {n}")
print(f"  With p_market:         {with_pm}")
print(f"  Accuracy:              {accuracy*100:.1f}% ({correct}/{n})")
print(f"  Realized NE_t total:   ${ne_total:.2f}")
print(f"  Realized NE_t/trade:   ${ne_per:.4f}")
print(f"  Annualized (6/hr):     ${ne_per * 6 * 24 * 365:.0f}/yr" if ne_per else "")

# ═══════════════════════════════════════════════════════════
# 2. Per-symbol accuracy
# ═══════════════════════════════════════════════════════════
print("\n── 2. PER-SYMBOL ACCURACY (900s) ──")

by_sym = defaultdict(list)
sym_ne = defaultdict(list)
for t in completed:
    sym = t.get("symbol", "?")
    by_sym[sym].append(t.get("prediction_correct", False))
    pm = t.get("p_market")
    if pm is not None:
        won = t.get("prediction_correct", False)
        if t["pred_direction"] == "up":
            pnl = (1 - pm - FEE) * STAKE if won else -(pm + FEE) * STAKE
        else:
            pnl = (pm - FEE) * STAKE if won else -((1 - pm) + FEE) * STAKE
        sym_ne[sym].append(pnl)

for sym in sorted(by_sym.keys()):
    vals = by_sym[sym]
    c = sum(vals)
    n_s = len(vals)
    ne_s = sym_ne.get(sym, [])
    ne_avg = sum(ne_s) / len(ne_s) if ne_s else 0
    print(f"  {sym}: n={n_s}, acc={c/n_s*100:.1f}%, NE_t/trade=${ne_avg:.4f}")

# ═══════════════════════════════════════════════════════════
# 3. Rolling-50 gate check
# ═══════════════════════════════════════════════════════════
print("\n── 3. ROLLING-50 GATE CHECK (900s) ──")

correct_arr = [1 if t.get("prediction_correct") else 0 for t in completed]

if len(correct_arr) >= 50:
    rolling = []
    for i in range(50, len(correct_arr) + 1):
        window_acc = sum(correct_arr[i-50:i]) / 50
        rolling.append(window_acc)

    latest = rolling[-1]
    min_r = min(rolling)
    max_r = max(rolling)

    # Batches of 50
    print(f"  Rolling-50 windows:    {len(rolling)}")
    print(f"  Latest:                {latest*100:.1f}%")
    print(f"  Min:                   {min_r*100:.1f}%")
    print(f"  Max:                   {max_r*100:.1f}%")
    print(f"  Gate (≥51.5%):         {'✅ PASS' if latest >= 0.515 else '❌ FAIL'}")

    # Print batch breakdown
    batch_size = 50
    for batch_start in range(0, len(correct_arr), batch_size):
        batch = correct_arr[batch_start:batch_start + batch_size]
        if not batch:
            break
        batch_acc = sum(batch) / len(batch)
        marker = " ← current" if batch_start + len(batch) >= len(correct_arr) else ""
        print(f"    Batch {batch_start//batch_size + 1} (trades {batch_start+1}–{batch_start+len(batch)}): "
              f" {batch_acc*100:.1f}%{marker}")

    # Declining check
    if len(rolling) >= 5:
        recent_5 = rolling[-5:]
        declining = all(recent_5[i] <= recent_5[i-1] for i in range(1, len(recent_5)))
        print(f"  Declining (last 5):    {'⚠️  YES' if declining else '✅ NO'}")
else:
    print(f"  Only {len(correct_arr)} trades — need 50 for rolling window")

# ═══════════════════════════════════════════════════════════
# 4. Accuracy by time period (weekly buckets)
# ═══════════════════════════════════════════════════════════
print("\n── 4. ACCURACY BY TIME PERIOD (weekly buckets) ──")

by_week = defaultdict(list)
by_week_ne = defaultdict(list)
for t in completed:
    ts = t.get("ts_model_ran_ms", 0)
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    # ISO week
    week_key = dt.strftime("%Y-W%V")
    by_week[week_key].append(t.get("prediction_correct", False))
    pm = t.get("p_market")
    if pm is not None:
        won = t.get("prediction_correct", False)
        if t["pred_direction"] == "up":
            pnl = (1 - pm - FEE) * STAKE if won else -(pm + FEE) * STAKE
        else:
            pnl = (pm - FEE) * STAKE if won else -((1 - pm) + FEE) * STAKE
        by_week_ne[week_key].append(pnl)

for week in sorted(by_week.keys()):
    vals = by_week[week]
    c = sum(vals)
    n_w = len(vals)
    ne_w = by_week_ne.get(week, [])
    ne_avg_w = sum(ne_w) / len(ne_w) if ne_w else 0
    print(f"  {week}: n={n_w}, acc={c/n_w*100:.1f}%, NE_t/trade=${ne_avg_w:.4f}")

# Also break by day for more granularity
print("\n  Daily breakdown:")
by_day = defaultdict(list)
for t in completed:
    ts = t.get("ts_model_ran_ms", 0)
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    day_key = dt.strftime("%m-%d (%a)")
    by_day[day_key].append(t.get("prediction_correct", False))

for day in sorted(by_day.keys()):
    vals = by_day[day]
    c = sum(vals)
    n_d = len(vals)
    bar = "█" * int(c / n_d * 20) + "░" * (20 - int(c / n_d * 20))
    print(f"    {day}: n={n_d:3d}  acc={c/n_d*100:5.1f}%  {bar}")

# ═══════════════════════════════════════════════════════════
# 5. Accuracy by UTC hour
# ═══════════════════════════════════════════════════════════
print("\n── 5. ACCURACY BY UTC HOUR (900s) ──")

by_hour = defaultdict(list)
for t in completed:
    ts = t.get("ts_model_ran_ms", 0)
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    by_hour[dt.hour].append(t.get("prediction_correct", False))

# Overnight vs day summary
overnight_hours = set(range(21, 24)) | set(range(0, 4))
overnight = []
daytime = []
for h in range(24):
    for v in by_hour.get(h, []):
        if h in overnight_hours:
            overnight.append(v)
        else:
            daytime.append(v)

if overnight:
    print(f"  Overnight (21:00-03:59): n={len(overnight)}, acc={sum(overnight)/len(overnight)*100:.1f}%")
if daytime:
    print(f"  Daytime   (04:00-20:59): n={len(daytime)}, acc={sum(daytime)/len(daytime)*100:.1f}%")
print()

for h in range(24):
    vals = by_hour.get(h, [])
    if not vals:
        print(f"    {h:02d}:00 UTC:  n=  0")
        continue
    c = sum(vals)
    n_h = len(vals)
    acc = c / n_h * 100
    bar = "█" * int(acc / 5) + "░" * (20 - int(acc / 5))
    marker = " *" if h in overnight_hours else ""
    print(f"    {h:02d}:00 UTC:  n={n_h:3d}  acc={acc:5.1f}%  {bar}{marker}")

print("\n  * = overnight hours (21:00-03:59 UTC)")
print("=" * 65)
