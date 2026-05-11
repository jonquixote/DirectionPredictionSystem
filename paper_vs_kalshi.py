"""
Part 3: Paper trader vs Kalshi outcome comparison.
For each placed Kalshi trade, compare what the paper trader resolved vs what Kalshi resolved.
"""
import json
from datetime import datetime, timezone

PAPER_PATH = "/tmp/vps_paper_trades_h300_fresh.jsonl"
KALSHI_PATH = "/tmp/vps_kalshi_orders_fresh.jsonl"

paper_raw = []
with open(PAPER_PATH) as f:
    for line in f:
        if not line.strip(): continue
        paper_raw.append(json.loads(line))

kalshi_raw = []
with open(KALSHI_PATH) as f:
    for line in f:
        if not line.strip(): continue
        kalshi_raw.append(json.loads(line))

# Build paper resolution map by boundary_ts
# Entries: boundary_ts -> entry data
entries_by_boundary = {}
for r in paper_raw:
    if r.get("record_type") == "trade_entry" and r.get("symbol") == "BTCUSDT" and r.get("contract_duration_seconds") == 900:
        bms = r.get("ts_contract_open_ms")
        if bms and not r.get("suppressed_reason"):
            entries_by_boundary[bms] = r

# Resolutions by prediction_id
resolutions = {}
for r in paper_raw:
    if r.get("record_type") == "trade_resolution" and r.get("symbol") == "BTCUSDT":
        resolutions[r.get("prediction_id")] = r

# Kalshi placed orders
kalshi_placed = [k for k in kalshi_raw if k.get("gate_result") == "PLACED" and k.get("symbol") == "BTCUSDT"]

# Kalshi real results from the API audit (manually entered from the output above)
kalshi_results = {
    "KXBTC15M-26MAY040615-15": "no",
    "KXBTC15M-26MAY040915-15": "no",
    "KXBTC15M-26MAY041200-00": "yes",
    "KXBTC15M-26MAY041230-30": "yes",
    "KXBTC15M-26MAY050915-15": "yes",
    "KXBTC15M-26MAY051430-30": "no",
    "KXBTC15M-26MAY051530-30": "yes",
    "KXBTC15M-26MAY051545-45": "yes",
    "KXBTC15M-26MAY051800-00": "yes",
    "KXBTC15M-26MAY052045-45": "no",
    "KXBTC15M-26MAY060100-00": "no",
    "KXBTC15M-26MAY060215-15": "no",
    "KXBTC15M-26MAY060300-00": "yes",
    "KXBTC15M-26MAY060315-15": "yes",
    "KXBTC15M-26MAY060330-30": "yes",
    "KXBTC15M-26MAY060345-45": "no",
    "KXBTC15M-26MAY061100-00": "no",
    "KXBTC15M-26MAY061245-45": "yes",
    "KXBTC15M-26MAY061545-45": "yes",
    "KXBTC15M-26MAY061815-15": "no",
    "KXBTC15M-26MAY061830-30": "no",
    "KXBTC15M-26MAY062015-15": "no",
    "KXBTC15M-26MAY062030-30": "no",
    "KXBTC15M-26MAY062145-45": "yes",
}

# Kalshi strike prices from the API
kalshi_strikes = {
    "KXBTC15M-26MAY040615-15": 79827.93,
    "KXBTC15M-26MAY040915-15": 78937.00,
    "KXBTC15M-26MAY041200-00": 79871.33,
    "KXBTC15M-26MAY041230-30": 79720.54,
    "KXBTC15M-26MAY050915-15": 81290.61,
    "KXBTC15M-26MAY051430-30": 81554.55,
    "KXBTC15M-26MAY051530-30": 81446.73,
    "KXBTC15M-26MAY051545-45": 81554.07,
    "KXBTC15M-26MAY051800-00": 81368.47,
    "KXBTC15M-26MAY052045-45": 80933.66,
    "KXBTC15M-26MAY060100-00": 81396.53,
    "KXBTC15M-26MAY060215-15": 81313.15,
    "KXBTC15M-26MAY060300-00": 81436.52,
    "KXBTC15M-26MAY060315-15": 81491.73,
    "KXBTC15M-26MAY060330-30": 81526.13,
    "KXBTC15M-26MAY060345-45": 81539.63,
    "KXBTC15M-26MAY061100-00": 81841.19,
    "KXBTC15M-26MAY061245-45": 81538.55,
    "KXBTC15M-26MAY061545-45": 81320.22,
    "KXBTC15M-26MAY061815-15": 81530.44,
    "KXBTC15M-26MAY061830-30": 81383.17,
    "KXBTC15M-26MAY062015-15": 81441.09,
    "KXBTC15M-26MAY062030-30": 81435.85,
    "KXBTC15M-26MAY062145-45": 81213.28,
}

print("=" * 120)
print("PAPER vs KALSHI OUTCOME COMPARISON")
print("=" * 120)
print(f"\n{'Timestamp':<22} {'Ticker':<30} {'Side':<5} {'Model Dir':>9} {'BinanceOpen':>12} {'KalshiStrike':>14} {'StrikeDelta':>12} {'Paper':>6} {'Kalshi':>6} {'Match':>6}")
print("-" * 120)

agree = 0
disagree = 0
paper_missing = 0

for k in kalshi_placed:
    ticker = k.get("ticker")
    side = k.get("side")
    ts = k.get("ts", "")
    bts = k.get("boundary_ts")
    bms = bts * 1000 if bts else 0
    model_p = k.get("model_p", 0)
    
    # Direction from model
    model_dir = "UP" if model_p > 0.5 else "DOWN"
    
    # Paper entry/resolution
    entry = entries_by_boundary.get(bms)
    paper_result = None
    binance_open = None
    if entry:
        pid = entry.get("prediction_id")
        res = resolutions.get(pid)
        binance_open = entry.get("price_at_open")
        if res:
            paper_result = "WIN" if res.get("prediction_correct") else "LOSS"
    
    # Kalshi real result
    kalshi_market_result = kalshi_results.get(ticker)
    kalshi_won = (kalshi_market_result == side) if kalshi_market_result else None
    kalshi_result_str = "WIN" if kalshi_won else "LOSS" if kalshi_won is not None else "???"
    
    # Strike price
    strike = kalshi_strikes.get(ticker, 0)
    
    if binance_open:
        delta = strike - binance_open
    else:
        delta = None
    
    # Compare
    if paper_result and kalshi_won is not None:
        match = "✓" if (paper_result == "WIN") == kalshi_won else "✗ DIFF"
        if (paper_result == "WIN") == kalshi_won:
            agree += 1
        else:
            disagree += 1
    else:
        match = "---"
        paper_missing += 1
    
    bo_str = f"${binance_open:>10,.2f}" if binance_open else "???".rjust(12)
    delta_str = f"{delta:>+11.2f}" if delta is not None else "???".rjust(12)
    pr_str = paper_result or "N/A"
    
    print(f"{ts:<22} {ticker:<30} {side:<5} {model_dir:>9} {bo_str} ${strike:>12,.2f} {delta_str} {pr_str:>6} {kalshi_result_str:>6} {match:>6}")

print(f"\nPaper-Kalshi Agreement: {agree}")
print(f"Paper-Kalshi Disagreement: {disagree}")
print(f"Paper resolution missing: {paper_missing}")
if agree + disagree > 0:
    print(f"Agreement Rate: {agree/(agree+disagree)*100:.1f}%")
