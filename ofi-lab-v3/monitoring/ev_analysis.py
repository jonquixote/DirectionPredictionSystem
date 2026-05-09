#!/usr/bin/env python3
import json, statistics

records = [json.loads(l) for l in open("/data/logs/predictions_h60.jsonl")]

# Merge predictions with resolutions by prediction_id
by_id = {}
for r in records:
    pid = r.get("prediction_id")
    if not pid:
        continue
    if pid not in by_id:
        by_id[pid] = {}
    by_id[pid].update(r)

merged = list(by_id.values())
live = [p for p in merged
        if p.get("p_market") is not None
        and p.get("prediction_correct") is not None
        and not p.get("warmup", False)]

fee = 0.009  # approximate — actual varies with p_market under 1.80% formula

results = []
for p in live:
    pm = p["pred_proba"]
    mkt = p["p_market"]
    correct = p["prediction_correct"]
    actual_up = (p["pred_direction"] == "up" and correct) or \
                (p["pred_direction"] == "down" and not correct)

    ev_current = (mkt - pm - fee) if p["pred_direction"] == "down" else (pm - mkt - fee)
    correct_direction = "up" if pm > mkt else "down"
    ev_correct = abs(pm - mkt) - fee  # positive when divergence exceeds fee

    correct_bet_wins = (correct_direction == "up" and actual_up) or \
                       (correct_direction == "down" and not actual_up)

    results.append({
        "symbol": p["symbol"],
        "p_model": pm,
        "p_market": mkt,
        "divergence": pm - mkt,
        "current_direction": p["pred_direction"],
        "correct_direction": correct_direction,
        "mismatch": correct_direction != p["pred_direction"],
        "ev_current": ev_current,
        "ev_correct": ev_correct,
        "current_bet_wins": correct,
        "correct_bet_wins": correct_bet_wins,
    })

total = len(results)
if total == 0:
    print("No predictions with both p_market and prediction_correct found yet.")
    exit(0)

mismatches = sum(1 for r in results if r["mismatch"])
pos_ev_current = sum(1 for r in results if r["ev_current"] > 0)
pos_ev_correct = sum(1 for r in results if r["ev_correct"] > 0)  # abs(pm - mkt) > fee

print(f"Total predictions with p_market: {total}")
print(f"Mismatches (system bet wrong direction): {mismatches}/{total} = {mismatches/total*100:.1f}%")
print(f"Positive EV under current (0.5) threshold:       {pos_ev_current}/{total} = {pos_ev_current/total*100:.1f}%")
print(f"Positive EV under correct (p_market) threshold:  {pos_ev_correct}/{total} = {pos_ev_correct/total*100:.1f}%")
print()
print(f"Mean EV per trade, current threshold: {sum(r['ev_current'] for r in results)/total:.5f}")
print(f"Mean EV per trade, correct threshold: {sum(r['ev_correct'] for r in results)/total:.5f}")
print()
acc_current = sum(1 for r in results if r["current_bet_wins"]) / total
acc_correct = sum(1 for r in results if r["correct_bet_wins"]) / total
print(f"Accuracy under current (0.5) betting:  {acc_current*100:.2f}%")
print(f"Accuracy under correct (p_market) betting: {acc_correct*100:.2f}%")
print()
divs = [abs(r["divergence"]) for r in results]
print(f"Mean |divergence|:   {statistics.mean(divs):.4f}")
print(f"Median |divergence|: {statistics.median(divs):.4f}")
print()

# High-divergence subset (top 25% by |p_model - p_market|)
threshold_75 = sorted(divs)[int(len(divs) * 0.75)]
high_div = [r for r in results if abs(r["divergence"]) >= threshold_75]
acc_high_current = sum(1 for r in high_div if r["current_bet_wins"]) / len(high_div)
acc_high_correct = sum(1 for r in high_div if r["correct_bet_wins"]) / len(high_div)
print(f"High-divergence subset (top 25%, |div| >= {threshold_75:.4f}): n={len(high_div)}")
print(f"  Accuracy under current threshold: {acc_high_current*100:.2f}%")
print(f"  Accuracy under correct threshold: {acc_high_correct*100:.2f}%")
print()

# Per-symbol breakdown
for sym in set(r["symbol"] for r in results):
    sub = [r for r in results if r["symbol"] == sym]
    mis = sum(1 for r in sub if r["mismatch"])
    acc_c = sum(1 for r in sub if r["correct_bet_wins"]) / len(sub)
    mean_div = sum(r["divergence"] for r in sub) / len(sub)
    print(f"{sym}: n={len(sub)}, mismatches={mis} ({mis/len(sub)*100:.0f}%), "
          f"mean divergence={mean_div:+.4f}, acc_correct={acc_c*100:.1f}%")
