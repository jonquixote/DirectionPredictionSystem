#!/usr/bin/env python3
"""Track 4 Step 0 — liquidity check on Kalshi BTC/ETH strike (above/below) markets.

Gate (registered in track4-signal-discovery.md): if the instrument can't absorb
~$100 at <=2c spread, the strike-market hypothesis is dead before the MI screen.
Operationalized here: for near-the-money strikes, report touch spread and resting
contracts within 2c of the touch on each side; PASS needs >=100 contracts within
2c on BOTH sides at a <=2c touch spread on at least one near-money strike per
underlying, plus nonzero traded volume today.

Public API, no auth. Stdlib only.
Usage: liquidity_check.py [--near 5]
"""
from __future__ import annotations
import argparse, json, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone

BASE = "https://api.elections.kalshi.com/trade-api/v2"

# candidate non-15M crypto series; nonexistent ones simply return no markets
SERIES = ["KXBTC", "KXBTCD", "KXETH", "KXETHD",
          "KXBTCRANGE", "KXETHRANGE", "KXBTCH", "KXETHH"]


def get(path: str, params: str = "") -> dict:
    url = f"{BASE}{path}" + (f"?{params}" if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": "track4-liquidity/1.0"})
    for attempt in range(5):
        try:
            time.sleep(0.7)  # stay well under the public rate limit
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                time.sleep(3 * (attempt + 1))
                continue
            if e.code == 404:
                return {}
            raise
    return {}


def spot(pair: str) -> float:
    req = urllib.request.Request(
        f"https://api.coinbase.com/v2/prices/{pair}/spot",
        headers={"User-Agent": "track4-liquidity/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return float(json.loads(r.read())["data"]["amount"])


def crypto_strike_markets():
    """Open markets in the candidate BTC/ETH strike series (targeted queries)."""
    out = []
    for s in SERIES:
        cursor = ""
        while True:
            params = f"series_ticker={s}&status=open&limit=200" + (
                f"&cursor={cursor}" if cursor else "")
            d = get("/markets", params)
            ms = d.get("markets", [])
            out.extend(ms)
            cursor = d.get("cursor") or ""
            if not cursor or not ms:
                break
    return out


def _levels(ob: dict, side: str):
    """Normalize either legacy cents ([[price_cents, qty]] under 'yes'/'no') or
    current dollars ([['0.42', '100.00']] under 'yes_dollars'/'no_dollars')
    to [(price_dollars, qty)]."""
    if ob.get(f"{side}_dollars"):
        return [(float(p), float(q)) for p, q in ob[f"{side}_dollars"]]
    if ob.get(side):
        return [(float(p) / 100.0, float(q)) for p, q in ob[side]]
    return []


def trades_24h(ticker: str):
    """(n_trades, contracts) in the last 24h from the public trades feed."""
    cutoff = datetime.now(timezone.utc).timestamp() - 86400
    n = qty = 0
    cursor = ""
    for _ in range(5):  # up to 5k trades
        d = get("/markets/trades",
                f"ticker={ticker}&limit=1000" + (f"&cursor={cursor}" if cursor else ""))
        for tr in d.get("trades", []):
            created = tr.get("created_time") or tr.get("ts") or ""
            if isinstance(created, str) and created:
                t = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
            else:
                t = float(created or 0)
            if t < cutoff:
                return n, qty
            n += 1
            qty += float(tr.get("count_fp") or tr.get("count") or 0)
        cursor = d.get("cursor") or ""
        if not cursor:
            break
    return n, qty


def book_stats(ticker: str):
    """-> (yes_bid, yes_ask, spread, bid_depth_2c, ask_depth_2c) in $ / contracts."""
    d = get(f"/markets/{ticker}/orderbook")
    ob = d.get("orderbook") or d.get("orderbook_fp") or {}
    yes = _levels(ob, "yes")   # resting YES bids
    no = _levels(ob, "no")     # resting NO bids -> YES asks at 1-p
    if not yes or not no:
        return None
    yb = max(p for p, _ in yes)
    ya = 1.0 - max(p for p, _ in no)
    bid_d = sum(q for p, q in yes if p >= yb - 0.02)
    ask_d = sum(q for p, q in no if (1.0 - p) <= ya + 0.02)
    return yb, ya, ya - yb, bid_d, ask_d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--near", type=int, default=5, help="near-money strikes per series")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    px = {"BTC": spot("BTC-USD"), "ETH": spot("ETH-USD")}
    print(f"Track 4 Step 0 liquidity snapshot @ {now:%Y-%m-%d %H:%M}Z  "
          f"spot BTC={px['BTC']:.0f} ETH={px['ETH']:.0f}")
    mkts = crypto_strike_markets()
    by_series = {}
    for m in mkts:
        by_series.setdefault(m["ticker"].split("-")[0], []).append(m)
    print(f"open BTC/ETH non-15M markets: {len(mkts)} in {len(by_series)} series: "
          f"{sorted(by_series)}\n")
    any_pass = False
    for series in sorted(by_series):
        ms = by_series[series]
        und = "BTC" if "BTC" in series else "ETH"
        # rank by |strike - spot| where a floor/cap strike exists, else keep order
        def dist(m):
            s = m.get("floor_strike") or m.get("cap_strike")
            return abs(float(s) - px[und]) if s is not None else 1e18
        ms.sort(key=dist)
        print(f"== {series} ({len(ms)} open) ==")
        for m in ms[:a.near]:
            t = m["ticker"]
            strike = m.get("floor_strike") or m.get("cap_strike")
            # volume fields are unpopulated on this API version; use the trades feed
            ntr, vol24 = trades_24h(t)
            close = (m.get("close_time") or "")[:16]
            bs = book_stats(t)
            if bs is None:
                print(f"  {t:44s} strike={strike} trades24h={ntr}/{vol24}ct "
                      f"BOOK EMPTY/ONE-SIDED")
                continue
            yb, ya, sp, bd, ad = bs
            gate = sp <= 0.02 + 1e-9 and bd >= 100 and ad >= 100 and vol24 > 0
            any_pass = any_pass or bool(gate)
            print(f"  {t:44s} strike={strike} close={close} "
                  f"bid/ask={yb:.2f}/{ya:.2f} spread={sp * 100:.0f}c "
                  f"depth2c(b/a)={bd:.0f}/{ad:.0f} trades24h={ntr} vol24={vol24}ct "
                  f"{'<-- GATE PASS' if gate else ''}")
        print()
    print("STEP-0 VERDICT:",
          "PASS — at least one near-money strike absorbs 100+ contracts at <=2c"
          if any_pass else
          "FAIL — no near-money strike meets 100 contracts within 2c at <=2c spread")
    return 0


if __name__ == "__main__":
    sys.exit(main())
