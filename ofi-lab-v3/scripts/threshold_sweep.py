import json
import sys
from collections import defaultdict
from datetime import datetime, timezone

# ─── Configuration ───────────────────────────────────────────────
THRESHOLDS = [0.51, 0.52, 0.53, 0.54, 0.545, 0.55, 0.555, 0.56, 0.57, 0.58, 0.59, 0.60]
KELLY_FRACTIONS = [0.20, 0.50, 1.00]
INITIAL_BANKROLL = 100.0

directories = {
    "Baseline":      "/data/logs/",
    "Model A":       "/data/logs_model_a/",
    "Model B":       "/data/logs_model_b/",
    "Model A Clone": "/data/logs_model_a_clone/",
}

model_files = ["h300"]  # Focus on h300 only per user's live system

def load_predictions_and_resolutions(log_dir, model_name, symbol="BTCUSDT"):
    """Load all predictions and their resolutions for a given model+symbol."""
    pred_file = f"{log_dir}predictions_{model_name}.jsonl"
    predictions = {}
    resolutions = {}

    try:
        with open(pred_file, "r") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    pid = d.get("prediction_id")
                    rtype = d.get("record_type")

                    if rtype == "prediction":
                        if d.get("symbol") != symbol:
                            continue
                        predictions[pid] = d
                    elif rtype == "resolution":
                        resolutions[pid] = d
                except Exception:
                    pass
    except FileNotFoundError:
        return [], {}

    # Sort by time
    sorted_preds = sorted(predictions.values(), key=lambda x: x.get("ts_model_ran_ms", 0))
    return sorted_preds, resolutions


def simulate(sorted_preds, resolutions, threshold, kelly_frac):
    """Run a simulation at a given threshold and Kelly fraction. Returns stats dict."""
    bankroll = INITIAL_BANKROLL
    wins = 0
    losses = 0
    unresolved = 0
    trade_details = []

    for p in sorted_preds:
        pid = p.get("prediction_id")
        res = resolutions.get(pid)

        proba = p.get("pred_proba", 0.5)
        direction = p.get("pred_direction")
        conf = proba if direction == "up" else (1.0 - proba)

        if conf < threshold:
            continue

        if not res:
            unresolved += 1
            continue

        is_correct = res.get("prediction_correct")
        if is_correct is None:
            unresolved += 1
            continue

        # Market price for Kelly sizing
        p_market = p.get("p_market")
        if p_market is None or p_market <= 0 or p_market >= 1:
            p_market = 0.50
        p_market_adj = p_market if direction == "up" else (1.0 - p_market)

        # Kelly stake
        edge = conf - p_market_adj
        if edge <= 0:
            stake = INITIAL_BANKROLL * 0.01  # minimum flat
        else:
            odds = (1.0 - p_market_adj) / p_market_adj if p_market_adj < 1.0 else 99.0
            kelly_raw = edge / odds
            kelly_adj = min(max(kelly_raw * kelly_frac, 0), 1.0)
            stake = bankroll * kelly_adj

        stake = max(0.01, min(stake, bankroll))
        if stake < 0.10:
            continue

        ts = p.get("ts_model_ran_ms", 0)
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)

        if is_correct:
            wins += 1
            profit = (stake / p_market_adj) - stake
            bankroll += profit
            trade_details.append((dt, direction, conf, "+", profit, bankroll))
        else:
            losses += 1
            bankroll -= stake
            trade_details.append((dt, direction, conf, "-", -stake, bankroll))

    total = wins + losses
    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "unresolved": unresolved,
        "win_rate": (wins / total * 100) if total > 0 else 0,
        "pnl": bankroll - INITIAL_BANKROLL,
        "final_bankroll": bankroll,
        "details": trade_details,
    }


# ═══════════════════════════════════════════════════════════════
# PART 1: Full threshold sweep (Kelly 20%)
# ═══════════════════════════════════════════════════════════════
print("=" * 90)
print("  PART 1: THRESHOLD SWEEP — ALL-TIME BTCUSDT h300 (Kelly=20%)")
print("=" * 90)

for name, d in directories.items():
    preds, ress = load_predictions_and_resolutions(d, "h300")
    if not preds:
        print(f"\n  [{name}] — No h300 predictions found.\n")
        continue

    print(f"\n  [{name}] — {len(preds)} total BTCUSDT predictions")
    print(f"  {'Thresh':>7} | {'Trades':>6} | {'W':>3} / {'L':>3} | {'WinRate':>7} | {'P&L':>8} | {'Final$':>8} | Unresolved")
    print(f"  {'-'*7}-+-{'-'*6}-+-{'-'*3}-+-{'-'*3}-+-{'-'*7}-+-{'-'*8}-+-{'-'*8}-+-{'-'*10}")

    for t in THRESHOLDS:
        r = simulate(preds, ress, t, 0.20)
        print(f"  {t:>7.3f} | {r['trades']:>6} | {r['wins']:>3} / {r['losses']:>3} | {r['win_rate']:>6.1f}% | ${r['pnl']:>+7.2f} | ${r['final_bankroll']:>7.2f} | {r['unresolved']}")

