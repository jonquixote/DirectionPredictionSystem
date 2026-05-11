"""
Comprehensive Kalshi Live Trading Audit
========================================
1. Binance price vs Kalshi strike price comparison
2. Real Kalshi settlement vs paper trader resolution comparison
3. H300 BTC 900s prediction performance at different confidence gates
4. Network health analysis
5. Trade-by-trade breakdown with actual Kalshi outcomes
"""

import json
from datetime import datetime, timezone
from collections import defaultdict, Counter

PAPER_PATH = "/tmp/vps_paper_trades_h300_fresh.jsonl"
KALSHI_PATH = "/tmp/vps_kalshi_orders_fresh.jsonl"
PRED_PATH = "/tmp/vps_predictions_h300.jsonl"

# ─── Load data ──────────────────────────────────────────────────────────

def load_jsonl(path):
    records = []
    with open(path) as f:
        for line in f:
            if not line.strip(): continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records

paper_raw = load_jsonl(PAPER_PATH)
kalshi_raw = load_jsonl(KALSHI_PATH)
pred_raw = load_jsonl(PRED_PATH)

# ─── 1. Paper trader: H300 BTC 900s prediction performance ─────────────

print("=" * 80)
print("SECTION 1: H300 BTC 900s PAPER TRADER PERFORMANCE")
print("=" * 80)

# Separate entries and resolutions
entries = [r for r in paper_raw if r.get("record_type") == "trade_entry" and r.get("symbol") == "BTCUSDT" and r.get("contract_duration_seconds") == 900]
resolutions = {r.get("prediction_id"): r for r in paper_raw if r.get("record_type") == "trade_resolution" and r.get("symbol") == "BTCUSDT"}

# Match entries to resolutions
resolved_trades = []
for e in entries:
    pid = e.get("prediction_id")
    res = resolutions.get(pid)
    if res:
        resolved_trades.append({
            "ts": e.get("ts_model_ran_ms", 0),
            "pred_proba": e.get("pred_proba"),
            "pred_direction": e.get("pred_direction"),
            "price_at_open": e.get("price_at_open"),
            "correct": res.get("prediction_correct"),
            "price_at_close": res.get("price_at_close"),
            "suppressed": e.get("suppressed_reason"),
            "pid": pid,
        })

# Only unsuppressed
active_trades = [t for t in resolved_trades if not t["suppressed"]]

print(f"\nTotal h300 BTC 900s trade entries: {len(entries)}")
print(f"Total resolved: {len(resolved_trades)}")
print(f"Active (not suppressed): {len(active_trades)}")

# ─── Confidence gate analysis ───────────────────────────────────────────

print("\n" + "-" * 60)
print("CONFIDENCE GATE ANALYSIS (H300 BTC 900s, ALL TIME)")
print("-" * 60)

gates = [0.50, 0.51, 0.52, 0.53, 0.54, 0.55, 0.56, 0.57, 0.58, 0.59, 0.60]

for gate in gates:
    # model_p > 0.5 means "up" confident, model_p < 0.5 means "down" confident
    # "confidence" = max(model_p, 1 - model_p) for directional confidence
    filtered = [t for t in active_trades if max(t["pred_proba"], 1 - t["pred_proba"]) >= gate]
    if not filtered:
        print(f"  Gate >= {gate:.2f}: 0 trades")
        continue
    wins = sum(1 for t in filtered if t["correct"])
    total = len(filtered)
    wr = wins / total * 100
    print(f"  Gate >= {gate:.2f}: {total:4d} trades, {wins:4d} wins, WR={wr:.1f}%")

# ─── Last 24h performance ──────────────────────────────────────────────

print("\n" + "-" * 60)
print("LAST 24 HOURS (H300 BTC 900s)")
print("-" * 60)

now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
cutoff_24h = now_ms - 24 * 3600 * 1000

recent_trades = [t for t in active_trades if t["ts"] >= cutoff_24h]

for gate in gates:
    filtered = [t for t in recent_trades if max(t["pred_proba"], 1 - t["pred_proba"]) >= gate]
    if not filtered:
        print(f"  Gate >= {gate:.2f}: 0 trades")
        continue
    wins = sum(1 for t in filtered if t["correct"])
    total = len(filtered)
    wr = wins / total * 100
    print(f"  Gate >= {gate:.2f}: {total:4d} trades, {wins:4d} wins, WR={wr:.1f}%")

# ─── Last 48h ──────────────────────────────────────────────────────────

print("\n" + "-" * 60)
print("LAST 48 HOURS (H300 BTC 900s)")
print("-" * 60)

