#!/usr/bin/env python3
"""Track A harvester — Polymarket up/down market + trade history.

Stage 1 (discover): enumerate every 5m/15m window boundary in [start, end]
for btc/eth/sol/xrp, resolve slugs via gamma (closed=true, 20-slug batches),
store conditionId + outcomePrices.

Stage 2 (trades): per discovered market, page data-api /trades?market=<cid>
(500/page) until exhausted. Resumable: markets.trades_done flags progress.

Rate limit: registered ceiling 5 req/s with exponential backoff on 429/5xx.

Usage (VPS):
    python3 harvest.py --db /data/probe_track_a.db \
        --start 2026-04-30 --end 2026-06-10 --stage all
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone

import aiohttp

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("harvest")

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
SYMBOLS = ["btc", "eth", "sol", "xrp"]
DURATIONS = [5, 15]  # minutes; 30m markets do not exist (registered Day 0)
BATCH = 20           # slugs per gamma request (probed)
RPS = 5.0
PAGE = 500
# data-api rejects offset >= 5000 with HTTP 400 (probed 2026-06-11).
# Markets with more than 5000 trades are CLIPPED to their most recent 5000;
# markets.clipped=1 marks them so analysis can quantify the bias.
MAX_PAGES = 10


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS markets (
        slug          TEXT PRIMARY KEY,
        condition_id  TEXT,
        symbol        TEXT NOT NULL,
        duration_min  INTEGER NOT NULL,
        boundary_ts   INTEGER NOT NULL,
        outcome_up    REAL,
        outcome_raw   TEXT,
        found         INTEGER NOT NULL DEFAULT 0,
        trades_done   INTEGER NOT NULL DEFAULT 0,
        n_trades      INTEGER,
        clipped       INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_mk_cid ON markets(condition_id);
    CREATE TABLE IF NOT EXISTS trades (
        condition_id  TEXT NOT NULL,
        wallet        TEXT NOT NULL,
        side          TEXT NOT NULL,
        price         REAL NOT NULL,
        size          REAL NOT NULL,
        ts            INTEGER NOT NULL,
        outcome       TEXT,
        outcome_index INTEGER,
        tx_hash       TEXT,
        UNIQUE(condition_id, tx_hash, wallet, side, price, size, ts)
    );
    CREATE INDEX IF NOT EXISTS idx_tr_cid ON trades(condition_id);
    CREATE INDEX IF NOT EXISTS idx_tr_wallet ON trades(wallet);
    """)
    return conn


class RateLimiter:
    def __init__(self, rps: float):
        self.min_interval = 1.0 / rps
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self):
        async with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self.min_interval:
                await asyncio.sleep(self.min_interval - delta)
            self._last = time.monotonic()


async def get_json(session, limiter, url, retries=6):
    for attempt in range(retries):
        await limiter.wait()
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    return await r.json()
                if r.status in (429, 500, 502, 503, 504):
                    wait = 2 ** attempt
                    log.warning("HTTP %d, backoff %ds: %s", r.status, wait, url[:120])
                    await asyncio.sleep(wait)
                    continue
                log.error("HTTP %d (no retry): %s", r.status, url[:120])
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            wait = 2 ** attempt
            log.warning("%s, backoff %ds", type(e).__name__, wait)
            await asyncio.sleep(wait)
    log.error("retries exhausted: %s", url[:120])
    return None


