#!/usr/bin/env python3
"""
Aggregate features_v2 (1-second bars) to 1-minute bars.
Reduces 102M rows to ~1.9M rows — fits in 8GB RAM for training.

For each 1-minute bucket, computes:
  - mid_price: last value (close)
  - spread, relative_spread: mean
  - mlofi, ofi: mean (average imbalance over the minute)
  - vpin: mean
  - vwap_deviation: mean
  - roll: mean
  - mlofi_1..10: mean
  - rolling features: last value (already smoothed)
  - spread_5m_pct: last value

Reads:  /data/features_v2/{symbol}/...parquet
Writes: /data/features_1m/{symbol}/{date}_{symbol}_features.parquet

Usage:
    python -m data.aggregate_1m --input-dir /data/features_v2 --output-dir /data/features_1m
"""

import os
import time
import shutil
import argparse
import logging
from pathlib import Path
from glob import glob

import polars as pl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("aggregate")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

# Columns to average over each minute
MEAN_COLS = [
    "spread", "relative_spread", "mlofi", "ofi", "vpin",
    "vwap_deviation", "roll",
    "mlofi_1", "mlofi_2", "mlofi_3", "mlofi_4", "mlofi_5",
    "mlofi_6", "mlofi_7", "mlofi_8", "mlofi_9", "mlofi_10",
    "mlofi_30s_mean", "mlofi_60s_mean", "mlofi_120s_mean",
    "ofi_30s_mean", "ofi_60s_mean",
    "mlofi_30s_std", "mlofi_60s_std", "ofi_60s_std",
]

# Columns to take last value (close of minute)
LAST_COLS = ["mid_price", "spread_5m_pct", "symbol"]


def aggregate_file(in_path: Path, out_path: Path) -> int:
    """Aggregate one day file to 1-minute bars. Returns row count."""
    df = pl.read_parquet(in_path)
    if len(df) == 0:
        return 0

    # Create minute bucket: floor cts to nearest 60,000 ms
    df = df.with_columns(
        (pl.col("cts") // 60_000 * 60_000).alias("minute_cts")
    )

    # Build aggregation expressions
    agg_exprs = [
        pl.col("cts").last().alias("cts"),  # keep last cts in the minute
    ]
    for col in MEAN_COLS:
        if col in df.columns:
            agg_exprs.append(pl.col(col).mean().alias(col))
    for col in LAST_COLS:
        if col in df.columns:
            agg_exprs.append(pl.col(col).last().alias(col))

    agg = df.group_by("minute_cts").agg(agg_exprs).sort("minute_cts")

    # Drop the minute_cts helper column
    agg = agg.drop("minute_cts")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    agg.write_parquet(out_path, compression="zstd", compression_level=12)
    return len(agg)


def main():
    parser = argparse.ArgumentParser(description="Aggregate features to 1-minute bars")
    parser.add_argument("--input-dir", type=str, default="/data/features_v2")
    parser.add_argument("--output-dir", type=str, default="/data/features_1m")
    parser.add_argument("--symbols", type=str, default="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("Aggregating to 1-minute bars")
    logger.info("Input:  %s", input_dir)
    logger.info("Output: %s", output_dir)
    logger.info("=" * 60)

    t0 = time.time()
    total_rows = 0

    for sym in symbols:
        sym_files = sorted(glob(str(input_dir / sym / "*.parquet")))
        logger.info("[%s] %d files", sym, len(sym_files))

        sym_rows = 0
        for f in sym_files:
            fname = os.path.basename(f)
            out_path = output_dir / sym / fname

            if out_path.exists():
                continue

            n = aggregate_file(Path(f), out_path)
            sym_rows += n

        total_rows += sym_rows
        logger.info("[%s] DONE: %d minute-rows", sym, sym_rows)

    elapsed = time.time() - t0
    logger.info("=" * 60)
    logger.info("FINISHED in %.1f seconds. Total: %d rows", elapsed, total_rows)
    logger.info("Free disk: %.1f GB", shutil.disk_usage(output_dir).free / (1024**3))
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
