"""
Analyze optimal confidence gate for h300 BTCUSDT 900s predictions.

Runs inside the container against /data/logs_model_a_clone/predictions_h300.jsonl.
"""
import json
import math
from datetime import datetime, timezone

records = []
with open("/data/logs_model_a_clone/predictions_h300.jsonl") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue

preds = [r for r in records if r.get("record_type") == "prediction"]
resolutions = [r for r in records if r.get("record_type") == "resolution"]
res_map = {}
for r in resolutions:
    pid = r.get("prediction_id")
    if pid:
        res_map[pid] = r

merged_all = []
merged_15m = []
for p in preds:
    pid = p.get("prediction_id")
    if p.get("symbol") != "BTCUSDT":
        continue
    if pid not in res_map:
        continue
    bms = p.get("ts_contract_open_ms")
    r = res_map[pid]
    entry = {
        "pp": p.get("pred_proba"),
        "correct": r.get("prediction_correct"),
        "ts": p.get("ts_model_ran_ms", 0),
        "bms": bms,
    }
    merged_all.append(entry)
    if bms and (bms // 1000) % 900 == 0:
        merged_15m.append(entry)

merged_all.sort(key=lambda m: m["ts"])
merged_15m.sort(key=lambda m: m["ts"])

first_ts = merged_all[0]["ts"] if merged_all else 0
last_ts = merged_all[-1]["ts"] if merged_all else 0
span_days = (last_ts - first_ts) / (1000 * 86400) if first_ts else 1

print("Dataset: %d resolved BTCUSDT h300 predictions (all boundaries)" % len(merged_all))
print("  Of which %d are 15-min boundary aligned" % len(merged_15m))
print("Span: %.1f days (%.1f months)" % (span_days, span_days / 30))
print("First: %s" % datetime.fromtimestamp(first_ts / 1000, tz=timezone.utc).isoformat())
print("Last:  %s" % datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).isoformat())
print()

# Kalshi fee model
MARKET_PRICE = 0.50
FEE_RATE = 0.07
FEE_PER_CONTRACT = FEE_RATE * MARKET_PRICE * (1 - MARKET_PRICE)


def simulate_compounding(data, gate, kelly_frac=0.5, bankroll_start=100.0):
    qualifying = [m for m in data if m["pp"] > gate or m["pp"] < (1 - gate)]
    if not qualifying:
        return {
            "gate": gate, "trades": 0, "final": bankroll_start,
            "growth": 0, "monthly_ret": 0, "max_dd": 0, "wr": 0,
            "tpd": 0, "wins": 0, "losses": 0,
        }

    bankroll = bankroll_start
    wins = 0
    losses = 0
    peak = bankroll
    max_dd = 0
    cost = MARKET_PRICE + FEE_PER_CONTRACT
    payout_if_win = 1.0 - cost
    payout_odds = payout_if_win / cost

    for m in qualifying:
        pp = m["pp"]
        conf = max(pp, 1 - pp)

        p_est = conf
        q_est = 1 - p_est
        kelly_full = (p_est * payout_odds - q_est) / payout_odds
        kelly_bet_frac = max(0, kelly_full * kelly_frac)
        kelly_bet_frac = min(kelly_bet_frac, 0.15)

        bet_usd = bankroll * kelly_bet_frac
        if bet_usd < 1.0:
            bet_usd = min(1.0, bankroll * 0.5)

        if m["correct"]:
            profit = bet_usd * payout_odds
            bankroll += profit
            wins += 1
        else:
            bankroll -= bet_usd
            losses += 1

        peak = max(peak, bankroll)
        dd = (peak - bankroll) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
        if bankroll <= 1.0:
            break

    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0
    growth = (bankroll / bankroll_start - 1) * 100
    if span_days > 0 and bankroll > 0:
        monthly_ret = ((bankroll / bankroll_start) ** (30 / span_days) - 1) * 100
    else:
        monthly_ret = 0
    tpd = total / span_days if span_days > 0 else 0

    return {
        "gate": gate, "trades": total, "wins": wins, "losses": losses,
        "wr": wr, "final": bankroll, "growth": growth,
        "monthly_ret": monthly_ret, "max_dd": max_dd * 100, "tpd": tpd,
    }


# ── ALL predictions ──
print("=" * 78)
print("COMPOUNDING SIMULATION — ALL predictions (half-Kelly, $100 start)")
print("=" * 78)
print("Gate   | Trades | WR     | T/day |  Final$ |  Growth | Mo.Ret  | MaxDD")
print("-" * 78)

