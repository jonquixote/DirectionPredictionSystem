import json
import time
import urllib.request
from datetime import datetime, timezone
from collections import defaultdict

# ─── Part 1: Dig into Model A Clone [0.55-0.56) bucket trades ───

print("=" * 90)
print("  PART 1: DEEP DIVE — Model A Clone [0.55-0.56) bucket (14 trades)")
print("=" * 90)

pred_file = "/data/logs_model_a_clone/predictions_h300.jsonl"
predictions = {}
resolutions = {}

with open(pred_file) as f:
    for line in f:
        try:
            d = json.loads(line)
            pid = d.get("prediction_id")
            rt = d.get("record_type")
            if rt == "prediction" and d.get("symbol") == "BTCUSDT":
                predictions[pid] = d
            elif rt == "resolution":
                resolutions[pid] = d
        except:
            pass

print(f"\n  {'Time (UTC)':>20} | {'Dir':>4} | {'Conf':>6} | {'Result':>6} | {'Price@Open':>11} | {'Price@Close':>12} | {'Move':>8} | {'Hour':>4} | {'DoW':>3}")
print(f"  {'-'*20}-+-{'-'*4}-+-{'-'*6}-+-{'-'*6}-+-{'-'*11}-+-{'-'*12}-+-{'-'*8}-+-{'-'*4}-+-{'-'*3}")

bucket_trades = []
for pid, p in sorted(predictions.items(), key=lambda x: x[1].get("ts_model_ran_ms", 0)):
    proba = p.get("pred_proba", 0.5)
    direction = p.get("pred_direction")
    conf = proba if direction == "up" else (1.0 - proba)
    
    if not (0.55 <= conf < 0.56):
        continue
    
    res = resolutions.get(pid)
    if not res:
        continue
    
    ts = p.get("ts_model_ran_ms", 0)
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    price_open = p.get("price_at_open", 0)
    price_close = res.get("price_at_contract_close", 0)
    correct = res.get("prediction_correct")
    contract_result = res.get("contract_result", "?")
    move = price_close - price_open
    move_pct = (move / price_open * 100) if price_open else 0
    
    result_str = "  WIN" if correct else " LOSS"
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow = dow_names[dt.weekday()]
    
    print(f"  {dt.strftime('%Y-%m-%d %H:%M'):>20} | {direction:>4} | {conf:>6.4f} | {result_str:>6} | ${price_open:>10.2f} | ${price_close:>11.2f} | {move:>+7.2f} | {dt.hour:>4} | {dow:>3}")
    
    bucket_trades.append({
        "direction": direction,
        "correct": correct,
        "hour": dt.hour,
        "dow": dt.weekday(),
        "move": move,
        "conf": conf,
        "p_market": p.get("p_market"),
    })

# Breakdown
wins = [t for t in bucket_trades if t["correct"]]
losses = [t for t in bucket_trades if not t["correct"]]
print(f"\n  Summary: {len(wins)}W / {len(losses)}L")

# Direction breakdown
up_trades = [t for t in bucket_trades if t["direction"] == "up"]
down_trades = [t for t in bucket_trades if t["direction"] == "down"]
up_w = sum(1 for t in up_trades if t["correct"])
down_w = sum(1 for t in down_trades if t["correct"])
print(f"  UP trades:   {len(up_trades)} total, {up_w}W / {len(up_trades)-up_w}L ({up_w/len(up_trades)*100:.0f}% WR)" if up_trades else "  UP trades: 0")
print(f"  DOWN trades: {len(down_trades)} total, {down_w}W / {len(down_trades)-down_w}L ({down_w/len(down_trades)*100:.0f}% WR)" if down_trades else "  DOWN trades: 0")

# Hour breakdown
print("\n  Hour breakdown (losses):")
hour_counts = defaultdict(int)
for t in losses:
    hour_counts[t["hour"]] += 1
for h in sorted(hour_counts):
    print(f"    {h:02d}:00 UTC — {hour_counts[h]} losses")


# ─── Part 2: Retroactively resolve Model A and Model B predictions ───
# using Binance klines

print("\n\n" + "=" * 90)
print("  PART 2: RETROACTIVE RESOLUTION — Model A & Model B")
print("  Using Binance 1m klines to check price 900s after each prediction")
print("=" * 90)

def fetch_klines(symbol, start_ms, end_ms):
    """Fetch 1m klines from Binance for a time range."""
    all_klines = {}
    current = start_ms
    while current < end_ms:
        batch_end = min(current + 500 * 60_000, end_ms)  # 500 candles max per request
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&startTime={current}&endTime={batch_end}&limit=1000"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                for k in data:
                    open_time = k[0]
                    close_price = float(k[4])
                    all_klines[open_time] = close_price
        except Exception as e:
            print(f"  [WARN] Kline fetch error at {current}: {e}")
            break
        current = batch_end + 60_000
        time.sleep(0.1)  # Rate limit
    return all_klines

