#!/usr/bin/env python3
"""
Feature engineering pipeline — reads raw L2 Parquet,
reconstructs order book, computes features, outputs feature matrix.

Reads:  /data/parquet/orderbook/{symbol}/{date}_{symbol}_ob200.parquet
Writes: /data/features/{symbol}/{date}_{symbol}_features.parquet

Features computed per ~1s downsampled event:
  Tier 1A: MLOFI (levels 1-10, 1/k weighted, MAD normalised), OFI (Cont 2014)
  Tier 1B: mid_price, spread, relative_spread
  Tier 1C: vwap_deviation (rolling VWAP vs mid)
  Tier 2A: roll_measure (from L2 mid-price returns, window=15)
  Tier 2C: vpin (window=15 buckets)

  TODO(tier1d): time-to-resolution — deferred, requires Polymarket metadata
  TODO(tier2b): cross-asset features — deferred until Tier 1 signal validated

Note: Roll measure is computed from L2 mid-price returns rather than external
OHLCV klines. This is more granular and avoids the Bybit REST API geo-block.

Usage:
    python -m data.build_features --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT \
        --start-date 2025-04-29 --end-date 2026-03-23 \
        --l2-dir /data/parquet/orderbook --output-dir /data/features
"""

import os
import json
import time
import shutil
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path
from collections import deque

import numpy as np
import polars as pl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("features")

# ── Constants ──────────────────────────────────────────────────
MLOFI_LEVELS = 10
MAD_WINDOW = 1000
VPIN_WINDOW = 15
ROLL_WINDOW = 15
VWAP_WINDOW = 100    # rolling window for VWAP computation
DOWNSAMPLE_MS = 1000  # aggregate to ~1-second bars (reduce output size)


# ── Order book reconstruction + feature computation ───────────

