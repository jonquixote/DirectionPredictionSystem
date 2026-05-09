#!/usr/bin/env python3
"""
Build features_v3 — enriched feature set based on SHAP analysis.

Reads features_v2 (1-second bars), adds enrichment features,
performs cross-asset joins, drops SHAP-confirmed noise,
aggregates to 1-minute bars, writes to /data/features_v3/.

Changes from v2:
  DROPPED: vpin (negligible SHAP), mlofi_120s_mean (diminishing returns)
  ADDED (VWAP enrichment):
    vwap_2m_deviation   — deviation from 2-minute rolling mean price
    vwap_dev_velocity   — first difference of vwap_deviation
    vwap_dev_30s_std    — 30s rolling std of vwap_deviation
  ADDED (Order flow enrichment):
    mlofi_momentum      — mlofi_30s_mean - mlofi_60s_mean (trajectory of trajectory)
  ADDED (Cross-asset):
    btc_vwap_deviation   — BTC's vwap_deviation (for ETH/SOL/XRP)
    btc_mlofi_30s_mean   — BTC's 30s rolling MLOFI (for ETH/SOL/XRP)
    eth_mlofi_30s_mean   — ETH's 30s rolling MLOFI (for BTC only)
    (set to 0.0 for non-applicable symbols)

Usage:
    python -m data.build_features_v3 \
        --input-dir /data/features_v2 \
        --output-dir /data/features_v3
"""

import os
import time
import shutil
import argparse
import logging
from pathlib import Path
from glob import glob
from datetime import datetime, timedelta

import numpy as np
import polars as pl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("features_v3")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

# Columns to drop (SHAP-confirmed noise)
DROP_COLS = ["vpin", "mlofi_120s_mean"]

# Columns to average when aggregating to 1-minute bars
MEAN_COLS = [
    "spread", "relative_spread", "mlofi", "ofi",
    "vwap_deviation", "roll",
    "mlofi_1", "mlofi_2", "mlofi_3", "mlofi_4", "mlofi_5",
    "mlofi_6", "mlofi_7", "mlofi_8", "mlofi_9", "mlofi_10",
    "mlofi_30s_mean", "mlofi_60s_mean",
    "ofi_30s_mean", "ofi_60s_mean",
    "mlofi_30s_std", "mlofi_60s_std", "ofi_60s_std",
    # New features
    "vwap_2m_deviation", "vwap_dev_velocity", "vwap_dev_30s_std",
    "mlofi_momentum",
    # Cross-asset
    "btc_vwap_deviation", "btc_mlofi_30s_mean", "eth_mlofi_30s_mean",
]

LAST_COLS = ["mid_price", "spread_5m_pct", "symbol"]


def enrich_symbol_day(df: pl.DataFrame) -> pl.DataFrame:
    """Add VWAP enrichment and order flow enrichment features to one day of 1s data."""
    if len(df) == 0:
        return df

    df = df.sort("cts")

    # Create datetime col for rolling operations
    df = df.with_columns(
        pl.from_epoch(pl.col("cts"), time_unit="ms").alias("_ts_dt")
    )

    # ── VWAP enrichment ──

    # vwap_2m_deviation: (mid_price - 2min_rolling_mean(mid_price)) / 2min_rolling_mean(mid_price)
    df = df.with_columns(
        pl.col("mid_price")
        .rolling_mean_by("_ts_dt", window_size="120000ms", min_periods=10)
        .alias("_mid_2m_mean")
    )
    df = df.with_columns(
        pl.when(pl.col("_mid_2m_mean") > 0)
        .then((pl.col("mid_price") - pl.col("_mid_2m_mean")) / pl.col("_mid_2m_mean"))
        .otherwise(0.0)
        .alias("vwap_2m_deviation")
    )
    df = df.drop("_mid_2m_mean")

    # vwap_dev_velocity: first difference of vwap_deviation
    df = df.with_columns(
        (pl.col("vwap_deviation") - pl.col("vwap_deviation").shift(1))
        .fill_null(0.0)
        .alias("vwap_dev_velocity")
    )

    # vwap_dev_30s_std: 30-second rolling std of vwap_deviation
    df = df.with_columns(
        pl.col("vwap_deviation")
        .rolling_std_by("_ts_dt", window_size="30000ms", min_periods=5)
        .fill_null(0.0)
        .alias("vwap_dev_30s_std")
    )

    # ── Order flow enrichment ──

    # mlofi_momentum: trajectory of the trajectory
    df = df.with_columns(
        (pl.col("mlofi_30s_mean") - pl.col("mlofi_60s_mean"))
        .alias("mlofi_momentum")
    )

    # ofi_30s_mean already exists in v2

    df = df.drop("_ts_dt")
    return df