def resolve_predictions(name, log_dir, klines_cache):
    """Load predictions, resolve using klines, return resolved list."""
    pred_file = f"{log_dir}predictions_h300.jsonl"
    preds = {}
    existing_res = {}
    
    with open(pred_file) as f:
        for line in f:
            try:
                d = json.loads(line)
                pid = d.get("prediction_id")
                rt = d.get("record_type")
                if rt == "prediction" and d.get("symbol") == "BTCUSDT":
                    preds[pid] = d
                elif rt == "resolution":
                    existing_res[pid] = d
            except:
                pass
    
    resolved = []
    unresolvable = 0
    
    for pid, p in sorted(preds.items(), key=lambda x: x[1].get("ts_model_ran_ms", 0)):
        # If already resolved, use existing
        if pid in existing_res:
            r = existing_res[pid]
            resolved.append({**p, "prediction_correct": r["prediction_correct"], 
                           "price_at_close": r.get("price_at_contract_close"),
                           "source": "existing"})
            continue
        
        # Resolve using klines
        contract_open_ms = p.get("ts_contract_open_ms", 0)
        resolve_ms = contract_open_ms + 900_000  # 900s later
        price_open = p.get("price_at_open", 0)
        direction = p.get("pred_direction")
        
        # Find the 1m candle that contains resolve_ms
        candle_ms = (resolve_ms // 60_000) * 60_000
        
        price_close = klines_cache.get(candle_ms)
        if price_close is None:
            # Try adjacent candles
            for offset in [-60_000, 60_000, -120_000, 120_000]:
                price_close = klines_cache.get(candle_ms + offset)
                if price_close:
                    break
        
        if price_close is None or price_open == 0:
            unresolvable += 1
            continue
        
        # Determine correctness
        if direction == "up":
            correct = price_close > price_open
        else:
            correct = price_close < price_open
        
        resolved.append({**p, "prediction_correct": correct, 
                        "price_at_close": price_close,
                        "source": "kline"})
    
    return resolved, unresolvable

# Collect all timestamps we need klines for
print("\n  Collecting timestamps from Model A and Model B...")
all_timestamps = []
for log_dir in ["/data/logs_model_a/", "/data/logs_model_b/"]:
    pred_file = f"{log_dir}predictions_h300.jsonl"
    try:
        with open(pred_file) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if d.get("record_type") == "prediction" and d.get("symbol") == "BTCUSDT":
                        ts = d.get("ts_contract_open_ms", 0)
                        if ts > 0:
                            all_timestamps.append(ts)
                            all_timestamps.append(ts + 900_000)
                except:
                    pass
    except:
        pass

if all_timestamps:
    min_ts = min(all_timestamps) - 120_000
    max_ts = max(all_timestamps) + 120_000
    print(f"  Fetching Binance klines from {datetime.fromtimestamp(min_ts/1000, tz=timezone.utc)} to {datetime.fromtimestamp(max_ts/1000, tz=timezone.utc)}")
    klines = fetch_klines("BTCUSDT", min_ts, max_ts)
    print(f"  Fetched {len(klines)} 1m candles")
else:
    klines = {}
    print("  No timestamps found!")

# Resolve both models
for name, log_dir in [("Model A", "/data/logs_model_a/"), ("Model B", "/data/logs_model_b/")]:
    resolved, unresolvable = resolve_predictions(name, log_dir, klines)
    
    print(f"\n  [{name}] — {len(resolved)} resolved, {unresolvable} unresolvable")
    
    # Confidence distribution with win rates
    buckets = [(0.50, 0.51), (0.51, 0.52), (0.52, 0.53), (0.53, 0.54),
               (0.54, 0.55), (0.55, 0.56), (0.56, 0.57), (0.57, 0.58),
               (0.58, 0.59), (0.59, 0.60), (0.60, 1.00)]
    
    print(f"  {'Bucket':>12} | {'Count':>5} | {'W':>3} / {'L':>3} | {'WinRate':>7}")
    print(f"  {'-'*12}-+-{'-'*5}-+-{'-'*3}-+-{'-'*3}-+-{'-'*7}")
    
    for lo, hi in buckets:
        bucket_items = []
        for r in resolved:
            proba = r.get("pred_proba", 0.5)
            direction = r.get("pred_direction")
            conf = proba if direction == "up" else (1.0 - proba)
            if lo <= conf < hi:
                bucket_items.append(r)
        
        w = sum(1 for r in bucket_items if r["prediction_correct"])
        l = sum(1 for r in bucket_items if not r["prediction_correct"])
        total = w + l
        wr = (w / total * 100) if total > 0 else 0
        label = f"[{lo:.2f}-{hi:.2f})"
        print(f"  {label:>12} | {total:>5} | {w:>3} / {l:>3} | {wr:>6.1f}%")
    
    # Threshold sweep
    print(f"\n  Threshold sweep:")
    print(f"  {'Thresh':>7} | {'Trades':>6} | {'W':>3} / {'L':>3} | {'WinRate':>7}")
    print(f"  {'-'*7}-+-{'-'*6}-+-{'-'*3}-+-{'-'*3}-+-{'-'*7}")
    
    for thresh in [0.51, 0.52, 0.53, 0.535, 0.54, 0.545, 0.55, 0.555, 0.56]:
        w = 0
        l = 0
        for r in resolved:
            proba = r.get("pred_proba", 0.5)
            direction = r.get("pred_direction")
            conf = proba if direction == "up" else (1.0 - proba)
            if conf >= thresh:
                if r["prediction_correct"]:
                    w += 1
                else:
                    l += 1
        total = w + l
        wr = (w / total * 100) if total > 0 else 0
        print(f"  {thresh:>7.3f} | {total:>6} | {w:>3} / {l:>3} | {wr:>6.1f}%")
