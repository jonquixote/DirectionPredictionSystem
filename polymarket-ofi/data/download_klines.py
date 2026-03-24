#!/usr/bin/env python3
"""
Bybit spot OHLCV (kline) data downloader.
Uses Bybit REST API: GET /v5/market/kline?category=spot

Paginated by start/end timestamps. Free, no auth needed.
Covers: Roll measure, VPIN, momentum, and price-based features.

Usage:
    python -m data.download_klines --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT \
        --start-date 2025-04-29 --end-date 2026-03-23 --output-dir /data/parquet/klines
"""

import time
import argparse
import logging
import requests
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("kline_downloader")

BYBIT_REST = "https://api.bybit.com"
KLINE_ENDPOINT = "/v5/market/kline"
MAX_LIMIT = 1000  # Bybit max per request


def fetch_klines(
    symbol: str,
    interval: str = "1",
    start_ms: int = 0,
    end_ms: int = 0,
) -> list[dict]:
    """Fetch one batch of klines from Bybit REST API."""
    params = {
        "category": "spot",
        "symbol": symbol,
        "interval": interval,
        "start": start_ms,
        "end": end_ms,
        "limit": MAX_LIMIT,
    }
    resp = requests.get(f"{BYBIT_REST}{KLINE_ENDPOINT}", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if data.get("retCode") != 0:
        raise ValueError(f"API error: {data.get('retMsg')}")

    # Bybit returns newest first — reverse for chronological order
    rows = data.get("result", {}).get("list", [])
    records = []
    for row in reversed(rows):
        records.append({
            "open_time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "turnover": float(row[6]),
        })
    return records


def download_symbol_klines(
    symbol: str,
    start: datetime,
    end: datetime,
    output_dir: Path,
    interval: str = "1",
) -> dict:
    """Download all klines for one symbol, paginating by time."""
    output_path = output_dir / f"{symbol}_klines_{interval}m.parquet"

    if output_path.exists():
        logger.info("  %s: already exists, skipping", symbol)
        return {"status": "skipped", "symbol": symbol}

    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    all_records = []
    current_start = start_ms

    logger.info("  %s: downloading %s to %s...", symbol, start.date(), end.date())

    while current_start < end_ms:
        try:
            batch = fetch_klines(symbol, interval, current_start, end_ms)
            if not batch:
                break

            all_records.extend(batch)
            last_ts = batch[-1]["open_time"]

            # Next batch starts after the last received candle
            current_start = last_ts + 60_000  # 1min = 60000ms

            # Rate limit: Bybit allows ~10 req/s for public endpoints
            time.sleep(0.15)

            if len(all_records) % 10000 == 0:
                logger.info("    %s: %d candles so far...", symbol, len(all_records))

        except Exception as e:
            logger.warning("    %s: error at %d: %s, retrying", symbol, current_start, e)
            time.sleep(2)

    if not all_records:
        logger.warning("  %s: no data retrieved", symbol)
        return {"status": "empty", "symbol": symbol}

    # Write Parquet
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame(all_records)
    df.write_parquet(output_path, compression="zstd")

    size_mb = output_path.stat().st_size / 1024 / 1024
    logger.info(
        "  %s: ✓ %d candles, %.1f MB",
        symbol, len(all_records), size_mb,
    )
    return {
        "status": "success",
        "symbol": symbol,
        "candles": len(all_records),
        "size_mb": size_mb,
    }


def main():
    parser = argparse.ArgumentParser(description="Download Bybit spot OHLCV (kline) data")
    parser.add_argument("--symbols", type=str, default="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT")
    parser.add_argument("--start-date", type=str, required=True)
    parser.add_argument("--end-date", type=str, required=True)
    parser.add_argument("--output-dir", type=str, default="/data/parquet/klines")
    parser.add_argument("--interval", type=str, default="1", help="Kline interval in minutes")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    start = datetime.strptime(args.start_date, "%Y-%m-%d")
    end = datetime.strptime(args.end_date, "%Y-%m-%d")

    logger.info("=" * 60)
    logger.info("Bybit Spot OHLCV Downloader")
    logger.info("Symbols: %s", ", ".join(symbols))
    logger.info("Period:  %s to %s", args.start_date, args.end_date)
    logger.info("Interval: %s min", args.interval)
    logger.info("=" * 60)

    for sym in symbols:
        result = download_symbol_klines(sym, start, end, output_dir, args.interval)
        logger.info("[%s] %s", sym, result)


if __name__ == "__main__":
    main()
