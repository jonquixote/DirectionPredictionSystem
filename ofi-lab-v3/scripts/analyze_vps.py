import json
from datetime import datetime, timezone
import os

cutoff_ms = int((datetime.now(timezone.utc).timestamp() - 48*3600) * 1000)

for c_name, l_path in [
    ("Control (Original)", "/data/logs/paper_trades_h300.jsonl"),
    ("Model A (Shifted)", "/data/logs_model_a/paper_trades_h300.jsonl"),
    ("Model B (Extended)", "/data/logs_model_b/paper_trades_h300.jsonl")
]:
    print("=" * 60)
    print(f"  {c_name} — Last 48 Hours")
    print("=" * 60)
    
    if not os.path.exists(l_path):
        print("  Not found:", l_path)
        continue

    trades = []
    with open(l_path) as f:
        for line in f:
            try:
                r = json.loads(line)
                if r.get("record_type") == "trade_resolution" and r.get("ts_contract_close_ms", 0) > cutoff_ms:
                    trades.append(r)
            except: pass

    wins = sum(1 for t in trades if t.get("trade_result") == "win")
    losses = sum(1 for t in trades if t.get("trade_result") == "loss")
    total = wins + losses
    if total > 0:
        wr = wins / total * 100
        net = sum(t.get("net_pnl", 0) for t in trades)
        print("  Trades: %d (%dW / %dL)" % (total, wins, losses))
        print("  Win Rate: %.1f%%" % wr)
        print("  Net P&L: $%.2f" % net)

        print()
        print("  Timeline (most recent 10):")
        for t in trades[-10:]:
            ts = t.get("ts_contract_close_ms", 0)
            dt = datetime.fromtimestamp(ts/1000, tz=timezone.utc).strftime("%m-%d %H:%M")
            result = t.get("trade_result", "")
            net_pnl = t.get("net_pnl", 0)
            emoji = "W" if result == "win" else "L"
            print("    %s | %s | net=$%+.2f" % (dt, emoji, net_pnl))
    else:
        print("  No trades in last 48h")