def process_day(
    date_str: str,
    input_dir: Path,
    output_dir: Path,
) -> dict:
    """
    Process one day: load all 4 symbols, enrich, cross-asset join,
    aggregate to 1min, save.
    """
    stats = {"symbols_done": 0, "total_rows": 0}

    # Load all symbols for this day
    sym_data = {}
    for sym in SYMBOLS:
        fpath = input_dir / sym / f"{date_str}_{sym}_features.parquet"
        if not fpath.exists():
            return stats
        sym_data[sym] = pl.read_parquet(fpath)

    # Enrich each symbol
    for sym in SYMBOLS:
        sym_data[sym] = enrich_symbol_day(sym_data[sym])

    # ── Cross-asset joins ──
    # Prepare BTC and ETH lookup tables (cts, feature)
    btc_cross = sym_data["BTCUSDT"].select([
        pl.col("cts"),
        pl.col("vwap_deviation").alias("btc_vwap_deviation"),
        pl.col("mlofi_30s_mean").alias("btc_mlofi_30s_mean"),
    ]).sort("cts")

    eth_cross = sym_data["ETHUSDT"].select([
        pl.col("cts"),
        pl.col("mlofi_30s_mean").alias("eth_mlofi_30s_mean"),
    ]).sort("cts")

    for sym in SYMBOLS:
        df = sym_data[sym].sort("cts")

        if sym != "BTCUSDT":
            # Join BTC features for ETH/SOL/XRP
            df = df.join_asof(
                btc_cross, on="cts", strategy="nearest", tolerance=1000  # 1s tolerance in ms
            )
            # eth_mlofi_30s_mean = 0 for non-BTC symbols
            df = df.with_columns(pl.lit(0.0).alias("eth_mlofi_30s_mean"))
        else:
            # Join ETH features for BTC
            df = df.join_asof(
                eth_cross, on="cts", strategy="nearest", tolerance=1000
            )
            # btc cross features = 0 for BTC itself
            df = df.with_columns([
                pl.lit(0.0).alias("btc_vwap_deviation"),
                pl.lit(0.0).alias("btc_mlofi_30s_mean"),
            ])

        # Fill any nulls from failed joins
        df = df.with_columns([
            pl.col("btc_vwap_deviation").fill_null(0.0),
            pl.col("btc_mlofi_30s_mean").fill_null(0.0),
            pl.col("eth_mlofi_30s_mean").fill_null(0.0),
        ])

        # Drop SHAP-confirmed noise
        cols_to_drop = [c for c in DROP_COLS if c in df.columns]
        if cols_to_drop:
            df = df.drop(cols_to_drop)

        # ── Aggregate to 1-minute bars ──
        df = df.with_columns(
            (pl.col("cts") // 60_000 * 60_000).alias("minute_cts")
        )

        agg_exprs = [pl.col("cts").last().alias("cts")]
        for col in MEAN_COLS:
            if col in df.columns:
                agg_exprs.append(pl.col(col).mean().alias(col))
        for col in LAST_COLS:
            if col in df.columns:
                agg_exprs.append(pl.col(col).last().alias(col))

        agg = df.group_by("minute_cts").agg(agg_exprs).sort("minute_cts")
        agg = agg.drop("minute_cts")

        # Drop any remaining nulls from warmup
        agg = agg.drop_nulls()

        # Save
        out_path = output_dir / sym / f"{date_str}_{sym}_features.parquet"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        agg.write_parquet(out_path, compression="zstd", compression_level=12)

        stats["symbols_done"] += 1
        stats["total_rows"] += len(agg)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Build enriched features v3")
    parser.add_argument("--input-dir", type=str, default="/data/features_v2")
    parser.add_argument("--output-dir", type=str, default="/data/features_v3")
    parser.add_argument("--start-date", type=str, default="2025-04-29")
    parser.add_argument("--end-date", type=str, default="2026-03-23")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    start = datetime.strptime(args.start_date, "%Y-%m-%d")
    end = datetime.strptime(args.end_date, "%Y-%m-%d")

    logger.info("=" * 60)
    logger.info("Feature Enrichment v3")
    logger.info("Input:  %s", input_dir)
    logger.info("Output: %s", output_dir)
    logger.info("Period: %s to %s", args.start_date, args.end_date)
    logger.info("Dropped: %s", DROP_COLS)
    logger.info("Added: vwap_2m_deviation, vwap_dev_velocity, vwap_dev_30s_std,")
    logger.info("       mlofi_momentum, btc_vwap_deviation, btc_mlofi_30s_mean,")
    logger.info("       eth_mlofi_30s_mean")
    logger.info("Free disk: %.1f GB", shutil.disk_usage(output_dir).free / (1024**3))
    logger.info("=" * 60)

    t0 = time.time()
    total_days = 0
    total_rows = 0

    d = start
    while d <= end:
        ds = d.strftime("%Y-%m-%d")

        # Skip if all 4 symbols already exist
        all_exist = all(
            (output_dir / sym / f"{ds}_{sym}_features.parquet").exists()
            for sym in SYMBOLS
        )
        if all_exist:
            d += timedelta(days=1)
            continue

        stats = process_day(ds, input_dir, output_dir)

        if stats["symbols_done"] == 4:
            total_days += 1
            total_rows += stats["total_rows"]
            if total_days % 30 == 0:
                logger.info(
                    "  Day %d (%s): %d rows (elapsed: %.0fs)",
                    total_days, ds, stats["total_rows"], time.time() - t0,
                )
        elif stats["symbols_done"] > 0:
            logger.warning("  %s: only %d/4 symbols processed", ds, stats["symbols_done"])

        d += timedelta(days=1)

    elapsed = time.time() - t0
    logger.info("=" * 60)
    logger.info("FINISHED in %.1f minutes", elapsed / 60)
    logger.info("Days: %d, Total rows: %d", total_days, total_rows)
    logger.info("Free disk: %.1f GB", shutil.disk_usage(output_dir).free / (1024**3))
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
