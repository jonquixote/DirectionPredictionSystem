#!/usr/bin/env python3
"""
Add rolling window features to existing feature Parquet files.

Reads:  /data/features/{symbol}/{date}_{symbol}_features.parquet
Writes: /data/features_v2/{symbol}/{date}_{symbol}_features.parquet

Rolling features added (all time-based using cts in MILLISECONDS):
  mlofi_30s_mean, mlofi_60s_mean, mlofi_120s_mean
  ofi_30s_mean, ofi_60s_mean
  mlofi_30s_std, mlofi_60s_std
  ofi_60s_std
  spread_5m_pct    (5-minute rolling percentile rank of spread, 0–1)

First 120 seconds of each symbol/day are dropped (warmup for longest window).

Usage:
    python -m data.add_rolling_features \
        --input-dir /data/features \
        --output-dir /data/features_v2 \
        --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT
"""

import os
import sys
import time
import shutil
import argparse
import logging
from pathlib import Path

import numpy as np
import polars as pl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rolling_features")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

# Rolling window sizes in milliseconds (cts is ms)
WINDOWS_MS = {
    "30s": 30_000,
    "60s": 60_000,
    "120s": 120_000,
    "5m": 300_000,
}

WARMUP_MS = 120_000  # drop first 120 seconds per symbol/day


def compute_rolling_features(df: pl.DataFrame) -> pl.DataFrame:
    """
    Add rolling window features to a single day's feature DataFrame.
    Uses time-based windows via polars rolling_mean/rolling_std with `by` param.

    cts is in MILLISECONDS (13 digits, verified from raw Bybit Parquet).
    """
    if len(df) == 0:
        return df

    # Sort by cts (should already be sorted, but be safe)
    df = df.sort("cts")

    # Convert cts to a proper datetime for polars rolling operations
    df = df.with_columns(
        pl.from_epoch(pl.col("cts"), time_unit="ms").alias("_ts_dt")
    )

    # ── Rolling means ──
    for col, windows in [
        ("mlofi", ["30s", "60s", "120s"]),
        ("ofi", ["30s", "60s"]),
    ]:
        for w_name in windows:
            w_dur = f"{WINDOWS_MS[w_name]}ms"
            new_col = f"{col}_{w_name}_mean"
            df = df.with_columns(
                pl.col(col)
                .rolling_mean_by("_ts_dt", window_size=w_dur, min_periods=2)
                .alias(new_col)
            )

    # ── Rolling standard deviations ──
    for col, windows in [
        ("mlofi", ["30s", "60s"]),
        ("ofi", ["60s"]),
    ]:
        for w_name in windows:
            w_dur = f"{WINDOWS_MS[w_name]}ms"
            new_col = f"{col}_{w_name}_std"
            df = df.with_columns(
                pl.col(col)
                .rolling_std_by("_ts_dt", window_size=w_dur, min_periods=2)
                .alias(new_col)
            )

    # ── Spread 5-minute rolling percentile rank ──
    # For each row, what fraction of spread values in the last 5 minutes
    # are <= the current spread value.
    # Polars doesn't have a native rolling percentile rank, so we compute
    # it using rolling_quantile to get the rank position.
    # Alternative: use rolling_map with a custom function.
    # Efficient approach: for each row, count values <= current / total count.
    # We approximate using rolling_min, rolling_max, and current value:
    #   pct_rank ≈ (current - min) / (max - min)
    # This is a linear interpolation, not exact percentile rank, but it's
    # computationally efficient and captures the regime signal.
    w5m = f"{WINDOWS_MS['5m']}ms"
    df = df.with_columns([
        pl.col("spread")
        .rolling_min_by("_ts_dt", window_size=w5m, min_periods=2)
        .alias("_spread_5m_min"),
        pl.col("spread")
        .rolling_max_by("_ts_dt", window_size=w5m, min_periods=2)
        .alias("_spread_5m_max"),
    ])
    df = df.with_columns(
        pl.when(pl.col("_spread_5m_max") > pl.col("_spread_5m_min"))
        .then(
            (pl.col("spread") - pl.col("_spread_5m_min"))
            / (pl.col("_spread_5m_max") - pl.col("_spread_5m_min"))
        )
        .otherwise(0.5)
        .alias("spread_5m_pct")
    )
    df = df.drop(["_spread_5m_min", "_spread_5m_max"])

    # ── Drop warmup rows (first 120 seconds) ──
    min_cts = df["cts"].min()
    warmup_end = min_cts + WARMUP_MS
    before = len(df)
    df = df.filter(pl.col("cts") >= warmup_end)
    dropped = before - len(df)

    # ── Drop any remaining NaN rows from rolling features ──
    rolling_cols = [
        "mlofi_30s_mean", "mlofi_60s_mean", "mlofi_120s_mean",
        "ofi_30s_mean", "ofi_60s_mean",
        "mlofi_30s_std", "mlofi_60s_std",
        "ofi_60s_std",
        "spread_5m_pct",
    ]
    before2 = len(df)
    df = df.drop_nulls(subset=rolling_cols)
    dropped2 = before2 - len(df)

    # Drop the temporary datetime column
    df = df.drop("_ts_dt")

    return df


