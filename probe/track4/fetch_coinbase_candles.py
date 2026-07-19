#!/usr/bin/env python3.12
"""Fetch Coinbase Exchange 1m candles for track4 training/labels.

Public API, no auth. 300 candles/request, paginate backwards.
Writes CSV per product: ts,open,high,low,close,volume (ts epoch seconds UTC, ascending).
"""
import argparse, csv, json, time, urllib.request
from datetime import datetime, timezone

API = "https://api.exchange.coinbase.com/products/{p}/candles?granularity=60&start={s}&end={e}"


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(product: str, t0: int, t1: int, out: str):
    rows = {}
    cur = t0
    n_req = 0
    while cur < t1:
        end = min(cur + 300 * 60, t1)
        url = API.format(p=product, s=iso(cur), e=iso(end))
        batch = []
        for attempt in range(6):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "track4-candles/1.0"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    batch = json.loads(r.read())
                break
            except Exception:  # noqa: BLE001 — retry any transient failure
                if attempt == 5:
                    raise
                time.sleep(2 ** attempt)
        for t, lo, hi, op, cl, vol in batch:
            rows[int(t)] = (op, hi, lo, cl, vol)
        n_req += 1
        if n_req % 100 == 0:
            print(f"[{product}] {n_req} req, {len(rows)} candles, at {iso(cur)}", flush=True)
        cur = end
        time.sleep(0.15)  # ~6.7 req/s, under the 10/s public limit
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for t in sorted(rows):
            op, hi, lo, cl, vol = rows[t]
            w.writerow([t, op, hi, lo, cl, vol])
    print(f"[{product}] DONE {len(rows)} candles -> {out}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True)
    ap.add_argument("--start", required=True, help="epoch seconds UTC")
    ap.add_argument("--end", required=True, help="epoch seconds UTC")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    fetch(a.product, int(a.start), int(a.end), a.out)
