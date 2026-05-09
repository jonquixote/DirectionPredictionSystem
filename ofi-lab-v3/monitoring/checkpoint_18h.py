#!/usr/bin/env python3
"""V3 debug + 3 analyses for 18h checkpoint."""
import json
from datetime import datetime, timezone
from collections import defaultdict

# ═══════════════════════════════════════════════════════════
# 0. V3 FEATURE DEBUG — check mid_price_dev_30d in live bars
# ═══════════════════════════════════════════════════════════
print("=" * 60)
print("0. V3 FEATURE DEBUG — mid_price_dev_30d in live predictions")
print("=" * 60)

v3_path = "/data/logs/predictions_h60_v3.jsonl"
v3_records = [json.loads(l) for l in open(v3_path)]
v3_preds = [r for r in v3_records if r.get("record_type") == "prediction" and not r.get("warmup", False)]

# Check if features dict is logged
has_features = sum(1 for p in v3_preds if p.get("features"))
print(f"V3 predictions with 'features' dict: {has_features}/{len(v3_preds)}")

# Try to get feature values from the last 10 SOL predictions
sol_preds = [p for p in v3_preds if p.get("symbol") == "SOLUSDT"][-10:]
btc_preds = [p for p in v3_preds if p.get("symbol") == "BTCUSDT"][-10:]

for label, subset in [("SOL", sol_preds), ("BTC", btc_preds)]:
    print(f"\n  Last 10 {label} predictions:")
    for p in subset:
        feats = p.get("features", {})
        # Try top-level keys too (features may be logged flat)
        dev = feats.get("mid_price_dev_30d") or p.get("mid_price_dev_30d")
        mid = feats.get("mid_price") or p.get("mid_price")
        if dev is not None and mid is not None:
            print(f"    dev_30d={dev:.4f}, mid={mid:.2f}, dir={p['pred_direction']}, proba={p['pred_proba']:.4f}")
        else:
            print(f"    [dev_30d={dev}, mid={mid}] dir={p['pred_direction']}, proba={p['pred_proba']:.4f}")

# Distribution of pred_proba for V3
all_probas = [p["pred_proba"] for p in v3_preds]
if all_probas:
    n_up = sum(1 for p in all_probas if p > 0.5)
    n_down = len(all_probas) - n_up
    print(f"\nV3 pred_proba distribution:")
    print(f"  Total: {len(all_probas)}, Up: {n_up} ({n_up/len(all_probas)*100:.1f}%), Down: {n_down}")
    print(f"  Mean: {sum(all_probas)/len(all_probas):.4f}")
    print(f"  Min: {min(all_probas):.4f}, Max: {max(all_probas):.4f}")

    # Per-symbol
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        sp = [p["pred_proba"] for p in v3_preds if p["symbol"] == sym]
        if sp:
            up = sum(1 for x in sp if x > 0.5)
            print(f"  {sym}: n={len(sp)}, mean_proba={sum(sp)/len(sp):.4f}, up={up/len(sp)*100:.1f}%")


# ═══════════════════════════════════════════════════════════
# 1. V3 INVERSION CHECK — is p_market already pointing correctly?
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("1. V3 INVERSION CHECK — p_market directional accuracy")
print("=" * 60)

by_id = {}
for r in v3_records:
    pid = r.get("prediction_id")
    if not pid:
        continue
    if pid not in by_id:
        by_id[pid] = {}
    by_id[pid].update(r)

resolved_pm = [v for v in by_id.values()
               if v.get("prediction_correct") is not None
               and v.get("p_market") is not None
               and not v.get("warmup", False)]

if resolved_pm:
    market_correct = 0
    for p in resolved_pm:
        correct = p["prediction_correct"]
        direction = p["pred_direction"]
        pm = p["p_market"]
        # Determine actual outcome
        actual_up = (direction == "up" and correct) or (direction == "down" and not correct)
        # Did p_market point correctly?
        market_pointed_up = pm > 0.5
        market_was_right = (market_pointed_up and actual_up) or (not market_pointed_up and not actual_up)
        if market_was_right:
            market_correct += 1

    print(f"Total resolved with p_market: {len(resolved_pm)}")
    print(f"p_market pointed correct direction: {market_correct}/{len(resolved_pm)} = {market_correct/len(resolved_pm)*100:.1f}%")
    print(f"If >= 65%: V3's anti-predictiveness = market being right, inversion won't help")
else:
    print("No resolved V3 trades with p_market")


# ═══════════════════════════════════════════════════════════
# 2. ROLLING-50 CRASH TIMING — UTC hour distribution
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("2. ROLLING-50 CRASH TIMING — UTC hour distribution of last 50 trades")
print("=" * 60)

