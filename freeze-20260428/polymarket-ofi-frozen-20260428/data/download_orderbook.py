#!/usr/bin/env python3
"""
Bybit spot L2 order book downloader with streaming Parquet conversion.
Downloads ZIP from public Bybit S3, parses JSON lines, writes Parquet, deletes ZIP.

Source: https://quote-saver.bycsi.com/orderbook/spot/{symbol}/{date}_{symbol}_ob200.data.zip
Format: newline-delimited JSON with ts, cts, type (snapshot/delta), u, seq, bids, asks

Usage:
    python -m data.download_orderbook --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT \
        --start-date 2025-04-29 --end-date 2026-03-23 --output-dir /data/parquet/orderbook
"""

import os
import io
import json
import time
import shutil
import zipfile
import argparse
import tempfile
import logging
import requests
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import polars as pl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("downloader")

BASE_URL = "https://quote-saver.bycsi.com/orderbook/spot"


def parse_record(data: dict) -> dict:
    """Parse one order book JSON record into flat structure."""
    return {
        "ts": data["ts"],
        "cts": data.get("cts"),
        "type": data["type"],
        "u": data["data"]["u"],
        "seq": data["data"]["seq"],
        "bids": json.dumps(data["data"].get("b", [])),
        "asks": json.dumps(data["data"].get("a", [])),
    }


def download_and_convert(
    symbol: str,
    date_str: str,
    output_dir: Path,
    max_retries: int = 3,
) -> dict:
    """Download one day's ZIP, convert to Parquet, delete ZIP."""
    parquet_path = output_dir / symbol / f"{date_str}_{symbol}_ob200.parquet"

    if parquet_path.exists():
        return {"status": "skipped", "date": date_str, "symbol": symbol}

    zip_filename = f"{date_str}_{symbol}_ob200.data.zip"
    url = f"{BASE_URL}/{symbol}/{zip_filename}"

    for attempt in range(max_retries):
        temp_zip = None
        try:
            start_time = time.time()

            # Download to temp file
            with requests.get(url, stream=True, timeout=120) as r:
                if r.status_code == 404:
                    logger.warning("  %s %s: NOT FOUND (404)", symbol, date_str)
                    return {"status": "not_found", "date": date_str, "symbol": symbol}
                r.raise_for_status()

                total_size = int(r.headers.get("content-length", 0))
                temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")

                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        temp_zip.write(chunk)
                temp_zip.close()

            # Parse JSON lines and convert to Parquet
            records = []
            errors = 0
            with zipfile.ZipFile(temp_zip.name, "r") as zf:
                data_file = zf.namelist()[0]
                with zf.open(data_file) as f:
                    for line in f:
                        try:
                            data = json.loads(line.decode("utf-8").strip())
                            records.append(parse_record(data))
                        except Exception:
                            errors += 1

            # Write Parquet
            parquet_path.parent.mkdir(parents=True, exist_ok=True)
            df = pl.DataFrame(records)
            df.write_parquet(parquet_path, compression="zstd", compression_level=12)

            # Delete temp ZIP
            os.unlink(temp_zip.name)

            elapsed = time.time() - start_time
            size_mb = parquet_path.stat().st_size / 1024 / 1024
            zip_mb = total_size / 1024 / 1024

            logger.info(
                "  %s %s: ✓ %.1f MB parquet (%.1f MB zip, %d rows, %d errors) in %.1fs",
                symbol, date_str, size_mb, zip_mb, len(records), errors, elapsed,
            )
            return {
                "status": "success",
                "date": date_str,
                "symbol": symbol,
                "rows": len(records),
                "parquet_mb": size_mb,
                "zip_mb": zip_mb,
                "errors": errors,
            }

        except requests.exceptions.Timeout:
            logger.warning("  %s %s: TIMEOUT, retry %d/%d", symbol, date_str, attempt + 1, max_retries)
            time.sleep(5)
        except Exception as e:
            logger.warning("  %s %s: ERROR %s, retry %d/%d", symbol, date_str, str(e)[:60], attempt + 1, max_retries)
            time.sleep(2)
        finally:
            if temp_zip and Path(temp_zip.name).exists():
                try:
                    os.unlink(temp_zip.name)
                except Exception:
                    pass

    logger.error("  %s %s: FAILED after %d attempts", symbol, date_str, max_retries)
    return {"status": "failed", "date": date_str, "symbol": symbol}


def daterange(start: datetime, end: datetime):
    """Generate dates from start to end inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def download_symbol(
    symbol: str,
    start: datetime,
    end: datetime,
    output_dir: Path,
    min_disk_gb: float = 10.0,
) -> dict:
    """Download all days for one symbol sequentially."""
    stats = {"success": 0, "failed": 0, "skipped": 0, "not_found": 0, "total_mb": 0}

    for date in daterange(start, end):
        # Check disk space periodically
        if stats["success"] % 10 == 0:
            free_gb = shutil.disk_usage(output_dir).free / (1024**3)
            if free_gb < min_disk_gb:
                logger.error("Disk space low (%.1f GB free), stopping", free_gb)
                break

        date_str = date.strftime("%Y-%m-%d")
        result = download_and_convert(symbol, date_str, output_dir)

        stats[result["status"]] = stats.get(result["status"], 0) + 1
        stats["total_mb"] += result.get("parquet_mb", 0)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Download Bybit spot L2 order book data")
    parser.add_argument("--symbols", type=str, default="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT")
    parser.add_argument("--start-date", type=str, required=True)
    parser.add_argument("--end-date", type=str, required=True)
    parser.add_argument("--output-dir", type=str, default="/data/parquet/orderbook")
    parser.add_argument("--min-disk-gb", type=float, default=10.0)
    parser.add_argument("--parallel", action="store_true", help="Download symbols in parallel")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    start = datetime.strptime(args.start_date, "%Y-%m-%d")
    end = datetime.strptime(args.end_date, "%Y-%m-%d")
    total_days = (end - start).days + 1

    logger.info("=" * 60)
    logger.info("Bybit Spot L2 Order Book Downloader")
    logger.info("Symbols: %s", ", ".join(symbols))
    logger.info("Period:  %s to %s (%d days)", args.start_date, args.end_date, total_days)
    logger.info("Output:  %s", output_dir)
    logger.info("Free disk: %.1f GB", shutil.disk_usage(output_dir).free / (1024**3))
    logger.info("=" * 60)

    start_time = time.time()

    if args.parallel:
        with ThreadPoolExecutor(max_workers=len(symbols)) as executor:
            futures = {
                executor.submit(download_symbol, sym, start, end, output_dir, args.min_disk_gb): sym
                for sym in symbols
            }
            for f in as_completed(futures):
                sym = futures[f]
                stats = f.result()
                logger.info("%s DONE: %s", sym, stats)
    else:
        for sym in symbols:
            logger.info("[%s] Starting download...", sym)
            stats = download_symbol(sym, start, end, output_dir, args.min_disk_gb)
            logger.info("[%s] DONE: %s", sym, stats)

    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info("FINISHED in %.1f minutes", elapsed / 60)
    logger.info("Free disk: %.1f GB", shutil.disk_usage(output_dir).free / (1024**3))


if __name__ == "__main__":
    main()
