#!/usr/bin/env python3
"""Bias investigation — all 4 analyses."""
import json, csv, sys
from datetime import datetime, timezone
from collections import defaultdict

# Load H60 predictions (all) and trades
preds = [json.loads(l) for l in open("/data/logs/predictions_h60.jsonl")]
pred_preds = [p for p in preds if p.get("record_type") == "prediction"]

records = [json.loads(l) for l in open("/data/logs/paper_trades_h60.jsonl")]
by_tid = {}
for r in records:
    tid = r.get("trade_id")
    if tid:
        by_tid.setdefault(tid, {}).update(r)
completed = [t for t in by_tid.values()
             if t.get("prediction_correct") is not None
             and not t.get("warmup", False)]
completed.sort(key=lambda t: t.get("ts_model_ran_ms", 0))

# Fix flat trades
for t in completed:
    op = t.get("price_at_contract_open", 0)
    cl = t.get("price_at_contract_close", 0)
    pd_dir = t.get("pred_direction")
    if cl > op: actual = "up"
    elif cl < op: actual = "down"
    else: actual = "flat"
    t["_correct"] = (pd_dir == actual)
    t["_actual"] = actual

n = len(completed)

# =============================================
print("=" * 80)
print("INVESTIGATION 1 - TRAINING DATA DATE RANGE")
print("=" * 80)
# From config: Apr 2025 - Mar 2026 window
# From run_training.py: TRAIN_END = 2025-12-31, VAL_END = 2026-02-15
# Training boundaries comment says "Apr 2025 - Mar 2026"
# mid_price_training_range: BTC [58000, 110000]
print()
print("  Training period: Apr 2025 - Dec 2025 (train)")
print("  Validation:      Jan 2026 - Feb 15, 2026")
print("  Test:            Feb 16, 2026 - Mar 23, 2026")
print()
print("  BTC training price range from config: $58,000 - $110,000")
print("  Current BTC price (live): ~$67,000-$68,000")
print()
print("  NOTE: Need external price data to confirm BTC start/end prices.")
print("  But: BTC range [$58k-$110k] spans both up and down regimes.")
print("  The current price ($67k) is in the LOWER 20% of the range.")
print("  If BTC was ~$67k in Apr 2025 and rose to $110k then fell back,")
print("  the model saw a full cycle, not a pure downtrend.")

# =============================================
print()
print("=" * 80)
print("INVESTIGATION 2 - FEATURE IMPORTANCE (LightGBM gain/split)")
print("=" * 80)

try:
    import lightgbm as lgb
    model = lgb.Booster(model_file="/data/models/latest_h60/model.lgb")
    feature_names = model.feature_name()

    gain_imp = model.feature_importance(importance_type="gain")
    split_imp = model.feature_importance(importance_type="split")

    gain_ranked = sorted(zip(feature_names, gain_imp), key=lambda x: -x[1])
    split_ranked = sorted(zip(feature_names, split_imp), key=lambda x: -x[1])

    print("\n  Top 15 by GAIN:")
    for i, (name, val) in enumerate(gain_ranked[:15]):
        marker = " *** PRICE FEATURE" if name in ("mid_price", "spread", "vwap_deviation") else ""
        print(f"    {i+1:>2}. {name:<25s} {val:>12.1f}{marker}")

    print("\n  Top 15 by SPLIT:")
    for i, (name, val) in enumerate(split_ranked[:15]):
        marker = " *** PRICE FEATURE" if name in ("mid_price", "spread", "vwap_deviation") else ""
        print(f"    {i+1:>2}. {name:<25s} {val:>8d}{marker}")

    # Where does mid_price rank?
    gain_rank = next(i+1 for i, (n, _) in enumerate(gain_ranked) if n == "mid_price")
    split_rank = next(i+1 for i, (n, _) in enumerate(split_ranked) if n == "mid_price")
    print(f"\n  mid_price rank: GAIN=#{gain_rank}, SPLIT=#{split_rank}")
    print(f"  mid_price gain share: {gain_imp[feature_names.index('mid_price')] / sum(gain_imp) * 100:.1f}%")

except Exception as e:
    print(f"  ERROR: {e}")

# =============================================
print()
print("=" * 80)
print("INVESTIGATION 3 - RAW PREDICTION PROBABILITY DISTRIBUTION")
print("=" * 80)