def process_symbol(
    symbol: str,
    input_dir: Path,
    output_dir: Path,
) -> dict:
    """Process all days for one symbol."""
    stats = {"success": 0, "skipped": 0, "failed": 0, "total_rows": 0, "dropped_warmup": 0}

    sym_in = input_dir / symbol
    sym_out = output_dir / symbol
    sym_out.mkdir(parents=True, exist_ok=True)

    if not sym_in.exists():
        logger.error("Input dir missing: %s", sym_in)
        return stats

    files = sorted(sym_in.glob("*.parquet"))
    logger.info("  %s: %d input files", symbol, len(files))

    for f in files:
        out_path = sym_out / f.name

        if out_path.exists():
            stats["skipped"] += 1
            continue

        try:
            t0 = time.time()
            df = pl.read_parquet(f)
            original_rows = len(df)

            df = compute_rolling_features(df)

            df.write_parquet(out_path, compression="zstd", compression_level=12)

            elapsed = time.time() - t0
            size_mb = out_path.stat().st_size / (1024 * 1024)
            stats["success"] += 1
            stats["total_rows"] += len(df)
            stats["dropped_warmup"] += original_rows - len(df)

            logger.info(
                "  %s %s: ✓ %d→%d rows, %.1f MB (%.1fs)",
                symbol, f.stem.split("_")[0], original_rows, len(df), size_mb, elapsed,
            )

        except Exception as e:
            logger.error("  %s %s: ERROR %s", symbol, f.name, str(e)[:150])
            import traceback
            traceback.print_exc()
            stats["failed"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(description="Add rolling window features to feature Parquet")
    parser.add_argument("--input-dir", type=str, default="/data/features")
    parser.add_argument("--output-dir", type=str, default="/data/features_v2")
    parser.add_argument("--symbols", type=str, default="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("Rolling Feature Augmentation")
    logger.info("Input:   %s", input_dir)
    logger.info("Output:  %s", output_dir)
    logger.info("Symbols: %s", ", ".join(symbols))
    logger.info("Free disk: %.1f GB", shutil.disk_usage(output_dir).free / (1024**3))
    logger.info("=" * 60)

    start_time = time.time()

    for sym in symbols:
        logger.info("[%s] Adding rolling features...", sym)
        stats = process_symbol(sym, input_dir, output_dir)
        logger.info("[%s] DONE: %s", sym, stats)

    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info("FINISHED in %.1f minutes", elapsed / 60)
    logger.info("Free disk: %.1f GB", shutil.disk_usage(output_dir).free / (1024**3))


if __name__ == "__main__":
    main()