for model, path in [("H60", "/data/logs/predictions_h60.jsonl"), ("H300", "/data/logs/predictions_h300.jsonl")]:
    records = [json.loads(l) for l in open(path)]
    mid = {}
    for r in records:
        pid = r.get("prediction_id")
        if not pid:
            continue
        if pid not in mid:
            mid[pid] = {}
        mid[pid].update(r)

    resolved = sorted(
        [v for v in mid.values() if v.get("prediction_correct") is not None and not v.get("warmup", False)],
        key=lambda x: x.get("ts_model_ran_ms", 0)
    )

    last_50 = resolved[-50:]
    if not last_50:
        print(f"{model}: no resolved trades")
        continue

    correct = sum(1 for p in last_50 if p["prediction_correct"])
    acc = correct / len(last_50) * 100

    hour_counts = defaultdict(int)
    hour_correct = defaultdict(int)
    for p in last_50:
        ts = p.get("ts_model_ran_ms", 0)
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        h = dt.hour
        hour_counts[h] += 1
        if p["prediction_correct"]:
            hour_correct[h] += 1

    first_ts = datetime.fromtimestamp(last_50[0]["ts_model_ran_ms"] / 1000, tz=timezone.utc)
    last_ts = datetime.fromtimestamp(last_50[-1]["ts_model_ran_ms"] / 1000, tz=timezone.utc)

    print(f"\n{model} last 50 trades: acc={acc:.1f}%, range={first_ts.strftime('%m-%d %H:%M')} to {last_ts.strftime('%m-%d %H:%M')}")

    # Group into time bands
    overnight = sum(hour_counts[h] for h in range(21, 24)) + sum(hour_counts[h] for h in range(0, 4))
    overnight_correct = sum(hour_correct[h] for h in range(21, 24)) + sum(hour_correct[h] for h in range(0, 4))
    day = sum(hour_counts[h] for h in range(4, 21))
    day_correct = sum(hour_correct[h] for h in range(4, 21))

    print(f"  21:00-03:59 UTC (overnight): n={overnight}, acc={overnight_correct/overnight*100:.1f}%" if overnight else "  Overnight: n=0")
    print(f"  04:00-20:59 UTC (day):       n={day}, acc={day_correct/day*100:.1f}%" if day else "  Day: n=0")

    print(f"  Per-hour breakdown:")
    for h in sorted(hour_counts.keys()):
        n = hour_counts[h]
        c = hour_correct[h]
        bar = "#" * n
        a = c / n * 100 if n > 0 else 0
        print(f"    {h:02d}:00 UTC: n={n:2d} acc={a:5.1f}% {bar}")


# ═══════════════════════════════════════════════════════════
# 3. H300 NE_t BY TIME PERIOD
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("3. H300 NE_t BY TIME PERIOD")
print("=" * 60)

h300_records = [json.loads(l) for l in open("/data/logs/predictions_h300.jsonl")]
h300_by_id = {}
for r in h300_records:
    pid = r.get("prediction_id")
    if not pid:
        continue
    if pid not in h300_by_id:
        h300_by_id[pid] = {}
    h300_by_id[pid].update(r)

h300_resolved = [v for v in h300_by_id.values()
                 if v.get("prediction_correct") is not None
                 and v.get("p_market") is not None
                 and not v.get("warmup", False)]

# Monday 13:00 UTC = 2026-03-31 13:00 UTC
# Tuesday 00:00 UTC = 2026-04-01 00:00 UTC
MON_13 = int(datetime(2026, 3, 31, 13, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
TUE_00 = int(datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

periods = {
    "Before Mon 13:00 UTC": lambda ts: ts < MON_13,
    "Mon 13:00-21:00 UTC":  lambda ts: MON_13 <= ts < MON_13 + 8*3600*1000,
    "Since Tue 00:00 UTC":  lambda ts: ts >= TUE_00,
}

fee = 0.009
for label, filt in periods.items():
    subset = [p for p in h300_resolved if filt(p.get("ts_model_ran_ms", 0))]
    if not subset:
        print(f"\n{label}: no trades")
        continue

    n = len(subset)
    correct = sum(1 for p in subset if p["prediction_correct"])
    acc = correct / n * 100

    ne_t_vals = []
    for p in subset:
        pm = p["pred_proba"]
        mkt = p["p_market"]
        ev = (mkt - pm - fee) if p["pred_direction"] == "down" else (pm - mkt - fee)
        ne_t_vals.append(ev * 10)  # $10 stake

    total_ne = sum(ne_t_vals)
    mean_ne = total_ne / n if n else 0

    print(f"\n{label}: n={n}, acc={acc:.1f}%, NE_t total=${total_ne:.2f}, mean=${mean_ne:.2f}/trade")