cutoff_48h = now_ms - 48 * 3600 * 1000

recent_48h = [t for t in active_trades if t["ts"] >= cutoff_48h]

for gate in gates:
    filtered = [t for t in recent_48h if max(t["pred_proba"], 1 - t["pred_proba"]) >= gate]
    if not filtered:
        print(f"  Gate >= {gate:.2f}: 0 trades")
        continue
    wins = sum(1 for t in filtered if t["correct"])
    total = len(filtered)
    wr = wins / total * 100
    print(f"  Gate >= {gate:.2f}: {total:4d} trades, {wins:4d} wins, WR={wr:.1f}%")


# ─── 2. Binance price vs Kalshi strike comparison ──────────────────────

print("\n" + "=" * 80)
print("SECTION 2: BINANCE PRICE vs KALSHI STRIKE PRICE COMPARISON")
print("=" * 80)

# Build a map: boundary_ts -> paper open price
paper_open_map = {}
for e in entries:
    bms = e.get("ts_contract_open_ms")
    if bms and not e.get("suppressed_reason"):
        paper_open_map[bms // 1000] = e.get("price_at_open")

# Parse Kalshi tickers to extract strike price
# Ticker format: KXBTC15M-26MAY060315-15
# The strike is encoded in the market data, not the ticker itself
# We need to compare what the paper trader saw vs what Kalshi was offering

kalshi_placed = [k for k in kalshi_raw if k.get("gate_result") == "PLACED" and k.get("symbol") == "BTCUSDT"]

print(f"\nKalshi placed orders: {len(kalshi_placed)}")
print(f"\nTrade-by-trade: Binance open price vs Kalshi market yes_mid")
print(f"{'Timestamp':<22} {'Ticker':<30} {'Side':<5} {'Binance Open':>14} {'Kalshi yesMid':>14} {'Confidence':>10}")
print("-" * 100)

for k in kalshi_placed:
    bts = k.get("boundary_ts")
    binance_open = paper_open_map.get(bts, "???")
    yes_mid = k.get("market_yes_price")
    ts = k.get("ts", "")
    ticker = k.get("ticker", "")
    side = k.get("side", "")
    conf = k.get("model_p", 0)
    dir_conf = max(conf, 1 - conf)
    
    if isinstance(binance_open, (int, float)):
        print(f"{ts:<22} {ticker:<30} {side:<5} ${binance_open:>12,.2f} {yes_mid:>13.4f} {dir_conf:>9.4f}")
    else:
        print(f"{ts:<22} {ticker:<30} {side:<5} {'???':>14} {yes_mid:>13.4f} {dir_conf:>9.4f}")


# ─── 3. Kalshi gate result breakdown ───────────────────────────────────

print("\n" + "=" * 80)
print("SECTION 3: KALSHI GATE RESULT BREAKDOWN")
print("=" * 80)

btc_orders = [k for k in kalshi_raw if k.get("symbol") == "BTCUSDT"]
gate_counts = Counter(k.get("gate_result") for k in btc_orders)

for reason, count in gate_counts.most_common():
    print(f"  {reason}: {count}")

print(f"\n  Total events: {len(btc_orders)}")


# ─── 4. Paper resolution vs Kalshi actual outcome comparison ────────────

print("\n" + "=" * 80)
print("SECTION 4: PAPER RESOLUTION vs KALSHI REAL OUTCOME")
print("(Uses paper trader's prediction_correct as ground truth check)")
print("=" * 80)

# For each Kalshi PLACED trade, find the paper resolution
paper_res_by_boundary = {}
for r in paper_raw:
    if r.get("record_type") == "trade_resolution" and r.get("symbol") == "BTCUSDT":
        bms = r.get("ts_contract_open_ms")
        if bms:
            paper_res_by_boundary[bms // 1000] = r

print(f"\n{'Timestamp':<22} {'Ticker':<30} {'Side':<5} {'Paper':>7} {'PaperOpen':>12} {'PaperClose':>12} {'Delta':>10}")
print("-" * 100)

paper_wins = 0
paper_losses = 0
paper_unresolved = 0

for k in kalshi_placed:
    bts = k.get("boundary_ts")
    res = paper_res_by_boundary.get(bts)
    side = k.get("side", "")
    ts = k.get("ts", "")
    ticker = k.get("ticker", "")
    
    if res:
        correct = res.get("prediction_correct")
        p_open = res.get("price_at_open", 0)
        p_close = res.get("price_at_close", 0)
        delta = p_close - p_open
        
        paper_result = "WIN" if correct else "LOSS"
        if correct:
            paper_wins += 1
        else:
            paper_losses += 1
        
        print(f"{ts:<22} {ticker:<30} {side:<5} {paper_result:>7} ${p_open:>10,.2f} ${p_close:>10,.2f} {delta:>+10.2f}")
    else:
        paper_unresolved += 1
        print(f"{ts:<22} {ticker:<30} {side:<5} {'PENDING':>7}")

print(f"\nPaper results: {paper_wins} wins, {paper_losses} losses, {paper_unresolved} pending")
if paper_wins + paper_losses > 0:
    print(f"Paper win rate: {paper_wins/(paper_wins+paper_losses)*100:.1f}%")


# ─── 5. Direction analysis ─────────────────────────────────────────────

print("\n" + "=" * 80)
print("SECTION 5: DIRECTION ANALYSIS")
print("(Does the model favor UP or DOWN? How does each perform?)")
print("=" * 80)

up_trades = [t for t in active_trades if t["pred_direction"] == "up"]
down_trades = [t for t in active_trades if t["pred_direction"] == "down"]

up_wins = sum(1 for t in up_trades if t["correct"])
down_wins = sum(1 for t in down_trades if t["correct"])

print(f"\nAll time (active, h300 BTC 900s):")
print(f"  UP predictions:   {len(up_trades):4d} | Wins: {up_wins:4d} | WR: {up_wins/max(1,len(up_trades))*100:.1f}%")
print(f"  DOWN predictions: {len(down_trades):4d} | Wins: {down_wins:4d} | WR: {down_wins/max(1,len(down_trades))*100:.1f}%")

# Last 24h
up_24h = [t for t in recent_trades if t["pred_direction"] == "up"]
down_24h = [t for t in recent_trades if t["pred_direction"] == "down"]
up_wins_24h = sum(1 for t in up_24h if t["correct"])
down_wins_24h = sum(1 for t in down_24h if t["correct"])

print(f"\nLast 24 hours:")
print(f"  UP predictions:   {len(up_24h):4d} | Wins: {up_wins_24h:4d} | WR: {up_wins_24h/max(1,len(up_24h))*100:.1f}%")
print(f"  DOWN predictions: {len(down_24h):4d} | Wins: {down_wins_24h:4d} | WR: {down_wins_24h/max(1,len(down_24h))*100:.1f}%")


# ─── 6. Kalshi live trader: Sizing & cost analysis ─────────────────────

print("\n" + "=" * 80)
print("SECTION 6: KALSHI SIZING & COST ANALYSIS")
print("=" * 80)

total_cost = 0
total_fees = 0

for k in kalshi_placed:
    side = k.get("side", "yes").lower()
    price_cents = k.get("final_yes_price_cents", 0)
    contracts = k.get("final_contracts", 0)
    fee = k.get("fee_estimate_usd", 0)
    
    if side == "no":
        cost_per = (100 - price_cents) / 100.0
    else:
        cost_per = price_cents / 100.0
    
    cost = contracts * cost_per + fee
    total_cost += cost
    total_fees += fee

print(f"\nTotal capital deployed across {len(kalshi_placed)} trades: ${total_cost:.2f}")
print(f"Total fees paid: ${total_fees:.2f}")
print(f"Average cost per trade: ${total_cost/max(1,len(kalshi_placed)):.2f}")
print(f"Average contracts per trade: {sum(k.get('final_contracts',0) for k in kalshi_placed)/max(1,len(kalshi_placed)):.1f}")


# ─── 7. Price movement magnitude analysis ──────────────────────────────

print("\n" + "=" * 80)
print("SECTION 7: PRICE MOVEMENT MAGNITUDE (PAPER TRADER)")
print("(How much does the price actually move in 15 minutes?)")
print("=" * 80)

close_deltas = []
for t in active_trades:
    if t.get("price_at_close") and t.get("price_at_open"):
        delta = abs(t["price_at_close"] - t["price_at_open"])
        pct = delta / t["price_at_open"] * 100
        close_deltas.append(pct)

if close_deltas:
    close_deltas.sort()
    print(f"\n15-min absolute price movement (BTC):")
    print(f"  Mean:   {sum(close_deltas)/len(close_deltas):.4f}%")
    print(f"  Median: {close_deltas[len(close_deltas)//2]:.4f}%")
    print(f"  P10:    {close_deltas[int(len(close_deltas)*0.1)]:.4f}%")
    print(f"  P90:    {close_deltas[int(len(close_deltas)*0.9)]:.4f}%")
    print(f"  Max:    {max(close_deltas):.4f}%")


# ─── 8. Confidence vs actual edge ──────────────────────────────────────

print("\n" + "=" * 80)
print("SECTION 8: CALIBRATION CHECK — PREDICTED CONFIDENCE vs ACTUAL WIN RATE")
print("=" * 80)

# Bin by directional confidence
bins = [(0.50, 0.52), (0.52, 0.54), (0.54, 0.56), (0.56, 0.58), (0.58, 0.60), (0.60, 0.65), (0.65, 1.0)]

print(f"\n{'Conf Range':<15} {'Trades':>7} {'Wins':>7} {'WR':>8} {'Expected':>10}")
print("-" * 50)

for lo, hi in bins:
    binned = [t for t in active_trades if lo <= max(t["pred_proba"], 1 - t["pred_proba"]) < hi]
    if not binned:
        print(f"[{lo:.2f}, {hi:.2f})   {'0':>7}")
        continue
    wins = sum(1 for t in binned if t["correct"])
    wr = wins / len(binned) * 100
    expected_mid = (lo + hi) / 2 * 100
    print(f"[{lo:.2f}, {hi:.2f})   {len(binned):>7} {wins:>7} {wr:>7.1f}% {expected_mid:>9.1f}%")


# ─── 9. Kalshi order fill analysis (partial fills, slippage) ────────────

print("\n" + "=" * 80)
print("SECTION 9: KALSHI FILL ANALYSIS")
print("(Do we get filled at the price we expected?)")
print("=" * 80)

print(f"\n{'Timestamp':<22} {'Side':<5} {'Contracts':>10} {'YesPx (c)':>10} {'YesMid':>8} {'Fee':>8}")
print("-" * 70)

for k in kalshi_placed:
    ts = k.get("ts", "")
    side = k.get("side", "")
    contracts = k.get("final_contracts", 0)
    yes_px = k.get("final_yes_price_cents", 0)
    yes_mid = k.get("market_yes_price", 0)
    fee = k.get("fee_estimate_usd", 0)
    
    print(f"{ts:<22} {side:<5} {contracts:>10} {yes_px:>10} {yes_mid:>8.4f} ${fee:>7.3f}")


# ─── 10. Win streak / losing streak analysis ───────────────────────────

print("\n" + "=" * 80)
print("SECTION 10: STREAK ANALYSIS (PAPER, ACTIVE H300 BTC 900s)")
print("=" * 80)

sorted_trades = sorted(active_trades, key=lambda t: t["ts"])

max_win_streak = 0
max_loss_streak = 0
current_streak = 0
streak_type = None

for t in sorted_trades:
    if t["correct"]:
        if streak_type == "win":
            current_streak += 1
        else:
            streak_type = "win"
            current_streak = 1
        max_win_streak = max(max_win_streak, current_streak)
    else:
        if streak_type == "loss":
            current_streak += 1
        else:
            streak_type = "loss"
            current_streak = 1
        max_loss_streak = max(max_loss_streak, current_streak)

print(f"\nMax win streak:  {max_win_streak}")
print(f"Max loss streak: {max_loss_streak}")

# Last 10 trades
last_10 = sorted_trades[-10:]
print(f"\nLast 10 paper trades:")
for t in last_10:
    ts_str = datetime.fromtimestamp(t["ts"]/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    result = "WIN" if t["correct"] else "LOSS"
    conf = max(t["pred_proba"], 1 - t["pred_proba"])
    print(f"  {ts_str} | {t['pred_direction']:>4} | conf={conf:.4f} | {result}")


# ─── 11. Time-of-day analysis ──────────────────────────────────────────

print("\n" + "=" * 80)
print("SECTION 11: HOUR-OF-DAY PERFORMANCE (UTC, H300 BTC 900s)")
print("=" * 80)

hour_stats = defaultdict(lambda: {"wins": 0, "losses": 0})

for t in active_trades:
    hour = datetime.fromtimestamp(t["ts"]/1000, tz=timezone.utc).hour
    if t["correct"]:
        hour_stats[hour]["wins"] += 1
    else:
        hour_stats[hour]["losses"] += 1

print(f"\n{'Hour UTC':>8} {'Trades':>8} {'Wins':>8} {'Losses':>8} {'WR':>8}")
print("-" * 45)

for h in sorted(hour_stats.keys()):
    s = hour_stats[h]
    total = s["wins"] + s["losses"]
    wr = s["wins"] / total * 100 if total > 0 else 0
    print(f"{h:>8} {total:>8} {s['wins']:>8} {s['losses']:>8} {wr:>7.1f}%")


print("\n" + "=" * 80)
print("AUDIT COMPLETE")
print("=" * 80)
