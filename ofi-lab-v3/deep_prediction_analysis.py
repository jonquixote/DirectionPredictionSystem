"""
Deep Prediction Analysis — mine the predictions ledger for filterable signals.

Analyzes h300 and h60 predictions to identify which features, conditions,
and meta-signals correlate with higher win rates, to design an advanced
prediction filtering system.
"""

import json
import collections
import statistics
import sys
from datetime import datetime, timezone

FILES = {
    "h300": "predictions_h300.jsonl",
    "h60": "predictions_h60.jsonl",
}

def load_predictions(path):
    """Load and join predictions with their resolutions."""
    preds = {}   # pid → record
    resolutions = {}  # pid → {prediction_correct, contract_result, price_at_close}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            rt = d.get("record_type")
            if rt == "prediction":
                pid = d.get("prediction_id")
                if pid:
                    preds[pid] = d
            elif rt == "resolution":
                pid = d.get("prediction_id")
                if pid:
                    resolutions[pid] = d
    # Join
    joined = []
    for pid, pred in preds.items():
        if pid in resolutions:
            res = resolutions[pid]
            pred["_won"] = res.get("prediction_correct", False)
            pred["_contract_result"] = res.get("contract_result")
            pred["_price_at_close"] = res.get("price_at_close")
            joined.append(pred)
    return preds, resolutions, joined


