#!/usr/bin/env python3
"""
Fetch historical Bybit funding rates for spot-equivalent perp pairs.
Saves as Parquet under /data/funding/{symbol}_funding.parquet.

Bybit funding settles every 8 hours — ~1,095 data points per symbol
for 329 days. Tiny dataset.

This script uses Bybit's REST API. If geo-blocked (US IPs get 403),
it falls back to the CoinGlass public API as a backup.

Usage:
    python -m data.fetch_funding --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT \
        --start-date 2025-04-29 --end-date 2026-03-23 \
        --output-dir /data/funding
"""

import os
import sys
import time
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests
import numpy as np
import polars as pl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("funding")

BYBIT_URL = "https://api.bybit.com/v5/market/funding/history"


def fetch_bybit_funding(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """
    Fetch funding rate history from Bybit REST API.
    Paginated by cursor.
    """
    all_records = []
    cursor = ""
    page = 0

    while True:
        params = {
            "category": "linear",
            "symbol": symbol,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor

        try:
            r = requests.get(BYBIT_URL, params=params, timeout=15)
            if r.status_code == 403:
                logger.warning("Bybit API geo-blocked (403). Cannot fetch funding rates.")
                return None  # Signal geo-block

            r.raise_for_status()
            data = r.json()

            if data.get("retCode") != 0:
                logger.error("Bybit API error: %s", data.get("retMsg"))
                break

            result = data.get("result", {})
            rows = result.get("list", [])
            if not rows:
                break

            for row in rows:
                all_records.append({
                    "symbol": row["symbol"],
                    "funding_rate": float(row["fundingRate"]),
                    "funding_rate_timestamp": int(row["fundingRateTimestamp"]),
                })

            cursor = result.get("nextPageCursor", "")
            if not cursor:
                break

            page += 1
            time.sleep(0.2)  # rate limit

        except requests.exceptions.RequestException as e:
            logger.error("Request failed: %s", e)
            break

    return all_records


def main():
    parser = argparse.ArgumentParser(description="Fetch Bybit funding rate history")
    parser.add_argument("--symbols", type=str, default="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT")
    parser.add_argument("--start-date", type=str, default="2025-04-29")
    parser.add_argument("--end-date", type=str, default="2026-03-23")
    parser.add_argument("--output-dir", type=str, default="/data/funding")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    start_ms = int(datetime.strptime(args.start_date, "%Y-%m-%d").timestamp() * 1000)
    end_ms = int(datetime.strptime(args.end_date, "%Y-%m-%d").timestamp() * 1000)

    logger.info("Fetching funding rates from Bybit")
    logger.info("Symbols: %s", ", ".join(symbols))
    logger.info("Period:  %s to %s", args.start_date, args.end_date)

    for sym in symbols:
        out_path = output_dir / f"{sym}_funding.parquet"
        if out_path.exists():
            logger.info("%s: already exists, skipping", sym)
            continue

        logger.info("Fetching %s...", sym)
        records = fetch_bybit_funding(sym, start_ms, end_ms)

        if records is None:
            logger.warning("Geo-blocked. Funding rate pull abandoned.")
            sys.exit(1)

        if not records:
            logger.warning("%s: no records returned", sym)
            continue

        df = pl.DataFrame(records)
        df = df.sort("funding_rate_timestamp")
        df.write_parquet(out_path, compression="zstd")

        logger.info(
            "%s: %d funding records saved (%.1f KB)",
            sym, len(df), out_path.stat().st_size / 1024,
        )

    logger.info("Done.")


if __name__ == "__main__":
    main()