best_all = None
for g100 in range(50, 58):
    gate = g100 / 100.0
    r = simulate_compounding(merged_all, gate)
    print("  %.2f | %6d | %5.1f%% | %5.1f | $%7.2f | %+7.1f%% | %+7.1f%% | %5.1f%%" % (
        r["gate"], r["trades"], r["wr"], r["tpd"],
        r["final"], r["growth"], r["monthly_ret"], r["max_dd"]))
    if best_all is None or r["monthly_ret"] > best_all["monthly_ret"]:
        best_all = r

print()
print("BEST ALL: gate=%.2f (%+.1f%%/mo, %d trades, %.1f%% WR, $%.2f final)" % (
    best_all["gate"], best_all["monthly_ret"], best_all["trades"],
    best_all["wr"], best_all["final"]))

# ── 15-MIN BOUNDARY ONLY ──
print()
print("=" * 78)
print("COMPOUNDING SIMULATION — 15-MIN BOUNDARY ONLY (half-Kelly, $100 start)")
print("=" * 78)
print("Gate   | Trades | WR     | T/day |  Final$ |  Growth | Mo.Ret  | MaxDD")
print("-" * 78)
best15 = None
for g100 in range(50, 58):
    gate = g100 / 100.0
    r = simulate_compounding(merged_15m, gate)
    print("  %.2f | %6d | %5.1f%% | %5.1f | $%7.2f | %+7.1f%% | %+7.1f%% | %5.1f%%" % (
        r["gate"], r["trades"], r["wr"], r["tpd"],
        r["final"], r["growth"], r["monthly_ret"], r["max_dd"]))
    if best15 is None or r["monthly_ret"] > best15["monthly_ret"]:
        best15 = r

print()
print("BEST 15m: gate=%.2f (%+.1f%%/mo, %d trades, %.1f%% WR, $%.2f final)" % (
    best15["gate"], best15["monthly_ret"], best15["trades"],
    best15["wr"], best15["final"]))

# ── EV analysis ──
cost = MARKET_PRICE + FEE_PER_CONTRACT
profit_if_win = 1.0 - cost
print()
print("=" * 78)
print("EXPECTED VALUE PER TRADE (cost=$%.4f, profit/win=$%.4f)" % (cost, profit_if_win))
print("=" * 78)

print()
print("ALL predictions:")
for g100 in range(50, 58):
    gate = g100 / 100.0
    q = [m for m in merged_all if m["pp"] > gate or m["pp"] < (1 - gate)]
    t = len(q)
    if t == 0:
        continue
    c = sum(1 for m in q if m["correct"])
    wr = c / t
    ev = wr * profit_if_win - (1 - wr) * cost
    # Future: only 15-min boundary trades, so divide trade count by 3
    monthly_trades_15m = (t / span_days * 30) / 3
    monthly_ev = ev * monthly_trades_15m
    print("  %.2f: WR=%.3f EV/trade=$%+.4f trades/mo(15m)=%.0f monthly_EV=$%+.3f" % (
        gate, wr, ev, monthly_trades_15m, monthly_ev))

print()
print("15-MIN BOUNDARY predictions:")
for g100 in range(50, 58):
    gate = g100 / 100.0
    q = [m for m in merged_15m if m["pp"] > gate or m["pp"] < (1 - gate)]
    t = len(q)
    if t == 0:
        continue
    c = sum(1 for m in q if m["correct"])
    wr = c / t
    ev = wr * profit_if_win - (1 - wr) * cost
    monthly_trades = t / span_days * 30 if span_days > 0 else 0
    monthly_ev = ev * monthly_trades
    print("  %.2f: WR=%.3f EV/trade=$%+.4f trades/mo=%.0f monthly_EV=$%+.3f" % (
        gate, wr, ev, monthly_trades, monthly_ev))

# ── Calibration map ──
print()
print("=" * 78)
print("CALIBRATION MAP")
print("=" * 78)
import os
cal_path = "/data/calibration.json"
if os.path.exists(cal_path):
    with open(cal_path) as f:
        cal = json.load(f)
    print("File: %s (%d entries)" % (cal_path, len(cal)))
    items = sorted(cal.items(), key=lambda x: float(x[0]))
    for k, v in items:
        print("  raw=%.3f -> calibrated=%.4f" % (float(k), v))
else:
    print("No calibration file found at %s" % cal_path)