def analyze_model(model_name, path):
    print(f"\n{'='*80}")
    print(f"  MODEL: {model_name}  —  {path}")
    print(f"{'='*80}")

    preds, resolutions, joined = load_predictions(path)
    print(f"\nTotal predictions: {len(preds)}")
    print(f"Total resolutions: {len(resolutions)}")
    print(f"Joined (resolved predictions): {len(joined)}")

    if not joined:
        print("No resolved predictions to analyze.")
        return

    # Filter out warmup predictions
    active = [p for p in joined if not p.get("warmup", False)]
    warmup = [p for p in joined if p.get("warmup", False)]
    print(f"Active (non-warmup): {len(active)}")
    print(f"Warmup: {len(warmup)}")

    if not active:
        print("No active predictions.")
        return

    # ── 1. Overall stats ──────────────────────────────────────────
    wins = sum(1 for p in active if p["_won"])
    total = len(active)
    print(f"\n{'─'*60}")
    print(f"OVERALL: {wins}/{total} = {wins/total*100:.1f}% win rate")
    print(f"{'─'*60}")

    # ── 2. By symbol ─────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("BY SYMBOL:")
    print(f"{'─'*60}")
    by_sym = collections.defaultdict(lambda: [0, 0])
    for p in active:
        sym = p.get("symbol", "?")
        by_sym[sym][0] += int(p["_won"])
        by_sym[sym][1] += 1
    for sym in sorted(by_sym.keys()):
        w, t = by_sym[sym]
        print(f"  {sym:12s}  {w:4d}/{t:4d}  = {w/t*100:.1f}%")

    # ── 3. By direction ──────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("BY DIRECTION:")
    print(f"{'─'*60}")
    by_dir = collections.defaultdict(lambda: [0, 0])
    for p in active:
        d = p.get("pred_direction", "?")
        by_dir[d][0] += int(p["_won"])
        by_dir[d][1] += 1
    for d in sorted(by_dir.keys()):
        w, t = by_dir[d]
        print(f"  {d:12s}  {w:4d}/{t:4d}  = {w/t*100:.1f}%")

    # ── 4. By symbol × direction ─────────────────────────────────
    print(f"\n{'─'*60}")
    print("BY SYMBOL × DIRECTION:")
    print(f"{'─'*60}")
    by_sd = collections.defaultdict(lambda: [0, 0])
    for p in active:
        key = f"{p.get('symbol','?')}_{p.get('pred_direction','?')}"
        by_sd[key][0] += int(p["_won"])
        by_sd[key][1] += 1
    for key in sorted(by_sd.keys()):
        w, t = by_sd[key]
        print(f"  {key:22s}  {w:4d}/{t:4d}  = {w/t*100:.1f}%")

    # ── 5. By confidence bucket ──────────────────────────────────
    print(f"\n{'─'*60}")
    print("BY CONFIDENCE BUCKET (side_conf):")
    print(f"{'─'*60}")
    conf_buckets = collections.defaultdict(lambda: [0, 0])
    for p in active:
        proba = p.get("pred_proba", 0.5)
        direction = p.get("pred_direction", "up")
        side_conf = proba if direction == "up" else (1.0 - proba)
        bucket = round(side_conf * 100) // 1 / 100  # 1% buckets
        conf_buckets[bucket][0] += int(p["_won"])
        conf_buckets[bucket][1] += 1
    for bucket in sorted(conf_buckets.keys()):
        w, t = conf_buckets[bucket]
        if t >= 3:
            print(f"  {bucket:.2f}  {w:4d}/{t:4d}  = {w/t*100:.1f}%")

    # ── 6. By hour of day (UTC) ──────────────────────────────────
    print(f"\n{'─'*60}")
    print("BY HOUR (UTC):")
    print(f"{'─'*60}")
    by_hour = collections.defaultdict(lambda: [0, 0])
    for p in active:
        h = p.get("utc_hour")
        if h is not None:
            by_hour[h][0] += int(p["_won"])
            by_hour[h][1] += 1
    for h in sorted(by_hour.keys()):
        w, t = by_hour[h]
        pct = w/t*100
        bar = "█" * int(pct / 2)
        flag = " ← STRONG" if pct >= 60 and t >= 10 else (" ← WEAK" if pct <= 45 and t >= 10 else "")
        print(f"  {h:02d}:00  {w:4d}/{t:4d}  = {pct:5.1f}%  {bar}{flag}")

    # ── 7. By day of week ────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("BY DAY OF WEEK:")
    print(f"{'─'*60}")
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    by_dow = collections.defaultdict(lambda: [0, 0])
    for p in active:
        dow = p.get("day_of_week")
        if dow is not None:
            by_dow[dow][0] += int(p["_won"])
            by_dow[dow][1] += 1
    for dow in sorted(by_dow.keys()):
        w, t = by_dow[dow]
        name = days[dow] if dow < len(days) else f"Day{dow}"
        print(f"  {name:3s}  {w:4d}/{t:4d}  = {w/t*100:.1f}%")

    # ── 8. Weekend vs weekday ────────────────────────────────────
    print(f"\n{'─'*60}")
    print("WEEKEND vs WEEKDAY:")
    print(f"{'─'*60}")
    by_we = collections.defaultdict(lambda: [0, 0])
    for p in active:
        we = "weekend" if p.get("is_weekend", False) else "weekday"
        by_we[we][0] += int(p["_won"])
        by_we[we][1] += 1
    for we in sorted(by_we.keys()):
        w, t = by_we[we]
        print(f"  {we:10s}  {w:4d}/{t:4d}  = {w/t*100:.1f}%")

    # ── 9. Feature correlation with wins ─────────────────────────
    print(f"\n{'─'*60}")
    print("FEATURE SIGNAL ANALYSIS (mean feature value: wins vs losses):")
    print(f"{'─'*60}")

    # Collect feature values for wins vs losses
    feature_keys = set()
    for p in active[:10]:
        feats = p.get("features", {})
        feature_keys.update(feats.keys())

    feature_stats = {}
    for fk in sorted(feature_keys):
        win_vals = []
        loss_vals = []
        for p in active:
            feats = p.get("features", {})
            val = feats.get(fk)
            if val is not None and isinstance(val, (int, float)):
                if p["_won"]:
                    win_vals.append(val)
                else:
                    loss_vals.append(val)
        if len(win_vals) >= 10 and len(loss_vals) >= 10:
            win_mean = statistics.mean(win_vals)
            loss_mean = statistics.mean(loss_vals)
            win_std = statistics.stdev(win_vals) if len(win_vals) > 1 else 0
            loss_std = statistics.stdev(loss_vals) if len(loss_vals) > 1 else 0
            pooled_std = ((win_std + loss_std) / 2) if (win_std + loss_std) > 0 else 1
            separation = abs(win_mean - loss_mean) / pooled_std if pooled_std > 0 else 0
            feature_stats[fk] = {
                "win_mean": win_mean, "loss_mean": loss_mean,
                "separation": separation,
                "win_n": len(win_vals), "loss_n": len(loss_vals),
            }

    # Sort by separation (most discriminative first)
    ranked = sorted(feature_stats.items(), key=lambda x: x[1]["separation"], reverse=True)
    print(f"  {'Feature':30s}  {'Win Mean':>12s}  {'Loss Mean':>12s}  {'Separation':>10s}")
    for fk, stats in ranked[:20]:
        print(f"  {fk:30s}  {stats['win_mean']:12.6f}  {stats['loss_mean']:12.6f}  {stats['separation']:10.4f}")

    # ── 10. MLOFI sign agreement with direction ──────────────────
    print(f"\n{'─'*60}")
    print("MLOFI SIGN AGREEMENT WITH PREDICTION DIRECTION:")
    print(f"  Does mlofi > 0 agree with pred_direction='up'?")
    print(f"{'─'*60}")
    agree_win = [0, 0]
    disagree_win = [0, 0]
    for p in active:
        feats = p.get("features", {})
        mlofi = feats.get("mlofi")
        direction = p.get("pred_direction")
        if mlofi is not None and direction is not None:
            mlofi_says_up = mlofi > 0
            model_says_up = direction == "up"
            agrees = mlofi_says_up == model_says_up
            if agrees:
                agree_win[0] += int(p["_won"])
                agree_win[1] += 1
            else:
                disagree_win[0] += int(p["_won"])
                disagree_win[1] += 1
    if agree_win[1] > 0:
        print(f"  AGREE    (mlofi confirms model):  {agree_win[0]}/{agree_win[1]} = {agree_win[0]/agree_win[1]*100:.1f}%")
    if disagree_win[1] > 0:
        print(f"  DISAGREE (mlofi opposes model):   {disagree_win[0]}/{disagree_win[1]} = {disagree_win[0]/disagree_win[1]*100:.1f}%")

    # ── 11. MLOFI 30s mean agreement ─────────────────────────────
    print(f"\n{'─'*60}")
    print("MLOFI_30S_MEAN AGREEMENT WITH PREDICTION:")
    print(f"{'─'*60}")
    agree_30s = [0, 0]
    disagree_30s = [0, 0]
    for p in active:
        feats = p.get("features", {})
        mlofi_30 = feats.get("mlofi_30s_mean")
        direction = p.get("pred_direction")
        if mlofi_30 is not None and direction is not None:
            mlofi_says_up = mlofi_30 > 0
            model_says_up = direction == "up"
            agrees = mlofi_says_up == model_says_up
            if agrees:
                agree_30s[0] += int(p["_won"])
                agree_30s[1] += 1
            else:
                disagree_30s[0] += int(p["_won"])
                disagree_30s[1] += 1
    if agree_30s[1] > 0:
        print(f"  AGREE    (30s_mean confirms model):  {agree_30s[0]}/{agree_30s[1]} = {agree_30s[0]/agree_30s[1]*100:.1f}%")
    if disagree_30s[1] > 0:
        print(f"  DISAGREE (30s_mean opposes model):   {disagree_30s[0]}/{disagree_30s[1]} = {disagree_30s[0]/disagree_30s[1]*100:.1f}%")

    # ── 12. VWAP deviation sign ──────────────────────────────────
    print(f"\n{'─'*60}")
    print("VWAP DEVIATION AGREEMENT WITH PREDICTION:")
    print(f"  vwap_deviation > 0 means price is above VWAP (bullish)")
    print(f"{'─'*60}")
    vwap_agree = [0, 0]
    vwap_disagree = [0, 0]
    for p in active:
        feats = p.get("features", {})
        vwap = feats.get("vwap_deviation")
        direction = p.get("pred_direction")
        if vwap is not None and direction is not None:
            vwap_says_up = vwap > 0
            model_says_up = direction == "up"
            agrees = vwap_says_up == model_says_up
            if agrees:
                vwap_agree[0] += int(p["_won"])
                vwap_agree[1] += 1
            else:
                vwap_disagree[0] += int(p["_won"])
                vwap_disagree[1] += 1
    if vwap_agree[1] > 0:
        print(f"  AGREE:    {vwap_agree[0]}/{vwap_agree[1]} = {vwap_agree[0]/vwap_agree[1]*100:.1f}%")
    if vwap_disagree[1] > 0:
        print(f"  DISAGREE: {vwap_disagree[0]}/{vwap_disagree[1]} = {vwap_disagree[0]/vwap_disagree[1]*100:.1f}%")

    # ── 13. Spread regime ────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("BY SPREAD REGIME (relative_spread):")
    print(f"{'─'*60}")
    spread_buckets = collections.defaultdict(lambda: [0, 0])
    for p in active:
        rs = p.get("relative_spread") or (p.get("features", {}).get("relative_spread"))
        if rs is not None:
            if rs <= 1e-6:
                bucket = "tight (≤1e-6)"
            elif rs <= 5e-6:
                bucket = "normal (1-5e-6)"
            elif rs <= 1e-5:
                bucket = "wide (5e-6-1e-5)"
            else:
                bucket = "very_wide (>1e-5)"
            spread_buckets[bucket][0] += int(p["_won"])
            spread_buckets[bucket][1] += 1
    for bucket in sorted(spread_buckets.keys()):
        w, t = spread_buckets[bucket]
        print(f"  {bucket:22s}  {w:4d}/{t:4d}  = {w/t*100:.1f}%")

    # ── 14. Absolute MLOFI magnitude buckets ─────────────────────
    print(f"\n{'─'*60}")
    print("BY |MLOFI| MAGNITUDE (order flow intensity):")
    print(f"{'─'*60}")
    mlofi_mag_buckets = collections.defaultdict(lambda: [0, 0])
    for p in active:
        feats = p.get("features", {})
        mlofi = feats.get("mlofi")
        if mlofi is not None:
            am = abs(mlofi)
            if am < 0.1:
                bucket = "quiet (<0.1)"
            elif am < 0.5:
                bucket = "mild (0.1-0.5)"
            elif am < 1.0:
                bucket = "moderate (0.5-1.0)"
            elif am < 2.0:
                bucket = "active (1.0-2.0)"
            else:
                bucket = "intense (>2.0)"
            mlofi_mag_buckets[bucket][0] += int(p["_won"])
            mlofi_mag_buckets[bucket][1] += 1
    for bucket in sorted(mlofi_mag_buckets.keys()):
        w, t = mlofi_mag_buckets[bucket]
        print(f"  {bucket:22s}  {w:4d}/{t:4d}  = {w/t*100:.1f}%")

    # ── 15. Volatility regime (mlofi_30s_std) ────────────────────
    print(f"\n{'─'*60}")
    print("BY VOLATILITY REGIME (mlofi_30s_std):")
    print(f"{'─'*60}")
    vol_buckets = collections.defaultdict(lambda: [0, 0])
    for p in active:
        feats = p.get("features", {})
        vol = feats.get("mlofi_30s_std")
        if vol is not None:
            if vol < 0.5:
                bucket = "low (<0.5)"
            elif vol < 1.0:
                bucket = "normal (0.5-1.0)"
            elif vol < 2.0:
                bucket = "elevated (1.0-2.0)"
            else:
                bucket = "high (>2.0)"
            vol_buckets[bucket][0] += int(p["_won"])
            vol_buckets[bucket][1] += 1
    for bucket in sorted(vol_buckets.keys()):
        w, t = vol_buckets[bucket]
        print(f"  {bucket:22s}  {w:4d}/{t:4d}  = {w/t*100:.1f}%")

    # ── 16. Consecutive outcomes (streaks) ───────────────────────
    print(f"\n{'─'*60}")
    print("STREAK ANALYSIS (after N consecutive wins/losses):")
    print(f"{'─'*60}")
    # Sort by time
    sorted_preds = sorted(active, key=lambda p: p.get("ts_contract_open_ms", 0))
    # BTC only for cleaner signal
    btc_preds = [p for p in sorted_preds if p.get("symbol") == "BTCUSDT"]
    if len(btc_preds) > 10:
        for streak_len in [1, 2, 3]:
            after_win_streak = [0, 0]  # [wins, total]
            after_loss_streak = [0, 0]
            for i in range(streak_len, len(btc_preds)):
                prev_results = [btc_preds[i-j-1]["_won"] for j in range(streak_len)]
                if all(prev_results):  # After N wins
                    after_win_streak[0] += int(btc_preds[i]["_won"])
                    after_win_streak[1] += 1
                elif not any(prev_results):  # After N losses
                    after_loss_streak[0] += int(btc_preds[i]["_won"])
                    after_loss_streak[1] += 1
            if after_win_streak[1] > 0:
                print(f"  After {streak_len} BTC wins:   {after_win_streak[0]}/{after_win_streak[1]} = {after_win_streak[0]/after_win_streak[1]*100:.1f}%")
            if after_loss_streak[1] > 0:
                print(f"  After {streak_len} BTC losses: {after_loss_streak[0]}/{after_loss_streak[1]} = {after_loss_streak[0]/after_loss_streak[1]*100:.1f}%")

    # ── 17. Cross-symbol confirmation ────────────────────────────
    print(f"\n{'─'*60}")
    print("CROSS-SYMBOL CONFIRMATION (BTC+ETH agree on direction at same boundary):")
    print(f"{'─'*60}")
    by_boundary = collections.defaultdict(dict)
    for p in active:
        ts = p.get("ts_contract_open_ms")
        sym = p.get("symbol")
        if ts and sym:
            by_boundary[ts][sym] = p

    both_agree = [0, 0]
    both_disagree = [0, 0]
    for ts, syms in by_boundary.items():
        if "BTCUSDT" in syms and "ETHUSDT" in syms:
            btc = syms["BTCUSDT"]
            eth = syms["ETHUSDT"]
            same_dir = btc.get("pred_direction") == eth.get("pred_direction")
            if same_dir:
                both_agree[0] += int(btc["_won"])
                both_agree[1] += 1
            else:
                both_disagree[0] += int(btc["_won"])
                both_disagree[1] += 1
    if both_agree[1] > 0:
        print(f"  BTC+ETH same direction (BTC WR):  {both_agree[0]}/{both_agree[1]} = {both_agree[0]/both_agree[1]*100:.1f}%")
    if both_disagree[1] > 0:
        print(f"  BTC+ETH diff direction (BTC WR):  {both_disagree[0]}/{both_disagree[1]} = {both_disagree[0]/both_disagree[1]*100:.1f}%")

    # ── 18. Multi-feature composite signal ───────────────────────
    print(f"\n{'─'*60}")
    print("COMPOSITE SIGNAL (mlofi_agrees AND vwap_agrees AND conf≥0.53):")
    print(f"{'─'*60}")
    composite_pass = [0, 0]
    composite_fail = [0, 0]
    composite_all = [0, 0]
    for p in active:
        feats = p.get("features", {})
        mlofi = feats.get("mlofi")
        vwap = feats.get("vwap_deviation")
        proba = p.get("pred_proba", 0.5)
        direction = p.get("pred_direction", "up")
        side_conf = proba if direction == "up" else (1.0 - proba)

        if mlofi is None or vwap is None:
            continue

        model_says_up = direction == "up"
        mlofi_agrees = (mlofi > 0) == model_says_up
        vwap_agrees = (vwap > 0) == model_says_up
        high_conf = side_conf >= 0.53

        composite_all[0] += int(p["_won"])
        composite_all[1] += 1

        if mlofi_agrees and vwap_agrees and high_conf:
            composite_pass[0] += int(p["_won"])
            composite_pass[1] += 1
        else:
            composite_fail[0] += int(p["_won"])
            composite_fail[1] += 1

    if composite_pass[1] > 0:
        print(f"  PASS (all 3 conditions):  {composite_pass[0]}/{composite_pass[1]} = {composite_pass[0]/composite_pass[1]*100:.1f}%")
    if composite_fail[1] > 0:
        print(f"  FAIL (any condition off): {composite_fail[0]}/{composite_fail[1]} = {composite_fail[0]/composite_fail[1]*100:.1f}%")
    if composite_all[1] > 0:
        print(f"  ALL predictions:          {composite_all[0]}/{composite_all[1]} = {composite_all[0]/composite_all[1]*100:.1f}%")

    # ── 19. ETH cross-correlation as BTC filter ──────────────────
    print(f"\n{'─'*60}")
    print("ETH MLOFI AS BTC FILTER (eth_mlofi_30s_mean agrees with BTC prediction):")
    print(f"{'─'*60}")
    btc_active = [p for p in active if p.get("symbol") == "BTCUSDT"]
    eth_agree = [0, 0]
    eth_disagree = [0, 0]
    for p in btc_active:
        feats = p.get("features", {})
        eth_mlofi = feats.get("eth_mlofi_30s_mean")
        direction = p.get("pred_direction", "up")
        if eth_mlofi is not None:
            model_says_up = direction == "up"
            eth_says_up = eth_mlofi > 0
            if eth_says_up == model_says_up:
                eth_agree[0] += int(p["_won"])
                eth_agree[1] += 1
            else:
                eth_disagree[0] += int(p["_won"])
                eth_disagree[1] += 1
    if eth_agree[1] > 0:
        print(f"  ETH confirms BTC:  {eth_agree[0]}/{eth_agree[1]} = {eth_agree[0]/eth_agree[1]*100:.1f}%")
    if eth_disagree[1] > 0:
        print(f"  ETH opposes BTC:   {eth_disagree[0]}/{eth_disagree[1]} = {eth_disagree[0]/eth_disagree[1]*100:.1f}%")

    # ── 20. Momentum alignment (mlofi_momentum) ─────────────────
    print(f"\n{'─'*60}")
    print("MOMENTUM ALIGNMENT (mlofi_momentum agrees with prediction):")
    print(f"{'─'*60}")
    mom_agree = [0, 0]
    mom_disagree = [0, 0]
    for p in active:
        feats = p.get("features", {})
        mom = feats.get("mlofi_momentum")
        direction = p.get("pred_direction", "up")
        if mom is not None:
            model_says_up = direction == "up"
            mom_says_up = mom > 0
            if mom_says_up == model_says_up:
                mom_agree[0] += int(p["_won"])
                mom_agree[1] += 1
            else:
                mom_disagree[0] += int(p["_won"])
                mom_disagree[1] += 1
    if mom_agree[1] > 0:
        print(f"  AGREE:    {mom_agree[0]}/{mom_agree[1]} = {mom_agree[0]/mom_agree[1]*100:.1f}%")
    if mom_disagree[1] > 0:
        print(f"  DISAGREE: {mom_disagree[0]}/{mom_disagree[1]} = {mom_disagree[0]/mom_disagree[1]*100:.1f}%")


# ── Run ──────────────────────────────────────────────────────────
for model, path in FILES.items():
    try:
        analyze_model(model, path)
    except FileNotFoundError:
        print(f"\nSkipping {model}: {path} not found")

print(f"\n{'='*80}")
print("ANALYSIS COMPLETE")
print(f"{'='*80}")
