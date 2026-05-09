#!/usr/bin/env python3
"""
Monitor live prediction splits (up vs down) and divergence from p_market.
Designed for 2-hour window checks during high-volume US market hours.

Usage:
    python -m monitoring.live_split --log-dir /data/logs --hours 2
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", type=str, default="/data/logs")
    parser.add_argument("--hours", type=float, default=2.0, help="Lookback window in hours")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    if not log_dir.exists():
        print(f"Directory {log_dir} not found.")
        sys.exit(1)

    now_utc = datetime.now(timezone.utc)
    cutoff_ms = int((now_utc - timedelta(hours=args.hours)).timestamp() * 1000)

    print(f"=== Live Split Monitor ({args.hours}h window) ===")
    print(f"Current UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Looking back to: {datetime.fromtimestamp(cutoff_ms/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n")

    for file_path in sorted(log_dir.glob("predictions_*.jsonl")):
        model_name = file_path.stem.replace("predictions_", "")
        
        preds = []
        try:
            with open(file_path, "r") as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        r = json.loads(line)
                        if r.get("record_type") == "prediction" and not r.get("warmup", False):
                            if r.get("ts_model_ran_ms", 0) >= cutoff_ms:
                                preds.append(r)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
            continue

        if not preds:
            print(f"Model: {model_name} (0 predictions in window)")
            continue

        by_sym = defaultdict(list)
        for p in preds:
            by_sym[p.get("symbol", "UNKNOWN")].append(p)

        print(f"Model: {model_name} ({len(preds)} total predictions)")
        print("-" * 50)
        
        for sym, sym_preds in sorted(by_sym.items()):
            n = len(sym_preds)
            n_up = sum(1 for p in sym_preds if p.get("pred_direction") == "up")
            n_down = len(sym_preds) - n_up
            pct_down = (n_down / n) * 100 if n > 0 else 0
            
            # calculate mean p_model
            mean_p_model = sum(p.get("pred_proba", 0.5) for p in sym_preds) / n if n > 0 else 0
            
            # calculate mean p_market and divergence for those with p_market
            with_pm = [p for p in sym_preds if p.get("p_market") is not None]
            pm_str = "N/A"
            div_str = "N/A"
            if with_pm:
                mean_pm = sum(p["p_market"] for p in with_pm) / len(with_pm)
                mean_div = sum((p.get("pred_proba", 0.5) - p["p_market"]) for p in with_pm) / len(with_pm)
                pm_str = f"{mean_pm:.4f}"
                div_str = f"{mean_div:+.4f}"

            print(f"  {sym:<8} | N={n:<3} | Up={n_up:<3} Down={n_down:<3} ({pct_down:4.1f}% ↓) | "
                  f"p_model={mean_p_model:.4f}  p_market={pm_str}  div={div_str}")
        print("\n")

if __name__ == "__main__":
    main()