def reconstruct_and_compute(l2_path: Path) -> list[dict]:
    """
    Read one day's L2 Parquet, reconstruct order book from snapshot/deltas,
    compute features at each ~1s downsampled event, return list of feature rows.
    """
    df = pl.read_parquet(l2_path)
    if len(df) == 0:
        return []

    # State for book reconstruction
    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    synced = False

    # State for MLOFI / MAD
    mlofi_history: deque = deque(maxlen=MAD_WINDOW)

    # State for VPIN
    volume_buys: deque = deque(maxlen=VPIN_WINDOW)
    volume_sells: deque = deque(maxlen=VPIN_WINDOW)

    # State for VWAP
    vwap_num: deque = deque(maxlen=VWAP_WINDOW)
    vwap_den: deque = deque(maxlen=VWAP_WINDOW)

    # State for OFI (previous best bid/ask)
    prev_best_bid_vol = 0.0
    prev_best_ask_vol = 0.0
    prev_best_bid_price = 0.0
    prev_best_ask_price = 0.0

    # State for Roll measure (rolling mid-prices)
    mid_price_history: deque = deque(maxlen=ROLL_WINDOW + 2)

    # Downsample state
    last_output_ts = 0

    rows = []

    for i in range(len(df)):
        row = df.row(i, named=True)
        ts = row["ts"]
        cts = row["cts"]
        msg_type = row["type"]

        # Parse bids/asks from JSON strings
        try:
            b_levels = json.loads(row["bids"]) if isinstance(row["bids"], str) else row["bids"]
            a_levels = json.loads(row["asks"]) if isinstance(row["asks"], str) else row["asks"]
        except (json.JSONDecodeError, TypeError):
            continue

        if msg_type == "snapshot":
            bids = {}
            asks = {}
            for price_str, size_str in b_levels:
                p, s = float(price_str), float(size_str)
                if s > 0:
                    bids[p] = s
            for price_str, size_str in a_levels:
                p, s = float(price_str), float(size_str)
                if s > 0:
                    asks[p] = s
            synced = True

        elif msg_type == "delta":
            if not synced:
                continue
            for price_str, size_str in b_levels:
                p, s = float(price_str), float(size_str)
                if s == 0.0:
                    bids.pop(p, None)
                else:
                    bids[p] = s
            for price_str, size_str in a_levels:
                p, s = float(price_str), float(size_str)
                if s == 0.0:
                    asks.pop(p, None)
                else:
                    asks[p] = s
        else:
            continue

        if not synced or not bids or not asks:
            continue

        # Downsample: only output one row per DOWNSAMPLE_MS
        if cts and (cts - last_output_ts) < DOWNSAMPLE_MS:
            continue
        last_output_ts = cts if cts else ts

        # ── Get top levels sorted ──
        sorted_bids = sorted(bids.items(), reverse=True)[:MLOFI_LEVELS]
        sorted_asks = sorted(asks.items())[:MLOFI_LEVELS]

        if not sorted_bids or not sorted_asks:
            continue

        best_bid_price, best_bid_vol = sorted_bids[0]
        best_ask_price, best_ask_vol = sorted_asks[0]

        # ── Mid-price and spread ──
        mid_price = (best_bid_price + best_ask_price) / 2.0
        spread = best_ask_price - best_bid_price
        relative_spread = spread / mid_price if mid_price > 0 else 0.0

        # Track mid-price for Roll measure
        mid_price_history.append(mid_price)

        # ── MLOFI (levels 1-10, 1/k weighted) ──
        mlofi_raw = 0.0
        mlofi_per_level = {}
        for k in range(1, MLOFI_LEVELS + 1):
            weight = 1.0 / k
            v_bid = sorted_bids[k - 1][1] if k <= len(sorted_bids) else 0.0
            v_ask = sorted_asks[k - 1][1] if k <= len(sorted_asks) else 0.0
            denom = v_bid + v_ask
            imb = (v_bid - v_ask) / denom if denom > 0 else 0.0
            mlofi_raw += weight * imb
            mlofi_per_level[f"mlofi_{k}"] = weight * imb

        # MAD normalise the aggregate MLOFI
        if len(mlofi_history) >= 10:
            arr = np.array(mlofi_history)
            median = np.median(arr)
            mad = np.median(np.abs(arr - median))
            mlofi_norm = (mlofi_raw - median) / mad if mad > 0 else 0.0
        else:
            mlofi_norm = 0.0
        mlofi_history.append(mlofi_raw)

        # ── OFI (Cont et al. 2014) ──
        ofi = 0.0
        if prev_best_bid_price > 0:
            if best_bid_price > prev_best_bid_price:
                delta_bid = best_bid_vol
            elif best_bid_price == prev_best_bid_price:
                delta_bid = best_bid_vol - prev_best_bid_vol
            else:
                delta_bid = -prev_best_bid_vol

            if best_ask_price < prev_best_ask_price:
                delta_ask = best_ask_vol
            elif best_ask_price == prev_best_ask_price:
                delta_ask = best_ask_vol - prev_best_ask_vol
            else:
                delta_ask = -prev_best_ask_vol

            ofi = delta_bid - delta_ask

        prev_best_bid_vol = best_bid_vol
        prev_best_ask_vol = best_ask_vol
        prev_best_bid_price = best_bid_price
        prev_best_ask_price = best_ask_price

        # ── VPIN (window=15 buckets) ──
        total_bid_vol = sum(v for _, v in sorted_bids)
        total_ask_vol = sum(v for _, v in sorted_asks)
        volume_buys.append(total_bid_vol)
        volume_sells.append(total_ask_vol)

        vpin = 0.5  # default during warmup
        if len(volume_buys) >= VPIN_WINDOW:
            vpin_sum = 0.0
            valid_buckets = 0
            for j in range(-VPIN_WINDOW, 0):
                vb = volume_buys[j]
                vs = volume_sells[j]
                total = vb + vs
                if total > 0:
                    vpin_sum += abs(vs - vb) / total
                    valid_buckets += 1
            vpin = vpin_sum / valid_buckets if valid_buckets > 0 else 0.5

        # ── VWAP deviation ──
        total_vol = total_bid_vol + total_ask_vol
        vwap_num.append(mid_price * total_vol)
        vwap_den.append(total_vol)

        vwap_dev = 0.0
        if len(vwap_den) >= 10:
            total_vwap_vol = sum(vwap_den)
            if total_vwap_vol > 0:
                vwap = sum(vwap_num) / total_vwap_vol
                vwap_dev = (mid_price - vwap) / vwap if vwap > 0 else 0.0

        # ── Roll measure (from L2 mid-price returns) ──
        roll = 0.0
        if len(mid_price_history) >= ROLL_WINDOW + 1:
            prices = np.array(mid_price_history)
            returns = np.diff(prices[-ROLL_WINDOW - 1:])
            if len(returns) >= 2:
                cov = np.cov(returns[:-1], returns[1:])[0, 1]
                roll = float(2 * np.sqrt(abs(cov)))

        # ── Build feature row ──
        feature_row = {
            "cts": cts if cts else ts,
            "mid_price": mid_price,
            "spread": spread,
            "relative_spread": relative_spread,
            "mlofi": mlofi_norm,
            "ofi": ofi,
            "vpin": vpin,
            "vwap_deviation": vwap_dev,
            "roll": roll,
        }
        # Add per-level MLOFI
        feature_row.update(mlofi_per_level)

        rows.append(feature_row)

    return rows