probas = [p.get("pred_proba", 0.5) for p in pred_preds if not p.get("warmup", False)]
probas.sort()
n_pred = len(probas)
mean_p = sum(probas) / n_pred
median_p = probas[n_pred // 2]
p25 = probas[int(n_pred * 0.25)]
p75 = probas[int(n_pred * 0.75)]

print(f"\n  Total live predictions: {n_pred}")
print(f"  Mean:   {mean_p:.4f}")
print(f"  Median: {median_p:.4f}")
print(f"  P25:    {p25:.4f}")
print(f"  P75:    {p75:.4f}")
print(f"  Min:    {min(probas):.4f}")
print(f"  Max:    {max(probas):.4f}")

above_50 = sum(1 for p in probas if p > 0.5)
below_50 = sum(1 for p in probas if p < 0.5)
at_50 = sum(1 for p in probas if p == 0.5)
print(f"\n  Above 0.50 (pred=up):  {above_50} ({above_50/n_pred*100:.1f}%)")
print(f"  Below 0.50 (pred=down): {below_50} ({below_50/n_pred*100:.1f}%)")
print(f"  Exactly 0.50:          {at_50}")

# Histogram bins
bins = [0.30, 0.35, 0.40, 0.42, 0.44, 0.46, 0.48, 0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.65, 0.70]
print(f"\n  Probability histogram:")
for i in range(len(bins) - 1):
    lo, hi = bins[i], bins[i+1]
    count = sum(1 for p in probas if lo <= p < hi)
    bar = "#" * (count // 5)
    print(f"    [{lo:.2f}, {hi:.2f}): {count:>5d}  {bar}")
count = sum(1 for p in probas if p >= bins[-1])
print(f"    [{bins[-1]:.2f}, 1.00 ): {count:>5d}")

# Per-symbol probability distribution
print(f"\n  Per-symbol mean probability:")
sym_probas = defaultdict(list)
for p in pred_preds:
    if not p.get("warmup", False):
        sym_probas[p.get("symbol", "?")].append(p.get("pred_proba", 0.5))
for sym in sorted(sym_probas):
    vals = sym_probas[sym]
    m = sum(vals) / len(vals)
    above = sum(1 for v in vals if v > 0.5)
    print(f"    {sym}: mean={m:.4f}, above_50={above}/{len(vals)} ({above/len(vals)*100:.1f}%)")

# =============================================
print()
print("=" * 80)
print("INVESTIGATION 4 - UP/DOWN SPLIT BY CHRONOLOGICAL HALF")
print("=" * 80)

half = n // 2
first_half = completed[:half]
second_half = completed[half:]

for label, trades in [("FIRST HALF", first_half), ("SECOND HALF", second_half)]:
    total = len(trades)
    ups = [t for t in trades if t.get("pred_direction") == "up"]
    downs = [t for t in trades if t.get("pred_direction") == "down"]
    up_correct = sum(1 for t in ups if t["_correct"])
    down_correct = sum(1 for t in downs if t["_correct"])
    all_correct = sum(1 for t in trades if t["_correct"])

    ts_start = datetime.fromtimestamp(trades[0].get("ts_model_ran_ms", 0) / 1000, tz=timezone.utc)
    ts_end = datetime.fromtimestamp(trades[-1].get("ts_model_ran_ms", 0) / 1000, tz=timezone.utc)

    print(f"\n  --- {label} (trades 1-{half if label == 'FIRST HALF' else n}, {ts_start:%m-%d %H:%M} to {ts_end:%m-%d %H:%M} UTC) ---")
    print(f"  Total: {total} trades, overall acc: {all_correct/total*100:.1f}%")
    print(f"  Up:   {len(ups):>4d} ({len(ups)/total*100:.1f}%)  acc: {up_correct/len(ups)*100:.1f}%" if ups else f"  Up:   0 (0.0%)")
    print(f"  Down: {len(downs):>4d} ({len(downs)/total*100:.1f}%)  acc: {down_correct/len(downs)*100:.1f}%" if downs else f"  Down: 0 (0.0%)")

    # Per-symbol breakdown
    for sym in ["BTCUSDT", "SOLUSDT"]:
        st = [t for t in trades if t.get("symbol") == sym]
        if not st:
            continue
        su = [t for t in st if t.get("pred_direction") == "up"]
        sd = [t for t in st if t.get("pred_direction") == "down"]
        print(f"    {sym}: up={len(su)} ({len(su)/len(st)*100:.0f}%) down={len(sd)} ({len(sd)/len(st)*100:.0f}%)")

    # What was the actual market direction in this half?
    actual_ups = sum(1 for t in trades if t["_actual"] == "up")
    actual_downs = sum(1 for t in trades if t["_actual"] == "down")
    actual_flat = sum(1 for t in trades if t["_actual"] == "flat")
    print(f"  Actual market direction: up={actual_ups} ({actual_ups/total*100:.1f}%) down={actual_downs} ({actual_downs/total*100:.1f}%) flat={actual_flat}")

print()
print("=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"\n  If the model is just a 'down' predictor:")
pure_down_acc = sum(1 for t in completed if t["_actual"] == "down") / n * 100
print(f"  'Always predict down' accuracy: {pure_down_acc:.1f}%")
print(f"  Model accuracy:                 {sum(1 for t in completed if t['_correct']) / n * 100:.1f}%")
print(f"  Alpha over naive down:          {sum(1 for t in completed if t['_correct']) / n * 100 - pure_down_acc:.1f}%")