# ═══════════════════════════════════════════════════════════════
# PART 2: Kelly fraction comparison at best thresholds
# ═══════════════════════════════════════════════════════════════
print("\n\n" + "=" * 90)
print("  PART 2: KELLY FRACTION COMPARISON (20% / 50% / Full)")
print("  — Only for thresholds 0.53, 0.54, 0.55, 0.56")
print("=" * 90)

focus_thresholds = [0.53, 0.54, 0.55, 0.56]

for name, d in directories.items():
    preds, ress = load_predictions_and_resolutions(d, "h300")
    if not preds:
        continue

    print(f"\n  [{name}]")
    print(f"  {'Thresh':>7} | {'Kelly':>5} | {'Trades':>6} | {'W':>3} / {'L':>3} | {'WinRate':>7} | {'P&L':>8} | {'Final$':>8}")
    print(f"  {'-'*7}-+-{'-'*5}-+-{'-'*6}-+-{'-'*3}-+-{'-'*3}-+-{'-'*7}-+-{'-'*8}-+-{'-'*8}")

    for t in focus_thresholds:
        for kf in KELLY_FRACTIONS:
            r = simulate(preds, ress, t, kf)
            kf_label = f"{int(kf*100)}%"
            print(f"  {t:>7.3f} | {kf_label:>5} | {r['trades']:>6} | {r['wins']:>3} / {r['losses']:>3} | {r['win_rate']:>6.1f}% | ${r['pnl']:>+7.2f} | ${r['final_bankroll']:>7.2f}")


# ═══════════════════════════════════════════════════════════════
# PART 3: Individual trade log for Model A & Model A Clone at 0.54
# ═══════════════════════════════════════════════════════════════
print("\n\n" + "=" * 90)
print("  PART 3: TRADE-BY-TRADE LOG — Model A & Clone @ 0.54 gate")
print("=" * 90)

for name in ["Model A", "Model A Clone"]:
    d = directories[name]
    preds, ress = load_predictions_and_resolutions(d, "h300")
    if not preds:
        continue

    r = simulate(preds, ress, 0.54, 0.20)
    print(f"\n  [{name}] — {r['trades']} trades at 0.54 gate")
    print(f"  {'Time (UTC)':>20} | {'Dir':>4} | {'Conf':>6} | {'Result':>6} | {'P&L':>8} | {'Bankroll':>9}")
    print(f"  {'-'*20}-+-{'-'*4}-+-{'-'*6}-+-{'-'*6}-+-{'-'*8}-+-{'-'*9}")

    for dt, direction, conf, result, pnl, bank in r["details"]:
        ts_str = dt.strftime("%Y-%m-%d %H:%M")
        print(f"  {ts_str:>20} | {direction:>4} | {conf:>6.4f} | {'  WIN' if result == '+' else ' LOSS':>6} | ${pnl:>+7.2f} | ${bank:>8.2f}")


# ═══════════════════════════════════════════════════════════════
# PART 4: Confidence distribution histogram
# ═══════════════════════════════════════════════════════════════
print("\n\n" + "=" * 90)
print("  PART 4: CONFIDENCE DISTRIBUTION (BTCUSDT h300)")
print("=" * 90)

buckets = [(0.50, 0.51), (0.51, 0.52), (0.52, 0.53), (0.53, 0.54),
           (0.54, 0.55), (0.55, 0.56), (0.56, 0.57), (0.57, 0.58),
           (0.58, 0.59), (0.59, 0.60), (0.60, 1.00)]

for name, d in directories.items():
    preds, ress = load_predictions_and_resolutions(d, "h300")
    if not preds:
        continue

    print(f"\n  [{name}] — {len(preds)} predictions")
    print(f"  {'Bucket':>12} | {'Count':>5} | {'Resolved':>8} | {'W':>3} / {'L':>3} | {'WinRate':>7}")
    print(f"  {'-'*12}-+-{'-'*5}-+-{'-'*8}-+-{'-'*3}-+-{'-'*3}-+-{'-'*7}")

    for lo, hi in buckets:
        count = 0
        w = 0
        l = 0
        for p in preds:
            proba = p.get("pred_proba", 0.5)
            direction = p.get("pred_direction")
            conf = proba if direction == "up" else (1.0 - proba)
            if lo <= conf < hi:
                count += 1
                pid = p.get("prediction_id")
                res = ress.get(pid)
                if res:
                    correct = res.get("prediction_correct")
                    if correct is True:
                        w += 1
                    elif correct is False:
                        l += 1

        resolved = w + l
        wr = (w / resolved * 100) if resolved > 0 else 0
        label = f"[{lo:.2f}-{hi:.2f})"
        print(f"  {label:>12} | {count:>5} | {resolved:>8} | {w:>3} / {l:>3} | {wr:>6.1f}%")