def process_symbol(
    symbol: str,
    start: datetime,
    end: datetime,
    l2_dir: Path,
    output_dir: Path,
) -> dict:
    """Process all days for one symbol."""
    stats = {"success": 0, "skipped": 0, "failed": 0, "total_rows": 0}

    d = start
    while d <= end:
        ds = d.strftime("%Y-%m-%d")
        l2_path = l2_dir / symbol / f"{ds}_{symbol}_ob200.parquet"
        out_path = output_dir / symbol / f"{ds}_{symbol}_features.parquet"

        if out_path.exists():
            stats["skipped"] += 1
            d += timedelta(days=1)
            continue

        if not l2_path.exists():
            logger.warning("  %s %s: L2 file missing, skipping", symbol, ds)
            stats["failed"] += 1
            d += timedelta(days=1)
            continue

        try:
            start_time = time.time()
            feature_rows = reconstruct_and_compute(l2_path)

            if not feature_rows:
                logger.warning("  %s %s: no feature rows produced", symbol, ds)
                stats["failed"] += 1
                d += timedelta(days=1)
                continue

            # Add symbol column
            for row in feature_rows:
                row["symbol"] = symbol

            # Write Parquet
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_df = pl.DataFrame(feature_rows)
            out_df.write_parquet(out_path, compression="zstd", compression_level=12)

            elapsed = time.time() - start_time
            size_mb = out_path.stat().st_size / (1024 * 1024)
            stats["success"] += 1
            stats["total_rows"] += len(feature_rows)

            logger.info(
                "  %s %s: ✓ %d rows, %.1f MB (%.1fs)",
                symbol, ds, len(feature_rows), size_mb, elapsed,
            )

        except Exception as e:
            logger.error("  %s %s: ERROR %s", symbol, ds, str(e)[:100])
            import traceback
            traceback.print_exc()
            stats["failed"] += 1

        d += timedelta(days=1)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Build feature matrices from L2 order book data")
    parser.add_argument("--symbols", type=str, default="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT")
    parser.add_argument("--start-date", type=str, required=True)
    parser.add_argument("--end-date", type=str, required=True)
    parser.add_argument("--l2-dir", type=str, default="/data/parquet/orderbook")
    parser.add_argument("--output-dir", type=str, default="/data/features")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    l2_dir = Path(args.l2_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    start = datetime.strptime(args.start_date, "%Y-%m-%d")
    end = datetime.strptime(args.end_date, "%Y-%m-%d")

    logger.info("=" * 60)
    logger.info("Feature Engineering Pipeline")
    logger.info("Symbols: %s", ", ".join(symbols))
    logger.info("Period:  %s to %s", args.start_date, args.end_date)
    logger.info("L2 dir:  %s", l2_dir)
    logger.info("Output:  %s", output_dir)
    logger.info("Free disk: %.1f GB", shutil.disk_usage(output_dir).free / (1024**3))
    logger.info("=" * 60)

    start_time = time.time()

    for sym in symbols:
        logger.info("[%s] Starting feature computation...", sym)
        stats = process_symbol(sym, start, end, l2_dir, output_dir)
        logger.info("[%s] DONE: %s", sym, stats)

    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info("FINISHED in %.1f minutes", elapsed / 60)
    logger.info("Free disk: %.1f GB", shutil.disk_usage(output_dir).free / (1024**3))


if __name__ == "__main__":
    main()