def all_boundaries(start_ts: int, end_ts: int):
    out = []
    for sym in SYMBOLS:
        for dur in DURATIONS:
            step = dur * 60
            b = (start_ts // step + 1) * step
            while b + step <= end_ts:  # window must END within range
                out.append((sym, dur, b))
                b += step
    return out


async def stage_discover(conn, session, limiter, start_ts, end_ts):
    todo = [(s, d, b) for (s, d, b) in all_boundaries(start_ts, end_ts)
            if conn.execute("SELECT 1 FROM markets WHERE slug=?",
                            (f"{s}-updown-{d}m-{b}",)).fetchone() is None]
    log.info("discover: %d slugs to resolve", len(todo))
    found = miss = 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        slugs = {f"{s}-updown-{d}m-{b}": (s, d, b) for (s, d, b) in chunk}
        q = "&".join(f"slug={sl}" for sl in slugs)
        data = await get_json(session, limiter, f"{GAMMA}/markets?closed=true&{q}")
        got = {}
        if data:
            for m in data:
                sl = m.get("slug")
                if sl not in slugs:
                    continue
                op = m.get("outcomePrices")
                try:
                    arr = json.loads(op) if isinstance(op, str) else op
                    outcome_up = float(arr[0]) if arr else None
                except Exception:
                    outcome_up = None
                got[sl] = (m.get("conditionId"), outcome_up, str(op))
        rows = []
        for sl, (s, d, b) in slugs.items():
            if sl in got:
                cid, ou, raw = got[sl]
                rows.append((sl, cid, s, d, b, ou, raw, 1))
                found += 1
            else:
                rows.append((sl, None, s, d, b, None, None, 0))
                miss += 1
        conn.executemany(
            "INSERT OR IGNORE INTO markets"
            " (slug, condition_id, symbol, duration_min, boundary_ts,"
            "  outcome_up, outcome_raw, found) VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        if (i // BATCH) % 50 == 0:
            log.info("discover progress: %d/%d (found=%d miss=%d)",
                     i + len(chunk), len(todo), found, miss)
    log.info("discover DONE: found=%d miss=%d", found, miss)


async def harvest_market(conn, session, limiter, slug, cid):
    total = 0
    clipped = 0
    for page in range(MAX_PAGES):
        url = f"{DATA}/trades?market={cid}&limit={PAGE}&offset={page * PAGE}"
        data = await get_json(session, limiter, url)
        if data is None:
            return None  # transient failure — leave market not-done
        rows = [(cid, t.get("proxyWallet"), t.get("side"), t.get("price"),
                 t.get("size"), t.get("timestamp"), t.get("outcome"),
                 t.get("outcomeIndex"), t.get("transactionHash"))
                for t in data if t.get("conditionId") == cid]
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?,?,?,?)", rows)
        total += len(rows)
        if len(data) < PAGE:
            break
    else:
        clipped = 1  # hit MAX_PAGES with full pages — older trades unreachable
    conn.execute(
        "UPDATE markets SET trades_done=1, n_trades=?, clipped=? WHERE slug=?",
        (total, clipped, slug))
    conn.commit()
    return total


async def stage_trades(conn, session, limiter):
    todo = conn.execute(
        "SELECT slug, condition_id FROM markets"
        " WHERE found=1 AND trades_done=0 ORDER BY boundary_ts").fetchall()
    log.info("trades: %d markets to harvest", len(todo))
    done = 0
    t0 = time.time()
    for slug, cid in todo:
        n = await harvest_market(conn, session, limiter, slug, cid)
        done += 1
        if done % 200 == 0:
            elapsed = time.time() - t0
            rate = done / elapsed
            eta_h = (len(todo) - done) / rate / 3600 if rate > 0 else -1
            ntr = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            log.info("trades progress: %d/%d markets, %d trade rows, eta %.1fh",
                     done, len(todo), ntr, eta_h)
    log.info("trades DONE: %d markets", done)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--stage", choices=["discover", "trades", "all"], default="all")
    args = ap.parse_args()

    start_ts = int(datetime.strptime(args.start, "%Y-%m-%d")
                   .replace(tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.strptime(args.end, "%Y-%m-%d")
                 .replace(tzinfo=timezone.utc).timestamp()) + 86400

    conn = open_db(args.db)
    limiter = RateLimiter(RPS)
    async with aiohttp.ClientSession() as session:
        if args.stage in ("discover", "all"):
            await stage_discover(conn, session, limiter, start_ts, end_ts)
        if args.stage in ("trades", "all"):
            await stage_trades(conn, session, limiter)

    n_mk = conn.execute("SELECT COUNT(*) FROM markets WHERE found=1").fetchone()[0]
    n_tr = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    log.info("SUMMARY: %d markets found, %d trade rows", n_mk, n_tr)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
